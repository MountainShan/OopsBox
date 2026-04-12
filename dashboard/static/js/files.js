// dashboard/static/js/files.js
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
    document.getElementById('fileList').innerHTML =
      `<div style="color:#fca5a5">${e.message}</div>`;
  }
}

function renderBreadcrumb(path) {
  const el = document.getElementById('breadcrumb');
  const parts = path ? path.split('/').filter(Boolean) : [];
  let html = `<span onclick="loadFiles('')">${_project}</span>`;
  let built = '';
  for (const part of parts) {
    built += (built ? '/' : '') + part;
    const p = built;
    html += ` / <span onclick="loadFiles('${p}')">${part}</span>`;
  }
  el.innerHTML = html;
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
    return `<div class="file-item" ondblclick="${f.is_dir ? `loadFiles('${f.path}')` : `downloadFile('${f.path}')`}">
      <span class="icon">${icon}</span>
      <span class="name">${f.name}</span>
      <span class="size">${size}</span>
      <span class="actions">
        ${!f.is_dir ? `<button class="btn-icon" title="Download" onclick="event.stopPropagation();downloadFile('${f.path}')">↓</button>` : ''}
        <button class="btn-icon" title="Rename" onclick="event.stopPropagation();renameFile('${f.path}','${f.name}')">✎</button>
        <button class="btn-icon" style="color:#fca5a5" title="Delete" onclick="event.stopPropagation();deleteFile('${f.path}','${f.name}')">✕</button>
      </span>
    </div>`;
  }).join('');
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
    } catch (e) { showToast(`${file.name}: ${e.message}`, true); }
  }
  input.value = '';
  await loadFiles();
  showToast(`Uploaded ${files.length} file(s)`);
}
