from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DangerAssessment:
    risk: str
    reasons: list[str]
    destructive: bool = False
    requires_typed_confirmation: bool = False

    @property
    def safe(self) -> bool:
        return self.risk == "low" and not self.destructive


DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str, bool]] = [
    (re.compile(r"\brm\s+(-[A-Za-z]*[rf][A-Za-z]*|-.*r.*f).*(/|\*|~|\$HOME)", re.I), "recursive forced removal", True),
    (re.compile(r"\brmdir\s+/s\b", re.I), "recursive directory removal", True),
    (re.compile(r"\bdel\s+(/s|/q).*(\\|\*)", re.I), "recursive Windows deletion", True),
    (re.compile(r"\bRemove-Item\b.*(-Recurse|-r)\b", re.I), "recursive PowerShell deletion", True),
    (re.compile(r"\bformat\b", re.I), "disk formatting command", True),
    (re.compile(r"\bmkfs(\.\w+)?\b", re.I), "filesystem creation command", True),
    (re.compile(r"\bdiskpart\b", re.I), "disk partition tool", True),
    (re.compile(r"\bdd\s+.*\bif=", re.I), "raw disk write command", True),
    (re.compile(r"\bshutdown\b|\breboot\b", re.I), "system shutdown or reboot", True),
    (re.compile(r"\bchmod\s+-R\s+777\b", re.I), "recursive world-writable permissions", True),
    (re.compile(r"\bchown\s+-R\b", re.I), "recursive ownership change", True),
    (re.compile(r"\breg\s+(add|delete|import)\b", re.I), "Windows registry modification", True),
    (re.compile(r"\bcurl\b.*\|\s*(sh|bash|zsh|pwsh|powershell)", re.I), "downloaded script piped to shell", True),
    (re.compile(r"\bwget\b.*\|\s*(sh|bash|zsh|pwsh|powershell)", re.I), "downloaded script piped to shell", True),
    (re.compile(r"\bsudo\b", re.I), "privilege escalation", False),
    (re.compile(r"\bnpm\s+install\s+-g\b", re.I), "global npm package installation", False),
    (re.compile(r"\bpip\s+install\b.*(git\+|https?://)", re.I), "pip install from remote URL", False),
]


MEDIUM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(git\s+clean|git\s+reset)\b", re.I), "git destructive working tree operation"),
    (re.compile(r"\bmv\b|\bmove\b", re.I), "file move operation"),
    (re.compile(r"\bcp\b|\bcopy\b", re.I), "file copy operation"),
    (re.compile(r"\bpython\b|\bnode\b|\bperl\b|\bruby\b|\bphp\b", re.I), "script execution"),
]


def assess_command(command: str) -> DangerAssessment:
    reasons: list[str] = []
    typed = False
    for pattern, reason, requires_typed in DANGEROUS_PATTERNS:
        if pattern.search(command):
            reasons.append(reason)
            typed = typed or requires_typed
    if reasons:
        return DangerAssessment(
            risk="high",
            reasons=sorted(set(reasons)),
            destructive=True,
            requires_typed_confirmation=typed,
        )

    for pattern, reason in MEDIUM_PATTERNS:
        if pattern.search(command):
            reasons.append(reason)
    if reasons:
        return DangerAssessment(risk="medium", reasons=sorted(set(reasons)))
    return DangerAssessment(risk="low", reasons=[])
