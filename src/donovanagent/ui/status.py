from __future__ import annotations

import shutil
import threading
import time
from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table


def status_table(title: str = "Doctor") -> Table:
    table = Table(title=title, box=box.SIMPLE_HEAVY)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Details")
    return table


_PULSE_FRAMES = [
    "\033[38;5;238m●\033[0m",
    "\033[38;5;242m●\033[0m",
    "\033[38;5;246m●\033[0m",
    "\033[38;5;250m●\033[0m",
    "\033[38;5;255m●\033[0m",
    "\033[38;5;250m●\033[0m",
    "\033[38;5;246m●\033[0m",
    "\033[38;5;242m●\033[0m",
]
_PULSE_DELAY = 0.18


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}m{s:02d}s"


class ActivityIndicator:
    """A simple pulsing-dot indicator shown while the agent is working.

    The agent calls ``set_word()`` to update the status text
    (e.g. "Thinking", "Recalling", "Running: list_directory").

    A live elapsed timer is shown on the same line, and the
    workspace bottom bar stays visible at all times.
    """

    def __init__(self, console: Console, workspace: str = "") -> None:
        self.console = console
        self.workspace = workspace
        self._word: str = "Thinking"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_time: float = 0.0

    def begin_step(self, label: str) -> None:
        pass

    def begin_tool(self, execution_id: str, name: str, args_preview: str = "") -> None:
        pass

    def end_tool(self, execution_id: str, name: str, success: bool = True, result: str = "", elapsed_ms: int = 0) -> None:
        pass

    def set_narration(self, text: str) -> None:
        pass

    def set_word(self, word: str) -> None:
        with self._lock:
            self._word = word

    def __enter__(self) -> "ActivityIndicator":
        self._start_time = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        f = self.console.file
        f.write("\r" + " " * 80 + "\r")
        f.flush()

    def _run(self) -> None:
        frame = 0
        while not self._stop.is_set():
            with self._lock:
                word = self._word
            elapsed = _fmt_elapsed(time.monotonic() - self._start_time)
            dot = _PULSE_FRAMES[frame % len(_PULSE_FRAMES)]
            self.console.file.write(f"\r{dot} {word}  {elapsed}".ljust(80))
            self.console.file.flush()
            frame += 1
            time.sleep(_PULSE_DELAY)
