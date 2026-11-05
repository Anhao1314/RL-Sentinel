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

One full-stack demo (trainer that hangs + agent + Prometheus + Grafana) is
one command away: `docker compose -f deploy/docker-compose.yml up --build`.

## Boundaries

- `planned_steps` is a producer claim, not a verified budget.
- `defer_seconds` under-reporting is a producer honesty problem; the strict
  core's replay is what eventually exposes it (see README's as-of section).
- The watchdog measures availability, not correctness: healthy-looking
  metrics from a hung trainer are exactly the failure mode it exists for.
