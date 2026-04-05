/* ── file-manager.js — File manager module ── */
import * as API from './api.js';

const FM = {};
window.FM = FM;

// ── State ──
let currentDir = '';
let entries = [];
let selectedSet = new Set();   // set of entry indices
let lastClickIdx = -1;
let clipboard = { mode: null, entries: [] }; // mode: 'copy' | 'cut'
let sortKey = localStorage.getItem('fm-sort-key') || 'name';
let sortAsc = (localStorage.getItem('fm-sort-asc') ?? 'true') === 'true';
let dragCounter = 0;

// ── Helpers ──
function formatSize(bytes) {
  if (bytes == null) return '--';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}
function formatDate(ts) {
  if (!ts) return '--';
  const d = new Date(ts * 1000);
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const h = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return `${m}/${day} ${h}:${min}`;
}
function fileIcon(name, type) {
  if (type === 'dir') return '📁';
  const ext = name.split('.').pop().toLowerCase();
  const map = {
    py: '🐍', js: '📜', ts: '📜', mjs: '📜', jsx: '📜', tsx: '📜',
    html: '🌐', htm: '🌐', css: '🎨', scss: '🎨', less: '🎨',
    json: '📋', yaml: '📋', yml: '📋', toml: '📋',
    md: '📝', markdown: '📝', txt: '📄',
    sh: '⚙️', bash: '⚙️', zsh: '⚙️',
    png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', svg: '🖼️', webp: '🖼️', ico: '🖼️', bmp: '🖼️',
    go: '🔵', rs: '🦀', c: '🔧', cpp: '🔧', h: '🔧', java: '☕',
    sql: '🗄️', dockerfile: '🐳', zip: '📦', tar: '📦', gz: '📦',
    pdf: '📕', doc: '📕', docx: '📕',
    mp3: '🎵', wav: '🎵', mp4: '🎬', avi: '🎬'
  };
  return map[ext] || '📄';
}
function isImage(name) { return /\.(png|jpg|jpeg|gif|svg|webp|ico|bmp)$/i.test(name); }

// ── DOM refs (populated on init) ──
let $body, $breadcrumb, $batchBar, $batchCount, $selectAll, $dropOverlay, $uploadProgress, $ctxMenu, $searchInput, $searchResults;

// ── Sort ──
function sortEntries(arr) {
  const dirs = arr.filter(e => e.type === 'dir');
  const files = arr.filter(e => e.type !== 'dir');
  const cmp = (a, b) => {
    let va, vb;
    if (sortKey === 'name') { va = a.name.toLowerCase(); vb = b.name.toLowerCase(); return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va); }
    if (sortKey === 'size') { va = a.size || 0; vb = b.size || 0; }
    if (sortKey === 'mtime') { va = a.mtime || 0; vb = b.mtime || 0; }
    return sortAsc ? va - vb : vb - va;
  };
  dirs.sort(cmp);
  files.sort(cmp);
  return [...dirs, ...files];
}

function setSort(key) {
  if (sortKey === key) sortAsc = !sortAsc;
  else { sortKey = key; sortAsc = true; }
  localStorage.setItem('fm-sort-key', sortKey);
  localStorage.setItem('fm-sort-asc', String(sortAsc));
  renderTable();
  updateSortIndicators();
}

function updateSortIndicators() {
  document.querySelectorAll('.fm-table th[data-sort]').forEach(th => {
    const key = th.dataset.sort;
    const icon = th.querySelector('.sort-icon');
    if (key === sortKey) {
      th.classList.add('sort-active');
      if (icon) icon.textContent = sortAsc ? '▲' : '▼';
    } else {
      th.classList.remove('sort-active');
    }
  });
}

// ── Breadcrumb ──
function updateBreadcrumb(path) {
  let html = '<span onclick="FM.loadDir(\'\')">/</span>';
  if (path) {
    const parts = path.split('/');
    let acc = '';
    for (let i = 0; i < parts.length; i++) {
      acc += (i ? '/' : '') + parts[i];
      const p = acc;
      html += `<span class="sep">/</span><span onclick="FM.loadDir('${p.replace(/'/g, "\\'")}')">${parts[i]}</span>`;
    }
  }
  $breadcrumb.innerHTML = html;
}

