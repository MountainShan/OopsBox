"""Scripted stand-ins for agent CLIs, used by test_chat_drivers.py.

    python fake_agents.py acp     — an ACP agent like `kiro-cli acp`
    python fake_agents.py codex   — a `codex app-server`

Each answers one prompt with a short text, one tool call that needs
permission, and the outcome of that permission.
"""
import json
import sys

_next_id = 1000


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)


_side = None


def ask(method, params, jsonrpc):
    """Send a request to the client and return its result. Requests the
    client sends meanwhile go to _side, if the agent has one."""
    global _next_id
    _next_id += 1
    rid = _next_id
    send({**({"jsonrpc": "2.0"} if jsonrpc else {}), "id": rid, "method": method, "params": params})
    while True:
        msg = read()
        if msg.get("id") == rid and "method" not in msg:
            return msg.get("result")
        if _side:
            _side(msg)


def acp():
    steered = []

    def reply(rid, result):
        send({"jsonrpc": "2.0", "id": rid, "result": result})

    def ext(msg):
        """Kiro's extension methods; also answered while a turn waits."""
        m = msg.get("method")
        if m == "_kiro.dev/commands/options":
            reply(msg["id"], {"options": [
                {"value": "m1", "label": "m1", "description": "first [active]", "group": "1x"},
                {"value": "m2", "label": "m2", "description": "second", "group": "2x"}], "hasMore": False})
        elif m == "_kiro.dev/commands/execute":
            c = msg["params"]["command"]
            if c["command"] == "gone":
                send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
            elif c["command"] == "mcp":
                reply(msg["id"], {"success": True, "message": "1 MCP server configured",
                                  "data": {"servers": [{"name": "ssh", "status": "running"}]}})
            else:
                reply(msg["id"], {"success": True, "message": f"ran {c['command']} {c['args'].get('value', '')}".strip()})
        elif m == "_session/steer":
            steered.append(msg["params"]["message"])
            reply(msg["id"], {})
        else:
            return False
        return True

    global _side
    _side = ext

    def update(sid, u):
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})

    while True:
        msg = read()
        m = msg.get("method")
        if m == "initialize":
            reply(msg["id"], {"protocolVersion": 1, "agentCapabilities": {"loadSession": False}})
        elif m == "session/new":
            reply(msg["id"], {"sessionId": "s1",
                              "modes": {"currentModeId": "a", "availableModes": [{"id": "a", "name": "A"},
                                                                                  {"id": "b", "name": "B"}]}})
            send({"jsonrpc": "2.0", "method": "_kiro.dev/commands/available", "params": {"sessionId": "s1", "commands": [
                {"name": "/model", "description": "pick a model",
                 "meta": {"inputType": "selection", "optionsMethod": "_kiro.dev/commands/model/options"}},
                {"name": "/mcp", "description": "servers", "meta": {"inputType": "panel", "subcommands": ["list"]}},
                {"name": "/clear", "description": "clear"},
                {"name": "/gone", "description": "removed in this version"},
                {"name": "/chat", "description": "sessions", "meta": {"inputType": "selection", "local": True}},
                {"name": "/voice", "description": "talk"},
            ]}})
        elif ext(msg):
            pass
        elif m == "session/set_mode":
            reply(msg["id"], {})
        elif m == "session/prompt":
            sid = msg["params"]["sessionId"]
            texts = [p.get("text", "") for p in msg["params"]["prompt"]]
            update(sid, {"sessionUpdate": "agent_message_chunk",
                         "content": {"type": "text", "text": f"got {len(texts)} parts. "}})
            update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "working"}})
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Running: ls",
                         "kind": "execute", "status": "pending", "rawInput": {"command": "ls"}})
            res = ask("session/request_permission", {
                "sessionId": sid, "toolCall": {"toolCallId": "t1", "title": "Running: ls"},
                "options": [{"optionId": "yes", "name": "Yes", "kind": "allow_once"},
                            {"optionId": "no", "name": "No", "kind": "reject_once"}],
            }, jsonrpc=True)
            chosen = res["outcome"].get("optionId")
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1",
                         "status": "completed" if chosen == "yes" else "failed",
                         "content": [{"type": "content", "content": {"type": "text", "text": f"chose {chosen}"}}]})
            reply(msg["id"], {"stopReason": "end_turn"})
        elif m == "session/cancel":
            pass


