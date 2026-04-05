# Chat Integration Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade OopsBox chat page with slash commands, complete tool call display, reliable permission prompts, SSE streaming, and skills detection.

**Architecture:** All changes target `dashboard/static/chat.html` (655-line monolith) and `dashboard/main.py` (FastAPI backend). Chat stays as a single HTML file — no module split needed since it's loaded in an iframe.

**Tech Stack:** Vanilla JS, FastAPI, SSE (EventSource), marked.js, highlight.js

---

### Task 1: Slash Command Autocomplete

**Files:**
- Modify: `dashboard/static/chat.html`

This is frontend-only. Slash commands are regular text that Claude Code CLI handles.

- [ ] **Step 1: Add autocomplete CSS**

Add after the `.send-btn:disabled` rule (~line 152):

```css
/* Slash command autocomplete */
.slash-popup{display:none;position:absolute;bottom:100%;left:0;right:0;background:var(--bg2);border:1px solid var(--border);
  border-radius:8px;max-height:220px;overflow-y:auto;z-index:50;box-shadow:0 -4px 16px rgba(0,0,0,.4);margin-bottom:4px}
.slash-popup.show{display:block}
.slash-item{display:flex;align-items:center;gap:10px;padding:8px 14px;cursor:pointer;transition:background .1s}
.slash-item:hover,.slash-item.active{background:var(--bg3)}
.slash-cmd{color:var(--accent);font-weight:600;font-size:13px;font-family:monospace;min-width:140px}
.slash-desc{color:var(--muted);font-size:12px}
```

- [ ] **Step 2: Add autocomplete popup HTML**

Add inside `.input-area`, right before the `.input-row` div (~line 206). The `.input-area` needs `position:relative` added to its CSS.

```html
<div class="slash-popup" id="slash-popup"></div>
```

Add `position:relative` to `.input-area` CSS rule.

- [ ] **Step 3: Add slash commands data and autocomplete logic**

Add after the `autoGrow` function (~line 624):

```js
// ── Slash command autocomplete ──
const SLASH_COMMANDS=[
  {cmd:'/compact',desc:'Compress conversation context'},
  {cmd:'/clear',desc:'Clear conversation history'},
  {cmd:'/help',desc:'Show help'},
  {cmd:'/cost',desc:'Show token usage'},
  {cmd:'/doctor',desc:'Check installation health'},
  {cmd:'/init',desc:'Initialize project with CLAUDE.md'},
  {cmd:'/login',desc:'Switch Anthropic account'},
  {cmd:'/logout',desc:'Sign out'},
  {cmd:'/status',desc:'Show account status'},
  {cmd:'/memory',desc:'Edit CLAUDE.md memory files'},
  {cmd:'/permissions',desc:'View permissions'},
  {cmd:'/model',desc:'Switch AI model'},
  {cmd:'/config',desc:'View configuration'},
  {cmd:'/vim',desc:'Toggle Vim mode'},
  {cmd:'/review',desc:'Request code review'},
  {cmd:'/pr-comments',desc:'View PR comments'},
  {cmd:'/terminal-setup',desc:'Install shell integration'},
];
let slashFiltered=[];
let slashIdx=-1;

function showSlash(items){
  const popup=document.getElementById('slash-popup');
  slashFiltered=items;slashIdx=-1;
  if(!items.length){hideSlash();return;}
  popup.innerHTML=items.map((it,i)=>
    `<div class="slash-item" data-idx="${i}" onmousedown="event.preventDefault();pickSlash(${i})">` +
    `<span class="slash-cmd">${it.cmd}</span><span class="slash-desc">${it.desc}</span></div>`
  ).join('');
  popup.classList.add('show');
}

function hideSlash(){
  const popup=document.getElementById('slash-popup');
  popup.classList.remove('show');popup.innerHTML='';
  slashFiltered=[];slashIdx=-1;
}

function highlightSlash(idx){
  slashIdx=idx;
  document.querySelectorAll('.slash-item').forEach((el,i)=>el.classList.toggle('active',i===idx));
  const active=document.querySelector('.slash-item.active');
  if(active)active.scrollIntoView({block:'nearest'});
}

function pickSlash(idx){
  if(idx<0||idx>=slashFiltered.length)return;
  const ta=document.getElementById('input');
  ta.value=slashFiltered[idx].cmd+' ';
  hideSlash();ta.focus();
}
```

