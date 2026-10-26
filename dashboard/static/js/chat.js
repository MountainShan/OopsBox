// dashboard/static/js/chat.js
// Chat with a project's agents. Each agent's conversation is a list of
// blocks streamed from /api/chat/<project>/<agent>/stream (SSE); this file
// renders them and sends prompts, answers and settings back. The controls
// (model, effort, mode, slash commands, images) are built from whatever the
// agent advertises in its state. A slash command that is not a prompt runs
// through the agent's own command interface and comes back as a `command`
// block — a message, a list to pick from, or a panel of data (chat-ui.js).

const Chat = (() => {
  const STATUS_LABEL = {
    stopped: 'stopped', starting: 'starting…', idle: 'idle', working: 'working…', error: 'error',
  };
  const TOOL_ICON = { execute: '❯', read: '◱', edit: '✎', search: '⌕', other: '◆' };
  const TOOL_STATUS = {
    running: 'running', completed: '', failed: 'failed', declined: 'denied',
    interrupted: 'interrupted', cancelled: 'cancelled',
  };
  const ALIVE = ['starting', 'idle', 'working'];
  // what each command kind does when picked from the slash list
  const KIND_ICON = { prompt: '', action: '▶', selection: '☰', panel: '▤', local: '⌂', terminal: '⧉' };

  const views = {};      // project name -> view
  let source = null;     // the one open EventSource; browsers cap connections per host
  let sourceKey = null;
  let markedReady = false;

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  async function ensureMarked() {
    if (markedReady) return;
    try {
      await _ensureMarked();  // viewer.js loads it from the CDN
      // Agent output is untrusted: raw HTML inside markdown is shown as text,
      // never rendered, and links/images only keep safe URL schemes.
      const safe = (href) => /^(https?:|mailto:|#|\/(?!\/)|\.{0,2}\/|[\w.-]+(\/|$))/i.test(href || '');
      marked.use({
        renderer: {
          html: (html) => esc(typeof html === 'string' ? html : html?.text),
          link: (href, title, text) => safe(href)
            ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer"${title ? ` title="${esc(title)}"` : ''}>${text}</a>`
            : text,
          image: (href, title, text) => safe(href) && /^https?:/i.test(href)
            ? `<img src="${esc(href)}" alt="${esc(text)}" style="max-width:100%">`
            : esc(text),
        },
      });
      markedReady = true;
    } catch (_) { /* fall back to plain text */ }
  }

  function md(text) {
    if (markedReady) {
      try { return marked.parse(text || '', { breaks: true, gfm: true }); } catch (_) {}
    }
    return `<p style="white-space:pre-wrap">${esc(text)}</p>`;
  }

  const api_ = (view, action, body = null) =>
    api.post(`/api/chat/${encodeURIComponent(view.name)}/${view.agent}/${action}`, body);

  // ── View (one per project) ──────────────────────
  function mount(name, container) {
    const el = document.createElement('div');
    el.className = 'chat-panel';
    el.innerHTML = `
      <div class="chat-bar">
        <div class="chat-agents"></div>
        <span class="chat-status"></span>
        <span class="nav-spacer"></span>
        <span class="chat-usage" title="Context usage"><span class="chat-usage-bar"><span></span></span><span class="chat-usage-text"></span></span>
        <label class="chat-autopilot" title="Allow every action automatically, without asking"><input type="checkbox"><span class="bar-label"> Autopilot</span></label>
        <button class="btn-icon chat-new" title="Start a new conversation (the old one stays on the server)">+<span class="bar-label"> New chat</span></button>
        <button class="btn-icon chat-history" title="Other conversations">🕘</button>
        <button class="btn-icon chat-settings" title="Config files and launch arguments">⚙</button>
        <button class="btn-icon chat-handoff" title="Continue this conversation with the CLI in the Terminal tab">⧉</button>
        <button class="btn-icon chat-power" title="Stop agent">⏻</button>
      </div>
      <div class="chat-thread"></div>
      <div class="chat-composer">
        <div class="chat-slash" hidden></div>
        <div class="chat-attachments" hidden></div>
        <textarea rows="1"></textarea>
        <div class="chat-tools">
          <button class="btn-icon chat-attach" title="Attach files or images (you can also paste or drag them in); saved to the project folder">📎</button>
          <input type="file" multiple hidden>
          <select class="chat-select chat-mode" title="Mode"></select>
          <select class="chat-select chat-model" title="Model"></select>
          <select class="chat-select chat-effort" title="Reasoning effort"></select>
          <span class="nav-spacer"></span>
          <span class="chat-queued"></span>
          <button class="btn-secondary chat-interrupt" hidden>Interrupt</button>
          <button class="btn-primary chat-send">Send</button>
        </div>
      </div>`;
    container.appendChild(el);

    const $ = (sel) => el.querySelector(sel);
    const view = {
      name, el, $,
      agent: null,
      agents: [],
      state: { status: 'stopped' },
      blocks: {}, nodes: {},
      pendingRender: new Set(),
      attachments: [],        // {mime, data(base64), url}
      autoStarted: {},        // agent -> true once we tried starting it
      slashIndex: 0,
      thread: $('.chat-thread'),
      input: $('textarea'),
    };
    views[name] = view;

    view.input.addEventListener('keydown', e => onKey(view, e));
    view.input.addEventListener('input', () => { autosize(view.input); updateSlash(view); });
    view.input.addEventListener('paste', e => onPaste(view, e));
    const composer = $('.chat-composer');
    composer.addEventListener('dragover', e => { e.preventDefault(); composer.classList.add('drop'); });
    composer.addEventListener('dragleave', () => composer.classList.remove('drop'));
    composer.addEventListener('drop', e => {
      e.preventDefault();
      composer.classList.remove('drop');
      if (e.dataTransfer?.files?.length) addFiles(view, e.dataTransfer.files);
    });
    $('.chat-send').onclick = () => send(view);
    $('.chat-interrupt').onclick = () => api_(view, 'cancel').catch(e => showToast(e.message, true));
    $('.chat-power').onclick = () => togglePower(view);
    $('.chat-new').onclick = () => newConversation(view);
    $('.chat-history').onclick = () => openConversations(view);
    $('.chat-settings').onclick = () => openSettings(view);
    $('.chat-handoff').onclick = () => handoff(view);
    $('.chat-autopilot input').onchange = e =>
      api_(view, 'autopilot', { enabled: e.target.checked }).catch(err => showToast(err.message, true));
    $('.chat-attach').onclick = () => $('input[type=file]').click();
    $('input[type=file]').onchange = e => { addFiles(view, e.target.files); e.target.value = ''; };
    $('.chat-mode').onchange = e => setting(view, 'mode', { mode: e.target.value });
    $('.chat-model').onchange = e => setting(view, 'model', { model: e.target.value });
    $('.chat-effort').onchange = e => setting(view, 'model', { effort: e.target.value });
    return view;
  }

  function autosize(ta) {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 220) + 'px';
  }

  // ── Showing / hiding ────────────────────────────
  // agent to open a project's chat on next time it shows (a notification
  // was tapped)
  const preferred = {};
  function prefer(name, agent) { preferred[name] = agent; }

  async function show(name, container) {
    const view = views[name] || mount(name, container);
    view.el.style.display = 'flex';
    ensureMarked();
    try {
      const info = await api.get(`/api/chat/${encodeURIComponent(name)}`);
      view.agents = info.agents;
      view.projectType = info.type;
      view.uploadMaxMb = info.upload_max_mb;
      const want = preferred[name];
      delete preferred[name];
      if (want && info.agents.some(a => a.name === want)) view.agent = want;
      else if (!view.agent || !info.agents.some(a => a.name === view.agent)) view.agent = info.default;
    } catch (e) {
      showToast('Could not load the agent list: ' + e.message, true);
    }
    renderAgents(view);
    if (view.agent) connect(view);
    else view.thread.innerHTML = '<div class="chat-empty">This project\'s classification allows no agents.</div>';
  }

  function hide() {
    if (source) { source.close(); source = null; sourceKey = null; }
  }

  function selectAgent(view, agent) {
    if (view.agent === agent) return;
    view.agent = agent;
    renderAgents(view);
    connect(view);
  }

  function connect(view) {
    const key = view.name + '/' + view.agent;
    if (source && sourceKey === key) return;
    hide();
    sourceKey = key;
    view.input.placeholder = `Message ${view.agent}…   Enter to send · Shift+Enter for a new line · / for commands`;
    source = new EventSource(`/api/chat/${encodeURIComponent(view.name)}/${view.agent}/stream`);
    source.onmessage = (e) => {
      let ev;
      try { ev = JSON.parse(e.data); } catch (_) { return; }
      handle(view, ev);
    };
    // EventSource reconnects by itself; each reconnect starts with a reset.
  }

  function handle(view, ev) {
    if (ev.type === 'reset') {
      view.blocks = {};
      view.nodes = {};
      view.thread.innerHTML = '';
      ev.blocks.forEach(b => { view.blocks[b.id] = b; renderBlock(view, b); });
      if (!ev.blocks.length) view.thread.innerHTML = '<div class="chat-empty">Send a message to start the conversation.</div>';
      setState(view, ev.state);
      scrollToEnd(view, true);
      autoStart(view);
    } else if (ev.type === 'block') {
      // what you just sent is followed even if you had scrolled up
      const stick = nearEnd(view) || view.followNext;
      if (ev.block.kind === 'user' || ev.block.kind === 'command') view.followNext = false;
      view.thread.querySelector('.chat-empty')?.remove();
      view.blocks[ev.block.id] = ev.block;
      renderBlock(view, ev.block);
      if (ev.block.kind === 'command') commandFollowUp(view, ev.block);
      if (stick) scrollToEnd(view);
    } else if (ev.type === 'delta') {
      const b = view.blocks[ev.id];
      if (!b) return;
      b[ev.field] = (b[ev.field] || '') + ev.delta;
      scheduleRender(view, ev.id);
    } else if (ev.type === 'state') {
      setState(view, ev.state);
    }
  }

  // Opening an agent starts it, so it is ready by the time you type. Only
  // once per page load: if it was stopped by hand or failed, it stays so.
  function autoStart(view) {
    if (view.state.status !== 'stopped' || view.state.handed_off || view.autoStarted[view.agent]) return;
    view.autoStarted[view.agent] = true;
    api_(view, 'start').then(() => window.onChatActivity?.(view.name)).catch(() => {});
  }

  // Streaming deltas arrive many per second; render at most once a frame.
  function scheduleRender(view, id) {
    view.pendingRender.add(id);
    if (view.pendingRender.size > 1) return;
    requestAnimationFrame(() => {
      const stick = nearEnd(view);
      view.pendingRender.forEach(i => view.blocks[i] && renderBlock(view, view.blocks[i]));
      view.pendingRender.clear();
      if (stick) scrollToEnd(view);
    });
  }

  function nearEnd(view) {
    const t = view.thread;
    return t.scrollHeight - t.scrollTop - t.clientHeight < 80;
  }
  function scrollToEnd(view, instant) {
    const t = view.thread;
    t.scrollTo({ top: t.scrollHeight, behavior: instant ? 'auto' : 'smooth' });
  }

  // ── State and controls ──────────────────────────
  function setState(view, state) {
    view.state = state;
    const s = state.status;
    const $ = view.$;
    const pill = $('.chat-status');
    pill.className = 'chat-status st-' + s;
    // idle / starting / working show as the agent button's dot or spinner;
    // the label is only for states that need words
    pill.textContent = s === 'stopped' && state.handed_off ? 'in Terminal'
      : ['idle', 'starting', 'working'].includes(s) ? '' : (STATUS_LABEL[s] || s);
    pill.title = state.error || '';
    $('.chat-autopilot input').checked = !!state.autopilot;
    $('.chat-autopilot').classList.toggle('on', !!state.autopilot);
    const working = s === 'working';
    $('.chat-interrupt').hidden = !working;
    $('.chat-send').textContent = working ? 'Steer' : 'Send';
    $('.chat-send').title = working ? 'Add a message while the agent is working' : '';
    $('.chat-send').disabled = s === 'starting';
    $('.chat-queued').textContent = state.queued ? `${state.queued} queued` : '';
    const power = $('.chat-power');
    power.classList.toggle('on', ALIVE.includes(s));
    power.title = ALIVE.includes(s) ? 'Stop agent' : 'Start agent';
    $('.chat-history').hidden = !state.features?.conversations;
    $('.chat-handoff').hidden = !state.features?.handoff;

    fillSelect($('.chat-mode'), state.modes, state.mode);
    fillSelect($('.chat-model'), state.models, state.model);
    const model = (state.models || []).find(m => m.id === state.model);
    const efforts = (model?.efforts || []).map(e => ({ id: e, name: 'Effort ' + e }));
    fillSelect($('.chat-effort'), efforts, state.effort || model?.default_effort, 'Effort default');

    const usage = $('.chat-usage');
    usage.hidden = !state.usage;
    if (state.usage) {
      const pct = state.usage.percent;
      usage.querySelector('.chat-usage-bar span').style.width = pct + '%';
      usage.classList.toggle('high', pct >= 80);
      usage.querySelector('.chat-usage-text').textContent = Math.round(pct) + '%';
      usage.title = 'Context usage: ' + (state.usage.label || pct + '%');
    }
    const a = view.agents.find(x => x.name === view.agent);
    if (a) { a.status = s; renderAgents(view); }
    updateSlash(view);
  }

  function fillSelect(sel, items, current, emptyLabel) {
    items = items || [];
    sel.hidden = !items.length;
    const sig = JSON.stringify([items.map(i => i.id), current, emptyLabel]);
    if (sel.dataset.sig === sig) return;
    sel.dataset.sig = sig;
    sel.innerHTML = '';
    if (emptyLabel && !items.some(i => i.id === current)) {
      sel.appendChild(new Option(emptyLabel, '', true, true));
    }
    const groups = {};
    items.forEach(i => {
      const o = new Option(i.name || i.id, i.id, false, i.id === current);
      if (i.description) o.title = i.description;
      if (!i.group) { sel.appendChild(o); return; }
      if (!groups[i.group]) {
        groups[i.group] = document.createElement('optgroup');
        groups[i.group].label = i.group;
        sel.appendChild(groups[i.group]);
      }
      groups[i.group].appendChild(o);
    });
  }

  async function setting(view, action, body) {
    try { await api_(view, action, body); }
    catch (e) { showToast(e.message, true); setState(view, view.state); }
  }

  function renderAgents(view) {
    const bar = view.$('.chat-agents');
    view.el.dataset.agent = view.agent || '';  // the chat takes this agent's colours
    bar.innerHTML = '';
    view.agents.forEach(a => {
      const btn = document.createElement('button');
      btn.className = 'chat-agent' + (a.name === view.agent ? ' active' : '');
      btn.dataset.agent = a.name;
      // working (or starting): a spinner in the agent's colour instead of the dot
      const busy = a.status === 'working' || a.status === 'starting';
      const mark = busy ? '<span class="agent-spin"></span>'
        : `<span class="dot ${ALIVE.includes(a.status) ? 'dot-green' : 'dot-gray'}"></span>`;
      btn.innerHTML = `${mark}${esc(a.name)}` + (a.experimental ? '<span class="agent-exp">exp</span>' : '');
      btn.title = busy ? `${a.name} is ${a.status}` : a.experimental
        ? `${a.name} (experimental: runs over ACP; not yet checked against its own CLI)` : '';
      btn.onclick = () => selectAgent(view, a.name);
      bar.appendChild(btn);
    });
  }

  // ── Blocks ──────────────────────────────────────
  function renderBlock(view, b) {
    let node = view.nodes[b.id];
    const fresh = !node;
    if (fresh) {
      node = document.createElement('div');
      view.nodes[b.id] = node;
      view.thread.appendChild(node);
    }
    switch (b.kind) {
      case 'user': renderUser(view, node, b); break;
      case 'assistant': renderAssistant(node, b); break;
      case 'thought': renderThought(node, b, fresh); break;
      case 'tool': renderTool(node, b, fresh); break;
      case 'permission': renderPermission(view, node, b); break;
      case 'question': renderQuestion(view, node, b); break;
      case 'plan': renderPlan(node, b); break;
      case 'notice': renderNotice(node, b); break;
      case 'command': renderCommand(view, node, b, fresh); break;
      default: node.className = 'chat-notice'; node.textContent = b.kind;
    }
  }

  function fileUrl(view, path, inline = true) {
    const base = view.projectType === 'ssh' ? '/api/ssh' : '/api/files';
    return `${base}/${encodeURIComponent(view.name)}/download?path=${encodeURIComponent(path)}${inline ? '&inline=true' : ''}`;
  }

  const humanSize = (n) => n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';

  function renderUser(view, node, b) {
    node.className = 'chat-user' + (b.queued ? ' queued' : '') + (b.dropped ? ' dropped' : '');
    const imgs = (b.images || []).map(f =>
      `<img src="/api/chat/${encodeURIComponent(view.name)}/${view.agent}/uploads/${encodeURIComponent(f)}" alt="">`).join('') +
      (b.files || []).filter(f => f.mime?.startsWith('image/')).map(f =>
        `<a href="${fileUrl(view, f.path)}" target="_blank" rel="noopener"><img src="${fileUrl(view, f.path)}" alt="${esc(f.name)}"></a>`).join('');
    const docs = (b.files || []).filter(f => !f.mime?.startsWith('image/')).map(f =>
      `<a class="chat-file" href="${fileUrl(view, f.path, false)}" title="${esc(f.path)}">📄 ${esc(f.name)} <small>${humanSize(f.size || 0)}</small></a>`).join('');
    node.innerHTML = (imgs ? `<div class="chat-user-images">${imgs}</div>` : '') +
      (docs ? `<div class="chat-user-files">${docs}</div>` : '') +
      `<div class="chat-user-text">${esc(b.text)}</div>` +
      (b.queued ? '<div class="chat-user-tag">Queued; sent after this turn</div>' : '') +
      (b.dropped ? '<div class="chat-user-tag">Cancelled</div>' : '');
  }

  // kiro marks where it took a message sent mid-turn with
  // "[STEERING steer-…: what it did]"; show just the note, as its TUI does.
  const STEER_DONE = /\[STEERING (steer-[^\s:]+): (.+?)\]/gs;
  const STEER_OPEN = /\[STEERING (steer-[^\s:]+)(?::[^\]]*)?$/s;

  function renderAssistant(node, b) {
    node.className = 'chat-assistant md-preview';
    const text = (b.text || '').replace(STEER_DONE, '*(steered: $2)*').replace(STEER_OPEN, '');
    node.innerHTML = md(text);
  }

  // Collapsible rows keep whatever open/closed state the user gave them.
  function details(node, fresh, cls) {
    node.className = cls;
    if (fresh || !node.firstElementChild) {
      node.innerHTML = '<details><summary></summary><div class="chat-body"></div></details>';
    }
    return { summary: node.querySelector('summary'), body: node.querySelector('.chat-body') };
  }

  function renderThought(node, b, fresh) {
    const text = (b.text || '').trim();
    if (!text) { node.className = 'chat-hidden'; node.innerHTML = ''; return; }
    const { summary, body } = details(node, fresh, 'chat-row chat-thought');
    const first = text.split('\n').find(l => l.trim()) || '';
    summary.innerHTML = `<span class="chat-row-icon">✻</span><span class="chat-row-title">${esc(first.replace(/\*\*/g, ''))}</span>`;
    body.textContent = text;
  }

  function renderTool(node, b, fresh) {
    const { summary, body } = details(node, fresh, 'chat-row chat-tool st-' + (b.status || 'running'));
    const icon = b.status === 'running' ? '<span class="chat-spin"></span>' : (TOOL_ICON[b.tool] || TOOL_ICON.other);
    const status = TOOL_STATUS[b.status] ?? b.status ?? '';
    summary.innerHTML =
      `<span class="chat-row-icon">${icon}</span>` +
      `<span class="chat-row-title">${esc(b.title || 'tool')}</span>` +
      (status || b.exit_code ? `<span class="chat-row-status">${esc(status)}${b.exit_code ? ' · exit ' + esc(b.exit_code) : ''}</span>` : '');
    let html = '';
    if (b.detail) html += `<pre class="chat-pre">${esc(b.detail)}</pre>`;
    if (b.output) html += `<pre class="chat-pre chat-output">${esc(b.output)}</pre>`;
    body.innerHTML = html || '<div class="chat-dim">(no output)</div>';
  }

  function renderPermission(view, node, b) {
    const opts = b.options || [];
    if (b.status !== 'pending') {
      // answered: one quiet line instead of the whole card
      const chosen = opts.find(o => o.id === b.choice);
      const ok = chosen && chosen.kind !== 'reject';
      const label = b.status === 'cancelled' ? 'Cancelled' : (chosen ? chosen.label : b.choice);
      node.className = 'chat-perm-line ' + (ok ? 'ok' : 'no');
      node.innerHTML = `<span>${ok ? '✓' : '✕'} ${esc(label)}${b.auto ? ' (Autopilot)' : ''}</span>` +
        `<span class="chat-perm-line-title">${esc(b.title)}</span>`;
      node.title = b.detail || '';
      return;
    }
    node.className = 'chat-permission';
    const detail = b.detail
      ? (b.markdown ? `<div class="chat-perm-md md-preview">${md(b.detail)}</div>` : `<pre class="chat-pre">${esc(b.detail)}</pre>`)
      : '';
    node.innerHTML = `<div class="chat-perm-title">⚠ ${esc(b.title)}</div>${detail}` +
      '<div class="chat-perm-actions">' + opts.map(o =>
        `<button class="${o.kind === 'allow_once' ? 'btn-primary' : 'btn-secondary'}" data-opt="${esc(o.id)}">${esc(o.label)}</button>`
      ).join('') + '</div>';
    node.querySelectorAll('button[data-opt]').forEach(btn => {
      btn.onclick = async () => {
        node.querySelectorAll('button').forEach(x => x.disabled = true);
        try { await api_(view, 'permission', { block_id: b.id, option_id: btn.dataset.opt }); }
        catch (e) { showToast(e.message, true); node.querySelectorAll('button').forEach(x => x.disabled = false); }
      };
    });
  }

  function renderQuestion(view, node, b) {
    const qs = b.questions || [];
    if (b.status !== 'pending') {
      node.className = 'chat-perm-line ' + (b.status === 'answered' ? 'ok' : 'no');
      const summary = b.status === 'answered'
        ? qs.map(q => `${q.header || q.question}: ${(b.answers?.[q.id] || []).join(', ') || '—'}`).join('; ')
        : 'Cancelled';
      node.innerHTML = `<span>? ${esc(summary)}</span>`;
      return;
    }
    if (node.dataset.rendered === b.id) return;  // keep choices in progress
    node.dataset.rendered = b.id;
    node.className = 'chat-permission chat-question';
    node.innerHTML = qs.map(q => `
      <div class="chat-q" data-q="${esc(q.id)}" data-multi="${q.multi ? 1 : 0}">
        <div class="chat-perm-title">${q.header ? `<span class="chat-q-header">${esc(q.header)}</span>` : ''}${esc(q.question)}</div>
        <div class="chat-q-options">${(q.options || []).map(o =>
          `<button class="chat-q-opt" data-label="${esc(o.label)}" title="${esc(o.description || '')}">${esc(o.label)}${o.description ? `<small>${esc(o.description)}</small>` : ''}</button>`).join('')}</div>
        ${q.other ? `<input class="chat-q-other" type="${q.secret ? 'password' : 'text'}" placeholder="${q.options?.length ? 'Or type another answer…' : 'Type an answer…'}">` : ''}
      </div>`).join('') +
      '<div class="chat-perm-actions"><button class="btn-primary chat-q-submit">Submit answer</button></div>';
    node.querySelectorAll('.chat-q').forEach(qel => {
      qel.querySelectorAll('.chat-q-opt').forEach(btn => btn.onclick = () => {
        if (qel.dataset.multi !== '1') qel.querySelectorAll('.chat-q-opt').forEach(x => x.classList.remove('on'));
        btn.classList.toggle('on');
      });
    });
    node.querySelector('.chat-q-submit').onclick = async () => {
      const answers = {};
      node.querySelectorAll('.chat-q').forEach(qel => {
        const picked = [...qel.querySelectorAll('.chat-q-opt.on')].map(x => x.dataset.label);
        const other = qel.querySelector('.chat-q-other')?.value.trim();
        if (other) picked.push(other);
        answers[qel.dataset.q] = picked;
      });
      try { await api_(view, 'answer', { block_id: b.id, answers }); }
      catch (e) { showToast(e.message, true); }
    };
  }

  function renderPlan(node, b) {
    node.className = 'chat-plan';
    node.innerHTML = `<div class="chat-plan-title">Plan</div><div class="chat-plan-body">${esc(b.text)}</div>`;
  }

  function renderNotice(node, b) {
    node.className = 'chat-notice lv-' + (b.level || 'info');
    node.textContent = b.text || '';
  }

  // ── Composer ────────────────────────────────────
  function onKey(view, e) {
    const pop = view.$('.chat-slash');
    if (!pop.hidden) {
      const items = pop.querySelectorAll('.chat-slash-item');
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        view.slashIndex = (view.slashIndex + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items.forEach((x, i) => x.classList.toggle('on', i === view.slashIndex));
        items[view.slashIndex]?.scrollIntoView({ block: 'nearest' });
        return;
      }
      if ((e.key === 'Tab' || e.key === 'Enter') && !e.isComposing && items[view.slashIndex]) {
        e.preventDefault();
        pickSlash(view, items[view.slashIndex].dataset.name);
        return;
      }
      if (e.key === 'Escape') { pop.hidden = true; return; }
    }
    // isComposing: Enter that confirms an IME candidate (注音/倉頡) must not send
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      send(view);
    }
  }

  function updateSlash(view) {
    const pop = view.$('.chat-slash');
    const text = view.input.value;
    const cmds = view.state.commands || [];
    let hits = [];
    const m = /^\/(\S*)$/.exec(text);
    // "/mcp " — the command is chosen; offer its subcommands
    const sub = /^(\/\S+) (\S*)$/.exec(text);
    if (m && cmds.length) {
      const q = m[1].toLowerCase();
      hits = cmds.filter(c => c.name.slice(1).toLowerCase().includes(q))
        .sort((a, b) => a.name.toLowerCase().indexOf(q) - b.name.toLowerCase().indexOf(q))
        .slice(0, 60)
        .map(c => ({ insert: c.name, name: c.name, hint: c.hint, description: c.description, kind: c.kind }));
    } else if (/(^|\s)@([^\s@]*)$/.test(text)) {
      mentionFiles(view, text);
      return;
    } else if (sub) {
      const c = cmds.find(x => x.name === sub[1]);
      const q = sub[2].toLowerCase();
      hits = (c?.subcommands || []).filter(sc => sc.name.toLowerCase().startsWith(q))
        .map(sc => ({ insert: `${c.name} ${sc.name}`, name: sc.name, hint: sc.hint, description: sc.description }));
    }
    if (!hits.length) { pop.hidden = true; return; }
    view.slashIndex = Math.min(view.slashIndex, hits.length - 1);
    pop.innerHTML = hits.map((c, i) =>
      `<div class="chat-slash-item${i === view.slashIndex ? ' on' : ''}" data-name="${esc(c.insert)}">` +
      `<span class="chat-slash-name">${c.kind && KIND_ICON[c.kind] ? `<b class="chat-kind" title="${esc(c.kind)}">${KIND_ICON[c.kind]}</b>` : ''}${esc(c.name)}${c.hint ? ` <i>${esc(c.hint)}</i>` : ''}</span>` +
      `<span class="chat-slash-desc">${esc((c.description || '').split('\n')[0])}</span></div>`).join('');
    pop.querySelectorAll('.chat-slash-item').forEach(x =>
      x.onmousedown = (e) => { e.preventDefault(); pickSlash(view, x.dataset.name); });
    pop.hidden = false;
  }

  // "@part" at the end of the input: offer project files, newest query wins
  function mentionFiles(view, text) {
    const at = /(^|\s)@([^\s@]*)$/.exec(text);
    const seq = (view.mentionSeq = (view.mentionSeq || 0) + 1);
    clearTimeout(view.mentionTimer);
    view.mentionTimer = setTimeout(async () => {
      let files = [];
      try {
        files = (await api.get(`/api/chat/${encodeURIComponent(view.name)}/${view.agent}/files?q=${encodeURIComponent(at[2])}`)).files;
      } catch (_) {}
      if (seq !== view.mentionSeq || view.input.value !== text) return;
      const pop = view.$('.chat-slash');
      if (!files.length) { pop.hidden = true; return; }
      const head = text.slice(0, at.index + at[1].length);
      view.slashIndex = Math.min(view.slashIndex, files.length - 1);
      pop.innerHTML = files.map((f, i) =>
        `<div class="chat-slash-item${i === view.slashIndex ? ' on' : ''}" data-name="${esc(head + '@' + f)}">` +
        `<span class="chat-slash-name">@${esc(f.split('/').pop())}</span><span class="chat-slash-desc">${esc(f)}</span></div>`).join('');
      pop.querySelectorAll('.chat-slash-item').forEach(x =>
        x.onmousedown = (e) => { e.preventDefault(); pickSlash(view, x.dataset.name); });
      pop.hidden = false;
    }, 150);
  }

  function pickSlash(view, name) {
    view.input.value = name + ' ';
    view.$('.chat-slash').hidden = true;
    view.slashIndex = 0;
    view.input.focus();
    autosize(view.input);
  }

  function onPaste(view, e) {
    const files = [...(e.clipboardData?.files || [])];
    if (!files.length) return;
    e.preventDefault();
    addFiles(view, files);
  }

  // Files go to the project's upload folder as soon as they are picked;
  // the message then names them. view.attachments: {name, size, mime,
  // url (image preview), progress, path (once saved), error, xhr}
  function addFiles(view, files) {
    const max = (view.uploadMaxMb || 100) * 1048576;
    [...files].forEach(f => {
      if (f.size > max) { showToast(`${f.name} exceeds the ${view.uploadMaxMb} MB upload limit (change it in Settings)`, true); return; }
      const a = { name: f.name || 'pasted.png', size: f.size, mime: f.type, progress: 0 };
      if (f.type.startsWith('image/')) a.url = URL.createObjectURL(f);
      view.attachments.push(a);
      const form = new FormData();
      form.append('file', f, a.name);
      const xhr = new XMLHttpRequest();
      a.xhr = xhr;
      xhr.open('POST', `/api/chat/${encodeURIComponent(view.name)}/${view.agent}/attachments`);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) { a.progress = e.loaded / e.total; renderAttachments(view); } };
      xhr.onload = () => {
        let body = {};
        try { body = JSON.parse(xhr.responseText); } catch (_) {}
        if (xhr.status >= 200 && xhr.status < 300) Object.assign(a, { path: body.path, name: body.name, progress: 1 });
        else a.error = body.detail || (xhr.status === 413 ? 'File too large' : `Upload failed (${xhr.status})`);
        if (a.error) showToast(`${a.name}: ${a.error}`, true);
        renderAttachments(view);
      };
      xhr.onerror = () => { a.error = 'Upload failed'; renderAttachments(view); };
      xhr.send(form);
    });
    renderAttachments(view);
  }

  function renderAttachments(view) {
    const box = view.$('.chat-attachments');
    box.hidden = !view.attachments.length;
    box.innerHTML = view.attachments.map((a, i) => {
      const state = a.error ? `<small class="err">${esc(a.error)}</small>`
        : a.path ? `<small>${humanSize(a.size)}</small>`
        : `<small>${Math.round(a.progress * 100)}%</small>`;
      return `<span class="chat-thumb${a.error ? ' failed' : ''}${a.path ? '' : ' busy'}" title="${esc(a.path || a.name)}">` +
        (a.url ? `<img src="${a.url}" alt="">` : '<span class="chat-thumb-icon">📄</span>') +
        `<span class="chat-thumb-name">${esc(a.name)}${state}</span><button data-i="${i}" title="Remove">✕</button></span>`;
    }).join('');
    box.querySelectorAll('button').forEach(btn => btn.onclick = () => {
      const a = view.attachments[+btn.dataset.i];
      if (a && !a.path && !a.error) a.xhr?.abort();
      view.attachments.splice(+btn.dataset.i, 1);
      renderAttachments(view);
    });
    view.$('.chat-send').disabled = view.state.status === 'starting' || view.attachments.some(a => !a.path && !a.error);
  }

  async function send(view) {
    const text = view.input.value.trim();
    if (view.attachments.some(a => !a.path && !a.error)) { showToast('Files are still uploading'); return; }
    const files = view.attachments.filter(a => a.path).map(a => a.path);
    if ((!text && !files.length) || !view.agent) return;
    const keep = { text: view.input.value, attachments: view.attachments };
    view.input.value = '';
    view.attachments = [];
    renderAttachments(view);
    autosize(view.input);
    view.$('.chat-slash').hidden = true;
    view.followNext = true;
    try {
      await api_(view, 'prompt', { text, files });
      window.onChatActivity?.(view.name);
    } catch (e) {
      view.input.value = keep.text;  // keep what was typed
      view.attachments = keep.attachments;
      renderAttachments(view);
      autosize(view.input);
      showToast(e.message, true);
    }
  }

  async function togglePower(view) {
    const alive = ALIVE.includes(view.state.status);
    try { await api_(view, alive ? 'stop' : 'start'); } catch (e) { showToast(e.message, true); }
    window.onChatActivity?.(view.name);
  }

  // ── Command blocks ──────────────────────────────
  function renderCommand(view, node, b, fresh) {
    node.className = 'chat-command st-' + b.status;
    if (fresh || !node.firstElementChild) {
      node.innerHTML = '<div class="chat-command-head"></div><div class="chat-command-msg"></div><div class="chat-command-body"></div>';
    }
    const spec = (view.state.commands || []).find(c => c.name === b.command);
    const icon = b.status === 'running' ? '<span class="chat-spin"></span>' : (KIND_ICON[spec?.kind] || '▶');
    node.querySelector('.chat-command-head').innerHTML =
      `<span class="chat-row-icon">${icon}</span><code>${esc(b.command)}${b.value ? ' ' + esc(b.value) : ''}</code>` +
      (b.status === 'error' ? '<span class="chat-row-status">failed</span>' : '');
    const msg = node.querySelector('.chat-command-msg');
    msg.textContent = b.message || '';
    msg.hidden = !b.message;
    const body = node.querySelector('.chat-command-body');
    const sig = JSON.stringify([b.status, b.data, b.options, b.handoff]);
    if (body.dataset.sig === sig) return;
    body.dataset.sig = sig;
    body.innerHTML = '';
    if (b.status === 'choose' && b.options) {
      ChatUI.picker(body, b.options, (o) =>
        api_(view, 'command', { name: b.command, value: o.value, block_id: b.id })
          .catch(e => showToast(e.message, true)));
    } else if (b.data && typeof b.data === 'object' && !b.data.open && !b.data.fill && !b.data.copy) {
      ChatUI.renderPanel(body, view.agent, b.command, b.data, {
        run: (command, value) => runCommand(view, command, value),
        refresh: () => runCommand(view, b.command, b.value, b.id),
        openSettings: () => openSettings(view),
      });
    }
    if (b.handoff && view.state.features?.handoff) {
      body.appendChild(ChatUI.button('⧉ Open in Terminal', () => handoff(view), 'btn-primary'));
    }
  }

  function runCommand(view, name, value = '', blockId = null) {
    return api_(view, 'command', { name, value, block_id: blockId }).catch(e => showToast(e.message, true));
  }

  // Some commands answer by asking the browser to do something — once, when
  // the answer arrives, not again when the conversation is reloaded.
  function commandFollowUp(view, b) {
    if (b.status !== 'ok' || !b.data) return;
    view.followed = view.followed || new Set();
    if (view.followed.has(b.id)) return;
    view.followed.add(b.id);
    if (b.data.open === 'conversations') openConversations(view);
    if (typeof b.data.copy === 'string') {
      navigator.clipboard?.writeText(b.data.copy).catch(() => showToast('The browser blocked clipboard access', true));
    }
    if (typeof b.data.fill === 'string') {
      view.input.value = b.data.fill + '\n\n';
      autosize(view.input);
      view.input.focus();
    }
  }

  // ── Dialogs: conversations, settings, Terminal ──
  async function openConversations(view) {
    const body = ChatUI.el('div', 'cu-conversations', '<div class="chat-dim">Loading…</div>');
    const dlg = ChatUI.dialog(`${view.agent} conversations`, body);
    try {
      const { conversations } = await api.get(`/api/chat/${encodeURIComponent(view.name)}/${view.agent}/conversations`);
      body.innerHTML = conversations.length ? '' : '<div class="chat-dim">No other conversations in this project yet.</div>';
      conversations.forEach(c => {
        const row = ChatUI.el('button', 'cu-conv' + (c.current ? ' current' : ''),
          `<span class="cu-conv-title">${c.current ? '● ' : ''}${esc(c.title || '(untitled ' + c.id.slice(0, 8) + ')')}</span>` +
          `<span class="cu-conv-when">${esc(ChatUI.when(c.updated))}</span>`);
        row.disabled = c.current;
        row.onclick = async () => {
          if (!confirm('Switch to this conversation? The current one will be archived on the server.')) return;
          dlg.close();
          try { await api_(view, `conversations/${encodeURIComponent(c.id)}/resume`); }
          catch (e) { showToast(e.message, true); }
        };
        body.appendChild(row);
      });
    } catch (e) {
      body.innerHTML = `<div class="chat-dim">${esc(e.message)}</div>`;
    }
  }

  async function openSettings(view) {
    const base = `/api/chat/${encodeURIComponent(view.name)}/${view.agent}`;
    const body = ChatUI.el('div', 'cu-settings', '<div class="chat-dim">Loading…</div>');
    ChatUI.dialog(`${view.agent} settings`, body);
    let launch, files = [], filesError = '';
    try { launch = await api.get(base + '/launch'); } catch (e) { body.textContent = e.message; return; }
    try { files = (await api.get(base + '/config')).files; } catch (e) { filesError = e.message; }
    const restartBtn = () => ChatUI.button('Restart agent to apply settings', async () => {
      try { await api_(view, 'stop'); await api_(view, 'start'); showToast('Restarted'); }
      catch (e) { showToast(e.message, true); }
    });
    const items = [{ label: 'Launch arguments', render: (pane) => {
      pane.innerHTML = `<p class="chat-dim">Arguments appended to the <code>${esc(view.agent)}</code> command; they take effect when the agent restarts. ` +
        (launch.reserved_flags.length ? `Set by OopsBox and cannot be overridden: <code>${launch.reserved_flags.map(esc).join(' ')}</code>` : '') + '</p>';
      const input = ChatUI.el('textarea', 'cu-code');
      input.rows = 3;
      input.value = launch.extra_args;
      input.placeholder = 'e.g. --agent my-agent --trust-tools fs_read';
      const flags = ChatUI.el('div', 'cu-flags');
      launch.known_flags.forEach(f => {
        const chip = ChatUI.el('button', 'cu-flag', `<code>${esc(f.flag)}${f.arg ? ' ' + esc(f.arg) : ''}</code><span>${esc(f.description)}</span>`);
        chip.onclick = () => {
          input.value = (input.value.trim() + ' ' + f.flag + (f.arg ? ' ' : '')).trimStart();
          input.focus();
        };
        flags.appendChild(chip);
      });
      const save = ChatUI.button('Save', async () => {
        try {
          await api.put(base + '/launch', { extra_args: input.value });
          launch.extra_args = input.value;
          showToast('Saved' + (ALIVE.includes(view.state.status) ? '; agent restarted' : ''));
        } catch (e) { showToast(e.message, true); }
      }, 'btn-primary');
      pane.append(input, ChatUI.el('div', 'cu-actions'), flags);
      pane.querySelector('.cu-actions').appendChild(save);
    } }];
    files.forEach(f => items.push({ label: f.scope, render: (pane) => {
      pane.innerHTML = `<p class="chat-dim">${esc(f.description)} · <code>${esc(f.path)}</code>${f.exists ? '' : ' (not created yet)'}</p>`;
      const ta = ChatUI.el('textarea', 'cu-code');
      ta.rows = 18;
      ta.spellcheck = false;
      ta.value = f.content;
      ta.placeholder = f.format === 'json' ? '{\n}' : '# TOML';
      const actions = ChatUI.el('div', 'cu-actions');
      actions.appendChild(ChatUI.button('Save', async () => {
        try {
          const r = await api.put(base + '/config', { scope: f.scope, content: ta.value });
          f.content = ta.value; f.exists = true;
          showToast('Saved (previous file backed up as .oopsbox-bak)');
          if (r.restart_suggested && !actions.querySelector('.cu-restart')) {
            const b = restartBtn(); b.classList.add('cu-restart'); actions.appendChild(b);
          }
        } catch (e) { showToast(e.message, true); }
      }, 'btn-primary'));
      pane.append(ta, actions);
    } }));
    body.innerHTML = '';
    body.appendChild(ChatUI.tabs(items));
    if (filesError) body.appendChild(ChatUI.el('p', 'chat-dim', esc(filesError)));
  }

  async function handoff(view) {
    if (!confirm(`Continue this conversation with the ${view.agent} CLI in the Terminal tab? The web ${view.agent} session will be stopped first.`)) return;
    try {
      const r = await api_(view, 'handoff');
      window.openTerminalTab?.();
      Terminal.type(view.name, r.command + '\r');
    } catch (e) { showToast(e.message, true); }
  }

  async function newConversation(view) {
    if (!confirm(`Start a new conversation with ${view.agent}? The current one will be archived on the server.`)) return;
    try { await api_(view, 'new'); } catch (e) { showToast(e.message, true); }
  }

  // The project poll reports every agent's status ({agent: {status}} for the
  // running ones); the buttons of agents other than the one shown follow it.
  function syncStatuses(name, running) {
    const view = views[name];
    if (!view?.agents) return;
    let changed = false;
    view.agents.forEach(a => {
      if (a.name === view.agent) return;  // the shown agent has its own live stream
      const st = running?.[a.name]?.status || 'stopped';
      if (a.status !== st) { a.status = st; changed = true; }
    });
    if (changed) renderAgents(view);
  }

  return { show, hide, prefer, syncStatuses };
})();
