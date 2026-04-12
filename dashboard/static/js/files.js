// dashboard/static/js/files.js
function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

let _project = null;
let _currentPath = '';

function initFiles(project) {
  _project = project;
  _currentPath = '';
  loadFiles();
}

async function loadFiles(path = _currentPath) {
  _currentPath = path;
  try {
    const data = await api.files.list(_project, path);
    renderFiles(data.files);
    renderBreadcrumb(path);
  } catch (e) {
    const errDiv = document.createElement('div');
    errDiv.style.color = '#fca5a5';
    errDiv.textContent = e.message;
    document.getElementById('fileList').replaceChildren(errDiv);
  }
}

function renderBreadcrumb(path) {
  const el = document.getElementById('breadcrumb');
  el.innerHTML = '';

  const rootSpan = document.createElement('span');
  rootSpan.textContent = _project;
  rootSpan.onclick = () => loadFiles('');
  el.appendChild(rootSpan);

  const parts = path ? path.split('/').filter(Boolean) : [];
  let built = '';
  for (const part of parts) {
    built += (built ? '/' : '') + part;
    const sep = document.createTextNode(' / ');
    el.appendChild(sep);
    const span = document.createElement('span');
    span.textContent = part;
    const p = built;
    span.onclick = () => loadFiles(p);
    el.appendChild(span);
  }
}

function renderFiles(files) {
  const el = document.getElementById('fileList');
  if (!files.length) {
    el.innerHTML = `<div style="color:var(--text3);font-size:13px;padding:20px">Empty folder</div>`;
    return;
  }
  el.innerHTML = files.map(f => {
    const icon = f.is_dir ? '📁' : getIcon(f.name);
    const size = f.is_dir ? '' : formatSize(f.size);
    return `<div class="file-item" data-path="${escHtml(f.path)}" data-name="${escHtml(f.name)}" data-isdir="${f.is_dir ? '1' : '0'}">
      <span class="icon">${icon}</span>
      <span class="name">${escHtml(f.name)}</span>
      <span class="size">${escHtml(size)}</span>
      <span class="actions">
        ${!f.is_dir ? `<button class="btn-icon" title="Download" data-action="download">↓</button>` : ''}
        <button class="btn-icon" title="Rename" data-action="rename">✎</button>
        <button class="btn-icon" style="color:#fca5a5" title="Delete" data-action="delete">✕</button>
      </span>
    </div>`;
  }).join('');

  // Attach event listeners using data-* attributes (no inline JS strings with user data)
  el.querySelectorAll('.file-item').forEach(item => {
    const path = item.dataset.path;
    const name = item.dataset.name;
    const isDir = item.dataset.isdir === '1';

    item.addEventListener('dblclick', () => {
      if (isDir) loadFiles(path);
      else downloadFile(path);
    });

    const btn = item.querySelector('[data-action="download"]');
    if (btn) btn.addEventListener('click', e => { e.stopPropagation(); downloadFile(path); });

    const renameBtn = item.querySelector('[data-action="rename"]');
    if (renameBtn) renameBtn.addEventListener('click', e => { e.stopPropagation(); renameFile(path, name); });

    const deleteBtn = item.querySelector('[data-action="delete"]');
    if (deleteBtn) deleteBtn.addEventListener('click', e => { e.stopPropagation(); deleteFile(path, name); });
  });
}

function getIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  const icons = { js: '🟨', ts: '🔷', py: '🐍', md: '📝', json: '📋', sh: '⚙️', html: '🌐', css: '🎨', txt: '📄', png: '🖼', jpg: '🖼', jpeg: '🖼', gif: '🖼', svg: '🖼', pdf: '📕', zip: '🗜', tar: '🗜', gz: '🗜' };
  return icons[ext] || '📄';
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1048576) return `${(bytes/1024).toFixed(1)}K`;
  return `${(bytes/1048576).toFixed(1)}M`;
}

function goUp() {
  const parts = _currentPath.split('/').filter(Boolean);
  parts.pop();
  loadFiles(parts.join('/'));
}

function downloadFile(path) {
  window.open(api.files.downloadUrl(_project, path));
}

async function deleteFile(path, name) {
  if (!confirm(`Delete "${name}"?`)) return;
  try {
    await api.files.delete(_project, path);
    await loadFiles();
    showToast(`Deleted "${name}"`);
  } catch (e) { showToast(e.message, true); }
}

async function renameFile(path, name) {
  const newName = prompt('New name:', name);
  if (!newName || newName === name) return;
  try {
    await api.files.rename(_project, path, newName);
    await loadFiles();
    showToast(`Renamed to "${newName}"`);
  } catch (e) { showToast(e.message, true); }
}

async function newFolder() {
  const name = prompt('Folder name:');
  if (!name) return;
  const path = _currentPath ? `${_currentPath}/${name}` : name;
  try {
    await api.files.mkdir(_project, path);
    await loadFiles();
  } catch (e) { showToast(e.message, true); }
}

async function uploadFiles(input) {
  const files = Array.from(input.files);
  if (!files.length) return;
  let succeeded = 0;
  for (const file of files) {
    const formData = new FormData();
    formData.append('file', file);
    const query = _currentPath ? `?path=${encodeURIComponent(_currentPath)}` : '';
    try {
      const r = await fetch(`/api/files/${_project}/upload${query}`, {
        method: 'POST',
        body: formData,
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error((await r.json()).detail || 'Upload failed');
      succeeded++;
    } catch (e) { showToast(`${file.name}: ${e.message}`, true); }
  }
  input.value = '';
  await loadFiles();
  if (succeeded > 0) showToast(`Uploaded ${succeeded} of ${files.length} file(s)`);
}