// ── Selection ──
function clearSelection() {
  selectedSet.clear();
  lastClickIdx = -1;
  updateSelectionUI();
}

function updateSelectionUI() {
  const rows = $body.querySelectorAll('tr[data-idx]');
  rows.forEach(tr => {
    const idx = parseInt(tr.dataset.idx);
    const cb = tr.querySelector('input[type="checkbox"]');
    if (selectedSet.has(idx)) {
      tr.classList.add('selected');
      if (cb) cb.checked = true;
    } else {
      tr.classList.remove('selected');
      if (cb) cb.checked = false;
    }
  });
  // Header checkbox
  if ($selectAll) {
    $selectAll.checked = entries.length > 0 && selectedSet.size === entries.length;
    $selectAll.indeterminate = selectedSet.size > 0 && selectedSet.size < entries.length;
  }
  // Batch bar
  if (selectedSet.size > 0) {
    $batchBar.classList.add('show');
    $batchCount.textContent = `${selectedSet.size} selected`;
  } else {
    $batchBar.classList.remove('show');
  }
}

function handleRowClick(idx, e) {
  if (e.ctrlKey || e.metaKey) {
    if (selectedSet.has(idx)) selectedSet.delete(idx);
    else selectedSet.add(idx);
    lastClickIdx = idx;
  } else if (e.shiftKey && lastClickIdx >= 0) {
    const lo = Math.min(lastClickIdx, idx);
    const hi = Math.max(lastClickIdx, idx);
    for (let i = lo; i <= hi; i++) selectedSet.add(i);
  } else {
    selectedSet.clear();
    selectedSet.add(idx);
    lastClickIdx = idx;
  }
  updateSelectionUI();
}

function handleRowDblClick(idx) {
  const e = entries[idx];
  if (!e) return;
  if (e.type === 'dir') FM.loadDir(e.path);
  else if (window.EDITOR) window.EDITOR.openFile(e.path, e.name);
}

function selectAll() {
  if (selectedSet.size === entries.length) { clearSelection(); return; }
  for (let i = 0; i < entries.length; i++) selectedSet.add(i);
  updateSelectionUI();
}

// ── Render table ──
function renderTable() {
  const sorted = sortEntries([...entries]);
  // Replace entries with sorted version (keep indices in sync)
  entries.splice(0, entries.length, ...sorted);
  $body.innerHTML = '';

  // ".." row
  if (currentDir) {
    const tr = document.createElement('tr');
    tr.className = 'fm-row-back';
    tr.innerHTML = '<td class="fm-col-check"></td><td class="fm-col-icon">&#8592;</td><td>..</td><td class="fm-col-size"></td><td class="fm-col-mtime"></td>';
    tr.ondblclick = tr.onclick = () => {
      const parts = currentDir.split('/'); parts.pop();
      FM.loadDir(parts.join('/'));
    };
    $body.appendChild(tr);
  }

  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    const tr = document.createElement('tr');
    tr.className = e.type === 'dir' ? 'fm-row-dir' : '';
    tr.dataset.idx = i;
    const icon = fileIcon(e.name, e.type);
    tr.innerHTML = `<td class="fm-col-check"><input type="checkbox" tabindex="-1"></td><td class="fm-col-icon">${icon}</td><td class="fm-col-name">${escHtml(e.name)}</td><td class="fm-col-size">${e.type === 'dir' ? '--' : formatSize(e.size)}</td><td class="fm-col-mtime">${formatDate(e.mtime)}</td>`;

    tr.onclick = (ev) => {
      if (ev.target.tagName === 'INPUT') return; // checkbox handled separately
      handleRowClick(i, ev);
    };
    tr.ondblclick = () => handleRowDblClick(i);

    // Checkbox click
    const cb = tr.querySelector('input[type="checkbox"]');
    cb.onclick = (ev) => {
      ev.stopPropagation();
      if (cb.checked) selectedSet.add(i);
      else selectedSet.delete(i);
      lastClickIdx = i;
      updateSelectionUI();
    };

    // Right-click
    tr.oncontextmenu = (ev) => {
      ev.preventDefault();
      // Select if not already selected
      if (!selectedSet.has(i)) {
        selectedSet.clear();
        selectedSet.add(i);
        lastClickIdx = i;
        updateSelectionUI();
      }
      showContextMenu(ev.clientX, ev.clientY, 'file', i);
    };

    $body.appendChild(tr);
  }

  if (!entries.length && !currentDir) {
    $body.innerHTML = '<tr><td colspan="5" class="fm-empty">No files</td></tr>';
  }

  clearSelection();
}

function escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Load directory ──
FM.loadDir = async function(path) {
  currentDir = path;
  clearSelection();
  updateBreadcrumb(path);
  $body.innerHTML = '<tr><td colspan="5" class="fm-empty">Loading...</td></tr>';
  try {
    const d = await API.listDir(path);
    entries = d.entries || [];
    renderTable();
    updateSortIndicators();
  } catch (err) {
    $body.innerHTML = `<tr><td colspan="5" class="fm-empty">Error: ${escHtml(err.message)}</td></tr>`;
  }
};

FM.getCurrentDir = function() { return currentDir; };

// ── Context menu ──
function showContextMenu(x, y, type, idx) {
  let html = '';
  if (type === 'file') {
    const e = entries[idx];
    html += `<div class="fm-ctx-item" data-action="open">📂 Open</div>`;
    html += `<div class="fm-ctx-item" data-action="rename">✏️ Rename</div>`;
    html += `<div class="fm-ctx-sep"></div>`;
    html += `<div class="fm-ctx-item" data-action="copy">📋 Copy</div>`;
    html += `<div class="fm-ctx-item" data-action="cut">✂️ Cut</div>`;
    html += `<div class="fm-ctx-item" data-action="download">⬇️ Download</div>`;
    html += `<div class="fm-ctx-sep"></div>`;
    html += `<div class="fm-ctx-item danger" data-action="delete">🗑️ Delete</div>`;
  } else {
    // Empty space context menu
    html += `<div class="fm-ctx-item" data-action="newfile">📄 New File</div>`;
    html += `<div class="fm-ctx-item" data-action="newfolder">📁 New Folder</div>`;
    if (clipboard.entries.length) {
      html += `<div class="fm-ctx-sep"></div>`;
      html += `<div class="fm-ctx-item" data-action="paste">📋 Paste (${clipboard.entries.length})</div>`;
    }
    html += `<div class="fm-ctx-sep"></div>`;
    html += `<div class="fm-ctx-item" data-action="upload">⬆️ Upload</div>`;
    html += `<div class="fm-ctx-item" data-action="refresh">🔄 Refresh</div>`;
  }
  $ctxMenu.innerHTML = html;

  // Position
  $ctxMenu.style.left = x + 'px';
  $ctxMenu.style.top = y + 'px';
  $ctxMenu.classList.add('open');

  // Adjust if off-screen
  requestAnimationFrame(() => {
    const rect = $ctxMenu.getBoundingClientRect();
    if (rect.right > window.innerWidth) $ctxMenu.style.left = (x - rect.width) + 'px';
    if (rect.bottom > window.innerHeight) $ctxMenu.style.top = (y - rect.height) + 'px';
  });

  // Handle clicks
  $ctxMenu.onclick = (ev) => {
    const item = ev.target.closest('.fm-ctx-item');
    if (!item) return;
    closeContextMenu();
    const action = item.dataset.action;
    handleContextAction(action, idx);
  };
}

function closeContextMenu() {
  $ctxMenu.classList.remove('open');
}

function handleContextAction(action, idx) {
  const selected = getSelectedEntries();
  switch (action) {
    case 'open':
      if (selected.length === 1) handleRowDblClick(idx);
      break;
    case 'rename':
      startRename(idx);
      break;
    case 'copy':
      clipboard = { mode: 'copy', entries: selected.map(e => ({ ...e })) };
      break;
    case 'cut':
      clipboard = { mode: 'cut', entries: selected.map(e => ({ ...e })) };
      break;
    case 'download':
      if (selected.length === 1) {
        window.open(API.downloadUrl(selected[0].path), '_blank');
      } else if (selected.length > 1) {
        window.open(API.zipDownloadUrl(selected.map(e => e.path)), '_blank');
      }
      break;
    case 'delete':
      doDelete(selected);
      break;
    case 'newfile':
      FM.showNewFileModal();
      break;
    case 'newfolder':
      FM.showNewFolderModal();
      break;
    case 'paste':
      doPaste();
      break;
    case 'upload':
      FM.triggerUpload();
      break;
    case 'refresh':
      FM.loadDir(currentDir);
      break;
  }
}