- [ ] **Step 4: Hook autocomplete into input events**

Modify the existing `handleInputKey` function and add an input listener:

```js
// Replace existing handleInputKey
function handleInputKey(e){
  const popup=document.getElementById('slash-popup');
  const visible=popup.classList.contains('show');
  if(visible){
    if(e.key==='ArrowDown'){e.preventDefault();highlightSlash(Math.min(slashIdx+1,slashFiltered.length-1));return;}
    if(e.key==='ArrowUp'){e.preventDefault();highlightSlash(Math.max(slashIdx-1,0));return;}
    if(e.key==='Enter'){
      e.preventDefault();
      if(slashIdx>=0)pickSlash(slashIdx);else{hideSlash();doSend();}
      return;
    }
    if(e.key==='Escape'){e.preventDefault();hideSlash();return;}
  }
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();doSend();}
}

// Add input event listener for autocomplete filtering
document.getElementById('input').addEventListener('input',function(){
  const val=this.value;
  if(val.startsWith('/')){
    const q=val.toLowerCase();
    showSlash(SLASH_COMMANDS.filter(c=>c.cmd.startsWith(q)));
  }else{hideSlash();}
});

// Close popup on outside click
document.addEventListener('click',e=>{if(!e.target.closest('.input-area'))hideSlash();});
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/static/chat.html
git commit -m "feat(chat): slash command autocomplete"
```

---

### Task 2: Complete Tool Call Display

**Files:**
- Modify: `dashboard/main.py` — enhance `session-messages` JSONL parsing
- Modify: `dashboard/static/chat.html` — add tool call rendering

- [ ] **Step 1: Enhance backend JSONL parsing**

Find the `session_messages` endpoint in `main.py` (search for `session-messages`). The current parser extracts basic messages. Enhance it to parse tool_use and tool_result entries.

Read the existing parsing code first, then modify to emit structured tool call messages:

When parsing JSONL, for entries with `type: "assistant"` that have `message.content` array:
- Extract `tool_use` items: `{type: "tool_use", name: "Read", input: {...}}`
- Pair them with subsequent `tool_result` items from the next `type: "user"` entry

Add a new message role `"tool_call"` to the output:
```python
{
    "role": "tool_call",
    "tool": "Read",  # tool name
    "input": {"file_path": "/path/to/file"},  # tool input params
    "output_preview": "first 200 chars of output...",  # truncated result
    "output_full": "complete output",  # full result for expand
    "status": "success"  # or "error"
}
```

Keep existing text messages as-is. Tool calls appear between assistant text and the next message.

- [ ] **Step 2: Add tool call CSS to chat.html**

```css
/* Tool call blocks */
.tool-call{margin:4px 0;border:1px solid var(--border);border-radius:6px;overflow:hidden;font-size:12px}
.tool-call-header{display:flex;align-items:center;gap:6px;padding:6px 10px;background:var(--bg2);cursor:pointer;user-select:none}
.tool-call-header:hover{background:var(--bg3)}
.tool-call-icon{font-size:14px}
.tool-call-name{font-weight:600;font-size:12px}
.tool-call-param{color:var(--muted);font-family:monospace;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.tool-call-status{font-size:10px;padding:1px 6px;border-radius:3px}
.tool-call-status.success{color:var(--green);background:color-mix(in srgb,var(--green) 10%,transparent)}
.tool-call-status.error{color:var(--red);background:color-mix(in srgb,var(--red) 10%,transparent)}
.tool-call-expand{color:var(--muted);font-size:10px;transition:transform .2s}
.tool-call-expand.open{transform:rotate(90deg)}
.tool-call-body{display:none;padding:8px 10px;border-top:1px solid var(--border);background:var(--bg);font-family:monospace;white-space:pre-wrap;max-height:300px;overflow-y:auto;font-size:11px;color:var(--muted)}
.tool-call-body.show{display:block}
.tool-call.tc-read{border-left:3px solid var(--color-read)}
.tool-call.tc-edit,.tool-call.tc-update{border-left:3px solid var(--color-edit)}
.tool-call.tc-bash{border-left:3px solid var(--color-bash)}
.tool-call.tc-grep,.tool-call.tc-search,.tool-call.tc-glob{border-left:3px solid var(--color-grep)}
.tool-call.tc-write{border-left:3px solid var(--color-write)}
```

