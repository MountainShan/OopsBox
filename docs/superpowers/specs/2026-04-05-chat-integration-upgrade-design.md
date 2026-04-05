# Chat Page Integration Upgrade — 5 Point Improvement

## Summary

Upgrade OopsBox chat page (`chat.html`) to close the gap with Claude Code CLI. Five areas: slash commands, skills/plugins, reliable permission prompts, faster streaming, and complete tool call display.

## Current Architecture

```
User types text → send-text API → tmux send-keys → Claude Code CLI (agents:$NAME)
Claude Code → writes JSONL → session-messages API (polls every 2s) → chat renders
Claude TUI → prompt-state API (captures last 8 tmux lines, polls 1.5s) → chat shows state
```

All interaction goes through tmux. Chat reads JSONL for messages and scrapes tmux pane for state.

---

## Point 1: Slash Commands

**Problem:** User cannot type `/init`, `/compact`, `/model` etc. in the chat input — they're sent as regular text to Claude, not as CLI commands.

**Solution:** Intercept `/` prefix in the input box before sending.

**Design:**
- When user types a message starting with `/`, check against known slash commands list
- Show autocomplete dropdown above input (same pattern as terminal-input.js design)
- On send: if text starts with a known slash command, send via `send-text` API (which sends literal text to tmux + Enter) — this already works because Claude Code CLI reads from stdin
- Slash commands ARE regular text input to Claude Code CLI — the CLI handles them. So actually **no backend change needed** — just need the autocomplete UI
- Commands list (same as terminal-input.js):
  ```
  /compact, /clear, /help, /cost, /doctor, /init, /login, /logout,
  /status, /memory, /permissions, /model, /config, /vim, /review,
  /pr-comments, /terminal-setup
  ```
- Arrow keys navigate, Enter selects, Escape dismisses
- Selected command fills input, user can add args then send normally

**Files to modify:**
- `dashboard/static/chat.html` — add autocomplete popup HTML + CSS + JS

---

## Point 2: Skills/Plugins Support

**Problem:** Chat page can't trigger skills like `/superpowers`, `/commit`, or custom plugin skills. These require the Skill tool within Claude Code which is only accessible via the CLI.

**Solution:** Skills are invoked by typing their name (e.g., `/commit`) as text input to Claude Code. Since Point 1 adds slash command support, we just need to extend the commands list with detected skills.

