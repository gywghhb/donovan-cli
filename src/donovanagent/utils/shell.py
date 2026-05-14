from __future__ import annotations

import functools
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShellInfo:
    kind: str
    executable: str
    supports_posix: bool

    def args_for(self, command: str) -> list[str]:
        if self.kind in {"bash", "zsh", "sh"}:
            return [self.executable, "-lc", command]
        if self.kind == "wsl":
            return [self.executable, "--", "bash", "-lc", command]
        if self.kind == "powershell":
            return [
                self.executable,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ]
        if self.kind == "cmd":
            return [self.executable, "/d", "/s", "/c", command]
        return [self.executable, "-lc", command]


@functools.lru_cache(maxsize=1)
def resolve_shell() -> ShellInfo:
    """Resolve the best available shell.

    The result is cached so that repeated calls don't re-run shell
    discovery (PATH lookups, health probes, WSL distro checks) on
    every tool invocation.
    """
    if os.name == "nt":
        return resolve_windows_shell()

    preferred = os.environ.get("SHELL")
    if preferred:
        path = Path(preferred)
        if path.exists():
            candidate = ShellInfo(kind=path.name, executable=preferred, supports_posix=True)
            if _shell_healthy(candidate):
                return candidate

    for name in ("bash", "zsh", "sh"):
        executable = shutil.which(name)
        if executable:
            candidate = ShellInfo(kind=name, executable=executable, supports_posix=True)
            if _shell_healthy(candidate):
                return candidate

    return ShellInfo(kind="sh", executable="/bin/sh", supports_posix=True)


def resolve_windows_shell() -> ShellInfo:
    # 1. Git Bash — most reliable, fixed install path
    for base_var in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(base_var)
        if not base:
            continue
        for sub in ("Git\\bin\\bash.exe", "Git\\usr\\bin\\bash.exe"):
            p = Path(base) / sub
            if p.exists():
                return ShellInfo(kind="bash", executable=str(p), supports_posix=True)

    # 2. bash.exe / bash on PATH (may be WSL launcher or standalone)
    for name in ("bash.exe", "bash"):
        executable = shutil.which(name)
        if executable:
            candidate = ShellInfo(kind="bash", executable=executable, supports_posix=True)
            # If it's WSL's launcher, verify the default distro is healthy
            # before committing. A broken distro (e.g. docker-desktop stopped)
            # makes every shell invocation hang.
            if not _is_wsl_launcher(executable) or _shell_healthy(candidate):
                return candidate

    # 3. wsl.exe directly
    wsl = shutil.which("wsl.exe")
    if wsl:
        candidate = ShellInfo(kind="wsl", executable=wsl, supports_posix=True)
        if _shell_healthy(candidate):
            return candidate

    # 4. PowerShell / pwsh
    for name in ("pwsh.exe", "powershell.exe"):
        executable = shutil.which(name)
        if executable:
            return ShellInfo(kind="powershell", executable=executable, supports_posix=False)

    # 5. cmd.exe
    cmd = shutil.which("cmd.exe") or r"C:\Windows\System32\cmd.exe"
    return ShellInfo(kind="cmd", executable=cmd, supports_posix=False)


def _is_wsl_launcher(path: str) -> bool:
    """Check if *path* is ``C:\\Windows\\System32\\bash.exe`` (the WSL launcher)."""
    try:
        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        return Path(path).resolve().parent.samefile(system32)
    except (OSError, FileNotFoundError):
        return False


def _shell_healthy(shell: ShellInfo, timeout: int = 5) -> bool:
    """Verify *shell* starts and runs a trivial command within *timeout* seconds.

    Catches:
    *   A broken WSL default distro (e.g. docker-desktop in Stopped state).
    *   A stale ``$SHELL`` environment variable pointing to a removed binary.
    *   Any shell that hangs on startup.
    """
    test_cmd = "true" if shell.supports_posix else "echo ok"
    try:
        proc = subprocess.run(
            shell.args_for(test_cmd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
