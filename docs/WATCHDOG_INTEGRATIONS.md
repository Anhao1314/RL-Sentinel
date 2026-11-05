# Watchdog integrations

How to wire real training loops into the staleness-aware watchdog without
touching the training code's behavior.  Every path produces schema-v2 events
that the frozen contract validates; the agent then watches the stream.

```text
training loop ──► producer SDK / adapter ──► events.jsonl ──► sentinel_agent watch
                                                    (or POST /events)   │
                                                       Prometheus /metrics, alerts
```

The watchdog is **observation-only**: adapters never abort a run, alerts
notify a human, nothing stops training.

## Generic training loop (any framework, no adapter)

```python
from sentinel_agent import SentinelProducer

producer = SentinelProducer.from_env(out_path="events.jsonl", task="go2w_flat",
                                     seed="42", planned_steps=1_000_000)
producer.start()
for step, metrics in training_loop():          # your loop, unmodified
    producer.sample(step, reward=metrics["rew_mean"], approx_kl=metrics["kl"])
producer.finish("completed")
producer.label("pass")                          # only after finish
```

Availability semantics live at the call site: write-time telemetry uses the
default; lagging signals (evaluation jobs, offline labels) declare their real
delay with `defer_seconds` instead of faking immediacy.

## Stable-Baselines3

```python
from sentinel_agent.integrations import sentinel_callback

callback = sentinel_callback(out_path="events.jsonl", task="go2w_flat", seed="42",
                             planned_steps=2_000_000)
model.learn(total_timesteps=2_000_000, callback=callback)
```

The callback maps SB3 logger keys to sample metrics
(`rollout/ep_rew_mean → reward`, `train/approx_kl → approx_kl`,
`train/value_loss → value_loss` by default; override with `metric_map`) and
emits every `emit_every_steps` (default 2048).  It returns `True` from
`_on_step` on every call.  On a crash, SB3 skips `_on_training_end` — call
`callback.mark_failed()` from your handler, or let the producer-silence
alert catch it.

## Lightning

```python
from sentinel_agent.integrations import sentinel_lightning_callback

trainer = Trainer(max_steps=100_000, callbacks=[
    sentinel_lightning_callback(out_path="events.jsonl", task="go2w_flat",
                                seed="42", planned_steps=100_000),
])
```

Works with `lightning` and `pytorch_lightning`.  `on_exception` reports a
failed finish; repeat steps and non-finite values are skipped, never faked.

## Multi-process training (torchrun / DDP)

`SentinelProducer.from_env` reads `WORLD_SIZE` / `RANK` and encodes the rank
into `run_id` (`go2w_flat-42-r3`), so each rank is a separate contract-valid
run without extending the frozen schema.  Each rank writes its own
`events.jsonl` (appending to one shared file from multiple writers is not
line-atomic and is deliberately unsupported), and each file gets its own
watchdog on a distinct port — Prometheus scrapes them all:

```yaml
scrape_configs:
  - job_name: sentinel-agent
    static_configs:
      - targets: ["agent-r0:9101", "agent-r1:9101", "agent-r2:9101", "agent-r3:9101"]
```

Alternatively, push events over HTTP to a single agent (idempotent by
`event_id`): `POST /events` with JSONL lines.

## Agent side

```bash
python -m sentinel_agent watch --events events.jsonl --stale-after 120 \
    --webhook-url "$SENTINEL_WEBHOOK"   # optional; notifies a human only
```

Open the agent's root path (`/`) for the built-in operator console: live
staleness/reward charts, the per-run lineage table and the alert feed, pushed
over Server-Sent Events from a single static HTML file (no build step, no
framework). Grafana (see `deploy/`) remains the long-range analytics surface.

One full-stack demo (trainer that hangs + agent + Prometheus + Grafana) is
one command away: `docker compose -f deploy/docker-compose.yml up --build`.

## Preemption and resume (no schema change)

Platform kills arrive as signals; SIGKILL cannot be intercepted at all. The
watchdog layer turns what *can* be intercepted into evidence:

```python
from sentinel_agent.resilience import preemption_guard

with preemption_guard(producer):     # SIGTERM/SIGINT -> finish("cancelled") + fsync
    run_training(producer)           # then the platform's kill proceeds as usual
```

A retried attempt uses a new `run_id` (the frozen contract already requires
this), and the agent derives the lineage from `(task, seed)` start ordering —
no new event kinds, no payload extension:

- `attempt` — observed-start ordinal for the run's `(task, seed)`;
- `resumed_after_cancelled` — the previous attempt ended cancelled;
- `sentinel_preemptions_total` / `sentinel_steps_lost_total` — a floor on
  wasted compute (each cancelled run's last observed step).

Independent reruns of the same `(task, seed)` are also counted as attempts;
the lineage is an agent-side derived view, not a producer claim.

## Boundaries

- `planned_steps` is a producer claim, not a verified budget.
- `defer_seconds` under-reporting is a producer honesty problem; the strict
  core's replay is what eventually exposes it (see README's as-of section).
- The watchdog measures availability, not correctness: healthy-looking
  metrics from a hung trainer are exactly the failure mode it exists for.