function getSelectedEntries() {
  return [...selectedSet].map(i => entries[i]).filter(Boolean);
}

// ── Inline rename ──
function startRename(idx) {
  const e = entries[idx];
  if (!e) return;
  const row = $body.querySelector(`tr[data-idx="${idx}"]`);
  if (!row) return;
  const nameCell = row.querySelector('.fm-col-name');
  const oldName = e.name;
  nameCell.innerHTML = `<input class="fm-rename-input" value="${escHtml(oldName)}" />`;
  const input = nameCell.querySelector('input');
  input.focus();
  // Select name without extension
  const dotIdx = oldName.lastIndexOf('.');
  if (dotIdx > 0 && e.type !== 'dir') input.setSelectionRange(0, dotIdx);
  else input.select();

  const finish = async (save) => {
    const newName = input.value.trim();
    if (save && newName && newName !== oldName) {
      const oldPath = e.path;
      const parentDir = oldPath.substring(0, oldPath.length - oldName.length);
      const newPath = parentDir + newName;
      try {
        await API.renameFile(oldPath, newPath);
        FM.loadDir(currentDir);
      } catch (err) {
        alert('Rename failed: ' + err.message);
        nameCell.textContent = oldName;
      }
    } else {
      nameCell.textContent = oldName;
    }
  };

  input.onkeydown = (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
    if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
  };
  input.onblur = () => finish(true);
}

// ── Delete ──
async function doDelete(selected) {
  if (!selected.length) return;
  const names = selected.map(e => e.name).join(', ');
  if (!confirm(`Delete ${selected.length} item(s)?\n${names}`)) return;
  try {
    await API.deleteFiles(selected.map(e => e.path));
    FM.loadDir(currentDir);
  } catch (err) {
    alert('Delete failed: ' + err.message);
  }
}

// ── Paste (copy/cut) ──
async function doPaste() {
  if (!clipboard.entries.length) return;
  try {
    if (clipboard.mode === 'copy') {
      await API.copyFiles(clipboard.entries.map(e => e.path), currentDir);
    } else {
      // Move each item
      for (const e of clipboard.entries) {
        const newPath = currentDir ? (currentDir + '/' + e.name) : e.name;
        await API.moveFile(e.path, newPath);
      }
      clipboard = { mode: null, entries: [] };
    }
    FM.loadDir(currentDir);
  } catch (err) {
    alert('Paste failed: ' + err.message);
  }
}

// ── Drag-and-drop upload ──
function initDragDrop() {
  const fm = document.getElementById('file-manager');
  fm.addEventListener('dragenter', (e) => {
    e.preventDefault();
    dragCounter++;
    $dropOverlay.classList.add('show');
  });
  fm.addEventListener('dragleave', (e) => {
    e.preventDefault();
    dragCounter--;
    if (dragCounter <= 0) { dragCounter = 0; $dropOverlay.classList.remove('show'); }
  });
  fm.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  fm.addEventListener('drop', (e) => {
    e.preventDefault();
    dragCounter = 0;
    $dropOverlay.classList.remove('show');
    const files = e.dataTransfer.files;
    if (files.length) uploadWithProgress(files);
  });
}

async function uploadWithProgress(fileList) {
  $uploadProgress.classList.add('show');
  $uploadProgress.innerHTML = '';

  const files = Array.from(fileList);
  for (const file of files) {
    const item = document.createElement('div');
    item.className = 'fm-upload-item';
    item.innerHTML = `<span class="up-name">${escHtml(file.name)}</span><div class="fm-upload-bar"><div class="fm-upload-bar-fill" style="width:0%"></div></div><span class="up-pct">0%</span>`;
    $uploadProgress.appendChild(item);

    const fill = item.querySelector('.fm-upload-bar-fill');
    const pct = item.querySelector('.up-pct');

    try {
      await API.uploadFiles(currentDir, [file], (progress) => {
        const p = Math.round(progress * 100);
        fill.style.width = p + '%';
        pct.textContent = p + '%';
      });
      fill.style.width = '100%';
      fill.style.background = 'var(--green)';
      pct.textContent = '✓';
    } catch (err) {
      fill.style.background = '#e05050';
      pct.textContent = '✗';
    }
  }

  setTimeout(() => {
    $uploadProgress.classList.remove('show');
    FM.loadDir(currentDir);
  }, 1200);
}

