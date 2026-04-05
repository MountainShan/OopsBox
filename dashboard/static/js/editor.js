/* ── editor.js — CodeMirror editor module ── */
import * as API from './api.js';

const EDITOR = {};
window.EDITOR = EDITOR;

// ── State ──
let editor = null;
let currentFile = null;
let previewMode = false;
let chunkState = null; // {path, total, offset, end}

// ── Helpers ──
function modeForFile(name) {
  const ext = name.split('.').pop().toLowerCase();
  const map = {
    js: 'javascript', jsx: 'javascript', ts: 'javascript', tsx: 'javascript', mjs: 'javascript',
    py: 'python', sh: 'shell', bash: 'shell', zsh: 'shell', md: 'markdown',
    html: 'htmlmixed', htm: 'htmlmixed', css: 'css', scss: 'css', less: 'css',
    json: { name: 'javascript', json: true }, yaml: 'yaml', yml: 'yaml', toml: 'toml',
    go: 'go', rs: 'rust', c: 'text/x-csrc', cpp: 'text/x-c++src', h: 'text/x-csrc',
    java: 'text/x-java', dockerfile: 'dockerfile', nginx: 'nginx', conf: 'nginx',
    sql: 'sql', xml: 'xml', svg: 'xml'
  };
  return map[ext] || 'text/plain';
}

function langName(name) {
  const ext = name.split('.').pop().toLowerCase();
  const map = {
    js: 'JavaScript', ts: 'TypeScript', py: 'Python', sh: 'Shell', md: 'Markdown',
    html: 'HTML', css: 'CSS', json: 'JSON', yaml: 'YAML', go: 'Go', rs: 'Rust',
    c: 'C', cpp: 'C++', java: 'Java', sql: 'SQL'
  };
  return map[ext] || ext.toUpperCase();
}

function isImage(name) { return /\.(png|jpg|jpeg|gif|svg|webp|ico|bmp)$/i.test(name); }

// ── Init CodeMirror (singleton) ──
function initEditor() {
  if (editor) return;
  const wrap = document.getElementById('editor-wrap');
  const ta = document.createElement('textarea');
  wrap.appendChild(ta);
  editor = CodeMirror.fromTextArea(ta, {
    theme: 'material-darker', lineNumbers: true, lineWrapping: false,
    indentUnit: 2, tabSize: 2, indentWithTabs: false,
    matchBrackets: true, styleActiveLine: true,
    extraKeys: {
      'Ctrl-S': () => { EDITOR.save(); return false; },
      'Cmd-S': () => { EDITOR.save(); return false; },
      'Ctrl-H': () => { EDITOR.findReplace(); return false; },
      'Cmd-H': () => { EDITOR.findReplace(); return false; },
    }
  });
  editor.on('cursorActivity', () => {
    const pos = editor.getCursor();
    document.getElementById('info-pos').textContent = `Ln ${pos.line + 1}, Col ${pos.ch + 1}`;
  });
  editor.on('change', () => {
    if (currentFile) {
      currentFile.modified = true;
      document.getElementById('editor-modified').textContent = '● modified';
    }
  });
}

// ── Open file ──
EDITOR.openFile = async function(path, name) {
  if (isImage(name || path)) {
    document.getElementById('image-filename').textContent = name || path.split('/').pop();
    document.getElementById('image-el').src = API.downloadUrl(path);
    document.getElementById('image-modal').classList.add('open');
    return;
  }
  try {
    const d = await API.readFile(path);
    currentFile = {
      path, name: d.name, content: d.content, modified: false,
      doc: CodeMirror.Doc(d.content, modeForFile(d.name))
    };
    initEditor();
    editor.swapDoc(currentFile.doc);
    document.getElementById('editor-filename').textContent = d.name;
    document.getElementById('editor-modified').textContent = '';
    document.getElementById('info-path').textContent = path;
    document.getElementById('info-lang').textContent = langName(d.name);

    // Chunked file indicator
    if (d.chunked) {
      chunkState = { path, total: d.total_lines, offset: d.offset, end: d.end };
      document.getElementById('info-chunk').textContent = `Lines ${d.offset + 1}-${d.end} of ${d.total_lines}`;
      document.getElementById('chunk-nav').style.display = 'flex';
    } else {
      chunkState = null;
      document.getElementById('info-chunk').textContent = '';
      document.getElementById('chunk-nav').style.display = 'none';
    }

    // Markdown preview button
    const isMd = /\.(md|markdown)$/i.test(path);
    document.getElementById('md-preview-btn').style.display = isMd ? 'flex' : 'none';
    if (previewMode) EDITOR.togglePreview();

    document.getElementById('editor-modal').classList.add('open');
    setTimeout(() => editor.refresh(), 50);
  } catch (e) {
    alert('Error opening file: ' + e.message);
  }
};

// ── Chunk navigation ──
EDITOR.loadChunk = async function(direction) {
  if (!chunkState) return;
  const CHUNK = 1000;
  let newOffset;
  if (direction === 'next') newOffset = chunkState.end;
  else if (direction === 'prev') newOffset = Math.max(0, chunkState.offset - CHUNK);
  else if (direction === 'first') newOffset = 0;
  else if (direction === 'last') newOffset = Math.max(0, chunkState.total - CHUNK);
  else return;

  if (currentFile && currentFile.modified) {
    if (!confirm('Unsaved changes will be lost. Continue?')) return;
  }
  try {
    const d = await API.readFile(chunkState.path, newOffset, CHUNK);
    currentFile.content = d.content;
    currentFile.modified = false;
    currentFile.doc = CodeMirror.Doc(d.content, modeForFile(currentFile.name));
    editor.swapDoc(currentFile.doc);
    chunkState.offset = d.offset;
    chunkState.end = d.end;
    document.getElementById('info-chunk').textContent = `Lines ${d.offset + 1}-${d.end} of ${d.total_lines}`;
    document.getElementById('editor-modified').textContent = '';
  } catch (e) { }
};

