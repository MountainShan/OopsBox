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
let _projectType = 'local';
const _state = {
  local:  { path: '' },
  remote: { path: '' },
};

function _apiBase(side) {
  return side === 'remote'
    ? `/api/ssh/${_project}`
    : `/api/files/${_project}`;
}

function _viewerBase(side) {
  return side === 'remote' ? '/api/ssh' : '/api/files';
}

function initFiles(project, type = 'local') {
  _project = project;
  _projectType = type;
  _state.local.path = '';
  _state.remote.path = '';

  if (type === 'ssh') {
    document.getElementById('remotePane').classList.remove('hidden');
    loadFiles('local');
    loadFiles('remote');
  } else {
    document.getElementById('remotePane').classList.add('hidden');
    loadFiles('local');
  }
}

async function loadFiles(side = 'local', path = _state[side].path) {
  _state[side].path = path;
  const listEl = document.getElementById(side === 'local' ? 'fileListLocal' : 'fileListRemote');
  try {
    const r = await fetch(`${_apiBase(side)}?path=${encodeURIComponent(path)}`, {
      credentials: 'same-origin',
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const data = await r.json();
    renderFiles(side, data.files);
    renderBreadcrumb(side, path);
  } catch (e) {
    const errDiv = document.createElement('div');
    errDiv.style.color = '#fca5a5';
    errDiv.textContent = e.message;
    listEl.replaceChildren(errDiv);
  }
}

function renderBreadcrumb(side, path) {
  const el = document.getElementById(side === 'local' ? 'breadcrumbLocal' : 'breadcrumbRemote');
  el.innerHTML = '';

  const rootSpan = document.createElement('span');
  rootSpan.textContent = side === 'remote' ? `${_project} (remote)` : _project;
  rootSpan.onclick = () => loadFiles(side, '');
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
    span.onclick = () => loadFiles(side, p);
    el.appendChild(span);
  }
}

function renderFiles(side, files) {
  const el = document.getElementById(side === 'local' ? 'fileListLocal' : 'fileListRemote');
  if (!files.length) {
    el.innerHTML = `<div style="color:var(--text3);font-size:13px;padding:20px">Empty folder</div>`;
    return;
  }
  const showTransfer = _projectType === 'ssh';
  const transferTitle = side === 'local' ? 'Send to Remote →' : '← Send to Local';

  el.innerHTML = files.map(f => {
    const icon = f.is_dir ? '📁' : getIcon(f.name);
    const size = f.is_dir ? '' : formatSize(f.size);
    return `<div class="file-item" data-path="${escHtml(f.path)}" data-name="${escHtml(f.name)}" data-isdir="${f.is_dir ? '1' : '0'}">
      <span class="icon">${icon}</span>
      <span class="name">${escHtml(f.name)}</span>
      <span class="size">${escHtml(size)}</span>
      <span class="actions">
        ${!f.is_dir ? `<button class="btn-icon" title="Download" data-action="download">↓</button>` : ''}
        ${showTransfer && !f.is_dir ? `<button class="btn-icon" title="${escHtml(transferTitle)}" data-action="transfer" style="color:var(--accent)">${side === 'local' ? '→' : '←'}</button>` : ''}
        <button class="btn-icon" title="Rename" data-action="rename">✎</button>
        <button class="btn-icon" style="color:#fca5a5" title="Delete" data-action="delete">✕</button>
      </span>
    </div>`;
  }).join('');

  el.querySelectorAll('.file-item').forEach(item => {
    const path = item.dataset.path;
    const name = item.dataset.name;
    const isDir = item.dataset.isdir === '1';

    item.addEventListener('click', () => {
      if (isDir) {
        loadFiles(side, path);
      } else {
        const downloadUrl = `${_apiBase(side)}/download?path=${encodeURIComponent(path)}`;
        const dl = document.getElementById('viewerDownloadLink');
        if (dl) { dl.href = downloadUrl; dl.download = name; }
        openFile(_project, path, name, _viewerBase(side));
      }
    });

    const downloadBtn = item.querySelector('[data-action="download"]');
    if (downloadBtn) downloadBtn.addEventListener('click', e => {
      e.stopPropagation();
      const url = `${_apiBase(side)}/download?path=${encodeURIComponent(path)}`;
      window.open(url);
    });

    const renameBtn = item.querySelector('[data-action="rename"]');
    if (renameBtn) renameBtn.addEventListener('click', e => { e.stopPropagation(); renameFile(side, path, name); });

    const deleteBtn = item.querySelector('[data-action="delete"]');
    if (deleteBtn) deleteBtn.addEventListener('click', e => { e.stopPropagation(); deleteFile(side, path, name); });

    const transferBtn = item.querySelector('[data-action="transfer"]');
    if (transferBtn) transferBtn.addEventListener('click', e => { e.stopPropagation(); transferFile(side, path, name); });
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

function goUp(side = 'local') {
  const parts = _state[side].path.split('/').filter(Boolean);
  parts.pop();
  loadFiles(side, parts.join('/'));
}

async function deleteFile(side, path, name) {
  if (!confirm(`Delete "${name}"?`)) return;
  try {
    const r = await fetch(`${_apiBase(side)}/delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ path }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    await loadFiles(side);
    showToast(`Deleted "${name}"`);
  } catch (e) { showToast(e.message, true); }
}

async function renameFile(side, path, name) {
  const newName = prompt('New name:', name);
  if (!newName || newName === name) return;
  try {
    const r = await fetch(`${_apiBase(side)}/rename`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ path, new_name: newName }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    await loadFiles(side);
    showToast(`Renamed to "${newName}"`);
  } catch (e) { showToast(e.message, true); }
}

async function newFile(side = 'local') {
  const name = prompt('File name:');
  if (!name) return;
  const filePath = _state[side].path ? `${_state[side].path}/${name}` : name;
  try {
    const r = await fetch(`${_apiBase(side)}/write`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ path: filePath, content: '' }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    await loadFiles(side);
    openFile(_project, filePath, name, _viewerBase(side));
  } catch (e) { showToast(e.message, true); }
}

async function newFolder(side = 'local') {
  const name = prompt('Folder name:');
  if (!name) return;
  const folderPath = _state[side].path ? `${_state[side].path}/${name}` : name;
  try {
    const r = await fetch(`${_apiBase(side)}/mkdir`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ path: folderPath }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    await loadFiles(side);
  } catch (e) { showToast(e.message, true); }
}

async function uploadFiles(input, side = 'local') {
  const files = Array.from(input.files);
  if (!files.length) return;
  let succeeded = 0;
  for (const file of files) {
    const formData = new FormData();
    formData.append('file', file);
    try {
      const r = await fetch(`${_apiBase(side)}/upload?path=${encodeURIComponent(_state[side].path)}`, {
        method: 'POST',
        body: formData,
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error((await r.json()).detail || 'Upload failed');
      succeeded++;
    } catch (e) { showToast(`${file.name}: ${e.message}`, true); }
  }
  input.value = '';
  await loadFiles(side);
  if (succeeded > 0) showToast(`Uploaded ${succeeded} of ${files.length} file(s)`);
}

async function transferFile(fromSide, path, name) {
  const toSide = fromSide === 'local' ? 'remote' : 'local';
  const destPath = _state[toSide].path;
  showToast(`Transferring "${name}"...`);
  try {
    // Download from source as blob
    const dlUrl = `${_apiBase(fromSide)}/download?path=${encodeURIComponent(path)}`;
    const dlRes = await fetch(dlUrl, { credentials: 'same-origin' });
    if (!dlRes.ok) throw new Error((await dlRes.json()).detail || 'Download failed');
    const blob = await dlRes.blob();

    // Upload to destination
    const formData = new FormData();
    formData.append('file', new File([blob], name));
    const ulUrl = `${_apiBase(toSide)}/upload?path=${encodeURIComponent(destPath)}`;
    const ulRes = await fetch(ulUrl, {
      method: 'POST',
      body: formData,
      credentials: 'same-origin',
    });
    if (!ulRes.ok) throw new Error((await ulRes.json()).detail || 'Upload failed');

    await loadFiles(toSide);
    showToast(`Transferred "${name}" to ${toSide}`);
  } catch (e) { showToast(`Transfer failed: ${e.message}`, true); }
}
