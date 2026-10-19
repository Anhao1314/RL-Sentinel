"""CLI for the RL-Sentinel research layer.

Examples:

    python -m research build --runs-root /tmp/go2w-fetched \
        --legacy-data data/datasets --out research/data

    python -m research report \
        --dataset research/data/<version> --out research/reports/<name>

    python -m research run --runs-root /tmp/go2w-fetched \
        --legacy-data data/datasets --out research/data \
        --report-out research/reports/<name> --name go2w-research-v1
"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

from research.dataset import DEFAULT_CHECKPOINTS, build_dataset
from research.live import render_live
from research.registry import append_experiment
from research.report import render_report


def _checkpoints(value: str) -> tuple[float, ...]:
    points = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    if not points or any(not 0 < item <= 1 for item in points):
        raise argparse.ArgumentTypeError("checkpoints must be in (0, 1]")
    if len(points) != len(set(points)):
        raise argparse.ArgumentTypeError("checkpoints must be distinct")
    return points


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RL-Sentinel research layer")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_build(p: argparse.ArgumentParser) -> None:
        p.add_argument("--runs-root", required=True,
                       help="Root containing Go2W run directories")
        p.add_argument("--legacy-data", default=None,
                       help="Optional legacy CSV directory (offline cohort)")
        p.add_argument("--out", required=True, help="Research data root")
        p.add_argument("--checkpoints", type=_checkpoints,
                       default=DEFAULT_CHECKPOINTS)

    build = sub.add_parser("build", help="Build a versioned dataset")
    add_build(build)

    report = sub.add_parser("report", help="Render a static research report")
    report.add_argument("--dataset", required=True)
    report.add_argument("--out", required=True)

    live = sub.add_parser("live", help="Render a live training page")
    live.add_argument("--snapshot", required=True)
    live.add_argument("--out", required=True)
    live.add_argument("--report-url", default=None)
    live.add_argument("--refresh", type=int, default=15)

    run = sub.add_parser("run", help="Build, render, and register")
    add_build(run)
    run.add_argument("--report-out", required=True)
    run.add_argument("--name", default="research")
    run.add_argument("--hypothesis", default="")
    run.add_argument("--registry", default="research/registry/experiments.jsonl")

    args = parser.parse_args(argv)
    if args.command == "build":
        manifest = build_dataset(
            pathlib.Path(args.runs_root).expanduser(),
            pathlib.Path(args.out).expanduser(),
            legacy_data=pathlib.Path(args.legacy_data).expanduser()
            if args.legacy_data else None,
            checkpoints=args.checkpoints,
        )
        _print(manifest)
        return 0
    if args.command == "report":
        record = render_report(
            pathlib.Path(args.dataset).expanduser(),
            pathlib.Path(args.out).expanduser(),
        )
        _print(record)
        return 0
    if args.command == "live":
        record = render_live(
            pathlib.Path(args.snapshot).expanduser(),
            pathlib.Path(args.out).expanduser(),
            report_url=args.report_url,
            refresh=args.refresh,
        )
        _print(record)
        return 0
    manifest = build_dataset(
        pathlib.Path(args.runs_root).expanduser(),
        pathlib.Path(args.out).expanduser(),
        legacy_data=pathlib.Path(args.legacy_data).expanduser()
        if args.legacy_data else None,
        checkpoints=args.checkpoints,
    )
    dataset_dir = pathlib.Path(args.out).expanduser() / manifest["dataset_version"]
    report_dir = pathlib.Path(args.report_out).expanduser()
    report_manifest = render_report(dataset_dir, report_dir)
    record = append_experiment(
        pathlib.Path(args.registry).expanduser(),
        name=args.name,
        hypothesis=args.hypothesis,
        dataset_manifest=manifest,
        report_manifest=report_manifest,
        report_dir=report_dir,
    )
    _print({
        "dataset_version": manifest["dataset_version"],
        "dataset_dir": str(dataset_dir),
        "report_dir": str(report_dir),
        "readiness": report_manifest["readiness"],
        "registry_record": record,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