- [ ] **Step 3: Add tool call rendering in chat.html**

Add tool call rendering function and modify the `render()` function:

```js
const TOOL_ICONS={Read:'📄',Edit:'✏️',Write:'📝',Bash:'>_',Grep:'🔍',Glob:'📁',
  Agent:'🤖',WebSearch:'🌐',WebFetch:'🌐',TodoWrite:'📋',
  mcp__plugin_telegram_telegram__reply:'💬'};
const TOOL_CLASSES={Read:'tc-read',Edit:'tc-edit',Write:'tc-write',Bash:'tc-bash',
  Grep:'tc-grep',Glob:'tc-glob',Search:'tc-grep',Update:'tc-edit'};

let toolCallId=0;
function renderToolCall(m){
  const id='tc-'+(toolCallId++);
  const icon=TOOL_ICONS[m.tool]||'🔧';
  const cls=TOOL_CLASSES[m.tool]||'';
  const statusCls=m.status==='error'?'error':'success';
  const statusText=m.status==='error'?'error':'ok';
  // Show the most relevant input param
  let param='';
  if(m.input){
    if(m.input.file_path)param=m.input.file_path;
    else if(m.input.command)param=m.input.command;
    else if(m.input.pattern)param=m.input.pattern;
    else if(m.input.query)param=m.input.query;
    else if(m.input.prompt)param=m.input.prompt?.slice(0,60)+'...';
    else{const keys=Object.keys(m.input);if(keys.length)param=keys[0]+': '+JSON.stringify(m.input[keys[0]]).slice(0,60);}
  }
  const preview=m.output_preview||'';
  const full=m.output_full||preview;
  return `<div class="tool-call ${cls}">
    <div class="tool-call-header" onclick="toggleToolCall('${id}')">
      <span class="tool-call-icon">${icon}</span>
      <span class="tool-call-name">${esc(m.tool)}</span>
      <span class="tool-call-param">${esc(param)}</span>
      <span class="tool-call-status ${statusCls}">${statusText}</span>
      <span class="tool-call-expand" id="${id}-arrow">▶</span>
    </div>
    <div class="tool-call-body" id="${id}">${esc(full)}</div>
  </div>`;
}

function toggleToolCall(id){
  const body=document.getElementById(id);
  const arrow=document.getElementById(id+'-arrow');
  if(!body)return;
  body.classList.toggle('show');
  if(arrow)arrow.classList.toggle('open');
}
```

Modify the `render()` function to handle `tool_call` role:
```js
// In the render() loop, add after the tool_output check:
else if(m.role==='tool_call'){
  html+=renderToolCall(m);
}
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/main.py dashboard/static/chat.html
git commit -m "feat(chat): complete tool call display with collapsible blocks"
```

---

### Task 3: Reliable Permission Prompts from JSONL

**Files:**
- Modify: `dashboard/main.py` — add prompt detection from JSONL
- Modify: `dashboard/static/chat.html` — use JSONL-based prompts

- [ ] **Step 1: Add prompt detection to session-messages backend**

In the `session-messages` endpoint, after parsing all messages, detect if there's a pending prompt:

```python
# After building the messages list, detect pending prompt
pending_prompt = None

# Check if the last assistant message contains a tool_use that hasn't been answered
# (no subsequent user message with tool_result)
# Or check if Claude is waiting for permission (assistant asked, no user reply yet)
if parsed_entries:
    last_entry = parsed_entries[-1]
    # If last entry is assistant with tool_use and no tool_result follows
    if last_entry.get("type") == "assistant":
        content = last_entry.get("message", {}).get("content", [])
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                # There's an unanswered tool call — Claude may be waiting for permission
                pending_prompt = {
                    "type": "permission",
                    "tool": item.get("name", ""),
                    "question": f"Allow {item.get('name', 'tool')} call?"
                }
```

