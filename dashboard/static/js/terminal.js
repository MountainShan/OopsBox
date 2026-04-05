/* ── terminal.js — Terminal controls module ── */
import * as API from './api.js';

const TERM = {};
window.TERM = TERM;

let mouseOn = true;

// ── Init ──
TERM.init = function() {
  const termIframe = document.getElementById('term-iframe');
  termIframe.src = API.TERM_URL;

  // Show local terminal tab for SSH/Container projects
  if (API.BACKEND === 'ssh' || API.BACKEND === 'container') {
    document.getElementById('ws-tab-local').style.display = '';
    document.getElementById('local-iframe').src = '/system/term/';
    document.getElementById('ws-tab-term').innerHTML =
      `<svg viewBox="0 0 24 24"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg> Remote`;
  }
};

// ── Tab switching ──
TERM.wsSwitch = function(tab) {
  const fm = document.getElementById('file-manager');
  const tp = document.getElementById('terminal-panel');
  const tFiles = document.getElementById('ws-tab-files');
  const tTerm = document.getElementById('ws-tab-term');
  const tLocal = document.getElementById('ws-tab-local');
  const termFrame = document.getElementById('term-iframe');
  const localFrame = document.getElementById('local-iframe');
  tFiles.classList.remove('on');
  tTerm.classList.remove('on');
  tLocal.classList.remove('on');
  if (tab === 'files') {
    fm.classList.remove('hidden');
    tp.classList.add('hidden');
    tFiles.classList.add('on');
  } else if (tab === 'term') {
    fm.classList.add('hidden');
    tp.classList.remove('hidden');
    termFrame.style.display = '';
    localFrame.style.display = 'none';
    tTerm.classList.add('on');
  } else if (tab === 'local') {
    fm.classList.add('hidden');
    tp.classList.remove('hidden');
    termFrame.style.display = 'none';
    localFrame.style.display = '';
    tLocal.classList.add('on');
  }
};

// ── Send keys via tmux ──
TERM.sendKey = async function(key) {
  const session = API.PROJECT === '_system' ? 'term-system' : `term-${API.PROJECT}`;
  const keyMap = {
    '\x03': 'C-c', '\x04': 'C-d', '\x1a': 'C-z', '\x0c': 'C-l',
    '\t': 'Tab', '\x1b[A': 'Up', '\x1b[B': 'Down'
  };
  const tmuxKey = keyMap[key] || key;
  try {
    await API.sendKeys(tmuxKey, session);
  } catch (e) { }
  const iframe = document.getElementById('term-iframe');
  if (iframe) iframe.focus();
};

// ── Mouse toggle ──
TERM.toggleMouse = async function() {
  mouseOn = !mouseOn;
  await API.toggleMouseApi(mouseOn);
  document.getElementById('mouse-label').textContent = mouseOn ? 'Select Mode' : 'Scroll Mode';
  document.getElementById('mouse-hint').textContent = mouseOn ? '' : 'Mouse off — long press to select text';
  document.getElementById('mouse-toggle').classList.toggle('primary', !mouseOn);
};

// ── Copy from tmux ──
TERM.copyFromTmux = async function() {
  try {
    const d = await API.getClipboard();
    let text = (d.text || '').replace(/\n+$/, '');
    if (!text) {
      const d2 = await API.getTerminalOutput(50);
      text = (d2.output || '').replace(/\n+$/, '');
    }
    if (!text) { alert('Nothing to copy'); return; }
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;left:-9999px';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    const ok = document.getElementById('copy-ok');
    ok.classList.add('show');
    setTimeout(() => ok.classList.remove('show'), 1500);
    if (!mouseOn) TERM.toggleMouse();
  } catch (e) {
    alert('Copy failed: ' + e.message);
  }
};

export default TERM;
