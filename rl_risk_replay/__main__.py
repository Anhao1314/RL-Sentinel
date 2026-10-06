"""Canonical v2 CLI. Legacy CSVs are audited, never guessed into as-of history."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import cast

from .controlled import experiment
from .engine import ConstantPolicy, Predictor, RuleConfig, RulePolicy, Timeline
from .events import ContractError, Kind, Origin, Scalar, canonical, integer, mapping, number, parse_json
from .legacy_audit import audit_legacy
from .metrics import summarize
from .reporting import render_html, runtime_info
from .storage import EventStore, decode_events, encode_events, publish_bundle, verify_bundle


def rule_config(path: str | None) -> RuleConfig:
    if path is None:
        return RuleConfig()
    obj = mapping(parse_json(Path(path).read_text(encoding="utf-8")))
    defaults = RuleConfig().to_dict()
    if not set(obj) <= set(defaults):
        raise ContractError("unknown policy configuration field")
    defaults.update(obj)
    return RuleConfig(integer(defaults["min_reward_points"], "min_reward_points", 2),
                      number(defaults["drawdown_stop"], "drawdown_stop"),
                      number(defaults["kl_watch"], "kl_watch"),
                      number(defaults["kl_tune"], "kl_tune"),
                      number(defaults["memory_resize"], "memory_resize"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RL Training Reliability v2 (recommendations only)")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create a new append-only event database")
    init.add_argument("--db", required=True)
    init.add_argument("--origin", choices=("observed", "synthetic", "controlled"), default="observed")
    ingest = sub.add_parser("import", help="atomically ingest a complete schema-v2 JSONL stream")
    ingest.add_argument("--events", required=True)
    ingest.add_argument("--db", required=True, help="existing initialized database")
    record = sub.add_parser("record", help="stamp a locally observed event with current availability")
    record.add_argument("--db", required=True)
    record.add_argument("--run-id", required=True)
    record.add_argument("--kind", choices=("start", "sample", "finish", "label"), required=True)
    record.add_argument("--payload", required=True, help="JSON object of allowed payload fields")
    record.add_argument("--event-time", type=float)
    replay = sub.add_parser("replay", help="time-correct replay from a versioned event source")
    source = replay.add_mutually_exclusive_group(required=True)
    source.add_argument("--db")
    source.add_argument("--events")
    replay.add_argument("--out", required=True, help="new output directory; never overwritten")
    replay.add_argument("--policy", choices=("rules", "always-continue", "always-stop", "all"), default="all")
    replay.add_argument("--progress", default="0.3,0.5,0.7")
    replay.add_argument("--evaluation-at", type=float)
    replay.add_argument("--rule-config")
    audit = sub.add_parser("audit-legacy", help="read-only inventory; does not infer missing timestamps")
    audit.add_argument("--dataset", default="data/datasets")
    audit.add_argument("--out", required=True)
    verify = sub.add_parser("verify", help="validate exact bundle membership and SHA-256 digests")
    verify.add_argument("--bundle", required=True)
    control = sub.add_parser("experiment", help="run real tabular Q-learning with controlled fault injection")
    control.add_argument("--out", required=True)
    control.add_argument("--seeds", type=int, default=6)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            store = EventStore.create(Path(args.db), cast(Origin, args.origin))
            result: dict[str, object] = {"database": str(store.path), "origin": args.origin}
        elif args.command == "import":
            events, origin = decode_events(Path(args.events).read_text(encoding="utf-8"))
            count = EventStore(Path(args.db)).append(events, expected_origin=origin)
            result = {"inserted_events": count, "origin": origin}
        elif args.command == "record":
            raw = mapping(parse_json(args.payload))
            payload: dict[str, Scalar] = {}
            for key, value in raw.items():
                if not isinstance(value, (str, int, float, bool, type(None))):
                    raise ContractError("record payload values must be scalar")
                payload[key] = value
            event = EventStore(Path(args.db)).record(args.run_id, cast(Kind, args.kind), payload,
                                                    event_time=args.event_time)
            result = event.to_dict()
        elif args.command == "verify":
            result = verify_bundle(Path(args.bundle))
        elif args.command == "audit-legacy":
            result = audit_legacy(Path(args.dataset))
            result["title"] = "历史数据兼容性审计"
            result["runtime"] = runtime_info()
            publish_bundle(Path(args.out), {"results.json": canonical(result) + "\n",
                                           "report.html": render_html(result)})
        elif args.command == "experiment":
            result = experiment(Path(args.out), args.seeds)
        else:
            config = rule_config(args.rule_config)
            points = tuple(number(float(x), "progress") for x in args.progress.split(","))
            if not points or len(points) != len(set(points)) or any(not 0 < p <= 1 for p in points):
                raise ContractError("progress checkpoints must be distinct and in (0,1]")
            events, origin = (EventStore(Path(args.db)).read() if args.db else
                              decode_events(Path(args.events).read_text(encoding="utf-8")))
            timeline = Timeline(events)
            policies: list[Predictor] = []
            if args.policy in ("rules", "all"):
                policies.append(RulePolicy(config))
            if args.policy in ("always-continue", "all"):
                policies.append(ConstantPolicy(False))
            if args.policy in ("always-stop", "all"):
                policies.append(ConstantPolicy(True))
            summaries: list[dict[str, object]] = []
            decisions: list[dict[str, object]] = []
            for policy in policies:
                for progress in points:
                    rows = timeline.replay(policy, progress, args.evaluation_at)
                    summaries.append({"policy": policy.name, "progress": progress, **summarize(rows, origin)})
                    decisions.extend(r.to_dict() for r in rows)
            result = {"title": "RL 训练时间正确性回放", "origin": origin,
                      "runtime": runtime_info(), "policy_config": config.to_dict(),
                      "evaluation_at": args.evaluation_at,
                      "summaries": summaries, "decisions": decisions}
            publish_bundle(Path(args.out), {"results.json": canonical(result) + "\n",
                                           "events.jsonl": encode_events(events, origin),
                                           "report.html": render_html(result)})
            result = {"output": args.out, "origin": origin, "summaries": summaries}
        print(canonical(result))
        return 0
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        print(f"rl-risk: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
