from __future__ import annotations

from pathlib import Path


def load_user_skill_files(config_dir: Path, workspace: str) -> list[tuple[str, str]]:
    """Return list of (name, content) from .md files in user skill dirs."""
    dirs = [
        config_dir / "skills",
        Path(workspace) / ".DonovanAgent" / "skills",
    ]
    skills: list[tuple[str, str]] = []
    seen: set[str] = set()
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            if f.name in seen:
                continue
            seen.add(f.name)
            try:
                content = f.read_text(encoding="utf-8").strip()
                if content:
                    skills.append((f.stem, content))
            except OSError:
                pass
    return skills