Add `pending_prompt` to the response JSON.

- [ ] **Step 2: Update frontend to prefer JSONL prompts**

In `pollState`, if `pending_prompt` is available from the last `pollMessages` call, use it instead of tmux scraping:

```js
let lastPendingPrompt=null;

// In pollMessages success handler, capture pending_prompt:
if(d.pending_prompt){
  lastPendingPrompt=d.pending_prompt;
}else{
  lastPendingPrompt=null;
}

// In pollState, check lastPendingPrompt first:
// If we have a JSONL-based prompt, render it directly without tmux scraping
```

Keep tmux `prompt-state` as fallback for choice navigation (checkbox up/down).

- [ ] **Step 3: Commit**

```bash
git add dashboard/main.py dashboard/static/chat.html
git commit -m "feat(chat): JSONL-based permission prompt detection"
```

---

### Task 4: SSE Streaming

**Files:**
- Modify: `dashboard/main.py` — add SSE endpoint
- Modify: `dashboard/static/chat.html` — replace polling with EventSource

- [ ] **Step 1: Add SSE endpoint to backend**

Add after the `session-messages` endpoint:

```python
import asyncio

@app.get("/api/projects/{name}/session-stream")
async def session_stream(name: str, after: int = 0):
    """Server-Sent Events stream for real-time session updates."""
    async def event_generator():
        last_count = after
        last_mtime = 0
        while True:
            try:
                # Find latest session file
                session_file = _get_latest_session_file(name)
                if not session_file:
                    await asyncio.sleep(1)
                    continue

                st = os.stat(session_file)
                if st.st_mtime_ns != last_mtime:
                    last_mtime = st.st_mtime_ns
                    # Parse and get new messages
                    all_messages = _parse_session_jsonl(session_file)
                    if len(all_messages) > last_count:
                        new_msgs = all_messages[last_count:]
                        last_count = len(all_messages)
                        for msg in new_msgs:
                            yield f"event: message\ndata: {json.dumps(msg)}\n\n"

                # Check tmux state for thinking indicator
                state = _get_prompt_state(name)
                yield f"event: state\ndata: {json.dumps({'state': state})}\n\n"

            except Exception:
                pass

            await asyncio.sleep(0.3)  # 300ms check interval

        # Heartbeat
        yield f": heartbeat\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )
```

Extract `_get_latest_session_file(name)` and `_parse_session_jsonl(file)` as helper functions from the existing `session-messages` endpoint (refactor to share code).

- [ ] **Step 2: Update frontend to use EventSource**

Replace the polling setup in chat.html:

```js
let eventSource=null;
let sseConnected=false;

function startSSE(){
  if(eventSource)eventSource.close();
  eventSource=new EventSource(`/api/projects/${PROJECT}/session-stream?after=${lastMsgCount}`);
  
  eventSource.addEventListener('message',function(e){
    try{
      const msg=JSON.parse(e.data);
      messages.push(msg);
      if(messages.length>MAX_DISPLAY*2)messages=messages.slice(-MAX_DISPLAY);
      lastMsgCount++;
      render();
    }catch(err){}
  });

  eventSource.addEventListener('state',function(e){
    try{
      const d=JSON.parse(e.data);
      const prev=currentState;
      currentState=d.state;
      if(currentState!==prev)render();
      // Update state display
      const stateEl=document.getElementById('state-display');
      stateEl.style.color='';
      stateEl.textContent=
        d.state==='thinking'?'thinking...':
        d.state==='waiting_choice'?'waiting for choice':
        d.state==='waiting_text'?'ready':'';
    }catch(err){}
  });

  eventSource.addEventListener('prompt',function(e){
    try{
      const d=JSON.parse(e.data);
      renderPrompt(d);
    }catch(err){}
  });

  eventSource.onerror=function(){
    sseConnected=false;
    // Fall back to polling
    setTimeout(startSSE,5000); // Retry in 5s
    if(!pollInterval)startPollingFallback();
  };

  eventSource.onopen=function(){
    sseConnected=true;
    // Stop polling fallback
    if(pollInterval){clearInterval(pollInterval);pollInterval=null;}
  };
}

function startPollingFallback(){
  pollInterval=setInterval(()=>{pollMessages();},2000);
  setInterval(pollState,1500);
}

// Initial load: get all messages first, then start SSE
async function init(){
  await pollMessages(); // Get initial messages
  startSSE();
  // Keep polling state as backup (for tmux-based prompt detection)
  setInterval(pollState,2000);
}
init();
```

