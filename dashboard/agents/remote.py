# dashboard/agents/remote.py
"""An SSH project's remote folder, made local for the agents.

The agents of an SSH project run on the OopsBox host, where they are
installed and signed in. They work in the remote folder through an sshfs
mount, and run commands on the remote machine with `remote <command>`.

Both go through one SSH master connection per project (a ControlMaster
socket), so a password is typed once, by sshpass, when the master starts.
The master and the mount stay up while OopsBox runs; ensure() brings
either back if it dropped.
"""
import asyncio
import logging
import os
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from .launch import ssh_target
from .session import REMOTE_ROOT, ProjectInfo

log = logging.getLogger(__name__)

ROOT = REMOTE_ROOT


class RemoteError(Exception):
    pass


def mount_dir(name: str) -> Path:
    return ROOT / name / "files"


def bin_dir(name: str) -> Path:
    return ROOT / name / "bin"


def _socket(name: str) -> str:
    # unix socket paths are short (108 bytes): keep it in /tmp
    return f"/tmp/oopsbox-{name}/ssh.sock"


def _dest(project: ProjectInfo) -> tuple[str, int, Optional[str]]:
    try:
        return ssh_target(project.ssh_user, project.ssh_host, project.ssh_port, project.ssh_key_path)
    except ValueError as e:
        raise RemoteError(str(e)) from e


