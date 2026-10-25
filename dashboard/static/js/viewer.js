// dashboard/static/js/viewer.js
// Multi-tab file viewer: Monaco (code), Markdown, HTML, Image, PDF, Binary.
//
// The file browser and every open file share one tab strip inside the Files
// tab, so the file list is always one click away while a document is in front.
// Switching tabs must not lose anything the user built up — scroll position,
// PDF page, unsaved edits — so each tab keeps its own pane in the DOM and is
// hidden rather than destroyed. Code and markdown
// source share ONE Monaco editor and swap models instead of creating an editor
// per file: a model is what Monaco is designed to hold a file in, and it
// carries the undo history and view state with it.

const MONACO_CDN = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs';
const MARKED_CDN = 'https://cdn.jsdelivr.net/npm/marked@11.0.0/marked.min.js';

const TEXT_EXTS = new Set([
  'js','ts','jsx','tsx','mjs','cjs',
  'py','go','rs','java','c','cpp','h','hpp','cs','rb','php','swift','kt',
  'css','scss','less','html','htm','xml','svg',
  'sh','bash','zsh','fish','ps1',
  'json','yaml','yml','toml','ini','env','conf','config',
  'sql','r','lua','vim','dockerfile','makefile','txt','log','gitignore',
  'md','markdown','mdx',
]);
const MD_EXTS    = new Set(['md','markdown','mdx']);
const HTML_EXTS  = new Set(['html','htm']);
const IMAGE_EXTS = new Set(['png','jpg','jpeg','gif','svg','webp','ico','bmp','avif']);
// Office documents are converted to PDF on the server and shown in the same
// pane a real PDF uses, so no separate rendering mode is needed.
const OFFICE_EXTS = new Set([
  'doc','docx','odt','rtf', 'ppt','pptx','odp', 'xls','xlsx','ods',
]);

let _monacoLoaded = false;
let _monacoEditor = null;
let _seq = 0;

const _tabs = new Map();   // id -> tab
let _activeId = null;

// ── CDN loaders ───────────────────────────────────────
function _loadScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) { resolve(); return; }
    const s = document.createElement('script');
    s.src = src; s.onload = resolve; s.onerror = reject;
    document.head.appendChild(s);
  });
}

async function _ensureMonaco() {
  if (_monacoLoaded) return;
  await _loadScript(`${MONACO_CDN}/loader.js`);
  await new Promise(resolve => {
    window.require.config({ paths: { vs: MONACO_CDN } });
    window.require(['vs/editor/editor.main'], resolve);
  });
  _monacoLoaded = true;
}

async function _ensureMarked() {
  if (window.marked) return;
  await _loadScript(MARKED_CDN);
}

// ── Helpers ───────────────────────────────────────────
function _kindOf(ext) {
  if (MD_EXTS.has(ext))     return 'markdown';
  if (HTML_EXTS.has(ext))   return 'html';
  if (IMAGE_EXTS.has(ext))  return 'image';
  if (ext === 'pdf')        return 'pdf';
  if (OFFICE_EXTS.has(ext)) return 'office';
  if (TEXT_EXTS.has(ext))  return 'code';
  return 'binary';
}

// Markdown and HTML can be shown rendered or as source; when showing source
// they borrow the shared Monaco editor, exactly as a code tab does.
function _usesMonaco(tab) {
  return tab.kind === 'code'
    || (tab.kind === 'markdown' && tab.sourceMode)
    || (tab.kind === 'html' && tab.sourceMode);
}
function _isEditable(tab) {
  return tab.kind === 'code' || tab.kind === 'markdown' || tab.kind === 'html';
}
function _downloadUrl(tab) {
  return `${tab.apiBase}/${tab.project}/download?path=${encodeURIComponent(tab.path)}`;
}
// Same bytes, but asking the server for an inline disposition so the browser
// renders it instead of saving it. Used by anything shown in place.
function _inlineUrl(tab) {
  return `${_downloadUrl(tab)}&inline=1`;
}
async function _readFile(tab) {
  const r = await fetch(
    `${tab.apiBase}/${tab.project}/read?path=${encodeURIComponent(tab.path)}`,
    { credentials: 'same-origin' });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return (await r.json()).content;
}

