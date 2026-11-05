"""Full-history credential scan with an explicit, reviewable incident allowlist.

A silent "known issue" exemption would defeat the point, so historical hits
are only suppressed when their exact fingerprint is listed in
``.security-allowlist.json`` together with a commit, path and reason.  See
SECURITY.md for the incident record.  Unknown hits fail the CI gate.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / ".security-allowlist.json"
TEXT_SUFFIXES = frozenset({".py", ".json", ".md", ".yml", ".yaml", ".toml",
                           ".txt", ".sh", ".cfg", ".ini", ".html", ".bat"})
# The scanner's own pattern definitions necessarily resemble credentials.
EXCLUDED_PATHS = frozenset({"audit/secrets.py", ".security-allowlist.json"})

# Patterns are assembled so this source file does not itself contain a
# credential-shaped literal.  The bot-webhook rule stays enabled after the
# owner confirmed the historical credential was revoked: git history still
# contains the value, and the rule must keep catching any recurrence or any
# new URL of the same shape.  Its allowlist entries are marked `revoked`,
# not deleted.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("feishu_webhook", re.compile(
        "https://open." + "feishu.cn/open-apis/bot/v2/hook/" + r"[A-Za-z0-9_-]{8,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile("-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("assigned_secret", re.compile(
        r"(?i)(?:password|passwd|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']")),
)


def _fingerprint(commit: str, path: str, value: str) -> str:
    return hashlib.sha256(f"{commit}\0{path}\0{value}".encode()).hexdigest()[:16]


def _finding(rule: str, commit: str, path: str, value: str, where: str) -> dict[str, Any]:
    return {
        "fingerprint": _fingerprint(commit, path, value),
        "rule": rule, "commit": commit, "path": path, "where": where,
        "redacted": value[:8] + "...",
    }


def _match_line(text: str, commit: str, path: str, where: str) -> list[dict[str, Any]]:
    if path in EXCLUDED_PATHS:
        return []
    found: list[dict[str, Any]] = []
    for rule, pattern in PATTERNS:
        for match in pattern.finditer(text):
            found.append(_finding(rule, commit, path, match.group(0), where))
    return found


def scan_history() -> tuple[list[dict[str, Any]], int]:
    proc = subprocess.run(
        ["git", "log", "--all", "-p", "--no-color",
         "--format=__AUDIT_COMMIT__%H", "--", ".", ":(exclude)data/**"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git log failed: {proc.stderr.strip()}")
    commit, path, commits = "unknown", "unknown", 0
    found: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        if line.startswith("__AUDIT_COMMIT__"):
            commit = line.split("__AUDIT_COMMIT__", 1)[1].strip()
            commits += 1
            path = "unknown"
        elif line.startswith("+++ b/"):
            path = line[6:].strip()
        elif line.startswith("+") and not line.startswith("+++"):
            found.extend(_match_line(line[1:], commit, path, "history"))
    return found, commits


def scan_worktree() -> list[dict[str, Any]]:
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, check=False).stdout.splitlines()
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                               cwd=ROOT, capture_output=True, text=True,
                               check=False).stdout.splitlines()
    found: list[dict[str, Any]] = []
    # CI checks out archive/v1 into .archive-checkout/ to verify the manifest.
    # Neither that tree nor the pinned data/ tree is main's tracked content.
    excluded_prefixes = ("data/", ".archive-checkout/")
    for relative in sorted(set(tracked) | set(untracked)):
        path = ROOT / relative
        if (relative in EXCLUDED_PATHS or relative.startswith(excluded_prefixes)
                or path.suffix.lower() not in TEXT_SUFFIXES
                or not path.is_file()):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            found.extend(_match_line(line, "working-tree", f"{relative}:{number}",
                                     "worktree"))
    return found


def load_allowlist() -> dict[str, dict[str, Any]]:
    if not ALLOWLIST.is_file():
        return {}
    raw = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    return {entry["fingerprint"]: entry for entry in raw.get("entries", [])}


# An entry is a registration while it is either awaiting rotation or revoked.
# Both are explicit, reviewed states; neither exempts a different commit.
REGISTERED_STATUSES = frozenset({"recorded-pending-rotation", "revoked"})


def _entry_for(item: dict[str, Any],
               allowlist: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Exact fingerprint first, then an explicit commit/path/rule registration.

    The same historical value appears in several diffs, and shallow versus
    full clones can attribute it to different commits.  Registering the exact
    commits keeps that explicit without exempting any new commit.  Revoking a
    credential changes its status, not whether the historical diff is known.
    """
    if item["fingerprint"] in allowlist:
        return allowlist[item["fingerprint"]]
    for entry in allowlist.values():
        if (entry.get("commit") == item["commit"]
                and entry.get("path") == item["path"]
                and entry.get("rule") == item["rule"]
                and entry.get("status") in REGISTERED_STATUSES):
            return entry
    return None


def run(*, report_only: bool = False) -> tuple[int, dict[str, Any]]:
    history, commits = scan_history()
    worktree = scan_worktree()
    allowlist = load_allowlist()
    seen: dict[str, dict[str, Any]] = {}
    for item in history + worktree:
        seen.setdefault(item["fingerprint"], item)
    unknown, acknowledged = [], []
    for item in sorted(seen.values(), key=lambda x: x["fingerprint"]):
        entry = _entry_for(item, allowlist)
        if entry is None:
            unknown.append(item)
        else:
            acknowledged.append({**item, "reason": entry.get("reason"),
                                 "allowlist_status": entry.get("status")})
    result: dict[str, Any] = {
        "status": "passed" if not unknown or report_only else "failed",
        "scanned_commits": commits,
        "total_hits": len(seen),
        "unknown_findings": unknown if report_only else [
            {k: v for k, v in item.items() if k != "redacted"} for item in unknown
        ],
        "acknowledged": [
            {"fingerprint": item["fingerprint"], "commit": item["commit"],
             "path": item["path"], "reason": item["reason"],
             "allowlist_status": item["allowlist_status"]}
            for item in acknowledged
        ],
        "scope": "git --all history (excluding data/) plus tracked and untracked text files",
        "excluded": sorted(EXCLUDED_PATHS),
    }
    return (0 if not unknown or report_only else 1), result