def codex():
    def reply(rid, result):
        send({"id": rid, "result": result})

    def note(method, params):
        send({"method": method, "params": params})

    turn = 0
    names, settings = [], []
    while True:
        msg = read()
        m = msg.get("method")
        if m == "initialize":
            reply(msg["id"], {"userAgent": "fake"})
        elif m == "thread/start":
            reply(msg["id"], {"thread": {"id": "th1"}, "developerInstructions": msg["params"].get("developerInstructions")})
            # echo the instructions back so the test can see they arrived
            note("test/instructions", {"threadId": "th1", "text": msg["params"].get("developerInstructions")})
        elif m == "turn/start":
            turn += 1
            tid = f"turn{turn}"
            reply(msg["id"], {"turn": {"id": tid, "items": [], "status": "inProgress"}})
            base = {"threadId": "th1", "turnId": tid}
            note("turn/started", {**base, "turn": {"id": tid}})
            note("item/started", {**base, "item": {"type": "agentMessage", "id": "a1", "text": ""}})
            note("item/agentMessage/delta", {**base, "itemId": "a1", "delta": "hel"})
            note("item/agentMessage/delta", {**base, "itemId": "a1", "delta": "lo"})
            note("item/completed", {**base, "item": {"type": "agentMessage", "id": "a1", "text": "hello"}})
            note("item/started", {**base, "item": {"type": "commandExecution", "id": "c1",
                                                   "command": "/bin/bash -lc 'touch x'", "status": "inProgress"}})
            res = ask("item/commandExecution/requestApproval",
                      {**base, "itemId": "c1", "command": "/bin/bash -lc 'touch x'"}, jsonrpc=False)
            ok = res["decision"] == "accept"
            note("item/completed", {**base, "item": {
                "type": "commandExecution", "id": "c1", "command": "/bin/bash -lc 'touch x'",
                "status": "completed" if ok else "declined", "exitCode": 0 if ok else None,
                "aggregatedOutput": "done" if ok else None}})
            note("turn/completed", {"threadId": "th1", "turn": {"id": tid, "status": "completed"}})
        elif m == "model/list":
            reply(msg["id"], {"data": [
                {"id": "m1", "displayName": "Model One", "description": "", "hidden": False,
                 "supportedReasoningEfforts": [{"reasoningEffort": "low", "description": ""},
                                               {"reasoningEffort": "high", "description": ""}]},
                {"id": "secret", "displayName": "Hidden", "hidden": True}]})
        elif m == "thread/name/set":
            names.append(msg["params"]["name"])
            reply(msg["id"], {})
        elif m == "thread/settings/update":
            settings.append(msg["params"])
            reply(msg["id"], {})
        elif m == "thread/list":
            reply(msg["id"], {"data": [{"id": "th1", "name": "first", "updatedAt": 1793410376},
                                       {"id": "th0", "preview": "older", "updatedAt": "2026-10-01T00:00:00Z"}]})
        elif m == "thread/resume":
            tid = msg["params"]["threadId"]
            if tid == "empty":  # never had a turn, so codex never wrote it
                send({"id": msg["id"], "error": {"code": -32600, "message": f"no rollout found for thread id {tid}"}})
            else:
                send({"id": msg["id"], "error": {"code": -32600, "message": f"thread {tid} is broken"}})
        elif m == "test/names":
            reply(msg["id"], {"names": names, "settings": settings})
        elif "id" in msg and m:
            send({"id": msg["id"], "error": {"code": -32601, "message": m}})


if __name__ == "__main__":
    {"acp": acp, "codex": codex}[sys.argv[1]]()
