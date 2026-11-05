"""pyproject.toml is the single authoritative dependency declaration.

The two surviving ``requirements*.txt`` files are CI snapshots, not a second
source of truth.  This gate fails when their package sets drift, and when a
v1-era requirements file reappears on main after the archive.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[<>=!~\[]|$)")


def _names(path: Path) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _NAME.match(line)
        if match:
            names.add(match.group(1).lower().replace("_", "-"))
    return names


def _declared(entries: list[str]) -> set[str]:
    return {m.group(1).lower().replace("_", "-")
            for entry in entries if (m := _NAME.match(entry))}


def run() -> dict[str, Any]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    runtime = _declared(project.get("dependencies", []))
    dev = _declared(project.get("optional-dependencies", {}).get("dev", []))
    research_file = ROOT / "requirements-research.txt"
    core_dev_file = ROOT / "requirements-core-dev.txt"
    research = _names(research_file)
    core_dev = _names(core_dev_file)
    revived = [name for name in ("requirements.txt", "requirements-dev.txt")
               if (ROOT / name).exists()]
    checks = {
        "research_snapshot_matches_runtime_dependencies": research == runtime,
        "core_dev_snapshot_is_subset_of_dev_extra": core_dev <= dev,
        "runtime_dependencies_are_not_empty": bool(runtime),
        "v1_requirements_stay_archived": not revived,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "runtime_dependencies": sorted(runtime),
        "research_snapshot": sorted(research),
        "core_dev_snapshot": sorted(core_dev),
        "dev_extra": sorted(dev),
        "revived_v1_files": revived,
        "note": "Only package names are compared: pyproject uses lower bounds while CI snapshots pin exact versions on purpose.",
    }
