"""Staleness-aware watchdog agent.

Two questions, deliberately separated:

* *What does the data say?*  — rule metrics from the last known sample.
* *Is the data still alive?* — wall-clock silence of the producer.

Most dashboards conflate the two: a hung trainer leaves its last metrics in
place, and naive alerting keeps reading "all healthy".  The agent alerts on
producer silence for active runs, using the agent's own ingest clock, and
exposes both signals to Prometheus.  It never touches the training process:
alerts notify a human (opt-in webhook), decisions stay recommendation-only.

Ingest is at-least-once with ``event_id`` dedup, and agent state is derived
by replaying the event stream, so a crashed agent rebuilds exactly.  Events
that fail the frozen contract are counted and dropped, never guessed into
the watchdog state.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from rl_risk_replay.events import ContractError, Event, canonical

from . import __version__

Alert = dict[str, Any]
AlertSink = Callable[[Alert], None]

_METRIC_KEYS = ("reward", "approx_kl", "value_loss", "memory_percent",
                "progress", "success_rate", "segment")


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError("expected an integer")
    return value


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("expected a number")
    return float(value)


@dataclass(frozen=True)
class AgentConfig:
    """Watchdog knobs.  ``stale_after`` is the producer-silence threshold."""

    stale_after: float = 120.0
    poll_interval: float = 1.0
    listen_host: str = "127.0.0.1"
    listen_port: int = 9101
    webhook_url: str | None = None

    def __post_init__(self) -> None:
        for name in ("stale_after", "poll_interval"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive number")
        if not self.listen_host or not 0 <= self.listen_port <= 65535:
            raise ValueError("invalid listen address")


@dataclass
class RunState:
    """Mutable per-run view; rebuilt by replay, never persisted."""

    run_id: str
    task: str | None = None
    seed: str | None = None
    started: bool = False
    finished: bool = False
    last_event_wall: float = 0.0
    last_available_at: float = 0.0
    last_step: int | None = None
    last_metrics: dict[str, float] = field(default_factory=dict[str, float])
    verdict: str | None = None
    events: int = 0
    alerted_silence: bool = False
    # Resume lineage, derived from (task, seed) starts — no schema change.
    attempt: int = 0
    resumed_after_cancelled: bool = False


@dataclass
class _Lineage:
    """Observed-start ordinal and cancel flag per (task, seed)."""

    starts: int = 0
    last_cancelled: bool = False


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def webhook_sink(url: str, *, timeout: float = 10.0) -> AlertSink:
    """Deliver alerts as Feishu-style text messages.  Delivery failures are
    counted by the caller and never propagate into the ingest/monitor loop."""

    def deliver(alert: Alert) -> None:
        body = json.dumps({"msg_type": "text",
                           "content": {"text": str(alert.get("message", ""))}}).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()

    return deliver


class Agent:
    """Contract-gated event ingest, silence detection, and metric exposition."""

    def __init__(self, config: AgentConfig, *, clock: Callable[[], float] = time.time,
                 alert_sink: AlertSink | None = None) -> None:
        self.config = config
        self._now = clock
        self._sink = alert_sink
        self._seen: set[str] = set()
        self._runs: dict[str, RunState] = {}
        self._counters: dict[str, int] = {
            "ingested": 0, "duplicates": 0, "invalid": 0,
            "silence_alerts": 0, "alert_failures": 0,
            "preemptions": 0, "steps_lost": 0,
        }
        self._lineage: dict[tuple[str, str], _Lineage] = {}
        self.alerts: deque[Alert] = deque(maxlen=100)

    # -- ingest --------------------------------------------------------------
    def ingest(self, raw: object) -> str:
        """Validate and absorb one event.  Returns ok / duplicate / invalid."""
        try:
            event = Event.from_dict(raw)
        except (ValueError, TypeError):
            self._counters["invalid"] += 1
            return "invalid"
        if event.event_id in self._seen:
            self._counters["duplicates"] += 1
            return "duplicate"
        self._seen.add(event.event_id)
        run = self._runs.setdefault(event.run_id, RunState(event.run_id))
        now = self._now()
        run.events += 1
        run.last_event_wall = now
        run.last_available_at = max(run.last_available_at, event.available_at)
        payload = event.payload()
        if event.kind == "start":
            run.started = True
            run.task = str(payload["task"])
            run.seed = str(payload["seed"])
            entry = self._lineage.setdefault((run.task, run.seed), _Lineage())
            run.attempt = entry.starts + 1
            run.resumed_after_cancelled = entry.last_cancelled
            entry.starts = run.attempt
            entry.last_cancelled = False
        elif event.kind == "sample":
            run.last_step = _as_int(payload["step"])
            run.last_metrics = {key: _as_float(payload[key]) for key in _METRIC_KEYS
                                if key in payload}
        elif event.kind == "finish":
            run.finished = True
            if payload["status"] == "cancelled":
                self._counters["preemptions"] += 1
                self._counters["steps_lost"] += run.last_step or 0
                if run.task is not None and run.seed is not None:
                    self._lineage.setdefault((run.task, run.seed),
                                             _Lineage()).last_cancelled = True
        elif event.kind == "label":
            run.verdict = str(payload["verdict"])
        # A producer that resumes sending clears the silence incident; the
        # next silence window can alert again.
        run.alerted_silence = False
        self._counters["ingested"] += 1
        return "ok"

    def ingest_line(self, line: str) -> str:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            self._counters["invalid"] += 1
            return "invalid"
        return self.ingest(raw)

    # -- silence detection -----------------------------------------------------
    def evaluate(self) -> list[Alert]:
        """Fire one alert per active run entering producer silence."""
        fired: list[Alert] = []
        now = self._now()
        for run in self._runs.values():
            if not run.started or run.finished or run.alerted_silence:
                continue
            stale = now - run.last_event_wall
            if stale <= self.config.stale_after:
                continue
            alert: Alert = {
                "type": "producer_silence", "run_id": run.run_id, "task": run.task,
                "stale_seconds": round(stale, 3), "stale_after": self.config.stale_after,
                "last_step": run.last_step, "last_metrics": dict(run.last_metrics),
                "message": (
                    f"[RL-Sentinel] producer silence: run={run.run_id} task={run.task} "
                    f"has emitted nothing for {stale:.0f}s (threshold "
                    f"{self.config.stale_after:.0f}s). Last sample step={run.last_step} "
                    f"metrics={run.last_metrics} may still look healthy — that is the "
                    "staleness trap. Verify the trainer process; no automatic action "
                    "was taken."),
            }
            run.alerted_silence = True
            self._counters["silence_alerts"] += 1
            self.alerts.append(alert)
            fired.append(alert)
        for alert in fired:
            if self._sink is not None:
                try:
                    self._sink(alert)
                except (OSError, ValueError, urllib.error.URLError):
                    self._counters["alert_failures"] += 1
        return fired

    # -- views -----------------------------------------------------------------
    def runs(self) -> dict[str, RunState]:
        return dict(self._runs)

    def counters(self) -> dict[str, int]:
        return dict(self._counters)

    def snapshot(self) -> dict[str, object]:
        now = self._now()
        return {
            "version": __version__,
            "stale_after": self.config.stale_after,
            "counters": self.counters(),
            "alerts": list(self.alerts),
            "runs": {run_id: {"task": run.task, "seed": run.seed,
                              "started": run.started,
                              "finished": run.finished, "events": run.events,
                              "last_step": run.last_step,
                              "last_metrics": run.last_metrics,
                              "verdict": run.verdict,
                              "attempt": run.attempt,
                              "resumed_after_cancelled": run.resumed_after_cancelled,
                              "staleness_seconds": round(now - run.last_event_wall, 3)
                              if run.events else None}
                     for run_id, run in self._runs.items()},
        }

    def metrics_text(self) -> str:
        """Prometheus text exposition.  Absent metrics are omitted, never faked."""
        now = self._now()
        states = sorted(self._runs.items())
        metric_names = sorted({key for _, run in states for key in run.last_metrics})
        lines: list[str] = [
            "# HELP sentinel_agent_info Agent build information.",
            "# TYPE sentinel_agent_info gauge",
            f'sentinel_agent_info{{version="{_escape_label(__version__)}"}} 1',
            "# HELP sentinel_events_ingested_total Contract-valid events absorbed.",
            "# TYPE sentinel_events_ingested_total counter",
            f"sentinel_events_ingested_total {self._counters['ingested']}",
            "# HELP sentinel_events_duplicate_total Events deduplicated by event_id.",
            "# TYPE sentinel_events_duplicate_total counter",
            f"sentinel_events_duplicate_total {self._counters['duplicates']}",
            "# HELP sentinel_events_invalid_total Events rejected by the frozen contract.",
            "# TYPE sentinel_events_invalid_total counter",
            f"sentinel_events_invalid_total {self._counters['invalid']}",
            "# HELP sentinel_alerts_silence_total Producer-silence alerts fired.",
            "# TYPE sentinel_alerts_silence_total counter",
            f"sentinel_alerts_silence_total {self._counters['silence_alerts']}",
            "# HELP sentinel_alert_failures_total Webhook delivery failures.",
            "# TYPE sentinel_alert_failures_total counter",
            f"sentinel_alert_failures_total {self._counters['alert_failures']}",
            "# HELP sentinel_preemptions_total Cancelled finishes observed (preemptions).",
            "# TYPE sentinel_preemptions_total counter",
            f"sentinel_preemptions_total {self._counters['preemptions']}",
            "# HELP sentinel_steps_lost_total Last steps of cancelled runs; a floor on wasted compute.",
            "# TYPE sentinel_steps_lost_total counter",
            f"sentinel_steps_lost_total {self._counters['steps_lost']}",
            "# HELP sentinel_run_staleness_seconds Seconds since the run last emitted any event.",
            "# TYPE sentinel_run_staleness_seconds gauge",
            "# HELP sentinel_run_data_age_seconds Seconds since the newest available_at.",
            "# TYPE sentinel_run_data_age_seconds gauge",
            "# HELP sentinel_run_finished Whether a finish event was observed.",
            "# TYPE sentinel_run_finished gauge",
            "# HELP sentinel_run_attempt Observed-start ordinal for the run's (task, seed).",
            "# TYPE sentinel_run_attempt gauge",
        ]
        for name in metric_names:
            lines.append(f"# HELP sentinel_run_{name} Last observed {name} of the run.")
            lines.append(f"# TYPE sentinel_run_{name} gauge")
        active = 0
        for run_id, run in states:
            labels = (f'{{run_id="{_escape_label(run_id)}",'
                      f'task="{_escape_label(run.task or "")}"}}')
            lines.append(f"sentinel_run_staleness_seconds{labels} "
                         f"{max(0.0, now - run.last_event_wall):.3f}")
            lines.append(f"sentinel_run_data_age_seconds{labels} "
                         f"{max(0.0, now - run.last_available_at):.3f}")
            lines.append(f"sentinel_run_finished{labels} {1 if run.finished else 0}")
            lines.append(f"sentinel_run_attempt{labels} {run.attempt}")
            if run.last_step is not None:
                lines.append(f"sentinel_run_last_step{labels} {run.last_step}")
            for key, value in run.last_metrics.items():
                lines.append(f"sentinel_run_{key}{labels} {value!r}")
            if run.started and not run.finished:
                active += 1
        lines.append("# HELP sentinel_runs_active Runs started and not finished.")
        lines.append("# TYPE sentinel_runs_active gauge")
        lines.append(f"sentinel_runs_active {active}")
        return "\n".join(lines) + "\n"


def iter_complete_lines(buffer: str) -> tuple[list[str], str]:
    """Split a decoded chunk into complete lines; keep a trailing partial line.

    A crashed writer may leave a half-written final line; tailing must not
    ingest or permanently drop it — it is retried once the rest arrives (or
    simply never completes, which is the honest outcome of a dead producer).
    """
    if "\n" not in buffer:
        return [], buffer
    head, _, tail = buffer.rpartition("\n")
    return head.split("\n"), tail


def _split_bytes(buffer: bytes) -> tuple[list[bytes], bytes]:
    if b"\n" not in buffer:
        return [], buffer
    head, _, tail = buffer.rpartition(b"\n")
    return head.split(b"\n"), tail


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], agent: Agent) -> None:
        self.agent = agent
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str = "", *args: object) -> None:
        pass  # keep daemon logs quiet

    @property
    def agent(self) -> Agent:
        """Narrow the bound server: this handler only ever serves AgentHTTPServer."""
        return cast(AgentHTTPServer, self.server).agent

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        agent = self.agent
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            from .console import console_html

            self._respond(200, "text/html; charset=utf-8",
                          console_html(__version__).encode("utf-8"))
        elif path == "/stream":
            self._stream_sse(agent)
        elif path == "/metrics":
            self._respond(200, "text/plain; charset=utf-8",
                          agent.metrics_text().encode("utf-8"))
        elif path == "/healthz":
            self._respond(200, "text/plain; charset=utf-8", b"ok")
        elif path == "/state":
            self._respond(200, "application/json",
                          canonical(agent.snapshot()).encode("utf-8"))
        else:
            self._respond(404, "text/plain; charset=utf-8", b"not found")

    def _stream_sse(self, agent: Agent) -> None:
        """Server-Sent Events: one full snapshot per poll interval.

        The page is a single static template, so nothing user-controlled is
        ever embedded in HTML; run data reaches the browser as JSON and is
        inserted via textContent.  A dead viewer is noticed by the next
        failed write and the handler exits quietly.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                self.wfile.write(b"data: "
                                 + canonical(agent.snapshot()).encode("utf-8") + b"\n\n")
                self.wfile.flush()
                time.sleep(agent.config.poll_interval)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_POST(self) -> None:
        agent = self.agent
        if self.path.split("?", 1)[0] != "/events":
            self._respond(404, "text/plain; charset=utf-8", b"not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._respond(400, "application/json", b'{"error": "bad content-length"}')
            return
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        results = {"ok": 0, "duplicate": 0, "invalid": 0}
        for line in body.splitlines():
            if line.strip():
                results[agent.ingest_line(line)] += 1
        self._respond(200, "application/json",
                      canonical({"status": "accepted", **results}).encode("utf-8"))


def replay_file(agent: Agent, events_path: Path) -> int:
    """Idempotently replay an events file into the agent (dedup makes this safe)."""
    try:
        raw = events_path.read_bytes()
    except FileNotFoundError:
        return 0
    count = 0
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if line.strip():
            agent.ingest_line(line)
            count += 1
    return count


def monitor_forever(agent: Agent, events_path: Path | None, stop: threading.Event,
                    poll_interval: float) -> None:
    """Follow the events file (if any) and evaluate silence every interval.

    Tailing tolerates late file creation and truncation/rotation; dedup
    absorbs whatever a reread replays.  A trailing partial line from a
    crashed writer is never ingested and never dropped — it is retried.
    """
    offset = 0
    pending = b""
    while not stop.is_set():
        stop.wait(poll_interval)
        if events_path is not None:
            try:
                size = events_path.stat().st_size
                if size < offset:  # truncated/rotated: honest reread
                    offset, pending = 0, b""
                if size > offset:
                    with events_path.open("rb") as handle:
                        handle.seek(offset)
                        pending += handle.read()
                    offset = size
            except (FileNotFoundError, OSError):
                pass
            lines, pending = _split_bytes(pending)
            for line in lines:
                if line.strip():
                    agent.ingest_line(line.decode("utf-8", errors="replace"))
        agent.evaluate()


def serve(config: AgentConfig, events_path: Path | None = None) -> None:
    """Blocking entrypoint: replay history, monitor the file, serve HTTP."""
    agent = Agent(config, alert_sink=webhook_sink(config.webhook_url)
                  if config.webhook_url else None)
    if events_path is not None:
        replay_file(agent, events_path)
        agent.evaluate()
    server = AgentHTTPServer((config.listen_host, config.listen_port), agent)
    stop = threading.Event()
    worker = threading.Thread(target=monitor_forever,
                              args=(agent, events_path, stop, config.poll_interval),
                              daemon=True)
    worker.start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        server.server_close()
