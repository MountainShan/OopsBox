// dashboard/static/js/terminal.js
// Terminal toolbar: always-visible keys + expandable panel

const ALWAYS_KEYS = [
  { label: '^C', key: 'C-c', title: 'Interrupt (Ctrl+C)' },
  { label: '^D', key: 'C-d', title: 'EOF / Logout (Ctrl+D)' },
  { label: 'Tab', key: 'Tab', title: 'Tab completion' },
  { label: '↑', key: 'Up', title: 'History up' },
  { label: '↓', key: 'Down', title: 'History down' },
];

const EXTRA_KEYS = [
  { label: '^Z', key: 'C-z', title: 'Suspend (Ctrl+Z)' },
  { label: '^L', key: 'C-l', title: 'Clear screen (Ctrl+L)' },
  { label: '^A', key: 'C-a', title: 'Start of line (Ctrl+A)' },
  { label: '^E', key: 'C-e', title: 'End of line (Ctrl+E)' },
  { label: 'Esc', key: 'Escape', title: 'Escape' },
  { label: 'PgUp', key: 'PPage', title: 'Scroll up' },
  { label: 'PgDn', key: 'NPage', title: 'Scroll down' },
];

function initToolbar(projectName, containerId, projectMeta) {
  const container = document.getElementById(containerId);
  let expanded = false;
  let activeWindow = 'claude';

  const windows = projectMeta?.type === 'ssh'
    ? ['claude', 'remote', 'local']
    : ['claude', 'shell'];

  function renderKey(k) {
    const btn = document.createElement('button');
    btn.className = 'btn-icon';
    btn.title = k.title;
    btn.textContent = k.label;
    btn.style.cssText = 'font-family:var(--mono);font-size:12px;min-width:38px;';
    btn.onclick = () => sendKey(projectName, k.key);
    return btn;
  }

  function render() {
    container.innerHTML = '';
    container.style.cssText = 'display:flex;gap:4px;align-items:center;flex-wrap:wrap;padding:6px 8px;background:var(--bg2);border-bottom:1px solid var(--bg3);';

    ALWAYS_KEYS.forEach(k => container.appendChild(renderKey(k)));

    if (expanded) {
      EXTRA_KEYS.forEach(k => container.appendChild(renderKey(k)));
    }

    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'btn-icon';
    toggleBtn.title = expanded ? 'Show fewer keys' : 'Show more keys';
    toggleBtn.textContent = expanded ? '✕' : '···';
    toggleBtn.style.cssText = 'font-family:var(--mono);font-size:12px;min-width:38px;margin-left:4px;';
    toggleBtn.onclick = () => { expanded = !expanded; render(); };
    container.appendChild(toggleBtn);

    // Spacer
    const spacer = document.createElement('div');
    spacer.style.cssText = 'flex:1;';
    container.appendChild(spacer);

    // tmux window tab buttons
    const winGroup = document.createElement('div');
    winGroup.style.cssText = 'display:flex;gap:2px;';
    windows.forEach(win => {
      const btn = document.createElement('button');
      btn.className = 'btn-icon';
      btn.textContent = win;
      btn.title = `Switch to ${win} window`;
      const isActive = win === activeWindow;
      btn.style.cssText = `font-family:var(--mono);font-size:11px;padding:3px 10px;border-radius:4px;${isActive ? 'background:var(--accent);color:#fff;opacity:1;' : ''}`;
      btn.onclick = async () => {
        try {
          await api.projects.selectWindow(projectName, win);
          activeWindow = win;
          render();
        } catch (e) {
          showToast(`Window "${win}" not available`, true);
        }
      };
      winGroup.appendChild(btn);
    });
    container.appendChild(winGroup);
  }

  render();
}

async function sendKey(projectName, key) {
  try {
    await api.projects.sendKeys(projectName, key);
  } catch (e) {
    showToast('Terminal not available', true);
  }
}
