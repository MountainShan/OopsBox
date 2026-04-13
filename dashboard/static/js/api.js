// dashboard/static/js/api.js

async function request(method, path, body = null) {
  const opts = {
    method,
    headers: body !== null ? { 'Content-Type': 'application/json' } : {},
    credentials: 'same-origin',
  };
  if (body !== null) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (r.status === 401) {
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }));
    const detail = err.detail;
    const msg = Array.isArray(detail)
      ? detail.map(d => `${d.loc?.slice(-1)[0] ?? 'field'}: ${d.msg}`).join(', ')
      : (detail || r.statusText);
    throw new Error(msg);
  }
  return r.json();
}

const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body),
  put: (path, body) => request('PUT', path, body),
  delete: (path) => request('DELETE', path),

  auth: {
    login: (u, p) => request('POST', '/api/auth/login', { username: u, password: p }),
    logout: () => request('POST', '/api/auth/logout'),
    status: () => request('GET', '/api/auth/status'),
  },
  projects: {
    list: () => api.get('/api/projects'),
    get: (n) => api.get(`/api/projects/${n}`),
    create: (data) => api.post('/api/projects', data),
    delete: (n) => api.delete(`/api/projects/${n}`),
    start: (n) => api.post(`/api/projects/${n}/start`),
    stop: (n) => api.post(`/api/projects/${n}/stop`),
    status: (n) => api.get(`/api/projects/${n}/status`),
    sendKeys: (n, keys) => api.post(`/api/projects/${n}/send-keys`, { keys }),
    setupSshKey: (n) => api.post(`/api/projects/${n}/setup-ssh-key`),
    clipboard: (n) => api.get(`/api/projects/${n}/clipboard`),
    mouse: (n, enabled) => api.post(`/api/projects/${n}/mouse`, { enabled }),
    selectWindow: (n, window) => api.post(`/api/projects/${n}/select-window`, { window }),
  },
  files: {
    list: (p, path = '') => api.get(`/api/files/${p}?path=${encodeURIComponent(path)}`),
    read: (p, path) => api.get(`/api/files/${p}/read?path=${encodeURIComponent(path)}`),
    // POST /delete (not HTTP DELETE) because the path is sent in the request body
    delete: (p, path) => api.post(`/api/files/${p}/delete`, { path }),
    rename: (p, path, new_name) => api.post(`/api/files/${p}/rename`, { path, new_name }),
    mkdir: (p, path) => api.post(`/api/files/${p}/mkdir`, { path }),
    downloadUrl: (p, path) => `/api/files/${p}/download?path=${encodeURIComponent(path)}`,
    write: (p, path, content) => api.put(`/api/files/${p}/write`, { path, content }),
  },
  system: {
    stats: () => api.get('/api/system'),
  },
};

function showToast(msg, isError = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (isError ? ' error' : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}
