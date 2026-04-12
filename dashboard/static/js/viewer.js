// dashboard/static/js/viewer.js
// Unified file viewer: Monaco (code), Markdown (marked), Image, PDF, Binary

const MONACO_CDN = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs';
const MARKED_CDN  = 'https://cdn.jsdelivr.net/npm/marked@11.0.0/marked.min.js';

const TEXT_EXTS = new Set([
  'js','ts','jsx','tsx','mjs','cjs',
  'py','go','rs','java','c','cpp','h','hpp','cs','rb','php','swift','kt',
  'css','scss','less','html','xml','svg',
  'sh','bash','zsh','fish','ps1',
  'json','yaml','yml','toml','ini','env','conf','config',
  'sql','r','lua','vim','dockerfile','makefile','txt','log','gitignore',
  'md','markdown','mdx',
]);
const MD_EXTS    = new Set(['md','markdown','mdx']);
const IMAGE_EXTS = new Set(['png','jpg','jpeg','gif','svg','webp','ico','bmp','avif']);

let _monacoLoaded = false;
let _monacoEditor = null;
let _viewerProject = null;
let _viewerPath    = null;
let _viewerMode    = null; // 'monaco' | 'markdown' | 'image' | 'pdf' | 'binary'
let _mdSourceMode  = false; // markdown: preview vs source
let _viewerApiBase = '/api/files';

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

// ── Public entry point ────────────────────────────────
async function openFile(project, filePath, name, apiBase = '/api/files') {
  _viewerApiBase = apiBase;
  _viewerProject = project;
  _viewerPath    = filePath;
  const ext = name.split('.').pop().toLowerCase();

  _showViewer(name);
  _setBody('loading', '');

  try {
    if (MD_EXTS.has(ext)) {
      await _openMarkdown(project, filePath);
    } else if (IMAGE_EXTS.has(ext)) {
      _openImage(project, filePath, name);
    } else if (ext === 'pdf') {
      _openPDF(project, filePath);
    } else if (TEXT_EXTS.has(ext)) {
      await _openMonaco(project, filePath, ext);
    } else {
      _openBinary(project, filePath, name);
    }
  } catch (e) {
    _setBody('error', e.message);
  }
}

// ── Viewer show/hide ──────────────────────────────────
function _showViewer(title) {
  document.getElementById('viewerTitle').textContent = title;
  document.getElementById('viewerOverlay').classList.remove('hidden');
  document.getElementById('viewerMdToggle').style.display = 'none';
  document.getElementById('viewerSave').style.display = 'none';
  _mdSourceMode = false;
}

function closeViewer() {
  document.getElementById('viewerOverlay').classList.add('hidden');
  if (_monacoEditor) { _monacoEditor.dispose(); _monacoEditor = null; }
  _viewerProject = _viewerPath = _viewerMode = null;
  _viewerApiBase = '/api/files';
}

// ── Body helpers ──────────────────────────────────────
function _setBody(mode, content) {
  _viewerMode = mode;
  const ids = ['viewerLoading','viewerError','monacoContainer','mdPreview','imgViewer','pdfViewer','binaryViewer'];
  ids.forEach(id => document.getElementById(id).style.display = 'none');

  if (mode === 'loading') {
    document.getElementById('viewerLoading').style.display = 'flex';
  } else if (mode === 'error') {
    const el = document.getElementById('viewerError');
    el.textContent = content;
    el.style.display = 'flex';
  } else if (mode === 'monaco') {
    document.getElementById('monacoContainer').style.display = 'block';
  } else if (mode === 'markdown') {
    document.getElementById('mdPreview').style.display = 'block';
  } else if (mode === 'image') {
    document.getElementById('imgViewer').style.display = 'flex';
  } else if (mode === 'pdf') {
    document.getElementById('pdfViewer').style.display = 'block';
  } else if (mode === 'binary') {
    document.getElementById('binaryViewer').style.display = 'flex';
  }
}

// ── Monaco ────────────────────────────────────────────
async function _openMonaco(project, filePath, ext) {
  await _ensureMonaco();
  const r = await fetch(`${_viewerApiBase}/${project}/read?path=${encodeURIComponent(filePath)}`, { credentials: 'same-origin' });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  const { content } = await r.json();

  if (_monacoEditor) { _monacoEditor.dispose(); _monacoEditor = null; }
  _setBody('monaco', '');

  const lang = _extToLang(ext);
  _monacoEditor = monaco.editor.create(document.getElementById('monacoContainer'), {
    value: content,
    language: lang,
    theme: 'vs-dark',
    fontSize: 13,
    lineHeight: 20,
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    wordWrap: 'on',
    automaticLayout: true,
  });

  document.getElementById('viewerSave').style.display = '';
}

function _extToLang(ext) {
  const map = {
    js:'javascript', mjs:'javascript', cjs:'javascript',
    ts:'typescript', jsx:'javascript', tsx:'typescript',
    py:'python', go:'go', rs:'rust', java:'java',
    c:'c', cpp:'cpp', h:'c', hpp:'cpp', cs:'csharp',
    rb:'ruby', php:'php', swift:'swift', kt:'kotlin',
    css:'css', scss:'scss', less:'less',
    html:'html', xml:'xml', svg:'xml',
    sh:'shell', bash:'shell', zsh:'shell',
    json:'json', yaml:'yaml', yml:'yaml', toml:'ini',
    sql:'sql', md:'markdown', dockerfile:'dockerfile',
  };
  return map[ext] || 'plaintext';
}