def _ssh_opts(project: ProjectInfo) -> list[str]:
    dest, port, key = _dest(project)
    opts = ["-p", str(port), "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]
    if key:
        opts += ["-i", key]
    return opts


async def _run(argv: list[str], env: Optional[dict] = None, timeout: float = 30) -> tuple[int, str]:
    """Run a command; its exit code and output. Output goes through a file,
    not a pipe: `ssh -f` and sshfs leave a background process holding
    whatever they were given, and a pipe would never reach its end."""
    with tempfile.TemporaryFile() as out:
        proc = await asyncio.create_subprocess_exec(
            *argv, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=out, stderr=out, start_new_session=True)
        try:
            await asyncio.wait_for(proc.wait(), timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return 124, "timed out"
        out.seek(0)
        return proc.returncode, out.read().decode(errors="replace").strip()


async def _master_alive(project: ProjectInfo) -> bool:
    dest, _, _ = _dest(project)
    if not os.path.exists(_socket(project.name)):
        return False
    code, _ = await _run(["ssh", "-S", _socket(project.name), "-O", "check", dest], timeout=10)
    return code == 0


async def _start_master(project: ProjectInfo) -> None:
    dest, _, _ = _dest(project)
    sock = _socket(project.name)
    os.makedirs(os.path.dirname(sock), exist_ok=True)
    if os.path.exists(sock):
        os.unlink(sock)  # left by a master that died
    argv = ["ssh", "-M", "-N", "-f", "-S", sock, "-o", "ControlPersist=yes",
            "-o", "ConnectTimeout=15", *_ssh_opts(project)]
    env = dict(os.environ)
    if project.ssh_password:
        argv = ["sshpass", "-e"] + argv
        env["SSHPASS"] = project.ssh_password
    else:
        argv += ["-o", "BatchMode=yes"]
    code, out = await _run(argv + ["--", dest], env=env, timeout=40)
    if code != 0:
        raise RemoteError(f"cannot connect to {dest}: {out.splitlines()[-1] if out else f'ssh exit {code}'}")


def _mounted(path: Path) -> bool:
    try:
        return os.path.ismount(path)
    except OSError:
        return False


async def _healthy(path: Path) -> bool:
    """A mount whose connection died hangs or errors on access."""
    try:
        await asyncio.wait_for(asyncio.to_thread(os.listdir, path), 8)
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def _unmount(path: Path) -> None:
    fusermount = shutil.which("fusermount3") or shutil.which("fusermount") or "fusermount"
    await _run([fusermount, "-u", "-z", str(path)], timeout=15)


async def _mount(project: ProjectInfo) -> None:
    if not shutil.which("sshfs"):
        raise RemoteError("sshfs is not installed on the OopsBox host (apt install sshfs)")
    dest, _, _ = _dest(project)
    mnt = mount_dir(project.name)
    mnt.mkdir(parents=True, exist_ok=True)
    remote = project.remote_path or "."
    ssh_cmd = shlex.join(["ssh", "-S", _socket(project.name), "-o", "ControlMaster=no", *_ssh_opts(project)])
    code, out = await _run(["sshfs", f"{dest}:{remote}", str(mnt),
                            "-o", f"ssh_command={ssh_cmd}",
                            "-o", "reconnect,idmap=user,follow_symlinks"], timeout=40)
    if code != 0 or not _mounted(mnt):
        raise RemoteError(f"cannot mount {dest}:{remote}: {out or f'sshfs exit {code}'}")


def _write_remote_command(project: ProjectInfo) -> None:
    """`remote <command>`: run a shell command on the remote machine, in the
    project's remote folder, over the master connection."""
    dest, _, _ = _dest(project)
    remote = project.remote_path or "~"
    cd = 'cd "$HOME"' if remote == "~" else (
        'cd "$HOME"/' + shlex.quote(remote[2:]) if remote.startswith("~/") else "cd " + shlex.quote(remote))
    ssh = shlex.join(["ssh", "-S", _socket(project.name), "-o", "ControlMaster=no", "-o", "BatchMode=yes",
                      *_ssh_opts(project)])
    script = f"""#!/bin/sh
# OopsBox: run a command on {dest} in {remote} (project {project.name})
if [ $# -eq 0 ]; then echo "usage: remote <command...>" >&2; exit 2; fi
exec {ssh} -- {shlex.quote(dest)} {shlex.quote(cd + " && ")}"$*"
"""
    d = bin_dir(project.name)
    d.mkdir(parents=True, exist_ok=True)
    f = d / "remote"
    f.write_text(script)
    f.chmod(0o755)


_locks: dict[str, asyncio.Lock] = {}


async def ensure(project: ProjectInfo) -> Path:
    """Make the remote folder available here; returns the mount point."""
    lock = _locks.setdefault(project.name, asyncio.Lock())
    async with lock:
        mnt = mount_dir(project.name)
        if not await _master_alive(project):
            if _mounted(mnt):
                await _unmount(mnt)  # it rode on the master that is gone
            await _start_master(project)
        if _mounted(mnt) and not await _healthy(mnt):
            await _unmount(mnt)
        if not _mounted(mnt):
            await _mount(project)
        _write_remote_command(project)
        return mnt


async def release(name: str, project: Optional[ProjectInfo] = None) -> None:
    """Unmount and close the master connection (project removed or moved)."""
    mnt = mount_dir(name)
    if _mounted(mnt):
        await _unmount(mnt)
    sock = _socket(name)
    if os.path.exists(sock) and project is not None:
        try:
            dest, _, _ = _dest(project)
            await _run(["ssh", "-S", sock, "-O", "exit", dest], timeout=10)
        except RemoteError:
            pass


def instructions(project: ProjectInfo) -> str:
    """What an agent of an SSH project needs to know about where it works."""
    dest, _, _ = _dest(project)
    return (
        "## Remote project (OopsBox)\n\n"
        f"This project lives on another machine, `{dest}`, in `{project.remote_path or '~'}`. "
        "Your working directory is that folder, mounted here over SSH: reading and editing "
        "files works as usual, but you run on the OopsBox host, not on that machine.\n\n"
        "- To run a command on the remote machine (build, test, run, inspect it), use "
        "`remote <command>`, e.g. `remote make test` or `remote 'systemctl status nginx'`. "
        "It runs in the project folder there.\n"
        "- Commands you run directly run here, on the OopsBox host, where the remote "
        "machine's programs and services are not.\n"
        "- Prefer `remote` for anything that walks many files (git, grep -r, builds): "
        "the mount is slower than the remote disk.\n"
    )