EDITOR.jumpToLine = async function() {
  if (!chunkState) return;
  const input = prompt(`Go to line (1-${chunkState.total}):`);
  if (!input) return;
  const line = parseInt(input, 10);
  if (isNaN(line) || line < 1 || line > chunkState.total) return;
  const CHUNK = 1000;
  const newOffset = Math.max(0, line - Math.floor(CHUNK / 2));
  if (currentFile && currentFile.modified) {
    if (!confirm('Unsaved changes will be lost. Continue?')) return;
  }
  try {
    const d = await API.readFile(chunkState.path, newOffset, CHUNK);
    currentFile.content = d.content;
    currentFile.modified = false;
    currentFile.doc = CodeMirror.Doc(d.content, modeForFile(currentFile.name));
    editor.swapDoc(currentFile.doc);
    chunkState.offset = d.offset;
    chunkState.end = d.end;
    document.getElementById('info-chunk').textContent = `Lines ${d.offset + 1}-${d.end} of ${d.total_lines}`;
    document.getElementById('editor-modified').textContent = '';
    const localLine = line - d.offset - 1;
    setTimeout(() => editor.setCursor(localLine, 0), 50);
  } catch (e) { }
};

// ── Save ──
EDITOR.save = async function() {
  if (!currentFile || !editor) return;
  const content = editor.getValue();
  try {
    await API.writeFile(currentFile.path, content);
    currentFile.modified = false;
    currentFile.content = content;
    document.getElementById('editor-modified').textContent = '';
    const ind = document.getElementById('saved-ind');
    ind.classList.add('show');
    setTimeout(() => ind.classList.remove('show'), 1500);
  } catch (e) {
    alert('Save failed: ' + e.message);
  }
};

EDITOR.saveAs = async function() {
  if (!currentFile || !editor) return;
  const newPath = prompt('Save as:', currentFile.path);
  if (!newPath) return;
  if (newPath === currentFile.path) { EDITOR.save(); return; }
  const content = editor.getValue();
  try {
    await API.writeFile(newPath, content);
    const name = newPath.split('/').pop();
    currentFile = {
      path: newPath, name, content, modified: false,
      doc: CodeMirror.Doc(content, modeForFile(name))
    };
    editor.swapDoc(currentFile.doc);
    document.getElementById('editor-filename').textContent = name;
    document.getElementById('editor-modified').textContent = '';
    document.getElementById('info-path').textContent = newPath;
    document.getElementById('info-lang').textContent = langName(name);
    const ind = document.getElementById('saved-ind');
    ind.classList.add('show');
    setTimeout(() => ind.classList.remove('show'), 1500);
    // Refresh file list
    if (window.FM) window.FM.loadDir(window.FM.getCurrentDir());
  } catch (e) {
    alert('Save failed: ' + e.message);
  }
};

EDITOR.doUndo = function() { if (editor) editor.undo(); };
EDITOR.doRedo = function() { if (editor) editor.redo(); };
EDITOR.findReplace = function() { if (editor) editor.execCommand('replace'); };

// ── Markdown preview ──
EDITOR.togglePreview = async function() {
  previewMode = !previewMode;
  const wrap = document.getElementById('editor-wrap');
  const prev = document.getElementById('md-preview');
  const btn = document.getElementById('md-preview-btn');
  if (previewMode) {
    wrap.style.display = 'none';
    prev.style.display = 'block';
    btn.classList.add('primary');
    const content = editor ? editor.getValue() : '';
    const renderer = new marked.Renderer();
    renderer.code = function({ text, lang }) {
      if (lang === 'mermaid') return `<div class="mermaid">${text}</div>`;
      return `<pre><code class="language-${lang || ''}">${text.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>`;
    };
    renderer.listitem = function({ text }) {
      if (text.startsWith('[x] ') || text.startsWith('[X] '))
        return `<li><input type="checkbox" checked disabled>${text.slice(4)}</li>`;
      if (text.startsWith('[ ] '))
        return `<li><input type="checkbox" disabled>${text.slice(4)}</li>`;
      return `<li>${text}</li>`;
    };
    marked.setOptions({ renderer, breaks: true, gfm: true });
    prev.innerHTML = marked.parse(content);
    try {
      const els = prev.querySelectorAll('.mermaid');
      for (let i = 0; i < els.length; i++) {
        const { svg } = await mermaid.render('mm-' + i + '-' + Date.now(), els[i].textContent);
        els[i].innerHTML = svg;
      }
    } catch (e) { }
  } else {
    wrap.style.display = '';
    prev.style.display = 'none';
    btn.classList.remove('primary');
    if (editor) editor.refresh();
  }
};

// ── Close ──
EDITOR.closeEditor = function() {
  if (currentFile && currentFile.modified) {
    if (!confirm(`${currentFile.name} has unsaved changes. Close anyway?`)) return;
  }
  document.getElementById('editor-modal').classList.remove('open');
  if (previewMode) EDITOR.togglePreview();
  currentFile = null;
  chunkState = null;
  document.getElementById('chunk-nav').style.display = 'none';
  document.getElementById('info-chunk').textContent = '';
};

EDITOR.closeImageModal = function() {
  document.getElementById('image-modal').classList.remove('open');
  document.getElementById('image-el').src = '';
};

EDITOR.downloadCurrentImage = function() {
  const src = document.getElementById('image-el').src;
  if (src) window.open(src, '_blank');
};

// ── Get current file (for download) ──
EDITOR.getCurrentFile = function() { return currentFile; };

export default EDITOR;