function _extToLang(ext) {
  const map = {
    js:'javascript', mjs:'javascript', cjs:'javascript',
    ts:'typescript', jsx:'javascript', tsx:'typescript',
    py:'python', go:'go', rs:'rust', java:'java',
    c:'c', cpp:'cpp', h:'c', hpp:'cpp', cs:'csharp',
    rb:'ruby', php:'php', swift:'swift', kt:'kotlin',
    css:'css', scss:'scss', less:'less',
    html:'html', htm:'html', xml:'xml', svg:'xml',
    sh:'shell', bash:'shell', zsh:'shell',
    json:'json', yaml:'yaml', yml:'yaml', toml:'ini',
    sql:'sql', md:'markdown', dockerfile:'dockerfile',
  };
  return map[ext] || 'plaintext';
}

// ── Browser / file mode ───────────────────────────────
// _activeId === null means the browser is in front.
function showBrowser() {
  document.getElementById('browserPanes').style.display = '';
  document.getElementById('viewerBody').style.display = 'none';
  _activeId = null;
  _syncToolbar(null);
  _renderTabs();
}

function _showFileArea() {
  document.getElementById('browserPanes').style.display = 'none';
  document.getElementById('viewerBody').style.display = '';
}

function _syncToolbar(tab) {
  const toggle = document.getElementById('viewerMdToggle');
  const save   = document.getElementById('viewerSave');
  const dl     = document.getElementById('viewerDownloadLink');
  if (!tab) {
    toggle.style.display = 'none';
    save.style.display = 'none';
    if (dl) dl.style.display = 'none';
    return;
  }
  const canToggle = tab.kind === 'markdown' || tab.kind === 'html';
  toggle.style.display = canToggle ? '' : 'none';
  toggle.textContent = tab.sourceMode ? 'Preview' : 'Source';
  save.style.display = _isEditable(tab) ? '' : 'none';
  if (dl) { dl.style.display = ''; dl.href = _downloadUrl(tab); dl.download = tab.name; }
}

// ── Public entry point ────────────────────────────────
async function openFile(project, filePath, name, apiBase = '/api/files') {
  // Re-opening a file that is already up just focuses it, so clicking around
  // the file list does not pile up duplicate tabs of the same thing.
  for (const t of _tabs.values()) {
    if (t.project === project && t.path === filePath && t.apiBase === apiBase) {
      _activate(t.id);
      return;
    }
  }

  const ext = (name.split('.').pop() || '').toLowerCase();
  const tab = {
    id: `vt${++_seq}`,
    project, path: filePath, name, apiBase, ext,
    kind: _kindOf(ext),
    pane: null, model: null, content: null, objectUrl: null,
    sourceMode: false, dirty: false, loaded: false,
  };
  _tabs.set(tab.id, tab);

  const pane = document.createElement('div');
  pane.className = 'viewer-pane hidden';
  pane.id = `pane-${tab.id}`;
  document.getElementById('viewerBody').appendChild(pane);
  tab.pane = pane;

  _renderTabs();
  _activate(tab.id);

  try {
    await _load(tab);
  } catch (e) {
    tab.pane.innerHTML =
      `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#fca5a5;padding:20px;text-align:center;">${escHtml(e.message)}</div>`;
  }
  if (_activeId === tab.id) _activate(tab.id);
}