// ── Markdown ──────────────────────────────────────────
async function _openMarkdown(project, filePath) {
  await _ensureMarked();
  const r = await fetch(`${_viewerApiBase}/${project}/read?path=${encodeURIComponent(filePath)}`, { credentials: 'same-origin' });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  const { content } = await r.json();

  _mdSourceMode = false;
  _renderMarkdownPreview(project, filePath, content);

  document.getElementById('viewerMdToggle').style.display = '';
  document.getElementById('viewerMdToggle').textContent = 'Source';
  document.getElementById('viewerSave').style.display = '';

  // Store content for source toggle
  document.getElementById('viewerOverlay').dataset.mdContent = content;
}

function _renderMarkdownPreview(project, filePath, content) {
  const dir = filePath.includes('/') ? filePath.split('/').slice(0, -1).join('/') : '';
  const preview = document.getElementById('mdPreview');

  preview.innerHTML = marked.parse(content, { breaks: true, gfm: true });

  // Rewrite relative image URLs → download API
  preview.querySelectorAll('img').forEach(img => {
    const src = img.getAttribute('src');
    if (src && !src.startsWith('http') && !src.startsWith('data:') && !src.startsWith('/')) {
      const imgPath = dir ? `${dir}/${src}` : src;
      img.src = `${_viewerApiBase}/${project}/download?path=${encodeURIComponent(imgPath)}`;
    }
  });

  // Open external links in new tab
  preview.querySelectorAll('a[href]').forEach(a => {
    const href = a.getAttribute('href');
    if (href.startsWith('http') || href.startsWith('//')) {
      a.target = '_blank';
      a.rel = 'noopener';
    }
  });

  _setBody('markdown', '');
}

async function toggleMdMode() {
  if (!MD_EXTS.has(_viewerPath?.split('.').pop().toLowerCase())) return;
  _mdSourceMode = !_mdSourceMode;
  const stored = document.getElementById('viewerOverlay').dataset.mdContent;

  if (_mdSourceMode) {
    document.getElementById('viewerMdToggle').textContent = 'Preview';
    await _ensureMonaco();
    if (_monacoEditor) { _monacoEditor.dispose(); _monacoEditor = null; }
    _setBody('monaco', '');
    _monacoEditor = monaco.editor.create(document.getElementById('monacoContainer'), {
      value: stored,
      language: 'markdown',
      theme: 'vs-dark',
      fontSize: 13,
      lineHeight: 20,
      minimap: { enabled: false },
      wordWrap: 'on',
      automaticLayout: true,
    });
  } else {
    document.getElementById('viewerMdToggle').textContent = 'Source';
    const content = _monacoEditor ? _monacoEditor.getValue() : stored;
    document.getElementById('viewerOverlay').dataset.mdContent = content;
    if (_monacoEditor) { _monacoEditor.dispose(); _monacoEditor = null; }
    _renderMarkdownPreview(_viewerProject, _viewerPath, content);
  }
}

// ── Image ─────────────────────────────────────────────
function _openImage(project, filePath, name) {
  const url = `${_viewerApiBase}/${project}/download?path=${encodeURIComponent(filePath)}`;
  const img = document.getElementById('imgViewerImg');
  img.src = url;
  img.alt = name;
  _setBody('image', '');
}

// ── PDF ───────────────────────────────────────────────
function _openPDF(project, filePath) {
  const url = `${_viewerApiBase}/${project}/download?path=${encodeURIComponent(filePath)}`;
  document.getElementById('pdfViewer').src = url;
  _setBody('pdf', '');
}

// ── Binary ────────────────────────────────────────────
function _openBinary(project, filePath, name) {
  const url = `${_viewerApiBase}/${project}/download?path=${encodeURIComponent(filePath)}`;
  const el = document.getElementById('binaryViewer');
  el.innerHTML = '';
  el.style.display = 'flex';
  const msg = document.createElement('div');
  msg.style.cssText = 'text-align:center;color:var(--text3);';
  msg.innerHTML = `<div style="font-size:32px;margin-bottom:12px;">📦</div>
    <div style="margin-bottom:16px;">${escHtml(name)}</div>
    <a href="${escHtml(url)}" download="${escHtml(name)}" class="btn-primary" style="padding:8px 16px;border-radius:6px;color:#fff;text-decoration:none;">Download</a>`;
  el.appendChild(msg);
  _setBody('binary', '');
}

// ── Save ──────────────────────────────────────────────
async function viewerSave() {
  if (!_viewerProject || !_viewerPath) return;
  let content;
  if (_monacoEditor) {
    content = _monacoEditor.getValue();
  } else if (_viewerMode === 'markdown') {
    content = document.getElementById('viewerOverlay').dataset.mdContent || '';
  } else {
    return;
  }
  try {
    const r = await fetch(`${_viewerApiBase}/${_viewerProject}/write`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ path: _viewerPath, content }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    if (_viewerMode === 'markdown') {
      document.getElementById('viewerOverlay').dataset.mdContent = content;
    }
    showToast('Saved');
    // Refresh the folder that contains the saved file
    const side = _viewerApiBase.includes('/api/ssh') ? 'remote' : 'local';
    if (typeof loadFiles === 'function') loadFiles(side);
  } catch (e) { showToast('Save failed: ' + e.message, true); }
}

// Ctrl+S to save
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    const overlay = document.getElementById('viewerOverlay');
    if (overlay && !overlay.classList.contains('hidden')) {
      e.preventDefault();
      viewerSave();
    }
  }
});

// Explicitly expose to global scope
window.openFile    = openFile;
window.closeViewer = closeViewer;
window.toggleMdMode = toggleMdMode;
window.viewerSave  = viewerSave;
console.log('[viewer.js] loaded, openFile:', typeof openFile);
