from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import time
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pathspec

from donovanagent.security.permissions import PathPermissions
from donovanagent.tools.base import ToolExecutionContext, ToolResult

_MAX_SEARCH_SIZE_BYTES = 1 * 1024 * 1024  # Skip files larger than 1MB
_SEARCH_TIMEOUT_SECONDS = 30


def read_file(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    path = PathPermissions(ctx.config).require_read(args["path"])
    max_bytes = int(args.get("max_bytes") or ctx.config.tools.filesystem.max_read_bytes)
    start_line = args.get("start_line")
    end_line = args.get("end_line")
    if not path.exists():
        return ToolResult(False, f"File not found: {path}")
    if not path.is_file():
        return ToolResult(False, f"Not a file: {path}")
    data = path.read_bytes()[: max_bytes + 1]
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    if b"\x00" in data:
        hex_preview = data[:256].hex(" ", 1)
        return ToolResult(True, f"[binary file: {len(data)} bytes]\n{hex_preview}", {"path": str(path), "binary": True})
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    lines = text.splitlines()
    if start_line is not None or end_line is not None:
        start = max(1, int(start_line or 1))
        end = min(len(lines), int(end_line or len(lines)))
        selected = lines[start - 1 : end]
        text = "\n".join(f"{idx}: {line}" for idx, line in enumerate(selected, start=start))
    suffix = "\n\n[truncated]" if truncated else ""
    ctx.db.add_audit("read_file", "agent", session_id=ctx.session_id, path=str(path), approved=True)
    return ToolResult(True, text + suffix, {"path": str(path), "truncated": truncated})


def write_file(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    path = PathPermissions(ctx.config).require_write(args["path"])
    content = str(args.get("content", ""))
    existed = path.exists() and path.is_file()

    diff = ""
    if existed:
        try:
            old = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            old = path.read_text(encoding="latin-1", errors="replace")
        diff = "\n".join(
            difflib.unified_diff(
                old.splitlines(),
                content.splitlines(),
                fromfile=f"{path} (before)",
                tofile=f"{path} (after)",
                lineterm="",
            )
        )
    if existed and ctx.config.tools.filesystem.require_approval_for_write and ctx.config.app.permission_mode != "full_autonomy":
        from donovanagent.tools.approval import ApprovalRequest

        decision = ctx.approval.request(
            ApprovalRequest(
                title=f"write {path.name}",
                body=f"Path: {path}\n\nDiff:\n{diff[:4000] or '[new file]'}",
                risk="medium",
            )
        )
        if not decision.approved:
            ctx.db.add_audit("write_file", "agent", session_id=ctx.session_id, path=str(path), approved=False)
            return ToolResult(False, f"Write denied: {decision.reason}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")
    ctx.db.add_audit(
        "write_file",
        "agent",
        session_id=ctx.session_id,
        path=str(path),
        risk_level="medium",
        approved=True,
    )
    return ToolResult(
        True,
        f"Wrote {path}" + (f"\n{diff[:2000]}" if diff else ""),
        {"path": str(path), "diff": diff},
    )


def patch_file(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    path = PathPermissions(ctx.config).require_write(args["path"])
    search = args.get("search")
    replace = args.get("replace")
    if search is None or replace is None:
        return ToolResult(False, "patch_file requires 'search' and 'replace' arguments")
    if not path.exists():
        return ToolResult(False, f"File not found: {path}")
    try:
        old = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        old = path.read_text(encoding="latin-1", errors="replace")
    count = int(args.get("count") or 1)
    try:
        new = robust_replace(old, str(search), str(replace), count)
    except ValueError as e:
        return ToolResult(False, str(e))
    diff = "\n".join(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"{path} (before)",
            tofile=f"{path} (after)",
            lineterm="",
        )
    )
    if ctx.config.tools.filesystem.require_approval_for_write and ctx.config.app.permission_mode != "full_autonomy":
        from donovanagent.tools.approval import ApprovalRequest

        decision = ctx.approval.request(
            ApprovalRequest(
                title=f"patch {path.name}",
                body=f"Path: {path}\n\nDiff:\n{diff[:4000]}",
                risk="medium",
            )
        )
        if not decision.approved:
            ctx.db.add_audit("patch_file", "agent", session_id=ctx.session_id, path=str(path), approved=False)
            return ToolResult(False, f"Patch denied: {decision.reason}")
    path.write_text(new, encoding="utf-8", errors="replace", newline=_detect_newline(old))
    ctx.db.add_audit(
        "patch_file",
        "agent",
        session_id=ctx.session_id,
        path=str(path),
        risk_level="medium",
        approved=True,
    )
    return ToolResult(
        True,
        f"Patched {path}",
        {"path": str(path), "diff": diff},
    )


def _detect_newline(text: str) -> str | None:
    return "\r\n" if "\r\n" in text else "\n"


def _levenshtein(a: str, b: str) -> int:
    """Levenshtein distance between two strings."""
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    curr = [0] * (len(b) + 1)
    for i, ca in enumerate(a, 1):
        curr[0] = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[len(b)]


def robust_replace(content: str, old_str: str, new_str: str, count: int = 1) -> str:
    """Port of OpenCode's multi-strategy replace from edit.ts.
    Tries progressively more flexible matching strategies."""
    if old_str == new_str:
        raise ValueError("No changes to apply: old_str and new_str are identical.")

    # Strategy 1: Simple exact match
    if old_str in content:
        if count > 1:
            return content.replace(old_str, new_str, count)
        idx = content.index(old_str)
        last = content.rindex(old_str)
        if idx == last:
            return content[:idx] + new_str + content[idx + len(old_str):]

    strategies = [
        _try_line_trimmed,
        _try_block_anchor,
        _try_whitespace_normalized,
        _try_indentation_flexible,
        _try_escape_normalized,
        _try_trimmed_boundary,
        _try_multi_occurrence,
    ]

    for strategy in strategies:
        found = strategy(content, old_str, new_str, count)
        if found is not None:
            return found

    raise ValueError(
        "Could not find old_str in the file. It must match exactly, including "
        "whitespace, indentation, and line endings."
    )


def _try_line_trimmed(content: str, old_str: str, new_str: str, count: int = 1) -> str | None:
    """Match each line after trimming whitespace."""
    original_lines = content.split("\n")
    search_lines = old_str.rstrip("\n").split("\n")

    for i in range(len(original_lines) - len(search_lines) + 1):
        for j in range(len(search_lines)):
            if original_lines[i + j].strip() != search_lines[j].strip():
                break
        else:
            # All lines matched
            match_start = sum(len(l) + 1 for l in original_lines[:i])
            match_end = match_start + sum(len(l) + 1 for l in original_lines[i:i + len(search_lines)]) - 1
            matched = content[match_start:match_end]
            result = content[:match_start] + new_str + content[match_end:]
            return result
    return None


def _try_block_anchor(content: str, old_str: str, new_str: str, count: int = 1) -> str | None:
    """Match using first/last lines as anchors, with similarity for middle lines."""
    search_lines = old_str.rstrip("\n").split("\n")
    if len(search_lines) < 3:
        return None

    first = search_lines[0].strip()
    last = search_lines[-1].strip()
    content_lines = content.split("\n")

    candidates: list[tuple[int, int]] = []
    for i, line in enumerate(content_lines):
        if line.strip() != first:
            continue
        for j in range(i + 2, len(content_lines)):
            if content_lines[j].strip() == last:
                candidates.append((i, j))
                break

    if not candidates:
        return None

    def _score(start: int, end: int) -> float:
        n_mid = min(len(search_lines) - 2, end - start - 1)
        if n_mid <= 0:
            return 1.0
        total = 0.0
        for k in range(1, min(len(search_lines) - 1, end - start)):
            sl = search_lines[k].strip()
            cl = content_lines[start + k].strip()
            if sl or cl:
                total += 1.0 - _levenshtein(sl, cl) / max(len(sl), len(cl), 1)
        return total / n_mid

    best = max(candidates, key=lambda c: _score(*c))
    sim = _score(*best)
    threshold = 0.0 if len(candidates) == 1 else 0.3
    if sim < threshold:
        return None

    start, end = best
    match_start = sum(len(l) + 1 for l in content_lines[:start])
    match_end = sum(len(l) + 1 for l in content_lines[:end + 1]) - 1
    return content[:match_start] + new_str + content[match_end:]


def _try_whitespace_normalized(content: str, old_str: str, new_str: str, count: int = 1) -> str | None:
    """Normalize all whitespace between words before matching."""
    def norm(s: str) -> str:
        return " ".join(s.split())

    norm_old = norm(old_str)
    lines = content.split("\n")

    # Single-line match
    for i, line in enumerate(lines):
        if norm(line) == norm_old:
            match_start = sum(len(l) + 1 for l in lines[:i])
            match_end = match_start + len(line)
            return content[:match_start] + new_str + content[match_end:]

    # Multi-line match
    old_lines = old_str.split("\n")
    if len(old_lines) > 1:
        norm_old_block = norm(old_str)
        for i in range(len(lines) - len(old_lines) + 1):
            block = "\n".join(lines[i:i + len(old_lines)])
            if norm(block) == norm_old_block:
                match_start = sum(len(l) + 1 for l in lines[:i])
                match_end = sum(len(l) + 1 for l in lines[:i + len(old_lines)]) - 1
                return content[:match_start] + new_str + content[match_end:]
    return None


def _try_indentation_flexible(content: str, old_str: str, new_str: str, count: int = 1) -> str | None:
    """Remove common indentation before matching."""
    def dedent(text: str) -> str:
        lines = text.split("\n")
        non_empty = [l for l in lines if l.strip()]
        min_indent = min((len(l) - len(l.lstrip()) for l in non_empty), default=0)
        return "\n".join(l[min_indent:] if l.strip() else l for l in lines)

    dedented_old = dedent(old_str)
    content_lines = content.split("\n")
    old_lines = old_str.split("\n")

    for i in range(len(content_lines) - len(old_lines) + 1):
        block = "\n".join(content_lines[i:i + len(old_lines)])
        if dedent(block) == dedented_old:
            match_start = sum(len(l) + 1 for l in content_lines[:i])
            match_end = sum(len(l) + 1 for l in content_lines[:i + len(old_lines)]) - 1
            return content[:match_start] + new_str + content[match_end:]
    return None


def _try_escape_normalized(content: str, old_str: str, new_str: str, count: int = 1) -> str | None:
    """Handle escaped characters in the search string."""
    def unescape(s: str) -> str:
        return s.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")

    unescaped = unescape(old_str)
    if unescaped in content:
        idx = content.index(unescaped)
        return content[:idx] + new_str + content[idx + len(unescaped):]

    # Try finding content where unescaped matches escaped content
    content_lines = content.split("\n")
    old_lines = old_str.split("\n")
    for i in range(len(content_lines) - len(old_lines) + 1):
        block = "\n".join(content_lines[i:i + len(old_lines)])
        if unescape(block) == unescaped:
            match_start = sum(len(l) + 1 for l in content_lines[:i])
            match_end = sum(len(l) + 1 for l in content_lines[:i + len(old_lines)]) - 1
            return content[:match_start] + new_str + content[match_end:]
    return None


def _try_trimmed_boundary(content: str, old_str: str, new_str: str, count: int = 1) -> str | None:
    """Try matching the trimmed version of old_str."""
    trimmed = old_str.strip()
    if trimmed == old_str or not trimmed:
        return None
    if trimmed in content:
        idx = content.index(trimmed)
        return content[:idx] + new_str + content[idx + len(trimmed):]
    return None


def _try_multi_occurrence(content: str, old_str: str, new_str: str, count: int = 1) -> str | None:
    """Find all exact occurrences and use last if count==1."""
    if old_str not in content:
        return None
    if count > 1:
        return content.replace(old_str, new_str, count)
    last = content.rindex(old_str)
    first = content.index(old_str)
    if first == last:
        return content[:first] + new_str + content[first + len(old_str):]
    return None


def list_directory(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    path = PathPermissions(ctx.config).require_read(args.get("path") or ctx.config.app.default_workspace)
    show_hidden = bool(args.get("show_hidden", False))
    if not path.exists():
        return ToolResult(False, f"Directory not found: {path}")
    if not path.is_dir():
        return ToolResult(False, f"Not a directory: {path}")
    entries = []
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if not show_hidden and child.name.startswith("."):
            continue
        stat = child.stat()
        entries.append(
            {
                "name": child.name,
                "path": str(child),
                "type": "directory" if child.is_dir() else "file",
                "size": stat.st_size,
            }
        )
    content = "\n".join(f"{entry['type'][:1]} {entry['size']:>10} {entry['name']}" for entry in entries)
    ctx.db.add_audit("list_directory", "agent", session_id=ctx.session_id, path=str(path), approved=True)
    return ToolResult(True, content, {"path": str(path), "entries": entries})


def search_files(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    query = str(args["query"])
    root = PathPermissions(ctx.config).require_read(args.get("path") or ctx.config.app.default_workspace)
    glob_pattern = args.get("glob")
    max_results = int(args.get("max_results") or 100)
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "--line-number", "--color", "never", "--hidden", "--glob", "!.git/"]
        if glob_pattern:
            cmd.extend(["--glob", str(glob_pattern)])
        cmd.append(query)
        cmd.append(str(root))
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        lines = proc.stdout.splitlines()[:max_results]
        success = proc.returncode in (0, 1)
        ctx.db.add_audit(
            "search_files",
            "agent",
            session_id=ctx.session_id,
            path=str(root),
            approved=True,
            details={"engine": "rg", "query": query},
        )
        return ToolResult(success, "\n".join(lines) or "No matches", {"engine": "rg", "matches": lines})

    matches = python_search(root, query, glob_pattern, max_results)
    ctx.db.add_audit(
        "search_files",
        "agent",
        session_id=ctx.session_id,
        path=str(root),
        approved=True,
        details={"engine": "python", "query": query},
    )
    return ToolResult(True, "\n".join(matches) or "No matches", {"engine": "python", "matches": matches})


_BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".pyc", ".pyo", ".pyd",
    ".ttf", ".otf", ".woff", ".woff2",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".db", ".sqlite",
})


def _is_likely_binary(path: Path) -> bool:
    return path.suffix.lower() in _BINARY_EXTENSIONS


def _do_python_search(root: Path, query: str, glob_pattern: str | None, max_results: int) -> list[str]:
    """Core search with file size limits and binary extension filtering."""
    spec = load_gitignore(root)
    matches: list[str] = []
    query_lower = query.lower()

    try:
        stat = root.stat()
        total_size = 0
    except OSError:
        return matches

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames if d != ".git" and not path_ignored(root, current / d, spec)
        ]
        for filename in filenames:
            path = current / filename
            if _is_likely_binary(path):
                continue
            if glob_pattern and not path.match(glob_pattern):
                continue
            if path_ignored(root, path, spec):
                continue

            try:
                st = path.stat()
                if st.st_size > _MAX_SEARCH_SIZE_BYTES:
                    continue
                total_size += st.st_size
            except OSError:
                continue

            try:
                data = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:1024]:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("utf-8", errors="ignore")
            for index, line in enumerate(text.splitlines(), start=1):
                if query_lower in line.lower():
                    matches.append(f"{path}:{index}:{line}")
                    if len(matches) >= max_results:
                        return matches
    return matches


def python_search(root: Path, query: str, glob_pattern: str | None, max_results: int) -> list[str]:
    """Run search with a hard timeout. Falls back to partial results on timeout."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_do_python_search, root, query, glob_pattern, max_results)
        try:
            return future.result(timeout=_SEARCH_TIMEOUT_SECONDS)
        except FutureTimeout:
            return [f"[Search timed out after {_SEARCH_TIMEOUT_SECONDS}s - showing partial results]"]
        except Exception as exc:
            return [f"[Search error: {exc}]"]


def load_gitignore(root: Path) -> pathspec.PathSpec | None:
    ignore = root / ".gitignore"
    if not ignore.exists():
        return None
    try:
        return pathspec.PathSpec.from_lines("gitwildmatch", ignore.read_text(encoding="utf-8").splitlines())
    except OSError:
        return None


def path_ignored(root: Path, path: Path, spec: pathspec.PathSpec | None) -> bool:
    if spec is None:
        return False
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return spec.match_file(str(rel))
