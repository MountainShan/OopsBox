// dashboard/static/js/statusbar.js
// The bar along the bottom: each agent's account usage and the machine's
// load (/api/overview), one line. Tapping an item shows its details above
// the bar.

const StatusBar = (() => {
  const { esc, el, bar } = ChatUI;
  const EVERY = 15000;
  // short names for the limits the agents report
  const SHORT = { '5h': '5h', 'Weekly': 'wk', 'Weekly (all models)': 'wk', 'Credits': '' };
  let data = null;
  let open = null;  // key of the item whose details are showing
  let pop = null;

  function gb(bytes) { return (bytes / 2 ** 30).toFixed(bytes < 10 * 2 ** 30 ? 1 : 0) + ' GB'; }

  function when(iso) {
    if (!iso) return '';
    const t = new Date(iso.length === 10 ? iso + 'T00:00:00' : iso);
    if (isNaN(t)) return iso;
    const mins = Math.round((t - Date.now()) / 60000);
    const abs = t.toLocaleString('en-US', { hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    if (mins <= 0) return `Resets ${abs}`;
    const rel = mins < 60 ? `${mins} min` : mins < 48 * 60 ? `${Math.round(mins / 60)} h` : `${Math.round(mins / 1440)} days`;
    return `Resets in ${rel} (${abs})`;
  }

  function uptime(s) {
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600);
    return d ? `${d}d ${h}h` : `${h}h ${Math.floor(s % 3600 / 60)}m`;
  }

  const pct = (p, alert = 80) => `<span class="sb-pct${p >= alert ? ' high' : ''}">${Math.round(p)}%</span>`;

  function agentItem(name, a) {
    const limits = (a.limits || []).filter(l => l.label in SHORT).slice(0, 2);
    const parts = limits.map(l => (SHORT[l.label] ? SHORT[l.label] + ' ' : '') + pct(l.percent, data.alert_pct || 80));
    // on a phone only the first limit shows (.sb-wide); the rest is in the details
    const text = parts.length ? parts[0] + parts.slice(1).map(p => `<span class="sb-wide"> · ${p}</span>`).join('')
      : '<span class="sb-dim">—</span>';
    return `<span class="sb-name">${esc(name)}</span> ${text}`;
  }

  function agentDetails(name, a) {
    const box = el('div', 'sb-details');
    box.dataset.agent = name;
    box.appendChild(el('div', 'sb-details-head', `<span class="sb-name">${esc(name)}</span>` +
      (a.plan ? `<span class="sb-chip">${esc(a.plan)}</span>` : '')));
    const at = a.at ? new Date(a.at * 1000).toLocaleTimeString('en-GB', { hour12: false, hour: '2-digit', minute: '2-digit' }) : '';
    const state = {
      stopped: 'Not running. Usage appears once its chat is open in any project.',
      stale: `Not running; figures from ${at}`,
      error: `Could not read usage: ${a.error}` + (at ? ` (figures from ${at})` : ''),
    }[a.state] || [a.running_in?.length ? `Running in ${a.running_in.join(', ')}` : '', at && `updated ${at}`].filter(Boolean).join(' · ');
    box.appendChild(el('div', 'sb-sub' + (a.state === 'error' ? ' sb-error' : ''), esc(state)));
    (a.limits || []).forEach(l => {
      const b = bar(l.label, l.percent, (l.detail ? l.detail + ' · ' : '') + Math.round(l.percent) + '%');
      if (l.resets_at) b.appendChild(el('div', 'sb-sub', esc(when(l.resets_at))));
      box.appendChild(b);
    });
    (a.notes || []).forEach(n => box.appendChild(el('div', 'sb-sub', esc(n))));
    return box;
  }

  function systemItem(s) {
    // disks and GPUs are left to the details on a phone (.sb-wide)
    const items = [`CPU ${pct(s.cpu_percent)}`, `RAM ${pct(s.ram.percent)}`];
    s.disks.forEach(d => items.push(`<span class="sb-wide">${esc(d.path)} ${pct(d.percent)}</span>`));
    s.gpus.forEach(g => items.push(`<span class="sb-wide">GPU ${pct(g.percent)}</span>`));
    if (s.gpu_error) items.push('<span class="sb-wide">GPU <span class="sb-error">!</span></span>');
    return items.map((x, i) => (i ? (x.startsWith('<span class="sb-wide">') ? x.replace('<span class="sb-wide">', '<span class="sb-wide"> · ') : ' · ' + x) : x)).join('');
  }

  function systemDetails(s) {
    const box = el('div', 'sb-details');
    box.appendChild(el('div', 'sb-details-head', '<span class="sb-name">System</span>' +
      `<span class="sb-chip">up ${esc(uptime(s.uptime))}</span>`));
    box.appendChild(bar(`CPU · ${s.cpus} cores`, s.cpu_percent, `${Math.round(s.cpu_percent)}%`));
    box.appendChild(el('div', 'sb-sub', `Load ${s.load.map(x => x.toFixed(2)).join(' / ')} (1 / 5 / 15 min)`));
    box.appendChild(bar('Memory', s.ram.percent, `${gb(s.ram.used)} / ${gb(s.ram.total)}`));
    s.disks.forEach(d => box.appendChild(bar(`Disk ${d.path}`, d.percent, `${gb(d.used)} / ${gb(d.total)}`)));
    s.gpus.forEach(g => {
      box.appendChild(bar(`GPU · ${g.name}`, g.percent, `${Math.round(g.percent)}% · ${Math.round(g.temp)}°C`));
      box.appendChild(bar('GPU memory', 100 * g.mem_used / g.mem_total, `${gb(g.mem_used)} / ${gb(g.mem_total)}`));
    });
    if (s.gpu_error) box.appendChild(el('div', 'sb-sub sb-error', 'GPU: ' + esc(s.gpu_error)));
    return box;
  }

  function details(key) {
    if (key === 'system') return systemDetails(data.system);
    return agentDetails(key, data.agents[key]);
  }

  function showPop() {
    pop?.remove();
    pop = null;
    if (!open || !data) return;
    pop = el('div', 'sb-pop');
    pop.appendChild(details(open));
    document.getElementById('statusBar').appendChild(pop);
  }

  function render() {
    const root = document.getElementById('statusBar');
    root.innerHTML = '';
    const line = el('div', 'sb-line');
    const add = (key, html, agent) => {
      const b = el('button', 'sb-item' + (open === key ? ' active' : ''), html);
      if (agent) b.dataset.agent = agent;
      b.onclick = (e) => { e.stopPropagation(); open = open === key ? null : key; render(); };
      line.appendChild(b);
    };
    // agents waiting for you come first, and put a count in the tab title
    const waiting = data.waiting || [];
    document.title = waiting.length ? `(${waiting.length}) OopsBox` : 'OopsBox';
    if (waiting.length) {
      const w = waiting[0];
      const b = el('button', 'sb-item sb-waiting',
        `⚠ <span class="sb-name">${esc(w.agent)}</span><span class="sb-wide"> needs you · ${esc(w.project)}</span>` +
        (waiting.length > 1 ? ` <span class="sb-dim">+${waiting.length - 1}</span>` : ''));
      b.dataset.agent = w.agent;
      b.title = waiting.map(x => `${x.agent} · ${x.project}`).join('\n');
      b.onclick = (e) => { e.stopPropagation(); window.openFromUrl?.(`/?open=${encodeURIComponent(w.project)}&agent=${w.agent}`); };
      line.appendChild(b);
    }
    Object.entries(data.agents).forEach(([name, a]) => add(name, agentItem(name, a), name));
    line.appendChild(el('span', 'sb-spacer'));
    add('system', systemItem(data.system));
    root.appendChild(line);
    pop = null;
    showPop();
  }

  async function refresh() {
    try { data = await api.get('/api/overview'); }
    catch (_) { return; }  // keep the last numbers; the next refresh may succeed
    render();
  }

  function start() {
    document.addEventListener('click', (e) => {
      if (open && !e.target.closest('#statusBar')) { open = null; render(); }
    });
    refresh();
    setInterval(() => { if (!document.hidden) refresh(); }, EVERY);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  }

  return { start };
})();

StatusBar.start();