// ── Loading, per kind ─────────────────────────────────
async function _load(tab) {
  if (tab.kind === 'code' || tab.kind === 'markdown' || tab.kind === 'html') {
    tab.content = await _readFile(tab);
  }

  if (tab.kind === 'code') {
    await _ensureMonaco();
    tab.model = monaco.editor.createModel(tab.content, _extToLang(tab.ext));
    _watchDirty(tab);
  } else if (tab.kind === 'markdown') {
    await _ensureMarked();
    _renderMarkdown(tab);
  } else if (tab.kind === 'html') {
    _renderHtml(tab);
  } else if (tab.kind === 'image') {
    tab.pane.innerHTML = '';
    tab.pane.style.cssText =
      'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;overflow:auto;padding:20px;background:var(--bg);';
    const img = document.createElement('img');
    img.src = _inlineUrl(tab);
    img.alt = tab.name;
    img.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;border-radius:4px;';
    tab.pane.appendChild(img);
  } else if (tab.kind === 'pdf') {
    tab.pane.innerHTML = '';
    const f = document.createElement('iframe');
    f.src = _inlineUrl(tab);
    f.style.cssText = 'width:100%;height:100%;border:none;';
    tab.pane.appendChild(f);
  } else if (tab.kind === 'office') {
    // Converted on the server: text documents become HTML so they reflow to the
    // screen, presentations become SVG so the layout survives and stays sharp
    // at any zoom. First conversion takes about a second and is then cached, so
    // say what is happening instead of showing a blank pane.
    tab.pane.innerHTML =
      `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text3);font-family:var(--mono);font-size:13px;">Converting ${escHtml(tab.name)}…</div>`;
    const url = `${tab.apiBase}/${tab.project}/preview?path=${encodeURIComponent(tab.path)}`;
    const r = await fetch(url, { credentials: 'same-origin' });
    if (!r.ok) {
      const detail = (await r.json().catch(() => ({}))).detail || r.statusText;
      throw new Error(detail);
    }
    const blob = await r.blob();
    tab.objectUrl = URL.createObjectURL(blob);
    tab.pane.innerHTML = '';

    // An iframe either way, never <img>: LibreOffice embeds a slide navigation
    // script in the SVG, and scripts inside an <img> never run.
    const f = document.createElement('iframe');
    f.setAttribute('sandbox', 'allow-scripts allow-popups');
    f.src = tab.objectUrl;
    f.style.cssText = 'width:100%;height:100%;border:none;background:#fff;';
    tab.pane.appendChild(f);
  } else {
    tab.pane.innerHTML =
      `<div style="display:flex;align-items:center;justify-content:center;height:100%;">
         <div style="text-align:center;color:var(--text3);">
           <div style="font-size:32px;margin-bottom:12px;">📦</div>
           <div style="margin-bottom:16px;">${escHtml(tab.name)}</div>
           <a href="${escHtml(_downloadUrl(tab))}" download="${escHtml(tab.name)}"
              class="btn-primary" style="padding:8px 16px;border-radius:6px;color:#fff;text-decoration:none;">Download</a>
         </div>
       </div>`;
  }
  tab.loaded = true;
}

function _watchDirty(tab) {
  if (!tab.model) return;
  tab.model.onDidChangeContent(() => {
    if (!tab.dirty) { tab.dirty = true; _renderTabs(); }
  });
}

function _renderMarkdown(tab) {
  const dir = tab.path.includes('/') ? tab.path.split('/').slice(0, -1).join('/') : '';
  tab.pane.className = 'viewer-pane md-preview';
  tab.pane.innerHTML = marked.parse(tab.content, { breaks: true, gfm: true });

  // Relative image sources are repointed at the download API, otherwise the
  // browser would resolve them against the dashboard's own URL.
  tab.pane.querySelectorAll('img').forEach(img => {
    const src = img.getAttribute('src');
    if (src && !src.startsWith('http') && !src.startsWith('data:') && !src.startsWith('/')) {
      const p = dir ? `${dir}/${src}` : src;
      img.src = `${tab.apiBase}/${tab.project}/download?path=${encodeURIComponent(p)}&inline=1`;
    }
  });
  tab.pane.querySelectorAll('a[href]').forEach(a => {
    const href = a.getAttribute('href');
    if (href.startsWith('http') || href.startsWith('//')) { a.target = '_blank'; a.rel = 'noopener'; }
  });
}

function _renderHtml(tab) {
  // Rendered in a sandboxed iframe: scripts run so charts and dashboards work,
  // but the page cannot reach the dashboard's own DOM or cookies. Relative
  // assets will not resolve — self-contained pages (inline or CDN assets) are
  // the case this serves.
  tab.pane.className = 'viewer-pane';
  tab.pane.innerHTML = '';
  const f = document.createElement('iframe');
  f.setAttribute('sandbox', 'allow-scripts allow-popups allow-forms');
  f.srcdoc = tab.content;
  f.style.cssText = 'width:100%;height:100%;border:none;background:#fff;';
  tab.pane.appendChild(f);
}

// ── Tab bar ───────────────────────────────────────────
function _renderTabs() {
  const bar = document.getElementById('viewerTabs');
  if (!bar) return;
  bar.innerHTML = '';

  // A permanent first tab back to the file browser. It hides the viewer rather
  // than closing it, so every open file keeps its model, scroll position and
  // unsaved edits — coming back lands exactly where you left.
  const browser = document.createElement('div');
  browser.className = 'viewer-tab viewer-tab-browser' + (_activeId === null ? ' active' : '');
  browser.title = 'File browser — open files stay open';
  browser.textContent = '\u{1F4C1} Browser';
  browser.onclick = () => showBrowser();
  bar.appendChild(browser);

  for (const tab of _tabs.values()) {
    const el = document.createElement('div');
    el.className = 'viewer-tab' + (tab.id === _activeId ? ' active' : '');
    el.title = tab.path;
    el.onclick = () => _activate(tab.id);

    const nm = document.createElement('span');
    nm.className = 'viewer-tab-name';
    nm.textContent = tab.name;
    el.appendChild(nm);

    const close = document.createElement('span');
    if (tab.dirty) {
      close.className = 'viewer-tab-dirty';
      close.textContent = '●';
      close.title = 'Unsaved changes';
    } else {
      close.className = 'viewer-tab-close';
      close.textContent = '✕';
    }
    close.onclick = e => { e.stopPropagation(); closeTab(tab.id); };
    el.appendChild(close);

    bar.appendChild(el);
  }
}

