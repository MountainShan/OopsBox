// dashboard/static/js/chat-ui.js
// Pieces of the chat that stand in for an agent's terminal screens: option
// lists, panels of structured data, and dialogs. chat.js decides when to
// show them; nothing here talks to an agent directly.

const ChatUI = (() => {
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  // ── Option list (a selection command's choices) ──
  // options: [{value, label, description, group, active}]
  function picker(container, options, onPick) {
    container.innerHTML = '';
    const box = el('div', 'cu-picker');
    const filter = el('input', 'cu-picker-filter');
    filter.placeholder = 'Filter…';
    const list = el('div', 'cu-picker-list');
    box.append(filter, list);
    container.appendChild(box);
    const draw = () => {
      const q = filter.value.trim().toLowerCase();
      const hits = options.filter(o => !q || `${o.label} ${o.value} ${o.description}`.toLowerCase().includes(q));
      let group = null;
      list.innerHTML = '';
      hits.forEach(o => {
        if (o.group && o.group !== group) {
          group = o.group;
          list.appendChild(el('div', 'cu-picker-group', esc(group)));
        }
        const row = el('button', 'cu-picker-item' + (o.active ? ' active' : ''),
          `<span class="cu-picker-label">${o.active ? '● ' : ''}${esc(o.label)}</span>` +
          (o.description ? `<span class="cu-picker-desc">${esc(o.description)}</span>` : ''));
        row.onclick = () => {
          list.querySelectorAll('button').forEach(b => b.disabled = true);
          onPick(o);
        };
        list.appendChild(row);
      });
      if (!hits.length) list.appendChild(el('div', 'chat-dim', 'No matching options'));
    };
    filter.oninput = draw;
    filter.hidden = options.length <= 8;
    draw();
  }

  // ── Panels ──────────────────────────────────────
  // A command's answer is whatever JSON its agent returns. Commands with a
  // renderer of their own get it; anything else is drawn generically, so a
  // command added in a later CLI version still shows its data.
  const renderers = {};

  function register(agent, command, fn) { renderers[agent + ' ' + command] = fn; }

  // ctx: {run(command, value)} — lets a panel's buttons run follow-up commands
  function renderPanel(container, agent, command, data, ctx) {
    container.innerHTML = '';
    const fn = renderers[agent + ' ' + command];
    if (fn) {
      try { fn(container, data, ctx); return; } catch (e) { console.warn('panel renderer failed', e); container.innerHTML = ''; }
    }
    container.appendChild(generic(data, 0));
  }

  const isPlain = (v) => v === null || ['string', 'number', 'boolean'].includes(typeof v);

  function fmt(v) {
    if (v === null || v === undefined) return '<span class="chat-dim">—</span>';
    if (typeof v === 'boolean') return v ? '✓' : '✕';
    if (typeof v === 'number') return esc(Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2));
    const s = String(v);
    return s.includes('\n') ? `<pre class="chat-pre">${esc(s.trim())}</pre>` : esc(s);
  }

  function generic(v, depth) {
    if (isPlain(v)) return el('div', 'cu-plain', fmt(v));
    if (Array.isArray(v)) {
      if (!v.length) return el('div', 'chat-dim', '(empty)');
      if (v.every(isPlain)) return el('ul', 'cu-list', v.map(x => `<li>${fmt(x)}</li>`).join(''));
      if (v.every(x => x && typeof x === 'object' && !Array.isArray(x))) return table(v, depth);
    }
    if (typeof v === 'object') {
      const t = el('table', 'cu-kv');
      Object.entries(v).forEach(([k, val]) => {
        const tr = el('tr');
        tr.appendChild(el('th', '', esc(k)));
        const td = el('td');
        if (isPlain(val)) td.innerHTML = fmt(val);
        else if (depth >= 2) {
          const d = el('details');
          d.appendChild(el('summary', '', Array.isArray(val) ? `${val.length} items` : 'Expand'));
          d.appendChild(generic(val, depth + 1));
          td.appendChild(d);
        } else td.appendChild(generic(val, depth + 1));
        tr.appendChild(td);
        t.appendChild(tr);
      });
      return scroll(t);
    }
    return el('div', '', esc(JSON.stringify(v)));
  }

  function table(rows, depth) {
    const cols = [];
    rows.forEach(r => Object.keys(r).forEach(k => {
      if (!cols.includes(k) && isPlain(r[k])) cols.push(k);
    }));
    const nested = [...new Set(rows.flatMap(r => Object.keys(r).filter(k => !isPlain(r[k]))))];
    const t = el('table', 'cu-table');
    t.innerHTML = `<thead><tr>${cols.slice(0, 8).map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>`;
    const tb = el('tbody');
    rows.forEach(r => {
      const tr = el('tr', '', cols.slice(0, 8).map(c => `<td>${fmt(r[c])}</td>`).join(''));
      tb.appendChild(tr);
      const extra = Object.fromEntries([...cols.slice(8), ...nested].filter(k => r[k] !== undefined).map(k => [k, r[k]]));
      if (Object.keys(extra).length) {
        const more = el('tr', 'cu-more');
        const td = el('td');
        td.colSpan = Math.min(cols.length, 8) || 1;
        const d = el('details');
        d.appendChild(el('summary', '', 'Details'));
        d.appendChild(generic(extra, depth + 1));
        td.appendChild(d);
        more.appendChild(td);
        tb.appendChild(more);
      }
    });
    t.appendChild(tb);
    return scroll(t);
  }

  function scroll(node) {
    const s = el('div', 'cu-scroll');
    s.appendChild(node);
    return s;
  }

  // warn: turn red near the limit (off for bars that are only relative sizes)
  function bar(label, percent, note, warn = true) {
    const p = Math.max(0, Math.min(100, Number(percent) || 0));
    return el('div', 'cu-bar' + (warn && p >= 80 ? ' high' : ''),
      `<div class="cu-bar-head"><span>${esc(label)}</span><span>${esc(note ?? Math.round(p) + '%')}</span></div>` +
      `<div class="cu-bar-track"><span style="width:${p}%"></span></div>`);
  }

  function button(label, onclick, cls = 'btn-secondary') {
    const b = el('button', cls + ' cu-btn', esc(label));
    b.onclick = onclick;
    return b;
  }

  // ── Dialog ──────────────────────────────────────
  function dialog(title, body) {
    const back = el('div', 'cu-dialog-back');
    const box = el('div', 'cu-dialog');
    const head = el('div', 'cu-dialog-head', `<span>${esc(title)}</span>`);
    const x = el('button', 'btn-icon', '✕');
    head.appendChild(x);
    const content = el('div', 'cu-dialog-body');
    content.appendChild(body);
    box.append(head, content);
    back.appendChild(box);
    document.body.appendChild(back);
    const close = () => { back.remove(); document.removeEventListener('keydown', onKey); };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    x.onclick = close;
    back.onclick = (e) => { if (e.target === back) close(); };
    document.addEventListener('keydown', onKey);
    return { close, content };
  }

  function tabs(items) {
    // items: [{label, render(container)}]
    const wrap = el('div', 'cu-tabs');
    const bar_ = el('div', 'cu-tabbar');
    const pane = el('div', 'cu-tabpane');
    wrap.append(bar_, pane);
    const pick = (i) => {
      bar_.querySelectorAll('button').forEach((b, j) => b.classList.toggle('active', i === j));
      pane.innerHTML = '';
      items[i].render(pane);
    };
    items.forEach((it, i) => {
      const b = el('button', 'cu-tab', esc(it.label));
      b.onclick = () => pick(i);
      bar_.appendChild(b);
    });
    if (items.length) pick(0);
    return wrap;
  }

  function when(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleString('en-US', { hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  return { esc, el, picker, register, renderPanel, generic, bar, button, dialog, tabs, when };
})();
