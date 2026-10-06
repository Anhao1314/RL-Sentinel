"""Check maintained documentation and execute its two quick-start commands.

Only the fixed, allowlisted experiment/verify commands run, in a temporary
working directory. Historical reports, datasets and runtime code are read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("README.md", "README.zh-CN.md", "docs/README.md",
        "docs/GETTING_STARTED.md", "docs/GETTING_STARTED.zh-CN.md")
ASSETS = ("sentinel-hero-light.svg", "sentinel-hero-dark.svg",
          "replay-pipeline-architecture.svg", "replay-pipeline-mobile.svg",
          "social-preview.svg", "sentinel-mark.svg")
OLD_URL = "github.com/Anhao1314/rl-training-risk-replay"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def anchors(text: str) -> set[str]:
    ids = set(re.findall(r'\bid="([^"]+)"', text))
    for heading in re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE):
        heading = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        ids.add(heading)
    return ids


def check_links(relative: str, text: str) -> int:
    clean = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    targets = re.findall(r"\[[^\]]*\]\(([^)]+)\)", clean)
    targets += re.findall(r'(?:href|src|srcset)="([^"]+)"', clean)
    count = 0
    for value in targets:
        url = urlsplit(html.unescape(value))
        if url.scheme or url.netloc:
            require(url.scheme in ("http", "https"), f"unsupported URL: {value}")
            continue
        path = (ROOT / relative).parent / unquote(url.path) if url.path else ROOT / relative
        path = path.resolve()
        require(path.is_relative_to(ROOT), f"path leaves repository: {value}")
        require(path.exists(), f"missing target in {relative}: {value}")
        if url.fragment:
            require(path.is_file(), f"anchor targets directory: {value}")
            require(unquote(url.fragment) in anchors(path.read_text(encoding="utf-8")),
                    f"missing anchor in {relative}: {value}")
        count += 1
    for tag in re.findall(r"<img\b[^>]*>", clean):
        require(bool(re.search(r'\balt="[^"]+"', tag)), f"missing image alt in {relative}")
    return count


def run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                               text=True, encoding="utf-8", timeout=120, check=False)
    require(completed.returncode == 0,
            f"command failed ({completed.returncode}): {args}\n{completed.stderr[-3000:]}")
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="artifacts/documentation-validation")
    args = parser.parse_args()
    links = 0
    hashes: dict[str, str] = {}
    for relative in DOCS:
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        require(not text.startswith("\ufeff"), f"unexpected BOM: {relative}")
        require("RL Sentinel" in text, f"missing brand: {relative}")
        require(OLD_URL not in text, f"obsolete active URL: {relative}")
        links += check_links(relative, text)
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ASSETS:
        path = ROOT / "docs/assets" / name
        raw = path.read_text(encoding="utf-8")
        svg = ET.fromstring(raw)
        require(svg.tag.endswith("svg"), f"invalid SVG root: {name}")
        require("viewBox" in svg.attrib, f"missing viewBox: {name}")
        require(any(el.tag.endswith("title") for el in svg.iter()), f"missing title: {name}")
        for el in svg.iter():
            require(el.tag.rsplit("}", 1)[-1] not in ("script", "foreignObject"),
                    f"active SVG content: {name}")
            require(not any(k.lower().startswith("on") for k in el.attrib),
                    f"SVG event handler: {name}")
            for key, value in el.attrib.items():
                if key.rsplit("}", 1)[-1] in ("href", "src"):
                    require(value.startswith("#"), f"nonlocal SVG dependency: {name}")
        require(OLD_URL not in raw, f"obsolete asset URL: {name}")
        hashes[f"docs/assets/{name}"] = hashlib.sha256(path.read_bytes()).hexdigest()

    starts: list[list[str]] = []
    for relative in DOCS[:2]:
        text = (ROOT / relative).read_text(encoding="utf-8")
        marked = text.split("<!-- sentinel:quickstart -->", 1)[1].split("<!-- /sentinel:quickstart -->", 1)[0]
        commands = re.search(r"```bash\n(.*?)\n```", marked, re.DOTALL)
        require(commands is not None, f"missing quickstart block: {relative}")
        assert commands is not None
        starts.append(commands.group(1).splitlines())
    require(starts[0] == starts[1], "English and Chinese quick starts differ")
    expected = ["git clone https://github.com/Anhao1314/RL-Sentinel.git", "cd RL-Sentinel",
                "python -m rl_risk_replay experiment --out artifacts/sentinel-demo",
                "python -m rl_risk_replay verify --bundle artifacts/sentinel-demo"]
    require(starts[0] == expected, "quickstart requires review before execution")
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8="1")
    with tempfile.TemporaryDirectory(prefix="sentinel-docs-") as tmp:
        cwd = Path(tmp)
        for command in starts[0][2:]:
            argv = shlex.split(command)
            run([sys.executable, *argv[1:]], cwd, env)
        bundle = cwd / "artifacts/sentinel-demo"
        members = sorted(p.name for p in bundle.iterdir())
        require(members == sorted(("events.jsonl", "manifest.json", "protocol.json",
                                   "q_tables.json", "report.html", "results.json")),
                "documented experiment output members differ")
        result = json.loads((bundle / "results.json").read_text(encoding="utf-8"))
        require(result.get("origin") == "controlled", "demo must remain controlled")
        require(all(s["readiness"] == "blocked" for s in result["summaries"]),
                "controlled demo must not claim operational readiness")
        run([sys.executable, "-m", "rl_risk_replay", "replay", "--events",
             str(bundle / "events.jsonl"), "--out", "artifacts/frozen-review",
             "--policy", "all", "--progress", "0.3,0.5,0.7"], cwd, env)
        run([sys.executable, "-m", "rl_risk_replay", "verify", "--bundle",
             "artifacts/frozen-review"], cwd, env)
    report = {"status": "passed", "documents": len(DOCS), "relative_links_checked": links,
              "svg_assets": len(ASSETS), "quickstart_languages_match": True,
              "runtime_commands_passed": 4, "experiment_members": members,
              "scope": "local links, SVG structure, quickstart and frozen replay; not visual rendering or external URL availability",
              "sha256": hashes}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, IndexError, ET.ParseError, subprocess.TimeoutExpired) as exc:
        print(f"documentation check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