function _activate(id) {
  const tab = _tabs.get(id);
  if (!tab) return;
  _activeId = id;
  _showFileArea();

  const monacoBox = document.getElementById('monacoContainer');
  _tabs.forEach(t => t.pane && t.pane.classList.add('hidden'));
  monacoBox.style.display = 'none';

  if (_usesMonaco(tab) && tab.model) {
    if (!_monacoEditor) {
      _monacoEditor = monaco.editor.create(monacoBox, {
        theme: 'vs-dark', fontSize: 13, lineHeight: 20,
        minimap: { enabled: false }, scrollBeyondLastLine: false,
        wordWrap: 'on', automaticLayout: true,
      });
    }
    _monacoEditor.setModel(tab.model);
    monacoBox.style.display = 'block';
    _monacoEditor.layout();
  } else {
    tab.pane.classList.remove('hidden');
  }

  _syncToolbar(tab);
  _renderTabs();
}

function closeTab(id) {
  const tab = _tabs.get(id);
  if (!tab) return;
  if (tab.dirty && !confirm(`${tab.name} has unsaved changes. Close anyway?`)) return;

  if (tab.model) {
    if (_monacoEditor && _monacoEditor.getModel() === tab.model) _monacoEditor.setModel(null);
    tab.model.dispose();
  }
  if (tab.objectUrl) URL.revokeObjectURL(tab.objectUrl);
  tab.pane?.remove();
  _tabs.delete(id);

  if (_activeId === id) {
    const next = [..._tabs.keys()].pop();
    if (next) { _activate(next); } else { showBrowser(); return; }
  }
  _renderTabs();
}

// Close every open file and return to the browser.
function closeViewer() {
  for (const tab of _tabs.values()) {
    if (tab.model) tab.model.dispose();
    if (tab.objectUrl) URL.revokeObjectURL(tab.objectUrl);
    tab.pane?.remove();
  }
  _tabs.clear();
  if (_monacoEditor) { _monacoEditor.setModel(null); }
  showBrowser();
}

// ── Source / preview toggle (markdown and html) ───────
async function toggleMdMode() {
  const tab = _tabs.get(_activeId);
  if (!tab || (tab.kind !== 'markdown' && tab.kind !== 'html')) return;

  if (!tab.sourceMode) {
    await _ensureMonaco();
    if (!tab.model) {
      tab.model = monaco.editor.createModel(
        tab.content, tab.kind === 'markdown' ? 'markdown' : 'html');
      _watchDirty(tab);
    }
    tab.sourceMode = true;
  } else {
    // Carry edits made in source view back into the rendered view.
    if (tab.model) tab.content = tab.model.getValue();
    tab.sourceMode = false;
    if (tab.kind === 'markdown') { await _ensureMarked(); _renderMarkdown(tab); }
    else { _renderHtml(tab); }
  }
  _activate(tab.id);
}

// ── Save ──────────────────────────────────────────────
async function viewerSave() {
  const tab = _tabs.get(_activeId);
  if (!tab || !_isEditable(tab)) return;

  const content = tab.model ? tab.model.getValue() : tab.content;
  try {
    const r = await fetch(`${tab.apiBase}/${tab.project}/write`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ path: tab.path, content }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    tab.content = content;
    tab.dirty = false;
    _renderTabs();
    showToast('Saved');
    const side = tab.apiBase.includes('/api/ssh') ? 'remote' : 'local';
    if (typeof loadFiles === 'function') loadFiles(side);
  } catch (e) { showToast('Save failed: ' + e.message, true); }
}

// Ctrl+S saves the tab in front
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's' && _activeId) {
    e.preventDefault();
    viewerSave();
  }
});

window.openFile     = openFile;
window.closeViewer  = closeViewer;
window.showBrowser  = showBrowser;
window.closeTab     = closeTab;
window.toggleMdMode = toggleMdMode;
window.viewerSave   = viewerSave;
