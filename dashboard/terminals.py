# dashboard/terminals.py
"""Browser terminals: ttyd, a shell, and nothing in between but dtach.

Each terminal is a systemd *user* unit, oopsbox-term-<name>, so it is a
daemon of its own: restarting OopsBox (whose service kills everything in
its cgroup) leaves it running. ttyd serves /terminal/<name>/ and, for
every browser that connects, runs `dtach -A`: the first attach starts the
shell, later ones join it. Closing the page detaches; the shell and
whatever it runs carry on. dtach adds no status bar, prefix key or
scrollback of its own, so the terminal behaves like a plain shell.

nginx learns each terminal's port from /tmp/oopsbox-<name>/ttyd.port
(bin/nginx-update-projects.sh), as before.
"""
import os
import re
import shlex
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Optional

_bin_dir = Path(os.environ.get("OOPSBOX_BIN_DIR") or Path.home() / "bin")


class TerminalError(RuntimeError):
    pass


def unit(name: str) -> str:
    # project names are letters, digits, . _ - (and "_global"), all valid in unit names
    return "oopsbox-term-" + re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _state_dir(name: str) -> Path:
    return Path(f"/tmp/oopsbox-{name}")


def _user_env() -> dict:
    """What systemctl --user needs, which a system service does not have."""
    env = dict(os.environ)
    run = f"/run/user/{os.getuid()}"
    env.setdefault("XDG_RUNTIME_DIR", run)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={run}/bus")
    return env


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, env=_user_env())


def is_running(name: str) -> bool:
    return _systemctl("is-active", "--quiet", unit(name)).returncode == 0


def running() -> list[str]:
    """Names of the terminals whose daemons are up."""
    r = _systemctl("list-units", "--type=service", "--state=active", "--plain", "--no-legend", "oopsbox-term-*")
    names = []
    for line in r.stdout.splitlines():
        u = line.split()[0] if line.split() else ""
        if u.startswith("oopsbox-term-") and u.endswith(".service"):
            names.append(u[len("oopsbox-term-"):-len(".service")])
    return names


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _update_nginx() -> None:
    script = _bin_dir / "nginx-update-projects.sh"
    if script.exists():
        subprocess.run([str(script)], capture_output=True)


def start(name: str, workdir: Path, first_command: Optional[str] = None,
          env: Optional[dict] = None) -> int:
    """Start the terminal daemon if it is not running. Returns its port.

    first_command runs in the shell before it turns interactive (an SSH
    project's terminal opens on the remote host); env is set for the
    terminal and kept out of the process list.
    """
    if not shutil.which("dtach"):
        raise TerminalError("dtach is needed: sudo apt install dtach")
    state = _state_dir(name)
    port_file = state / "ttyd.port"
    if is_running(name) and port_file.exists():
        return int(port_file.read_text())
    _systemctl("reset-failed", unit(name))
    state.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    sock = state / "shell.dtach"
    # -A attach or create, -E no detach key, -z no suspend key,
    # -r winch: redraw by resizing when a browser (re)attaches
    shell = "bash -l" if not first_command else f"bash -lc {shlex.quote(first_command + '; exec bash -l')}"
    argv = ["systemd-run", "--user", "--unit", unit(name), "--collect", "--quiet",
            "-p", f"WorkingDirectory={workdir}",
            "-p", "Restart=on-failure",
            "--setenv", f"TERM=xterm-256color",
            "--setenv", f"PATH={Path.home()}/.local/bin:{Path.home()}/.npm-global/bin:/usr/local/bin:/usr/bin:/bin"]
    for k, v in (env or {}).items():
        argv += ["--setenv", f"{k}={v}"]
    argv += ["ttyd", "--port", str(port), "--interface", "lo",  # loopback only: nginx is the way in
             "--base-path", f"/terminal/{name}", "--writable", "-t", "copyOnSelect=true",
             "--", "dtach", "-A", str(sock), "-E", "-z", "-r", "winch", *shlex.split(shell)]
    r = subprocess.run(argv, capture_output=True, text=True, env=_user_env())
    if r.returncode != 0:
        raise TerminalError(r.stderr.strip() or "Could not start the terminal")
    port_file.write_text(str(port))
    _update_nginx()
    return port


def stop(name: str) -> None:
    """Stop the daemon; the shell and everything it runs ends with it."""
    _systemctl("stop", unit(name))
    for f in ("ttyd.port", "shell.dtach"):
        (_state_dir(name) / f).unlink(missing_ok=True)
    _update_nginx()
