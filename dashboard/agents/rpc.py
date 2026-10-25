# dashboard/agents/rpc.py
"""Newline-delimited JSON-RPC over a child process's stdio.

Both protocols OopsBox speaks are this shape: `codex app-server` (JSON-RPC
without the "jsonrpc" field) and ACP, used by `kiro-cli acp` (JSON-RPC 2.0).
Messages flow both ways — the agent sends notifications and also requests
of its own (approvals, file reads), so each side is a client and a server.
"""
import asyncio
import json
import logging
import os
import signal
from collections import deque
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("oopsbox.rpc")

# A single message can carry a whole file or a long command output.
_LINE_LIMIT = 64 * 1024 * 1024

NotificationHandler = Callable[[str, dict], Awaitable[None]]
RequestHandler = Callable[[str, dict], Awaitable[Any]]
ExitHandler = Callable[[Optional[int], str], Awaitable[None]]


class RpcError(Exception):
    def __init__(self, error: dict):
        self.error = error
        super().__init__(error.get("message") or json.dumps(error))

    @property
    def code(self) -> Optional[int]:
        return self.error.get("code")


class JsonRpcProcess:
    def __init__(
        self,
        argv: list[str],
        cwd: Optional[str],
        on_notification: NotificationHandler,
        on_request: RequestHandler,
        on_exit: ExitHandler,
        env: Optional[dict] = None,
        jsonrpc_field: bool = True,
    ):
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self._on_notification = on_notification
        self._on_request = on_request
        self._on_exit = on_exit
        self._jsonrpc_field = jsonrpc_field
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._next_id = 0
        self._pending: dict[Any, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._write_lock = asyncio.Lock()
        # The last lines of stderr explain most startup failures (not logged
        # in, binary missing on a remote host), so they are kept for display.
        self.stderr_tail: deque[str] = deque(maxlen=40)
        self._closing = False

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=self.cwd,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_LINE_LIMIT,
            # Own process group, so stop() can take down whatever the agent
            # spawned (shells, MCP servers) and not only the agent itself.
            start_new_session=True,
        )
        self.spawn(self._read_stdout())
        self.spawn(self._read_stderr())

    def spawn(self, coro) -> None:
        """Run a task tied to this process's lifetime."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _frame(self, msg: dict) -> dict:
        if self._jsonrpc_field:
            return {"jsonrpc": "2.0", **msg}
        return msg

    async def _write(self, msg: dict) -> None:
        if not self.running:
            raise ConnectionError("agent process is not running")
        data = (json.dumps(self._frame(msg), ensure_ascii=False) + "\n").encode()
        async with self._write_lock:
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()

    async def request(self, method: str, params: dict, timeout: Optional[float] = None) -> Any:
        self._next_id += 1
        rid = self._next_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._write({"id": rid, "method": method, "params": params})
            return await asyncio.wait_for(fut, timeout) if timeout else await fut
        finally:
            self._pending.pop(rid, None)

    async def notify(self, method: str, params: dict) -> None:
        await self._write({"method": method, "params": params})

    async def _answer(self, rid: Any, method: str, params: dict) -> None:
        try:
            result = await self._on_request(method, params)
            reply = {"id": rid, "result": result if result is not None else {}}
        except RpcError as e:
            reply = {"id": rid, "error": e.error}
        except Exception as e:  # never leave the agent waiting on an answer
            log.exception("handler for %s failed", method)
            reply = {"id": rid, "error": {"code": -32603, "message": str(e)}}
        try:
            await self._write(reply)
        except ConnectionError:
            pass

    async def _read_stdout(self) -> None:
        assert self._proc is not None
        while True:
            try:
                line = await self._proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                log.warning("dropped an oversized line from %s", self.argv[0])
                continue
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self.stderr_tail.append(line.decode(errors="replace").rstrip())
                continue
            if not isinstance(msg, dict):
                continue
            method = msg.get("method")
            if method is not None and "id" in msg:
                self.spawn(self._answer(msg["id"], method, msg.get("params") or {}))
            elif method is not None:
                self.spawn(self._safe_notify(method, msg.get("params") or {}))
            elif "id" in msg:
                fut = self._pending.get(msg["id"])
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_exception(RpcError(msg["error"] or {}))
                    else:
                        fut.set_result(msg.get("result"))
        code = await self._proc.wait()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("agent process exited"))
        if not self._closing:
            await self._on_exit(code, "\n".join(self.stderr_tail))

    async def _safe_notify(self, method: str, params: dict) -> None:
        try:
            await self._on_notification(method, params)
        except Exception:
            log.exception("notification handler for %s failed", method)

    async def _read_stderr(self) -> None:
        assert self._proc is not None
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                return
            self.stderr_tail.append(line.decode(errors="replace").rstrip())

    async def close(self, grace: float = 3.0) -> None:
        self._closing = True
        if self._proc is not None:
            await kill_process_group(self._proc, grace)


async def kill_process_group(proc: asyncio.subprocess.Process, grace: float = 3.0) -> None:
    """Stop a process started with start_new_session=True, and its children."""
    if proc.returncode is not None:
        return
    try:
        proc.stdin.close()
    except Exception:
        pass
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), wait)
            return
        except asyncio.TimeoutError:
            continue
