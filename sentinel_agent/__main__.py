"""Sentinel agent CLI: watch events, run the hang demo, or self-test.

Subcommands mirror the strict-core CLI conventions: canonical JSON on stdout,
exit code 2 with a ``sentinel-agent:`` prefixed message on contract errors.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import cast

from rl_risk_replay.events import canonical
from rl_risk_replay.policy import rules_manifest
from rl_risk_replay.reporting import bundle_provenance
from rl_risk_replay.storage import publish_bundle

from .agent import Agent, AgentConfig, replay_file
from .hang_demo import run_demo
from .producer import SentinelProducer

_SELFTEST_BASE = 1_700_000_000.0


class _VirtualClock:
    """Deterministic clock for the self-test; no sleeps, CI-safe."""

    def __init__(self, start: float) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _write_hung_run(path: Path, clock: _VirtualClock, *, stale_after: float) -> int:
    """Write a run that goes silent well beyond ``stale_after`` and never finishes."""
    producer = SentinelProducer(out_path=path, run_id="selftest-hang", task="hang-demo",
                                seed="selftest", planned_steps=1000, clock=clock,
                                fsync_every=64)
    try:
        producer.start()
        for step in range(1, 901):
            producer.sample(step, reward=round(0.3 + 0.1 * step, 6),
                            approx_kl=0.012, progress=round(step / 1000, 6))
            clock.advance(1)  # distinct event times: keeps the stream replay-valid
        clock.advance(stale_after * 4)  # writer deadlocks here; nothing more arrives
    finally:
        producer.close()
    return producer.written


def _write_cancelled_and_resume(preempt_path: Path, resume_path: Path,
                                clock: _VirtualClock) -> int:
    """A cancelled attempt and its retry: same (task, seed), new run_ids.

    The frozen contract already forbids a second start per run ("retries need
    a new run_id"), so preemption/resume needs no schema change — the agent
    derives the lineage from start ordering.
    """
    written = 0
    preempt = SentinelProducer(out_path=preempt_path, run_id="selftest-preempt",
                               task="hang-demo", seed="resume", planned_steps=1000,
                               clock=clock)
    try:
        preempt.start()
        for step in range(1, 11):
            preempt.sample(step, reward=round(0.4 + 0.05 * step, 6), approx_kl=0.012)
        clock.advance(1)
        preempt.finish("cancelled")
    finally:
        preempt.close()
    written += preempt.written
    resume = SentinelProducer(out_path=resume_path, run_id="selftest-resume",
                              task="hang-demo", seed="resume", planned_steps=1000,
                              clock=clock)
    try:
        resume.start()
        clock.advance(1)
        resume.finish("completed")
    finally:
        resume.close()
    return written + resume.written


def selftest(out: Path) -> dict[str, object]:
    """Prove the staleness trap detection end-to-end and publish it as evidence.

    The run's last samples look healthy; the producer then goes silent.  The
    agent must alert on data availability alone — without a finish event,
    without a metric threshold crossing — and must stay quiet once the run
    finishes.  Deterministic: a virtual clock, no sleeps, no network.
    """
    import tempfile

    from rl_risk_replay.events import Event

    config = AgentConfig(stale_after=60.0, listen_host="127.0.0.1", listen_port=0)
    with tempfile.TemporaryDirectory(prefix="sentinel-selftest-") as tmp:
        tmp_path = Path(tmp)
        events_path = tmp_path / "events.jsonl"
        clock = _VirtualClock(_SELFTEST_BASE)
        events = _write_hung_run(events_path, clock, stale_after=config.stale_after)

        agent = Agent(config, clock=clock, alert_sink=lambda alert: None)
        replay_file(agent, events_path)
        assert agent.evaluate() == []  # silence window not yet exceeded
        clock.advance(config.stale_after + 1)
        alerts = agent.evaluate()

        # A late-arriving sample clears the incident; the next silence window
        # may re-alert, so the total must stay 1 across the rest of the test.
        clock.advance(10)
        late_sample = Event.from_dict({
            "schema_version": 2, "event_id": "late-sample-1", "run_id": "selftest-hang",
            "kind": "sample", "event_time": clock(), "available_at": clock(),
            "payload": {"step": 11, "reward": 1.4, "approx_kl": 0.012},
        })
        agent.ingest(late_sample.to_dict())
        assert agent.evaluate() == []

        finished = Event.from_dict({
            "schema_version": 2, "event_id": "selftest-finish-1", "run_id": "selftest-hang",
            "kind": "finish", "event_time": clock(), "available_at": clock(),
            "payload": {"status": "failed"},
        })
        agent.ingest(finished.to_dict())
        assert agent.evaluate() == []  # finished runs never alert

        # Resilience chain: a cancelled attempt and its resume, same (task, seed).
        preempt_path = tmp_path / "preempt.jsonl"
        resume_path = tmp_path / "resume.jsonl"
        _write_cancelled_and_resume(preempt_path, resume_path, clock)
        replay_file(agent, preempt_path)
        replay_file(agent, resume_path)
        state = agent.snapshot()
        runs_view = cast("dict[str, dict[str, object]]", state["runs"])
        resume = runs_view["selftest-resume"]
        resilience: dict[str, object] = {
            "preemptions_total": agent.counters()["preemptions"],
            "steps_lost_total": agent.counters()["steps_lost"],
            "resumed_attempt": resume["attempt"],
            "resumed_after_cancel": bool(resume["resumed_after_cancelled"]),
        }
        assert agent.evaluate() == []  # cancelled and resumed runs are finished: no alerts

        # Bridge: the SAME stream, now analyzed by the frozen core (origin
        # declared by the caller).  The checkpoints are observable, the rule
        # policy decides "continue" on the healthy ramp, and readiness stays
        # blocked — one labeled run is not a statistical conclusion.
        analysis_bundle = tmp_path / "replay-bundle"
        from .bridge import analyze

        analysis = analyze([events_path], "synthetic", analysis_bundle)
        summaries = cast("list[dict[str, object]]", analysis["summaries"])
        analysis_doc: dict[str, object] = {
            "origin": analysis["origin"],
            "decisions": analysis["decisions"],
            "readiness": summaries[0]["readiness"],
        }

        counters = agent.counters()
        result: dict[str, object] = {
            "title": "sentinel agent staleness self-test",
            "agent": {
                "staleness_alert_fired": len(alerts) == 1
                and alerts[0]["type"] == "producer_silence",
                "alerts_total": counters["silence_alerts"],
                "alert_message_first": (alerts[0]["message"] if alerts else None),
                "active_runs_after_finish": sum(
                    1 for run in agent.runs().values() if run.started and not run.finished),
                "ingested": counters["ingested"],
                "duplicates": counters["duplicates"],
                "invalid": counters["invalid"],
            },
            "producer": {"events_written": events, "schema_version": 2,
                         "virtual_clock_base": _SELFTEST_BASE},
            "resilience": resilience,
            "analysis": analysis_doc,
        }
        stream = events_path.read_bytes()
        publish_bundle(out, {"results.json": canonical(result) + "\n"},
                       provenance=bundle_provenance(
                           input_sha256=hashlib.sha256(stream).hexdigest(),
                           event_count=counters["ingested"],
                           rule_registry=rules_manifest()))
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="RL Sentinel staleness-aware watchdog (recommendations only)")
    sub = parser.add_subparsers(dest="command", required=True)
    watch = sub.add_parser("watch", help="tail an events file and serve metrics/alerts")
    watch.add_argument("--events", required=True)
    watch.add_argument("--host", default="127.0.0.1")
    watch.add_argument("--port", type=int, default=9101)
    watch.add_argument("--stale-after", type=float, default=120.0)
    watch.add_argument("--poll-interval", type=float, default=1.0)
    watch.add_argument("--webhook-url", default=None,
                       help="optional Feishu-style webhook; alerts notify a human, "
                            "no training process is ever touched")
    demo = sub.add_parser("demo", help="run the hang demo trainer (writes events, then silences)")
    demo.add_argument("--out", required=True)
    demo.add_argument("--samples", type=int, default=40)
    demo.add_argument("--interval", type=float, default=1.0)
    demo.add_argument("--complete", action="store_true",
                      help="finish normally instead of hanging")
    export = sub.add_parser("export",
                            help="dedupe raw producer JSONL into a replay-ready versioned stream")
    export.add_argument("--events", action="append", required=True,
                        help="raw events file; repeatable (multi-attempt runs)")
    export.add_argument("--origin", required=True, choices=("observed", "synthetic", "controlled"),
                        help="provenance is an explicit human claim, not a file default")
    export.add_argument("--out", required=True, help="versioned stream path (never overwritten)")
    analyze = sub.add_parser("analyze", help="export + frozen-core as-of replay in one command")
    analyze.add_argument("--events", action="append", required=True)
    analyze.add_argument("--origin", required=True, choices=("observed", "synthetic", "controlled"))
    analyze.add_argument("--out", required=True, help="new evidence-bundle directory")
    analyze.add_argument("--policy", choices=("rules", "always-continue", "always-stop", "all"),
                         default="all")
    analyze.add_argument("--progress", default="0.3,0.5,0.7")
    test = sub.add_parser("selftest", help="deterministic staleness-detection evidence bundle")
    test.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "watch":
            from .agent import serve

            serve(AgentConfig(stale_after=args.stale_after,
                              poll_interval=args.poll_interval,
                              listen_host=args.host, listen_port=args.port,
                              webhook_url=args.webhook_url),
                  events_path=Path(args.events))
            result: dict[str, object] = {"status": "stopped"}
        elif args.command == "demo":
            result = run_demo(Path(args.out), samples=args.samples, interval=args.interval,
                              hang=not args.complete)
        elif args.command == "export":
            from .bridge import export_stream

            result = export_stream([Path(p) for p in args.events], args.origin, Path(args.out))
        elif args.command == "analyze":
            from .bridge import analyze

            result = analyze([Path(p) for p in args.events], args.origin, Path(args.out),
                             policy=args.policy, progress=args.progress)
        else:
            result = selftest(Path(args.out))
        print(canonical(result))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"sentinel-agent: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