// ── Search ──
let searchDebounce = null;

function initSearch() {
  $searchInput.addEventListener('input', () => {
    clearTimeout(searchDebounce);
    const q = $searchInput.value.trim();
    if (!q) { $searchResults.classList.remove('open'); return; }
    // Instant filter local entries
    searchDebounce = setTimeout(() => filterLocal(q), 150);
  });
  $searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const q = $searchInput.value.trim();
      if (q) doRecursiveSearch(q);
    }
    if (e.key === 'Escape') {
      $searchResults.classList.remove('open');
      $searchInput.blur();
    }
  });
}

function filterLocal(q) {
  const lq = q.toLowerCase();
  const matches = entries.filter(e => e.name.toLowerCase().includes(lq));
  showSearchResults(matches, false);
}

async function doRecursiveSearch(q) {
  try {
    const d = await API.searchFiles(q, currentDir);
    const results = d.results || [];
    showSearchResults(results, true);
  } catch (e) {
    $searchResults.innerHTML = '<div class="fm-search-no-results">Search error</div>';
    $searchResults.classList.add('open');
  }
}

function showSearchResults(results, isRecursive) {
  if (!results.length) {
    $searchResults.innerHTML = '<div class="fm-search-no-results">No matches</div>';
    $searchResults.classList.add('open');
    return;
  }
  $searchResults.innerHTML = results.slice(0, 50).map(e => {
    const icon = fileIcon(e.name, e.type);
    const pathHint = e.path || '';
    return `<div class="fm-search-result" data-path="${escHtml(e.path)}" data-type="${e.type}" data-name="${escHtml(e.name)}"><span class="sr-icon">${icon}</span>${escHtml(e.name)}<span class="sr-path">${escHtml(pathHint)}</span></div>`;
  }).join('');
  $searchResults.classList.add('open');

  $searchResults.onclick = (ev) => {
    const item = ev.target.closest('.fm-search-result');
    if (!item) return;
    $searchResults.classList.remove('open');
    $searchInput.value = '';
    const path = item.dataset.path;
    const type = item.dataset.type;
    const name = item.dataset.name;
    if (type === 'dir') {
      FM.loadDir(path);
    } else {
      if (window.EDITOR) window.EDITOR.openFile(path, name);
    }
  };
}

// ── New file modal ──
FM.showNewFileModal = function() {
  document.getElementById('newfile-modal').classList.add('open');
  const inp = document.getElementById('newfile-path');
  inp.value = '';
  setTimeout(() => inp.focus(), 50);
};
FM.hideNewFileModal = function() {
  document.getElementById('newfile-modal').classList.remove('open');
};
FM.createNewFile = async function() {
  const name = document.getElementById('newfile-path').value.trim();
  if (!name) return;
  const path = currentDir ? (currentDir + '/' + name) : name;
  try {
    await API.writeFile(path, '');
    FM.hideNewFileModal();
    FM.loadDir(currentDir);
  } catch (e) {
    alert('Failed to create file');
  }
};

// ── New folder modal ──
FM.showNewFolderModal = function() {
  document.getElementById('newfolder-modal').classList.add('open');
  const inp = document.getElementById('newfolder-path');
  inp.value = '';
  setTimeout(() => inp.focus(), 50);
};
FM.hideNewFolderModal = function() {
  document.getElementById('newfolder-modal').classList.remove('open');
};
FM.createNewFolder = async function() {
  const name = document.getElementById('newfolder-path').value.trim();
  if (!name) return;
  const path = currentDir ? (currentDir + '/' + name) : name;
  try {
    await API.mkdirApi(path);
    FM.hideNewFolderModal();
    FM.loadDir(currentDir);
  } catch (e) {
    alert('Failed to create folder');
  }
};

