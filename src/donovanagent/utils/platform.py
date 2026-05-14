from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformInfo:
    system: str
    release: str
    machine: str
    python: str
    executable: str
    is_windows: bool
    is_macos: bool
    is_linux: bool
    encoding: str


def get_platform_info() -> PlatformInfo:
    system = platform.system()
    return PlatformInfo(
        system=system,
        release=platform.release(),
        machine=platform.machine(),
        python=platform.python_version(),
        executable=sys.executable,
        is_windows=system == "Windows",
        is_macos=system == "Darwin",
        is_linux=system == "Linux",
        encoding=sys.stdout.encoding or os.device_encoding(1) or "unknown",
    )
