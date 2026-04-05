/* ── api.js — Centralized API layer ── */

const params = new URLSearchParams(location.search);
export const PROJECT = params.get('project');
export const BACKEND = params.get('backend') || 'local';
export const TERM_URL = params.get('termUrl') || `/proj/${PROJECT}/term/`;

const base = `/api/files/${PROJECT}`;
const projBase = `/api/projects/${PROJECT}`;

function qs(obj) {
  return Object.entries(obj)
    .filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join('&');
}

export async function listDir(path) {
  const r = await fetch(`${base}?path=${encodeURIComponent(path)}`, { credentials: 'include' });
  if (!r.ok) throw new Error('Failed to list directory');
  return r.json();
}

export async function readFile(path, offset, limit) {
  const q = qs({ path, offset, limit });
  const r = await fetch(`${base}/read?${q}`, { credentials: 'include' });
  if (!r.ok) throw new Error('Cannot open file');
  return r.json();
}

export async function writeFile(path, content) {
  const r = await fetch(`${base}/write?path=${encodeURIComponent(path)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
    credentials: 'include'
  });
  if (!r.ok) throw new Error('Failed to write file');
  return r.json();
}

export function uploadFiles(path, files, onProgress) {
  // Returns a promise. Uses XHR for progress events.
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const f of files) form.append('file', f);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${base}/upload?path=${encodeURIComponent(path)}`);
    xhr.withCredentials = true;
    if (onProgress) {
      xhr.upload.addEventListener('progress', e => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      });
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error('Upload failed'));
    };
    xhr.onerror = () => reject(new Error('Upload network error'));
    xhr.send(form);
  });
}

export function downloadUrl(path) {
  return `${base}/download?path=${encodeURIComponent(path)}`;
}

export async function renameFile(path, newName) {
  const r = await fetch(`/api/files/${PROJECT}/rename?path=${encodeURIComponent(path)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_name: newName }), credentials: 'include'
  });
  if (!r.ok) throw new Error('Failed to rename');
  return r.json();
}

export async function deleteFiles(paths) {
  const r = await fetch(`${base}/delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths }),
    credentials: 'include'
  });
  if (!r.ok) throw new Error('Delete failed');
  return r.json();
}

export async function mkdirApi(path) {
  const r = await fetch(`${base}/mkdir`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
    credentials: 'include'
  });
  if (!r.ok) throw new Error('Mkdir failed');
  return r.json();
}

export async function copyFiles(paths, dest) {
  const r = await fetch(`${base}/copy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths, destination: dest }),
    credentials: 'include'
  });
  if (!r.ok) throw new Error('Copy failed');
  return r.json();
}

export async function moveFile(oldPath, newPath) {
  const newName = newPath.split('/').pop();
  return renameFile(oldPath, newName);
}

export async function searchFiles(query, path) {
  const q = qs({ q: query, path });
  const r = await fetch(`${base}/search?${q}`, { credentials: 'include' });
  if (!r.ok) throw new Error('Search failed');
  return r.json();
}

export function zipDownloadUrl(paths) {
  return `/api/files/${PROJECT}/zip-download?paths=${paths.map(encodeURIComponent).join(',')}`;
}

export async function sendKeys(key, session) {
  await fetch(`${projBase}/send-keys?keys=${encodeURIComponent(key)}&session=${encodeURIComponent(session)}`, {
    method: 'POST', credentials: 'include'
  });
}

export async function toggleMouseApi(on) {
  await fetch(`${projBase}/mouse?on=${on}`, { method: 'POST', credentials: 'include' });
}

export async function getClipboard() {
  const r = await fetch(`${projBase}/clipboard`, { credentials: 'include' });
  if (!r.ok) throw new Error('API error');
  return r.json();
}

export async function getTerminalOutput(lines) {
  const r = await fetch(`${projBase}/terminal-output?lines=${lines}`, { credentials: 'include' });
  if (!r.ok) throw new Error('API error');
  return r.json();
}
