# RL Training Reliability & Risk Replay

A **recommendation-only reliability layer** for reinforcement-learning experiments.
Version 0.2 introduces a typed, dependency-free core: timestamped observation capture,
as-of replay, explicit decision metrics, transactional event storage and inspectable evidence.
It does **not** automatically stop training, predict failure with a calibrated probability,
or claim measured production compute savings.

[中文](README.zh-CN.md) · [Protocol and migration](docs/RELIABILITY_V2.md) · [Historical validation](docs/portfolio-validation.md)

## Run a real experiment, without a GPU

Python 3.12 or newer, from the repository root:

```bash
python -m rl_risk_replay experiment --out artifacts/controlled-run
python -m rl_risk_replay verify --bundle artifacts/controlled-run
```

This executes **18 actual tabular Q-learning runs** in a 4x4 grid, not prerecorded traces:
6 training seeds under normal learning, disabled learning, and a late policy-reset fault.
It records observations into SQLite, labels runs using final evaluation, then compares
rules, always-continue and always-stop at 30%, 50% and 70% of the declared step budget.
The faults are controlled interventions in a toy scene, **not a Go2W generalization benchmark**.

The new output directory contains an offline `report.html`, `results.json`, immutable
`events.jsonl`, final `q_tables.json`, the exact protocol and a SHA-256 manifest.
Existing outputs are never overwritten. Full execution evidence is uploaded by CI;
a workflow file is not itself a claim that validation passed.

## What changed

| Concern | v2 contract |
| --- | --- |
| Future metadata leaking into a predictor | Target view contains only validated start fields and observations visible at the cutoff. No target verdict or final duration. |
| Labels treated as available when a run ends | Finish and label are separate events. Historical membership requires both to be visible strictly before the target start. |
| Delayed telemetry with old step numbers | Both `event_time` and `available_at` are checked; a small step number does not make a late record visible. |
| Ambiguous false-kill denominator | Separate `stop_precision`, `false_stop_share`, `pass_kill_rate` and `fail_recall`, with nulls for undefined rates. |
| Missing data treated as a healthy run | The rules can abstain. Decision coverage, missing labels and skipped runs remain visible. |
| Partial multi-file publication | SQLite batches are transactional; reports are staged and published as a checksummed bundle. |
| Claims based on manufactured history | Legacy CSVs can be inventoried, but missing availability timestamps are never inferred or backfilled. |

```text
Live producer -> immutable schema-v2 events -> SQLite batch transaction
                                         -> as-of view -> recommendation
Frozen events + evaluation outcomes      -> replay metrics + evidence bundle
Historical CSVs                          -> read-only audit / explicit v1 analysis
```

## Capture and replay new training observations

```bash
python -m rl_risk_replay init --db artifacts/training.sqlite
python -m rl_risk_replay record --db artifacts/training.sqlite --run-id run-001 \
  --kind start --payload '{"task":"go2w-navigation","seed":"1","planned_steps":2000000}'
python -m rl_risk_replay record --db artifacts/training.sqlite --run-id run-001 \
  --kind sample --payload '{"step":100000,"reward":15.2,"approx_kl":0.02}'
python -m rl_risk_replay replay --db artifacts/training.sqlite --out artifacts/replay-001
```

Use the Python `EventStore.record` API inside a training observer for continuous capture.
Record one aggregated sample per increasing step; a restarted attempt needs a new run ID.
The recorder stamps local observation availability. Source clocks must be comparable;
future-dated events are rejected rather than silently corrected. Finish and evaluation/manual
label events must be recorded separately. See the [full schema and API](docs/RELIABILITY_V2.md).

Replay a frozen event stream:

```bash
python -m rl_risk_replay replay --events artifacts/controlled-run/events.jsonl \
  --out artifacts/replay-frozen --policy all --progress 0.3,0.5,0.7
python -m rl_risk_replay audit-legacy --dataset data/datasets --out artifacts/historical-audit
```

`--evaluation-at` is an actual observation cutoff. It is not the old `--today` report label.
Policy configuration is versioned in the output; `--rule-config` accepts the documented
threshold fields and rejects unknown keys. The v2 rule subset is intentionally not a claim
of behavioral parity with every historical risk rule.

## Evidence, not headline accuracy

CI separately checks the strict core on Linux and Windows, preserves the historical test
suite on Linux, requires **strict core Pyright**, runs 200 valid future-data mutations plus
a visible-data negative control, and executes the controlled RL experiment.

The historical path still has known type debt. Full-repository Pyright remains explicitly
advisory, with diagnostics uploaded. Core typing passing does not erase historical findings.
Dependencies for the strict runtime are only Python's standard library; CI tool versions are
pinned, and each experiment records the installed environment. Historical analysis dependencies
still use ranges and are not represented as a fully locked scientific environment.

## Legacy compatibility and boundaries

`backtest_engine.py` and `backtest_rules.py` are now compatibility shims. Old CLI execution
requires `--legacy-retrospective`; old Python imports retain historical behavior for regression
and post-hoc analysis. Frozen implementations live under `legacy/`.
**The legacy online view is still not time-correct. Do not use it for online efficacy claims.**

Legacy collection, factor/risk rules, CSV schemas, dashboards, manual-label authority and
historical datasets/reports are preserved. The previous 265-test run and historical quality
findings belong to the [old validation record](docs/portfolio-validation.md), not this release.
No historical timestamps, successful checkpoints or labels were fabricated.

## Remaining limitations

The new replay contract assumes honest producer timestamps and valid run identity. It is
not a security sandbox for untrusted Python predictors, an authenticated provenance service,
or a distributed telemetry system. SQLite and snapshot construction currently validate the
in-memory event set, so large-scale performance still needs profiling. Bundle hashes detect
changes relative to the manifest; they are not a signature against a party rewriting both.
Directory publication uses a cooperative writer lock and is not a universal power-loss guarantee.

A sample floor of 5 passes and 10 failures only controls readiness reporting; it does not
establish independence or statistical sufficiency. Synthetic and controlled datasets remain
blocked from operational effectiveness claims, even when they exceed that floor.
Remaining wall time is **not** effective GPU time, failure onset, causal regret or realized savings.
There is no automatic stop/restart, external notification, new public deployment or license change.
