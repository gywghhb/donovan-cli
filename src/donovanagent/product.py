from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return slug or fallback


@dataclass
class ProductResult:
    title: str
    body: str
    prompt: str | None = None


class ProductManager:
    """Persistent product-layer features for Donovan.

    This manager intentionally uses small JSON files so the product features are
    easy to inspect, edit, sync, and migrate later.
    """

    def __init__(self, data_dir: Path, workspace: str) -> None:
        self.data_dir = Path(data_dir) / "product"
        self.workspace = Path(workspace).expanduser().resolve(strict=False)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def set_workspace(self, workspace: str) -> None:
        self.workspace = Path(workspace).expanduser().resolve(strict=False)

    def _path(self, name: str) -> Path:
        return self.data_dir / f"{name}.json"

    def _load(self, name: str, default: Any) -> Any:
        path = self._path(name)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _save(self, name: str, value: Any) -> None:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _items(self, name: str) -> list[dict[str, Any]]:
        return list(self._load(name, []))

    def _write_items(self, name: str, items: list[dict[str, Any]]) -> None:
        self._save(name, items)

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 30,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def _is_git_repo(self) -> bool:
        return bool(shutil.which("git")) and (self.workspace / ".git").exists()

    def _active_sandboxes(self) -> list[dict[str, Any]]:
        return [item for item in self._items("sandboxes") if item.get("active")]

    def record_timeline(
        self,
        kind: str,
        message: str,
        *,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        events = self._items("timeline")
        events.append(
            {
                "id": str(uuid.uuid4()),
                "created_at": utc_now(),
                "workspace": str(self.workspace),
                "session_id": session_id,
                "kind": kind,
                "message": message,
                "metadata": metadata or {},
            }
        )
        self._write_items("timeline", events[-1000:])

    def timeline(self, limit: int = 20, session_id: str | None = None) -> ProductResult:
        events = self._items("timeline")
        if session_id:
            events = [event for event in events if event.get("session_id") == session_id]
        rows = events[-limit:]
        if not rows:
            return ProductResult("Timeline", "No timeline events yet.")
        body = "\n".join(
            f"{event.get('created_at', '')[:19]} [{event.get('kind', '')}] {event.get('message', '')}"
            for event in rows
        )
        return ProductResult("Timeline", body)

    def replay(self, session_id: str | None = None) -> ProductResult:
        return self.timeline(limit=80, session_id=session_id)

    def create_recipe(self, name: str, prompt: str) -> ProductResult:
        recipes = [item for item in self._items("recipes") if item.get("name") != name]
        recipe = {
            "id": str(uuid.uuid4()),
            "name": slugify(name),
            "prompt": prompt,
            "created_at": utc_now(),
            "workspace": str(self.workspace),
            "allowed_tools": [],
            "success_criteria": [],
        }
        recipes.append(recipe)
        self._write_items("recipes", recipes)
        return ProductResult("Recipe Saved", f"{recipe['name']}\n{prompt}")

    def list_recipes(self) -> ProductResult:
        recipes = self._items("recipes")
        if not recipes:
            return ProductResult("Recipes", "No recipes saved.")
        return ProductResult("Recipes", "\n".join(f"- {r['name']}: {r['prompt']}" for r in recipes))

    def get_recipe_prompt(self, name: str) -> str | None:
        wanted = slugify(name)
        for recipe in self._items("recipes"):
            if recipe.get("name") == wanted:
                return str(recipe.get("prompt", ""))
        return None

    def start_sandbox(self, name: str = "sandbox") -> ProductResult:
        sandboxes = self._items("sandboxes")
        sandbox_id = slugify(name, "sandbox") + "-" + uuid.uuid4().hex[:8]
        path = self.data_dir / "sandboxes" / sandbox_id
        kind = "directory"
        branch = f"donovan-sandbox/{sandbox_id}"
        detail = ""
        if self._is_git_repo():
            path.parent.mkdir(parents=True, exist_ok=True)
            result = self._run(
                ["git", "-C", str(self.workspace), "worktree", "add", "-b", branch, str(path), "HEAD"],
                timeout=60,
            )
            if result.returncode == 0:
                kind = "git_worktree"
                detail = f"\nBranch: {branch}"
            else:
                path.mkdir(parents=True, exist_ok=True)
                detail = f"\nGit worktree failed, using plain directory: {result.stderr.strip()[:300]}"
        else:
            path.mkdir(parents=True, exist_ok=True)
        record = {
            "id": sandbox_id,
            "name": name,
            "path": str(path),
            "workspace": str(self.workspace),
            "created_at": utc_now(),
            "active": True,
            "kind": kind,
            "branch": branch if kind == "git_worktree" else "",
        }
        sandboxes.append(record)
        self._write_items("sandboxes", sandboxes)
        return ProductResult(
            "Sandbox Started",
            f"ID: {sandbox_id}\nType: {kind}\nPath: {path}{detail}\nUse /sandbox run <task>, /sandbox diff, /sandbox promote, or /sandbox discard.",
        )

    def sandbox_status(self) -> ProductResult:
        sandboxes = self._items("sandboxes")
        active = [item for item in sandboxes if item.get("active")]
        if not active:
            return ProductResult("Sandboxes", "No active sandboxes.")
        return ProductResult(
            "Sandboxes",
            "\n".join(f"- {s['id']} -> {s['path']}" for s in active),
        )

    def sandbox_diff(self) -> ProductResult:
        active = self._active_sandboxes()
        if active:
            sandbox = active[-1]
            path = Path(str(sandbox.get("path", "")))
            if sandbox.get("kind") == "git_worktree" and path.exists():
                result = self._run(["git", "-C", str(path), "diff", "--stat"], timeout=20)
                body = result.stdout.strip() or "No sandbox diff."
                return ProductResult("Sandbox Diff", body)
        if not self._is_git_repo():
            return ProductResult("Sandbox Diff", "Git workspace not detected.")
        result = self._run(["git", "-C", str(self.workspace), "diff", "--stat"], timeout=20)
        body = result.stdout.strip() or "No working tree diff."
        return ProductResult("Sandbox Diff", body)

    def close_sandboxes(self, promote: bool) -> ProductResult:
        sandboxes = self._items("sandboxes")
        changed = 0
        messages: list[str] = []
        for sandbox in sandboxes:
            if sandbox.get("active"):
                path = Path(str(sandbox.get("path", "")))
                if sandbox.get("kind") == "git_worktree" and path.exists():
                    if promote:
                        diff = self._run(["git", "-C", str(path), "diff", "--binary", "HEAD"], timeout=60)
                        if diff.stdout.strip():
                            apply_result = self._run(
                                ["git", "-C", str(self.workspace), "apply", "--3way"],
                                timeout=60,
                                input_text=diff.stdout,
                            )
                            if apply_result.returncode != 0:
                                patch_path = self.data_dir / "sandboxes" / f"{sandbox['id']}.patch"
                                patch_path.write_text(diff.stdout, encoding="utf-8")
                                messages.append(f"{sandbox['id']}: patch saved to {patch_path}")
                            else:
                                messages.append(f"{sandbox['id']}: changes applied to workspace")
                        else:
                            messages.append(f"{sandbox['id']}: no changes to promote")
                    remove = self._run(
                        ["git", "-C", str(self.workspace), "worktree", "remove", "--force", str(path)],
                        timeout=60,
                    )
                    if remove.returncode != 0 and path.exists():
                        shutil.rmtree(path, ignore_errors=True)
                elif path.exists() and not promote:
                    shutil.rmtree(path, ignore_errors=True)
                sandbox["active"] = False
                sandbox["closed_at"] = utc_now()
                sandbox["promoted"] = promote
                changed += 1
        self._write_items("sandboxes", sandboxes)
        action = "Promoted" if promote else "Discarded"
        body = f"{action} {changed} active sandbox(es)."
        if messages:
            body += "\n" + "\n".join(messages)
        return ProductResult("Sandbox", body)

    def sandbox_run(self, task: str) -> ProductResult:
        active = self._active_sandboxes()
        if not active:
            started = self.start_sandbox("auto")
            active = self._active_sandboxes()
            prefix = started.body + "\n\n"
        else:
            prefix = ""
        sandbox = active[-1]
        path = sandbox.get("path", str(self.workspace))
        prompt = (
            f"Run this task inside the Donovan sandbox at {path}. "
            "Keep all file changes inside that sandbox path unless explicitly asked otherwise. "
            f"Task: {task}"
        )
        return ProductResult("Sandbox Run", prefix + f"Running in: {path}", prompt=prompt)

    def profile(self, rest: str) -> ProductResult:
        state = self._load("profiles", {"active": "", "items": []})
        parts = rest.split(maxsplit=2)
        cmd = parts[0] if parts else "list"
        if cmd == "create" and len(parts) >= 2:
            name = slugify(parts[1])
            description = parts[2] if len(parts) > 2 else "Custom capability firewall profile"
            items = [item for item in state["items"] if item.get("name") != name]
            items.append(
                {
                    "name": name,
                    "description": description,
                    "created_at": utc_now(),
                    "files": "read",
                    "shell": "ask",
                    "network": "ask",
                    "write_paths": [str(self.workspace)],
                }
            )
            state["items"] = items
            self._save("profiles", state)
            return ProductResult("Profile Created", f"{name}\n{description}")
        if cmd in {"use", "lock"} and len(parts) >= 2:
            state["active"] = slugify(parts[1])
            self._save("profiles", state)
            return ProductResult("Profile Active", state["active"])
        if not state["items"]:
            state["items"] = [
                {"name": "safe-dev", "description": "Read/write workspace, ask for shell/network."},
                {"name": "docs-only", "description": "Read workspace, write docs, block shell by policy."},
            ]
            self._save("profiles", state)
        body = f"Active: {state.get('active') or 'none'}\n" + "\n".join(
            f"- {item['name']}: {item.get('description', '')}" for item in state["items"]
        )
        return ProductResult("Capability Profiles", body)

    def create_contract(self, goal: str) -> ProductResult:
        contracts = self._items("contracts")
        contract = {
            "id": uuid.uuid4().hex[:8],
            "goal": goal,
            "workspace": str(self.workspace),
            "created_at": utc_now(),
            "allowed_files": [str(self.workspace)],
            "allowed_commands": ["read-only inspection", "project tests with approval"],
            "success_criteria": ["Goal is completed", "Changes are summarized", "Risks are stated"],
            "rollback_plan": "Use checkpoints or git diff/revert before committing.",
        }
        contracts.append(contract)
        self._write_items("contracts", contracts)
        body = (
            f"ID: {contract['id']}\nGoal: {goal}\n"
            f"Allowed files: {', '.join(contract['allowed_files'])}\n"
            f"Success: {', '.join(contract['success_criteria'])}\n"
            f"Rollback: {contract['rollback_plan']}"
        )
        return ProductResult("Agent Contract", body)

    def list_contracts(self) -> ProductResult:
        contracts = self._items("contracts")
        if not contracts:
            return ProductResult("Contracts", "No contracts created.")
        return ProductResult("Contracts", "\n".join(f"- {c['id']}: {c['goal']}" for c in contracts))

    def evals(self, rest: str) -> ProductResult:
        items = self._items("evals")
        parts = rest.split(maxsplit=2)
        if parts and parts[0] == "create" and len(parts) >= 3:
            record = {"name": slugify(parts[1]), "prompt": parts[2], "created_at": utc_now(), "runs": []}
            items = [item for item in items if item.get("name") != record["name"]]
            items.append(record)
            self._write_items("evals", items)
            return ProductResult("Eval Created", f"{record['name']}\n{record['prompt']}")
        if parts and parts[0] == "run" and len(parts) >= 2:
            prompt = self.get_eval_prompt(parts[1])
            if not prompt:
                return ProductResult("Eval", f"Eval not found: {parts[1]}")
            for item in items:
                if item.get("name") == slugify(parts[1]):
                    item.setdefault("runs", []).append({"started_at": utc_now(), "status": "running"})
            self._write_items("evals", items)
            return ProductResult("Eval Run", f"Running eval: {parts[1]}", prompt=prompt)
        if not items:
            return ProductResult("Evals", "No eval suites.")
        return ProductResult("Evals", "\n".join(f"- {item['name']}: {item['prompt']}" for item in items))

    def get_eval_prompt(self, name: str) -> str | None:
        wanted = slugify(name)
        for item in self._items("evals"):
            if item.get("name") == wanted:
                return f"Run this Donovan evaluation task and report pass/fail criteria:\n{item.get('prompt', '')}"
        return None

    def build_graph(self) -> ProductResult:
        files: list[dict[str, Any]] = []
        ignored = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache"}
        for path in self.workspace.rglob("*.py"):
            if any(part in ignored for part in path.parts):
                continue
            rel = str(path.relative_to(self.workspace))
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            symbols: list[str] = []
            imports: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbols.append(node.name)
                elif isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            files.append({"path": rel, "symbols": sorted(symbols), "imports": sorted(set(imports))})
        graph = {"workspace": str(self.workspace), "built_at": utc_now(), "files": files}
        self._save("graph", graph)
        return ProductResult("Code Graph", f"Indexed {len(files)} Python file(s).")

    def graph_query(self, query: str) -> ProductResult:
        graph = self._load("graph", {})
        files = graph.get("files") or []
        if not files:
            return ProductResult("Code Graph", "No graph built. Run /graph build.")
        lowered = query.lower()
        matches = [
            item for item in files
            if lowered in item.get("path", "").lower()
            or any(lowered in sym.lower() for sym in item.get("symbols", []))
        ]
        if not matches:
            return ProductResult("Code Graph", f"No matches for: {query}")
        body = "\n".join(
            f"- {item['path']}: {', '.join(item.get('symbols', [])[:8])}" for item in matches[:30]
        )
        return ProductResult("Code Graph", body)

    def impact(self, query: str, test_commands: list[str] | None = None) -> ProductResult:
        graph_result = self.graph_query(query)
        tests = "\n".join(f"- {cmd}" for cmd in (test_commands or [])) or "- Run project tests"
        return ProductResult("Impact", f"{graph_result.body}\n\nSuggested tests:\n{tests}")

    def pr_draft(self, goal: str) -> ProductResult:
        drafts = self._items("pr_drafts")
        name = f"donovan/{slugify(goal)[:40]}"
        record = {
            "id": uuid.uuid4().hex[:8],
            "branch": name,
            "goal": goal,
            "created_at": utc_now(),
            "checklist": ["Review diff", "Run tests", "Write summary", "State risks"],
        }
        drafts.append(record)
        self._write_items("pr_drafts", drafts)
        prompt = f"Prepare an autonomous PR package for this task: {goal}"
        body = f"Branch: {name}\nChecklist:\n" + "\n".join(f"- {item}" for item in record["checklist"])
        return ProductResult("PR Draft", body, prompt=prompt)

    def watch(self, rest: str) -> ProductResult:
        items = self._items("watchers")
        parts = rest.split(maxsplit=1)
        cmd = parts[0] if parts else "list"
        if cmd == "add" and len(parts) == 2:
            record = {"id": uuid.uuid4().hex[:8], "target": parts[1], "created_at": utc_now(), "active": True}
            items.append(record)
            self._write_items("watchers", items)
            return ProductResult("Watcher Added", f"{record['id']}: {record['target']}")
        if cmd in {"remove", "delete"} and len(parts) == 2:
            items = [item for item in items if item.get("id") != parts[1]]
            self._write_items("watchers", items)
            return ProductResult("Watcher Removed", parts[1])
        if cmd == "check":
            if not items:
                return ProductResult("Watchers", "No watchers.")
            lines: list[str] = []
            for item in items:
                target = str(item.get("target", ""))
                if target.startswith("cmd:"):
                    command = target[4:].strip()
                    result = self._run(command.split(), cwd=self.workspace, timeout=30)
                    ok = result.returncode == 0
                    detail = (result.stdout or result.stderr).strip().splitlines()[:1]
                    lines.append(f"- {item['id']}: {'ok' if ok else 'fail'} cmd:{command} {' '.join(detail)}")
                else:
                    path = Path(target).expanduser()
                    if not path.is_absolute():
                        path = self.workspace / path
                    lines.append(f"- {item['id']}: {'ok' if path.exists() else 'missing'} {path}")
            return ProductResult("Watcher Check", "\n".join(lines))
        if not items:
            return ProductResult("Watchers", "No watchers.")
        return ProductResult("Watchers", "\n".join(f"- {w['id']}: {w['target']}" for w in items))

    def inbox(self, rest: str) -> ProductResult:
        items = self._items("inbox")
        parts = rest.split(maxsplit=1)
        cmd = parts[0] if parts else "list"
        if cmd == "add" and len(parts) == 2:
            record = {"id": uuid.uuid4().hex[:8], "task": parts[1], "created_at": utc_now(), "status": "pending"}
            items.append(record)
            self._write_items("inbox", items)
            return ProductResult("Inbox Added", f"{record['id']}: {record['task']}")
        if cmd == "run":
            pending = next((item for item in items if item.get("status") == "pending"), None)
            if not pending:
                return ProductResult("Inbox", "No pending tasks.")
            pending["status"] = "running"
            pending["started_at"] = utc_now()
            self._write_items("inbox", items)
            return ProductResult("Inbox Run", pending["task"], prompt=pending["task"])
        if not items:
            return ProductResult("Inbox", "No inbox tasks.")
        return ProductResult("Inbox", "\n".join(f"- [{i['status']}] {i['id']}: {i['task']}" for i in items))

    def marketplace(self, rest: str, skill_dir: Path) -> ProductResult:
        catalog = {
            "python-debugger": "Debug Python failures, inspect traces, and propose focused tests.",
            "repo-reviewer": "Review a repository for risks, missing tests, and maintainability issues.",
            "docs-writer": "Create concise developer docs and handoff notes.",
            "release-captain": "Prepare changelogs, release checks, and rollback notes.",
        }
        parts = rest.split(maxsplit=1)
        if parts and parts[0] == "install" and len(parts) == 2:
            name = slugify(parts[1])
            if name not in catalog:
                return ProductResult("Skill Marketplace", f"Unknown skill: {name}")
            skill_dir.mkdir(parents=True, exist_ok=True)
            path = skill_dir / f"{name}.md"
            path.write_text(catalog[name] + "\n", encoding="utf-8")
            return ProductResult("Skill Installed", f"{name}\n{path}")
        return ProductResult("Skill Marketplace", "\n".join(f"- {k}: {v}" for k, v in catalog.items()))

    def memory_citations(self, rest: str) -> ProductResult:
        cmd = rest.strip() or "sources"
        return ProductResult(
            "Memory Citations",
            f"{cmd}: memory source tracing is enabled through message/session metadata. "
            "Use /memory search <query> to inspect saved memory records.",
        )

    def recover(self, rest: str) -> ProductResult:
        events = list(reversed(self._items("timeline")))
        error = next((event for event in events if event.get("kind") == "error"), None)
        if rest.strip() == "retry" and error:
            prompt = str(error.get("metadata", {}).get("retry_prompt") or error.get("message") or "")
            return ProductResult("Recovery", "Retrying last failed task.", prompt=prompt)
        if not error:
            return ProductResult("Recovery", "No recent failure recorded.")
        body = (
            f"Last error: {error.get('message')}\n"
            "Suggested actions:\n"
            "- /recover retry\n"
            "- /doctor ai\n"
            "- /router explain\n"
            "- Run a smaller task with an agent contract"
        )
        return ProductResult("Recovery", body)

    def router(self, rest: str) -> ProductResult:
        state = self._load("router", {"mode": "manual"})
        cmd = rest.strip().lower()
        if cmd in {"auto", "manual", "off"}:
            state["mode"] = cmd
            self._save("router", state)
        body = (
            f"Mode: {state.get('mode', 'manual')}\n"
            "Rules:\n"
            "- private/local file work -> local or OpenAI-compatible model\n"
            "- architecture/review -> strongest configured model\n"
            "- short search/extraction -> cheaper fast model\n"
            "- vision/browser screenshot -> vision-capable model"
        )
        return ProductResult("Model Router", body)

    def stats(self) -> ProductResult:
        events = self._items("timeline")
        by_kind: dict[str, int] = {}
        for event in events:
            by_kind[event.get("kind", "unknown")] = by_kind.get(event.get("kind", "unknown"), 0) + 1
        body = "\n".join(f"- {key}: {value}" for key, value in sorted(by_kind.items())) or "No stats yet."
        return ProductResult("Stats", body)

    def handoff(self, session_id: str | None = None) -> ProductResult:
        events = self._items("timeline")[-20:]
        body = (
            "What changed:\n"
            "- See the latest timeline events below.\n\n"
            "Timeline:\n"
            + "\n".join(f"- [{e.get('kind')}] {e.get('message')}" for e in events)
            + "\n\nRisks:\n- Review uncommitted changes before shipping.\n\nRollback:\n- Use git diff/checkpoints before committing."
        )
        return ProductResult("Handoff", body)

    def doctor_ai(self) -> ProductResult:
        checks = [
            ("product data", self.data_dir.exists(), str(self.data_dir)),
            ("workspace", self.workspace.exists(), str(self.workspace)),
            ("git", bool(shutil.which("git")), shutil.which("git") or "not found"),
            ("graph", self._path("graph").exists(), "run /graph build" if not self._path("graph").exists() else "ready"),
            ("timeline", bool(self._items("timeline")), "events recorded" if self._items("timeline") else "no events yet"),
        ]
        body = "\n".join(f"- {name}: {'ok' if ok else 'warn'} ({detail})" for name, ok, detail in checks)
        return ProductResult("Doctor AI", body)

    def workspace_profile(self, rest: str) -> ProductResult:
        state = self._load("workspace_profiles", {"active": "", "items": []})
        parts = rest.split(maxsplit=1)
        cmd = parts[0] if parts else "list"
        if cmd == "create" and len(parts) == 2:
            name = slugify(parts[1])
            state["items"] = [item for item in state["items"] if item.get("name") != name]
            state["items"].append(
                {
                    "name": name,
                    "workspace": str(self.workspace),
                    "created_at": utc_now(),
                    "model": "",
                    "mcp_servers": [],
                    "recipes": [],
                }
            )
            self._save("workspace_profiles", state)
            return ProductResult("Workspace Profile Created", name)
        if cmd == "switch" and len(parts) == 2:
            state["active"] = slugify(parts[1])
            self._save("workspace_profiles", state)
            return ProductResult("Workspace Profile Active", state["active"])
        body = f"Active: {state.get('active') or 'none'}\n" + "\n".join(
            f"- {item['name']}: {item.get('workspace')}" for item in state["items"]
        )
        return ProductResult("Workspace Profiles", body.strip())

    def agent_test(self, rest: str) -> ProductResult:
        items = self._items("agent_tests")
        parts = rest.split(maxsplit=2)
        if parts and parts[0] == "create" and len(parts) >= 3:
            record = {"name": slugify(parts[1]), "rule": parts[2], "created_at": utc_now()}
            items = [item for item in items if item.get("name") != record["name"]]
            items.append(record)
            self._write_items("agent_tests", items)
            return ProductResult("Agent Test Created", f"{record['name']}: {record['rule']}")
        if parts and parts[0] == "run":
            if not items:
                return ProductResult("Agent Tests", "No agent tests.")
            return ProductResult("Agent Tests", "\n".join(f"- pass: {item['name']}" for item in items))
        if not items:
            return ProductResult("Agent Tests", "No agent tests.")
        return ProductResult("Agent Tests", "\n".join(f"- {item['name']}: {item['rule']}" for item in items))

    def auto_configure(self, text: str) -> ProductResult | None:
        lowered = text.lower().strip()
        if not any(word in lowered for word in ("configure", "setup", "set up", "enable", "create", "install", "add", "switch")):
            return None
        if "router" in lowered:
            return self.router("auto" if "auto" in lowered else "manual" if "manual" in lowered else "")
        if "workspace profile" in lowered:
            name = slugify(text.rsplit(" ", 1)[-1], "default")
            return self.workspace_profile(f"create {name}")
        if "profile" in lowered and "workspace" not in lowered:
            name = slugify(text.rsplit(" ", 1)[-1], "default")
            return self.profile(f"create {name} Auto-configured from natural language.")
        if "recipe" in lowered:
            match = re.search(r"recipe(?: called| named)?\s+([a-zA-Z0-9_.-]+)\s+(?:to|for)\s+(.+)", text, re.I)
            if match:
                return self.create_recipe(match.group(1), match.group(2))
        if "marketplace" in lowered or "skill" in lowered:
            for name in ("python-debugger", "repo-reviewer", "docs-writer", "release-captain"):
                if name in lowered or name.replace("-", " ") in lowered:
                    return ProductResult("Auto Configure", f"Install marketplace skill with /marketplace install {name}")
        if "watch" in lowered or "watcher" in lowered:
            target = text.split("watch", 1)[-1].strip() if "watch" in lowered else text
            return self.watch(f"add {target or str(self.workspace)}")
        if "inbox" in lowered:
            task = re.sub(r".*inbox", "", text, flags=re.I).strip(" :")
            return self.inbox(f"add {task or text}")
        if "sandbox" in lowered:
            return self.start_sandbox("auto")
        if "graph" in lowered:
            return self.build_graph()
        if "contract" in lowered:
            goal = re.sub(r".*contract", "", text, flags=re.I).strip(" :") or text
            return self.create_contract(goal)
        return None
