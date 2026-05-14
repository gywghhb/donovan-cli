from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolEntry:
    """A single tool invocation within a step."""
    name: str
    args_preview: str = ""
    status: str = "running"   # running, completed, failed
    result: str = ""
    elapsed_ms: int = 0
    execution_id: str = ""


@dataclass
class StepEntry:
    """A named phase of work, containing one or more tool calls."""
    label: str
    status: str = "running"   # running, completed
    tools: list[ToolEntry] = field(default_factory=list)
    start_time: float = 0.0
    elapsed_display: str = ""


# Pulse frames for the active dot
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

# Separator line between step groups
_SEPARATOR = "\033[38;5;237m" + "─" * 60 + "\033[0m"

# Dim bullet for completed items
_BULLET = "\033[38;5;244m●\033[0m"

# Checkmark for successful items
_CHECK = "\033[38;5;242m✓\033[0m"

# Cross for failed items
_CROSS = "\033[38;5;242m✕\033[0m"

# Dim prefix for tool lines with tree connector
_TOOL_PREFIX = "\033[38;5;244m  ├\033[0m "


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}m{s:02d}s"


class StepTreeIndicator:
    """Renders a live list of agent steps and tool calls to the terminal.

    Completed steps are printed as static lines (they scroll up naturally).
    The currently active step and tool update in place using ``\\r`` and
    ANSI cursor-up escapes.

    Layout::

        ● Let me search the web...                                   12.0s
          · web_search "the query"

        ───...                      ← separator

        ● read_file "path"

        ● Let me edit the code...                                    3.2s
          · write_file "target"
    """

    def __init__(self, file) -> None:
        self._file = file
        self._lock = threading.Lock()
        self._stop = threading.Event()

        # --- state ---
        self._simple_word: str = "Thinking"
        self._start_time: float = 0.0

        # --- tree state ---
        self._narration: str = ""
        self._completed_lines: list[str] = []
        self._active_step: StepEntry | None = None
        self._last_render_height: int = 0

        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API — called from agent
    # ------------------------------------------------------------------

    def begin_step(self, label: str) -> None:
        """Start a new named step (phase of work)."""
        with self._lock:
            self._finalize_active_step()
            self._narration = label
            self._active_step = StepEntry(label=label, status="running", start_time=time.monotonic())

    def begin_tool(self, execution_id: str, name: str, args_preview: str = "") -> None:
        """Record that a tool is starting within the current step."""
        with self._lock:
            if self._active_step is None:
                self._active_step = StepEntry(label=name, status="running", start_time=time.monotonic())
                self._narration = f"Running {name}..."
                self._active_step.tools.append(ToolEntry(execution_id=execution_id, name=name, args_preview=args_preview, status="running"))
                return

            # If this execution_id already exists (shouldn't happen, but be safe), update it
            for t in self._active_step.tools:
                if t.execution_id == execution_id:
                    t.status = "running"
                    if args_preview:
                        t.args_preview = args_preview
                    return

            # Collapse consecutive identical tool calls — reuse entry, update preview
            if self._active_step.tools and self._active_step.tools[-1].name == name:
                self._active_step.tools[-1].status = "running"
                self._active_step.tools[-1].execution_id = execution_id
                if args_preview:
                    self._active_step.tools[-1].args_preview = args_preview
                return

            # New unique tool in this step
            self._active_step.tools.append(ToolEntry(execution_id=execution_id, name=name, args_preview=args_preview, status="running"))

    def end_tool(self, execution_id: str, name: str, success: bool = True, result: str = "", elapsed_ms: int = 0) -> None:
        """Mark a tool execution as completed/failed."""
        with self._lock:
            if self._active_step and self._active_step.tools:
                for t in reversed(self._active_step.tools):
                    if t.execution_id == execution_id:
                        t.status = "completed" if success else "failed"
                        t.result = result
                        t.elapsed_ms = elapsed_ms
                        return
                # Fallback: match by name if execution_id not found (backward compat)
                for t in reversed(self._active_step.tools):
                    if t.name == name and t.status == "running":
                        t.status = "completed" if success else "failed"
                        t.result = result
                        t.elapsed_ms = elapsed_ms
                        return

    def set_narration(self, text: str) -> None:
        """Update the current narration line."""
        with self._lock:
            self._narration = text

    def set_word(self, word: str) -> None:
        """Fallback simple state word when no tools are running."""
        with self._lock:
            self._simple_word = word

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._start_time = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        with self._lock:
            self._finalize_active_step()
        f = self._file
        # Move cursor past our render block (cursor was at top of block after last \033[{total}A)
        if self._last_render_height > 0:
            f.write(f"\033[{self._last_render_height}B")
        self._last_render_height = 0
        f.write("\r" + " " * 80 + "\r")
        f.flush()

    def __enter__(self) -> "StepTreeIndicator":
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _finalize_active_step(self) -> None:
        if self._active_step is None:
            return
        lines = self._build_step_lines(self._active_step, frame_index=0, is_active=False)
        if lines:
            if self._completed_lines:
                self._completed_lines.append(_SEPARATOR)
            self._completed_lines.extend(lines)
        self._active_step = None
        self._narration = ""

    def _build_step_lines(self, step: StepEntry, frame_index: int, is_active: bool) -> list[str]:
        """Build the display lines for a step."""
        lines: list[str] = []
        elapsed = _fmt_elapsed(time.monotonic() - step.start_time) if is_active else ""
        dot = _PULSE_FRAMES[frame_index % len(_PULSE_FRAMES)] if is_active else _BULLET

        if step.label:
            ts = f"  {elapsed}" if elapsed else ""
            lines.append(f"  {dot} {step.label}{ts}")

        # Show each unique tool name only once (last occurrence wins)
        seen: dict[str, ToolEntry] = {}
        for tool in step.tools:
            seen[tool.name] = tool
        for tool in seen.values():
            marker = _CHECK if tool.status == "completed" else _CROSS if tool.status == "failed" else ""
            if tool.args_preview:
                lines.append(f"{_TOOL_PREFIX}{tool.args_preview}")
            else:
                lines.append(f"{_TOOL_PREFIX}{tool.name}")
        return lines

    def _render(self, frame_index: int) -> None:
        """Write the current state to the terminal, overwriting previous render."""
        with self._lock:
            has_active = self._active_step is not None

            if not has_active and not self._completed_lines:
                elapsed = _fmt_elapsed(time.monotonic() - self._start_time)
                dot = _PULSE_FRAMES[frame_index % len(_PULSE_FRAMES)]
                self._file.write(f"\r{dot} {self._simple_word}  {elapsed}   ".ljust(80))
                self._last_render_height = 0
                self._file.flush()
                return

            all_lines: list[str] = list(self._completed_lines)

            if has_active and self._active_step:
                all_lines.extend(self._build_step_lines(self._active_step, frame_index, is_active=True))

            if not all_lines:
                return

            total = len(all_lines)

            # Move cursor back to the start of our previous render block
            if self._last_render_height > 0:
                self._file.write(f"\033[{self._last_render_height}A\r")

            # Write all lines — always start from column 0 with \r
            for line in all_lines:
                self._file.write(f"\r{line}".ljust(80) + "\n")

            self._last_render_height = total

            # Move cursor back up so next render overwrites from the top
            self._file.write(f"\033[{total}A")
            self._file.flush()

    def _run(self) -> None:
        frame_index = 0
        while not self._stop.is_set():
            self._render(frame_index)
            frame_index += 1
            time.sleep(_PULSE_DELAY)