// ── Upload trigger ──
FM.triggerUpload = function() {
  document.getElementById('file-upload-input').click();
};
FM.handleUpload = function(e) {
  const files = e.target.files;
  if (!files.length) return;
  uploadWithProgress(files);
  e.target.value = '';
};

// ── Download ──
FM.downloadSelected = function() {
  const sel = getSelectedEntries();
  if (sel.length === 1) {
    window.open(API.downloadUrl(sel[0].path), '_blank');
  } else if (sel.length > 1) {
    window.open(API.zipDownloadUrl(sel.map(e => e.path)), '_blank');
  } else {
    alert('Select a file first');
  }
};

// ── Batch actions ──
FM.batchDownload = function() {
  const sel = getSelectedEntries();
  if (!sel.length) return;
  if (sel.length === 1) window.open(API.downloadUrl(sel[0].path), '_blank');
  else window.open(API.zipDownloadUrl(sel.map(e => e.path)), '_blank');
};
FM.batchCopy = function() {
  clipboard = { mode: 'copy', entries: getSelectedEntries().map(e => ({ ...e })) };
  clearSelection();
};
FM.batchCut = function() {
  clipboard = { mode: 'cut', entries: getSelectedEntries().map(e => ({ ...e })) };
  clearSelection();
};
FM.batchDelete = function() {
  doDelete(getSelectedEntries());
};

// ── Sort click ──
FM.setSort = setSort;

// ── Keyboard ──
function initKeyboard() {
  document.addEventListener('keydown', (e) => {
    // Only when file manager is visible and no modal open
    const fmEl = document.getElementById('file-manager');
    if (fmEl.classList.contains('hidden')) return;
    if (document.querySelector('.modal-overlay.open')) return;
    if (document.querySelector('.small-modal-bg.open')) return;
    // Ignore if typing in an input
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    if (e.key === 'Delete' || e.key === 'Backspace') {
      const sel = getSelectedEntries();
      if (sel.length) { e.preventDefault(); doDelete(sel); }
    }
    if (e.key === 'F2') {
      const idxArr = [...selectedSet];
      if (idxArr.length === 1) { e.preventDefault(); startRename(idxArr[0]); }
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
      e.preventDefault();
      selectAll();
    }
  });
}

// ── Init ──
FM.init = function() {
  $body = document.getElementById('fm-body');
  $breadcrumb = document.getElementById('fm-breadcrumb');
  $batchBar = document.getElementById('fm-batch-bar');
  $batchCount = document.getElementById('fm-batch-count');
  $selectAll = document.getElementById('fm-select-all');
  $dropOverlay = document.getElementById('fm-drop-overlay');
  $uploadProgress = document.getElementById('fm-upload-progress');
  $ctxMenu = document.getElementById('fm-context-menu');
  $searchInput = document.getElementById('fm-search');
  $searchResults = document.getElementById('fm-search-results');

  // Select-all checkbox
  if ($selectAll) {
    $selectAll.onclick = () => selectAll();
  }

  // Empty-space right-click
  const tableWrap = document.getElementById('fm-table-wrap');
  tableWrap.addEventListener('contextmenu', (e) => {
    // Only if click on wrap or table (not on a row)
    if (e.target.closest('tr[data-idx]')) return;
    e.preventDefault();
    showContextMenu(e.clientX, e.clientY, 'empty', -1);
  });

  // Close context menu on outside click
  document.addEventListener('click', (e) => {
    if (!$ctxMenu.contains(e.target)) closeContextMenu();
  });
  // Close search results on outside click
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.fm-search-wrap')) {
      $searchResults.classList.remove('open');
    }
  });

  // Close modals on backdrop click
  document.getElementById('newfile-modal').addEventListener('click', e => { if (e.target === e.currentTarget) FM.hideNewFileModal(); });
  document.getElementById('newfolder-modal').addEventListener('click', e => { if (e.target === e.currentTarget) FM.hideNewFolderModal(); });

  initDragDrop();
  initSearch();
  initKeyboard();
  updateSortIndicators();
};

export default FM;
