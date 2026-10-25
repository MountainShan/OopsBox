// dashboard/static/js/terminal.js
// Browser terminal: ttyd at /terminal/<project>/, a shell kept alive by a
// daemon on the server (dashboard/terminals.py) — closing the page leaves
// it running. The toolbar types the keys a touchscreen keyboard lacks
// straight into ttyd's xterm, which the same-origin iframe exposes as
// window.term.

const Terminal = (() => {
  const ALWAYS_KEYS = [
    { label: '^C', seq: '\x03', title: 'Interrupt (Ctrl+C)' },
    { label: '^D', seq: '\x04', title: 'EOF / Logout (Ctrl+D)' },
    { label: 'Tab', seq: '\t', title: 'Tab completion' },
    { label: '↑', seq: '\x1b[A', title: 'History up' },
    { label: '↓', seq: '\x1b[B', title: 'History down' },
  ];
  const EXTRA_KEYS = [
    { label: '^Z', seq: '\x1a', title: 'Suspend (Ctrl+Z)' },
    { label: '^L', seq: '\x0c', title: 'Clear screen (Ctrl+L)' },
    { label: '^A', seq: '\x01', title: 'Start of line (Ctrl+A)' },
    { label: '^E', seq: '\x05', title: 'End of line (Ctrl+E)' },
    { label: '^R', seq: '\x12', title: 'Search history (Ctrl+R)' },
    { label: 'Esc', seq: '\x1b', title: 'Escape' },
    { label: '←', seq: '\x1b[D', title: 'Left' },
    { label: '→', seq: '\x1b[C', title: 'Right' },
  ];

  // OopsBox's own colours (app.css :root), so the terminal looks part of the
  // page. Applied from here rather than as ttyd options, so running
  // terminals pick it up without restarting their shells.
  const THEME = {
    background: '#18181b', foreground: '#fafafa',
    cursor: '#60a5fa', cursorAccent: '#18181b',
    selectionBackground: 'rgba(59, 130, 246, 0.4)', selectionForeground: '#fafafa',
    black: '#3f3f46', brightBlack: '#71717a',
    red: '#ef4444', brightRed: '#f87171',
    green: '#22c55e', brightGreen: '#4ade80',
    yellow: '#eab308', brightYellow: '#facc15',
    blue: '#3b82f6', brightBlue: '#60a5fa',
    magenta: '#a855f7', brightMagenta: '#c084fc',
    cyan: '#06b6d4', brightCyan: '#22d3ee',
    white: '#d4d4d8', brightWhite: '#fafafa',
  };
  const FONT = "'SF Mono', 'Fira Code', 'Cascadia Code', Menlo, monospace";

  const views = {};  // project name -> { el, iframe, toolbar, expanded }

  function mount(name, container) {
    const el = document.createElement('div');
    el.className = 'term-panel';
    const toolbar = document.createElement('div');
    toolbar.className = 'term-toolbar';
    const iframe = document.createElement('iframe');
    iframe.className = 'term-frame';
    iframe.setAttribute('allow', 'clipboard-read; clipboard-write');
    const overlay = document.createElement('div');
    overlay.className = 'term-overlay';
    el.append(toolbar, iframe, overlay);
    container.appendChild(el);

    // ttyd sizes its terminal from the iframe at the moment its websocket
    // connects, and the iframe is zero-sized while another tab shows. Telling
    // it about every size change makes it recompute.
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => nudge(iframe)).observe(iframe);
    }
    const view = { name, el, toolbar, iframe, overlay, expanded: false };
    views[name] = view;
    renderToolbar(view);
    return view;
  }

  function nudge(iframe) {
    try { iframe.contentWindow?.dispatchEvent(new Event('resize')); } catch (_) {}
  }

  function xterm(view) {
    try { return view.iframe.contentWindow?.term || null; } catch (_) { return null; }
  }

  // xterm appears a moment after the iframe loads; style it once it does.
  async function applyTheme(view) {
    for (let i = 0; i < 40; i++) {
      const term = xterm(view);
      if (term?.options) {
        term.options.theme = THEME;
        term.options.fontFamily = FONT;
        try { view.iframe.contentDocument.body.style.background = THEME.background; } catch (_) {}
        nudge(view.iframe);  // the font changes the cell size
        return;
      }
      await new Promise(res => setTimeout(res, 150));
    }
  }

  function button(label, title, onclick, extraStyle = '') {
    const b = document.createElement('button');
    b.className = 'btn-icon';
    b.textContent = label;
    b.title = title;
    b.style.cssText = 'font-family:var(--mono);font-size:12px;min-width:38px;' + extraStyle;
    // keep the focus (and the on-screen keyboard) in the terminal
    b.onmousedown = (e) => e.preventDefault();
    b.onclick = onclick;
    return b;
  }

  function renderToolbar(view) {
    const t = view.toolbar;
    t.innerHTML = '';
    const keys = view.expanded ? ALWAYS_KEYS.concat(EXTRA_KEYS) : ALWAYS_KEYS;
    keys.forEach(k => t.appendChild(button(k.label, k.title, () => input(view, k.seq))));
    t.appendChild(button(view.expanded ? '✕' : '···', view.expanded ? 'Fewer keys' : 'More keys',
      () => { view.expanded = !view.expanded; renderToolbar(view); }, 'margin-left:4px;'));
    t.appendChild(button('⎘', 'Copy selected text', () => copySelection(view), 'margin-left:4px;'));
    const spacer = document.createElement('div');
    spacer.style.flex = '1';
    t.appendChild(spacer);
    t.appendChild(button('⏻', 'Close this terminal (programs running in it will stop too)', () => close(view), 'margin-left:4px;'));
  }

  function input(view, seq) {
    const term = xterm(view);
    if (!term) { showToast('Terminal is not connected yet', true); return; }
    if (typeof term.input === 'function') term.input(seq, true);
    else term._core?.coreService?.triggerDataEvent(seq, true);
    term.focus?.();
  }

  async function copySelection(view) {
    const text = xterm(view)?.getSelection?.() || '';
    if (!text) { showToast('Select some text in the terminal first'); return; }
    try { await navigator.clipboard.writeText(text); showToast('Copied'); }
    catch (_) { showToast('The browser blocked clipboard access', true); }
  }

  function setOverlay(view, msg) {
    view.overlay.textContent = msg || '';
    view.overlay.style.display = msg ? 'flex' : 'none';
  }

  async function show(name, container) {
    const view = views[name] || mount(name, container);
    view.el.style.display = 'flex';
    if (view.iframe.src.includes('/terminal/')) return;
    setOverlay(view, 'Starting terminal…');
    try {
      await api.post(`/api/projects/${encodeURIComponent(name)}/start-terminal`);
    } catch (e) {
      setOverlay(view, 'Could not start terminal: ' + e.message);
      return;
    }
    // nginx learns the new ttyd port on reload; give it a moment
    for (let i = 0; i < 20; i++) {
      const r = await fetch(`/terminal/${encodeURIComponent(name)}/`, { credentials: 'same-origin' }).catch(() => null);
      if (r && r.ok) break;
      await new Promise(res => setTimeout(res, 300));
    }
    setOverlay(view, '');
    view.iframe.onload = () => { applyTheme(view); setTimeout(() => nudge(view.iframe), 150); setTimeout(() => nudge(view.iframe), 600); };
    view.iframe.src = `/terminal/${encodeURIComponent(name)}/`;
  }

  // Type text into a terminal once it is connected (a conversation handed
  // over from the chat continues with the agent's own CLI).
  async function type(name, text) {
    for (let i = 0; i < 50; i++) {
      const view = views[name];
      const term = view && xterm(view);
      if (term) { setTimeout(() => input(view, text), 400); return; }
      await new Promise(res => setTimeout(res, 200));
    }
    showToast('Terminal is not connected; run this manually: ' + text.trim(), true);
  }

  function hide(name) {
    const view = views[name];
    if (view) view.el.style.display = 'none';
  }

  async function close(view) {
    if (!confirm('Close this terminal? Programs running in it will stop too.')) return;
    try { await api.post(`/api/projects/${encodeURIComponent(view.name)}/stop-terminal`); }
    catch (e) { showToast(e.message, true); return; }
    view.iframe.removeAttribute('src');
    setOverlay(view, 'Terminal closed. Switch to another tab and back to open a new one.');
    window.onTerminalEnded?.(view.name);
  }

  // drop a terminal's view once its tab is gone
  function forget(name) {
    views[name]?.el.remove();
    delete views[name];
  }

  return { show, hide, type, forget };
})();