Remove the old `startPolling()` call at the bottom of the script.

- [ ] **Step 3: Add nginx SSE support**

The existing nginx config may buffer SSE responses. The `X-Accel-Buffering: no` header should handle this, but verify the API location in nginx passes it through.

- [ ] **Step 4: Commit**

```bash
git add dashboard/main.py dashboard/static/chat.html
git commit -m "feat(chat): SSE streaming replaces polling for real-time updates"
```

---

### Task 5: Skills/Plugins Detection

**Files:**
- Modify: `dashboard/main.py` — add available-skills endpoint
- Modify: `dashboard/static/chat.html` — merge skills into autocomplete

- [ ] **Step 1: Add backend endpoint to detect skills**

```python
@app.get("/api/projects/{name}/available-skills")
async def available_skills(name: str):
    """Scan installed plugins for available skills."""
    skills = []
    plugins_dir = Path.home() / ".claude" / "plugins"
    
    if not plugins_dir.exists():
        return {"skills": skills}
    
    # Scan marketplace plugins
    for marketplace in plugins_dir.glob("marketplaces/*/"):
        # Check cache for skill definitions
        cache_dir = Path.home() / ".claude" / "plugins" / "cache" / marketplace.name
        for skill_dir in cache_dir.glob("**/skills/*/"):
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                try:
                    content = skill_file.read_text()
                    # Parse frontmatter for name and description
                    if content.startswith("---"):
                        end = content.index("---", 3)
                        fm = content[3:end]
                        name_match = re.search(r'name:\s*(.+)', fm)
                        desc_match = re.search(r'description:\s*(.+)', fm)
                        if name_match:
                            skill_name = name_match.group(1).strip()
                            skill_desc = desc_match.group(1).strip() if desc_match else ''
                            # Check if it's user-invocable (has a trigger pattern)
                            trigger = re.search(r'trigger.*?/(\S+)', fm, re.IGNORECASE)
                            cmd = '/' + skill_name.split(':')[-1] if ':' in skill_name else '/' + skill_name
                            skills.append({
                                "cmd": cmd,
                                "desc": skill_desc[:80],
                                "source": marketplace.name
                            })
                except Exception:
                    pass
    
    return {"skills": skills}
```

- [ ] **Step 2: Fetch skills on chat init and merge into autocomplete**

In chat.html, after the SLASH_COMMANDS definition:

```js
// Fetch installed skills and merge into commands list
async function loadSkills(){
  try{
    const r=await fetch(`/api/projects/${PROJECT}/available-skills`,{credentials:'include'});
    if(!r.ok)return;
    const d=await r.json();
    for(const s of d.skills){
      // Avoid duplicates
      if(!SLASH_COMMANDS.find(c=>c.cmd===s.cmd)){
        SLASH_COMMANDS.push({cmd:s.cmd,desc:s.desc+' ('+s.source+')'});
      }
    }
    // Sort alphabetically
    SLASH_COMMANDS.sort((a,b)=>a.cmd.localeCompare(b.cmd));
  }catch(e){}
}
loadSkills();
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/main.py dashboard/static/chat.html
git commit -m "feat(chat): detect and show available skills in autocomplete"
```

---

### Task 6: Deploy and Sync

- [ ] **Step 1: Sync all files to live**

```bash
cp /home/mountain/projects/OopsBox/dashboard/main.py /opt/dashboard/main.py
cp /home/mountain/projects/OopsBox/dashboard/static/chat.html /opt/dashboard/static/chat.html
```

- [ ] **Step 2: Restart dashboard**

```bash
kill $(pgrep -f "uvicorn.*5000") 2>/dev/null
# Dashboard auto-restarts via systemd or supervisor
```

- [ ] **Step 3: Push**

```bash
git push
```