**Design:**
- New backend endpoint: `GET /api/projects/{name}/available-skills`
  - Reads `.claude/settings.local.json` and plugin manifests to detect installed skills
  - Scans `~/.claude/plugins/` for registered plugins and their skills
  - Returns: `{skills: [{cmd: "/commit", desc: "Create a git commit", source: "superpowers"}, ...]}`
  - Cache results (skills don't change often) — refresh on explicit request
- Frontend merges built-in slash commands + detected skills into one autocomplete list
- Skills are visually distinguished (different icon or label showing source plugin)
- User types `/commit -m "fix bug"` → sent as regular text → Claude Code CLI handles it

**Files to modify:**
- `dashboard/main.py` — add `/api/projects/{name}/available-skills` endpoint
- `dashboard/static/chat.html` — merge skills into autocomplete, fetch on init

---

## Point 3: Reliable Permission Prompts

**Problem:** Current permission detection scrapes last 8 lines of tmux pane output, looking for patterns like numbered choices and `❯` cursor. This is fragile — it breaks when output format changes, races with rendering, and misses prompts that scroll off screen.

**Solution:** Read permission prompts from the JSONL session file instead of tmux scraping.

**Design:**
- Claude Code writes structured data to JSONL including permission requests
- The `session-messages` API already parses JSONL — extend it to extract permission/prompt events
- New fields in message response:
  ```json
  {
    "messages": [...],
    "pending_prompt": {
      "type": "permission" | "choice" | "text",
      "options": [{"num": "1", "text": "Yes"}, {"num": "2", "text": "No"}],
      "question": "Allow Read tool on /path/to/file?"
    }
  }
  ```
- Backend: parse JSONL for the latest unanswered prompt
  - Look for entries with `type: "user"` that have `permissionMode` or choice patterns
  - Look for assistant messages that end with a question and no subsequent user message
  - Fallback: keep tmux scraping as secondary detection if JSONL doesn't have prompt info
- Frontend: render prompts from the structured data instead of tmux pane scraping
- Remove dependency on tmux pane capture for prompt detection (keep it only for state like "thinking")
- Prompt rendering stays the same (inline buttons at bottom of messages)

**Files to modify:**
- `dashboard/main.py` — enhance `session-messages` to parse prompts from JSONL
- `dashboard/static/chat.html` — use `pending_prompt` from messages API instead of `prompt-state` for choices

---

## Point 4: Faster Streaming

**Problem:** Messages update every 2 seconds via polling. User sends text and waits up to 2s to see the response start appearing. Fast polling (500ms for 15s) helps but still feels laggy vs real-time.

**Solution:** Replace polling with Server-Sent Events (SSE) for message streaming.

**Design:**
- New backend endpoint: `GET /api/projects/{name}/session-stream` (SSE)
  - Uses `watchdog` or `inotify` to watch the JSONL session file for changes
  - When file is modified, parse new lines and send as SSE events
  - Event types: `message` (new user/assistant/tool message), `state` (thinking/ready/choice)
  - Falls back to file polling (500ms) if inotify not available
  - Connection kept alive with heartbeat every 15s
  - Client reconnects automatically (EventSource built-in)

- SSE event format:
  ```
  event: message
  data: {"role":"assistant","text":"Here's the code...","tool":"Edit"}

  event: state
  data: {"state":"thinking"}

  event: prompt
  data: {"type":"choice","options":[...]}
  ```

- Frontend changes:
  - Replace `setInterval(pollMessages, 2000)` with `EventSource`
  - Keep `pollState` as fallback for tmux state detection (thinking indicator)
  - On SSE `message` event: append to messages array, render
  - On SSE `state` event: update thinking indicator
  - On SSE `prompt` event: render inline prompt
  - On connection lost: fall back to polling until reconnected

- Backend implementation:
  ```python
  @app.get("/api/projects/{name}/session-stream")
  async def session_stream(name: str):
      async def event_generator():
          # Watch JSONL file for changes
          last_size = 0
          session_file = get_latest_session_file(name)
          while True:
              current_size = os.path.getsize(session_file)
              if current_size > last_size:
                  # Read new lines, parse, yield SSE events
                  new_data = read_new_lines(session_file, last_size)
                  last_size = current_size
                  for msg in parse_messages(new_data):
                      yield f"event: message\ndata: {json.dumps(msg)}\n\n"
              await asyncio.sleep(0.3)  # 300ms check interval
      return StreamingResponse(event_generator(), media_type="text/event-stream")
  ```

**Files to modify:**
- `dashboard/main.py` — add SSE endpoint
- `dashboard/static/chat.html` — replace polling with EventSource, fallback to polling

---

## Point 5: Complete Tool Call Display

**Problem:** Current JSONL parsing extracts basic user/assistant/tool messages but may miss structured tool call details — input parameters, execution status, errors, and tool-specific metadata that Claude Code records.

**Solution:** Parse the full JSONL structure and render rich tool call blocks.

**Design:**
- JSONL entries from Claude Code have this structure:
  ```json
  {"type": "assistant", "message": {
    "content": [
      {"type": "text", "text": "Let me read that file."},
      {"type": "tool_use", "name": "Read", "input": {"file_path": "/path/to/file"}}
    ]
  }}
  ```
  Followed by:
  ```json
  {"type": "user", "message": {
    "content": [
      {"type": "tool_result", "tool_use_id": "...", "content": "file contents..."}
    ]
  }}
  ```

- Backend: enhance `session-messages` parsing to extract structured tool calls:
  ```json
  {
    "role": "tool_call",
    "tool": "Read",
    "input": {"file_path": "/path/to/file"},
    "status": "success",
    "output_preview": "first 5 lines..."
  }
  ```

- Frontend rendering for tool calls:
  - Collapsible tool call blocks (collapsed by default, click to expand)
  - Color-coded by tool type (existing step-block colors: Read=blue, Edit=orange, Bash=green, etc.)
  - Header shows: tool icon + tool name + key parameter (e.g., file path)
  - Expandable body shows: full input parameters + truncated output with "Show full" toggle
  - Error tool calls highlighted in red
  - Agent/subagent tool calls shown as nested blocks

- Example rendering:
  ```
  📄 Read  /src/main.py                           [▶ expand]
  ✏️ Edit  /src/main.py:45-52                      [▶ expand]
  >_ Bash  git status                              [▶ expand]
     └─ Exit code: 0, 3 lines output
  🔍 Grep  pattern="TODO" path="/src"              [▶ expand]
  ```

**Files to modify:**
- `dashboard/main.py` — enhance JSONL parsing in `session-messages`
- `dashboard/static/chat.html` — add tool call rendering with collapse/expand

---

## Implementation Priority

1. **Point 1 (Slash commands)** — quickest win, frontend-only change
2. **Point 5 (Tool call display)** — improves core chat experience significantly
3. **Point 3 (Permission prompts)** — fixes reliability issue
4. **Point 4 (SSE streaming)** — best UX improvement but most complex
5. **Point 2 (Skills detection)** — depends on Point 1, adds polish

## Not in Scope
- Replacing chat with terminal view
- Headless Claude Code mode (no TUI)
- Multi-session management within chat
- Voice input
