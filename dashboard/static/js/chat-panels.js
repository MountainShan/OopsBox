// dashboard/static/js/chat-panels.js
// Panels drawn for particular commands, where the generic data view in
// chat-ui.js would bury what matters. Each renderer gets the command's data
// as the agent sent it, and ctx.run(command, value) for follow-up actions.

(() => {
  const { esc, el, bar, button, generic, register } = ChatUI;
  const pct = (n) => `${(Number(n) || 0).toFixed(1)}%`;
  const num = (n) => (Number(n) || 0).toLocaleString();

  // ── kiro ────────────────────────────────────────
  const KIRO_CONTEXT = {
    contextFiles: 'Context files', tools: 'Tool definitions', kiroResponses: 'Kiro responses',
    yourPrompts: 'Your prompts', sessionFiles: 'Session files',
  };

  register('kiro', '/context', (c, d) => {
    c.appendChild(bar(`context (${d.model || ''})`, d.contextUsagePercentage));
    const rows = Object.entries(d.breakdown || {}).map(([k, v]) =>
      `<tr><th>${esc(KIRO_CONTEXT[k] || k)}</th><td>${num(v.tokens)} tokens</td><td>${pct(v.percent)}</td></tr>`).join('');
    c.appendChild(el('table', 'cu-kv', rows));
    const files = d.breakdown?.contextFiles?.items || [];
    if (files.length) {
      const det = el('details', '', `<summary>Context files (${files.length})</summary>`);
      det.appendChild(el('table', 'cu-table', '<thead><tr><th>File</th><th>tokens</th><th></th></tr></thead><tbody>' +
        files.map(f => `<tr><td>${esc(f.name)}</td><td>${num(f.tokens)}</td><td>${f.matched ? 'loaded' : '<span class="chat-dim">no match</span>'}</td></tr>`).join('') +
        '</tbody>'));
      c.appendChild(det);
    }
    const groups = d.breakdown?.tools?.groups || [];
    if (groups.length) {
      const det = el('details', '', '<summary>Tool definitions</summary>');
      groups.forEach(g => det.appendChild(el('div', 'chat-dim',
        `${esc(g.name)}: ${num(g.tokens)} tokens · ${(g.items || []).map(i => esc(i.name)).join(', ')}`)));
      c.appendChild(det);
    }
  });

  register('kiro', '/usage', (c, d) => {
    c.appendChild(el('div', 'cu-plain', `<b>${esc(d.planName || '')}</b>  Billing cycle resets: ${esc(d.billingCycleReset || '—')}`));
    (d.usageBreakdowns || []).forEach(u => {
      const note = u.hasLimit ? `${num(u.used)} / ${num(u.limit)} (${u.percentage}%)` : num(u.used);
      c.appendChild(bar(u.displayName || u.resourceType, u.percentage, note));
      if (u.currentOverages) {
        c.appendChild(el('div', 'chat-dim',
          `Overage ${num(u.currentOverages)}, charges ${esc(u.currency || '')} ${(Number(u.overageCharges) || 0).toFixed(2)}`));
      }
    });
    const extra = [...(d.bonusCredits || []), ...(d.addOnCredits || [])];
    if (extra.length) c.appendChild(generic(extra, 1));
  });

  const TOOL_STATUS = { 'requires-approval': 'ask every time', trusted: 'trusted', denied: 'blocked' };

  register('kiro', '/tools', (c, d, ctx) => {
    const head = el('div', 'cu-row-actions');
    head.append(button('Trust all', () => ctx.run('/tools', 'trust-all')),
                button('Reset to defaults', () => ctx.run('/tools', 'reset')));
    c.appendChild(head);
    const t = el('table', 'cu-table', '<thead><tr><th>Tool</th><th>Source</th><th>Permission</th><th></th></tr></thead>');
    const tb = el('tbody');
    (d.tools || []).forEach(tool => {
      const tr = el('tr', '', `<td title="${esc((tool.description || '').trim())}">${esc(tool.name)}</td>` +
        `<td>${esc(tool.source || '')}</td><td>${esc(TOOL_STATUS[tool.status] || tool.status || '')}</td>`);
      const td = el('td', 'cu-row-actions');
      if (tool.status !== 'trusted') td.appendChild(button('Trust', () => ctx.run('/tools', `trust ${tool.name}`)));
      else td.appendChild(button('Untrust', () => ctx.run('/tools', `untrust ${tool.name}`)));
      tr.appendChild(td);
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    const s = el('div', 'cu-scroll');
    s.appendChild(t);
    c.appendChild(s);
  });

  register('kiro', '/mcp', (c, d, ctx) => {
    const servers = d.servers || [];
    if (!servers.length) { c.appendChild(el('div', 'chat-dim', 'No MCP servers configured.')); }
    else {
      c.appendChild(el('table', 'cu-table', '<thead><tr><th>Server</th><th>Status</th><th>Tools</th></tr></thead><tbody>' +
        servers.map(s => `<tr><td>${esc(s.name)}</td><td>${esc(s.authenticating ? 'signing in' : s.status)}</td><td>${num(s.toolCount)}</td></tr>`).join('') +
        '</tbody>'));
    }
    c.appendChild(el('div', 'chat-dim', 'To add or remove: <code>/mcp add …</code>, <code>/mcp remove &lt;name&gt;</code>, or edit mcp.json in ⚙ settings.'));
    c.appendChild(button('Refresh', () => ctx.refresh()));
  });

  register('kiro', '/rewind', (c, d, ctx) => {
    const turns = d.turns || [];
    if (!turns.length) { c.appendChild(el('div', 'chat-dim', 'No turns to go back to yet.')); return; }
    c.appendChild(el('div', 'chat-dim', 'Go back to before a turn (this branches into a new conversation; the original is kept):'));
    turns.forEach((t, i) => {
      const index = t.turnIndex ?? t.logIndex ?? i;
      const row = el('div', 'cu-rewind');
      row.appendChild(button(String(t.label || '').slice(0, 90), () => {
        if (!confirm('Go back to before this turn?')) return;
        ctx.run('/rewind', String(index));  // kiro's logIndex
      }));
      if (t.responseSnippet) row.appendChild(el('div', 'chat-dim', esc(t.responseSnippet.split('\n')[0].slice(0, 140))));
      c.appendChild(row);
    });
  });

  // ── codex ───────────────────────────────────────
  function codexLimits(c, rl) {
    if (!rl) return;
    const win = (w) => !w ? null : (w.windowDurationMins >= 10080 ? 'weekly' : w.windowDurationMins >= 1440 ? 'daily' : `${Math.round(w.windowDurationMins / 60)} h`);
    const reset = (w) => w?.resetsAt ? 'resets ' + new Date(w.resetsAt * 1000).toLocaleString('en-GB', { hour12: false }) : '';
    if (rl.primary) c.appendChild(bar(`Limit (${win(rl.primary)})`, rl.primary.usedPercent, `${rl.primary.usedPercent}% · ${reset(rl.primary)}`));
    if (rl.secondary) c.appendChild(bar(`Limit (${win(rl.secondary)})`, rl.secondary.usedPercent, `${rl.secondary.usedPercent}% · ${reset(rl.secondary)}`));
    if (rl.credits && !rl.credits.unlimited) c.appendChild(el('div', 'chat-dim', `Credits balance: ${esc(rl.credits.balance)}`));
  }

  register('codex', '/status', (c, d) => {
    const rows = [['Model', d.model], ['Reasoning effort', d.effort || 'default'], ['Permissions', d.permissions],
      ['Plan mode', d.plan_mode ? 'on' : 'off'], ['Fast', d.fast ? 'on' : 'off'], ['Personality', d.personality],
      ['Account', d.account ? `${d.account.email || ''} (${d.account.planType || d.account.type || ''})` : '—'],
      ['Folder', d.cwd], ['Conversation', d.thread]];
    c.appendChild(el('table', 'cu-kv', rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v ?? '—')}</td></tr>`).join('')));
    if (d.context) c.appendChild(bar('context', d.context.percent, d.context.label));
    codexLimits(c, d.rate_limits);
    if (d.config && Object.keys(d.config).length) {
      const det = el('details', '', '<summary>Effective config.toml settings</summary>');
      det.appendChild(generic(d.config, 1));
      c.appendChild(det);
    }
  });

  register('codex', '/usage', (c, d) => {
    codexLimits(c, d.rate_limits);
    if (d.summary) c.appendChild(el('div', 'chat-dim',
      `Lifetime ${num(d.summary.lifetimeTokens)} tokens · daily peak ${num(d.summary.peakDailyTokens)} · ${d.summary.currentStreakDays}-day streak`));
    if (d.daily?.length) {
      const max = Math.max(...d.daily.map(x => x.tokens || 0), 1);
      d.daily.forEach(x => c.appendChild(bar(x.startDate, 100 * (x.tokens || 0) / max, num(x.tokens), false)));
    }
  });

  register('codex', '/mcp', (c, d, ctx) => {
    const head = el('div', 'cu-row-actions');
    head.appendChild(button('Reload config', () => ctx.run('/mcp', 'reload')));
    c.appendChild(head);
    (d.servers || []).forEach(sv => {
      const row = el('div', 'cu-plain', `<b>${esc(sv.name)}</b> <span class="chat-dim">${esc(sv.auth)} · ${sv.tools.length} tools</span>`);
      if (/not|unauth|required/i.test(sv.auth || '')) row.appendChild(button('Sign in', () => ctx.run('/mcp', 'login ' + sv.name)));
      const det = el('details', '', '<summary>Tools</summary>');
      det.appendChild(el('div', 'chat-dim', sv.tools.map(esc).join(', ') || '(none)'));
      c.append(row, det);
    });
  });

  function toggleTable(c, rows, cols, isOn, onToggle) {
    const t = el('table', 'cu-table', `<thead><tr>${cols.map(x => `<th>${esc(x[0])}</th>`).join('')}<th></th></tr></thead>`);
    const tb = el('tbody');
    rows.forEach(r => {
      const tr = el('tr', '', cols.map(x => `<td>${esc(r[x[1]] ?? '')}</td>`).join(''));
      const td = el('td');
      td.appendChild(button(isOn(r) ? 'Disable' : 'Enable', () => onToggle(r)));
      tr.appendChild(td);
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    const sc = el('div', 'cu-scroll');
    sc.appendChild(t);
    c.appendChild(sc);
  }

  register('codex', '/skills', (c, d, ctx) => toggleTable(c, d.skills || [],
    [['Skill', 'name'], ['Scope', 'scope'], ['Description', 'description']],
    (r) => r.enabled, (r) => ctx.run('/skills', `${r.enabled ? 'disable' : 'enable'} ${r.name}`)));

  register('codex', '/experimental', (c, d, ctx) => toggleTable(c, d.features || [],
    [['Feature', 'name'], ['Stage', 'stage'], ['Description', 'description']],
    (r) => r.enabled, (r) => ctx.run('/experimental', `${r.enabled ? 'disable' : 'enable'} ${r.name}`)));

  register('codex', '/plugins', (c, d, ctx) => {
    if (!d.installed?.length) { c.appendChild(el('div', 'chat-dim', 'No plugins installed.')); return; }
    d.installed.forEach(pl => {
      const row = el('div', 'cu-row-actions', `<span><b>${esc(pl.name)}</b> <span class="chat-dim">${esc(pl.version || '')}${pl.enabled ? '' : ' (disabled)'}</span></span>`);
      row.appendChild(button('Remove', () => { if (confirm(`Remove ${pl.name}?`)) ctx.run('/plugins', 'uninstall ' + pl.id); }));
      c.appendChild(row);
    });
  });

  register('codex', '/diff', (c, d) => {
    if (d.untracked?.length) c.appendChild(el('div', 'chat-dim', 'Untracked: ' + d.untracked.map(esc).join(', ')));
    if (d.diff) c.appendChild(el('pre', 'chat-pre', esc(d.diff)));
  });

  // ── claude ──────────────────────────────────────
  const CLAUDE_WINDOWS = { five_hour: '5 h', seven_day: 'Weekly', seven_day_opus: 'Weekly (Opus)', seven_day_sonnet: 'Weekly (Sonnet)' };

  function claudeLimits(c, rl) {
    Object.entries(rl || {}).forEach(([k, v]) => {
      if (!v || typeof v !== 'object' || v.utilization == null) return;
      const reset = v.resets_at ? 'resets ' + new Date(v.resets_at).toLocaleString('en-GB', { hour12: false }) : '';
      c.appendChild(bar(CLAUDE_WINDOWS[k] || k, v.utilization, `${v.utilization}% · ${reset}`));
    });
  }

  register('claude', '/context', (c, d) => {
    const total = d.totalTokens ?? (d.categories || []).filter(x => !x.isDeferred).reduce((a, x) => a + (x.tokens || 0), 0);
    if (d.maxTokens || d.rawMaxTokens) c.appendChild(bar(`context (${d.model || ''})`, 100 * total / (d.maxTokens || d.rawMaxTokens),
      `${num(total)} / ${num(d.maxTokens || d.rawMaxTokens)}`));
    c.appendChild(el('table', 'cu-kv', (d.categories || []).map(x =>
      `<tr><th>${esc(x.name)}</th><td>${num(x.tokens)} tokens${x.isDeferred ? ' (deferred)' : ''}</td></tr>`).join('')));
    const rest = Object.fromEntries(Object.entries(d).filter(([k]) => !['categories', 'totalTokens', 'maxTokens', 'rawMaxTokens', 'model', 'percentage'].includes(k)));
    if (Object.keys(rest).length) {
      const det = el('details', '', '<summary>Details</summary>');
      det.appendChild(generic(rest, 1));
      c.appendChild(det);
    }
  });

  register('claude', '/usage', (c, d) => {
    if (d.subscription_type) c.appendChild(el('div', 'cu-plain', `Plan: <b>${esc(d.subscription_type)}</b>`));
    claudeLimits(c, d.rate_limits);
    const ss = d.session || {};
    c.appendChild(el('div', 'chat-dim', `This conversation: USD ${(Number(ss.total_cost_usd) || 0).toFixed(2)} · ` +
      `API ${Math.round((ss.total_api_duration_ms || 0) / 1000)} s · +${ss.total_lines_added || 0} −${ss.total_lines_removed || 0} lines`));
  });

  register('claude', '/status', (c, d) => {
    const a = d.account || {};
    const rows = [['Account', a.email || a.emailAddress || '—'], ['Plan', a.subscriptionType || a.organization || '—'],
      ['Model', d.model], ['Reasoning effort', d.effort || 'default'], ['Permission mode', d.mode], ['Folder', d.cwd], ['Conversation', d.session]];
    c.appendChild(el('table', 'cu-kv', rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v ?? '—')}</td></tr>`).join('')));
    if (d.context) c.appendChild(bar('context', d.context.percent, d.context.label));
    claudeLimits(c, d.rate_limits);
    if (d.mcp?.length) c.appendChild(el('div', 'chat-dim', 'MCP: ' + d.mcp.map(m => `${esc(m.name)} (${esc(m.status)})`).join(', ')));
  });

  register('claude', '/mcp', (c, d, ctx) => {
    const t = el('table', 'cu-table', '<thead><tr><th>Server</th><th>Status</th><th>Scope</th><th></th></tr></thead>');
    const tb = el('tbody');
    (d.servers || []).forEach(sv => {
      const tr = el('tr', '', `<td title="${esc(sv.error || '')}">${esc(sv.name)}</td><td>${esc(sv.status)}</td><td>${esc(sv.scope || '')}</td>`);
      const td = el('td', 'cu-row-actions');
      if (sv.status === 'disabled') td.appendChild(button('Enable', () => ctx.run('/mcp', 'enable ' + sv.name)));
      else {
        td.appendChild(button('Disable', () => ctx.run('/mcp', 'disable ' + sv.name)));
        if (sv.status !== 'connected') td.appendChild(button('Reconnect', () => ctx.run('/mcp', 'reconnect ' + sv.name)));
      }
      tr.appendChild(td);
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    c.appendChild(t);
    c.appendChild(el('div', 'chat-dim', 'To add a server: edit it in ⚙ settings, or run <code>claude mcp add</code> in the Terminal.'));
  });

  register('claude', '/permissions', (c, d, ctx) => {
    c.appendChild(el('div', 'cu-plain', `Current mode: <b>${esc(d.mode)}</b>`));
    [['allow', 'Allow'], ['ask', 'Ask every time'], ['deny', 'Deny'], ['additionalDirectories', 'Additional directories']].forEach(([k, label]) => {
      c.appendChild(el('div', 'cu-plain', `<b>${label}</b> (${(d[k] || []).length})`));
      if ((d[k] || []).length) c.appendChild(el('ul', 'cu-list', d[k].map(r => `<li><code>${esc(r)}</code></li>`).join('')));
    });
    c.appendChild(button('Edit config file', () => ctx.openSettings()));
  });

  register('claude', '/hooks', (c, d, ctx) => {
    const events = Object.entries(d.hooks || {});
    if (!events.length) c.appendChild(el('div', 'chat-dim', 'No hooks configured.'));
    events.forEach(([ev, list]) => {
      c.appendChild(el('div', 'cu-plain', `<b>${esc(ev)}</b>`));
      c.appendChild(generic(list, 1));
    });
    c.appendChild(button('Edit config file', () => ctx.openSettings()));
  });

  register('claude', '/rewind', (c, d, ctx) => {
    if (d.files) { c.appendChild(el('ul', 'cu-list', d.files.map(f => `<li>${esc(f)}</li>`).join(''))); return; }
    if (!d.messages?.length) { c.appendChild(el('div', 'chat-dim', 'No messages to rewind to in this web conversation yet.')); return; }
    c.appendChild(el('div', 'chat-dim', 'Restore files to before this message was sent:'));
    d.messages.forEach(m => c.appendChild(button(m.text.split('\n')[0].slice(0, 90), () => {
      if (confirm('Revert all file changes claude made after this?')) ctx.run('/rewind', m.id);
    })));
  });

  register('claude', '/tasks', (c, d, ctx) => {
    if (!d.tasks?.length) { c.appendChild(el('div', 'chat-dim', 'No background tasks.')); return; }
    d.tasks.forEach(t => {
      const row = el('div', 'cu-row-actions', `<span>${esc(t.description)} <span class="chat-dim">${esc(t.task_type)}</span></span>`);
      row.appendChild(button('Stop', () => ctx.run('/tasks', 'stop ' + t.task_id)));
      c.appendChild(row);
    });
  });

  register('claude', '/plugin', (c, d, ctx) => {
    const t = el('table', 'cu-table', '<thead><tr><th>Plugin</th><th>Version</th><th>Scope</th><th></th></tr></thead>');
    const tb = el('tbody');
    (d.plugins || []).forEach(pl => {
      const tr = el('tr', '', `<td>${esc(pl.id)}</td><td>${esc(pl.version || '')}</td><td>${esc(pl.scope || '')}</td>`);
      const td = el('td', 'cu-row-actions');
      td.appendChild(button(pl.enabled ? 'Disable' : 'Enable', () => ctx.run('/plugin', `${pl.enabled ? 'disable' : 'enable'} ${pl.id}`)));
      td.appendChild(button('Remove', () => { if (confirm(`Remove ${pl.id}?`)) ctx.run('/plugin', 'uninstall ' + pl.id); }));
      tr.appendChild(td);
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    c.appendChild(t);
    c.appendChild(el('div', 'chat-dim', 'Install: <code>/plugin install &lt;name@marketplace&gt;</code>'));
  });
})();

