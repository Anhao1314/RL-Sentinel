# Getting started with RL Sentinel

[Project home](../README.md) · [Documentation](README.md) · [简体中文](GETTING_STARTED.zh-CN.md)

## 1. Run from the checkout

Check `python --version`: the strict core requires **Python 3.12 or newer**. Use `python3` instead of `python` when that is the command for your compatible interpreter.

```bash
git clone https://github.com/Anhao1314/RL-Sentinel.git
cd RL-Sentinel
python -m rl_risk_replay experiment --out artifacts/sentinel-demo
python -m rl_risk_replay verify --bundle artifacts/sentinel-demo
```

These commands run without pip installation. They use the Python standard library and CPU; no model download, GPU or API credential is needed. If you already cloned the old repository, preserve local changes and update its remote:

```bash
git remote set-url origin https://github.com/Anhao1314/RL-Sentinel.git
```

Your existing directory need not be renamed. Keep `rl_risk_replay` in Python imports and module commands. The installed command remains `rl-risk`; optional editable installation is `python -m pip install --no-deps -e .` and may need network access for the build backend.

## 2. Read the output

Open `artifacts/sentinel-demo/report.html` directly in a browser. No local web server is required. Read the source category and readiness status first, then the summary for one policy and checkpoint, then the corresponding decisions and their inputs.

| File | What it records |
| --- | --- |
| `report.html` | Offline report, summaries and per-decision evidence |
| `results.json` | Machine-readable results, inputs and runtime metadata |
| `events.jsonl` | Versioned source declaration and experiment events |
| `protocol.json` | Controlled-experiment settings |
| `q_tables.json` | Final learned Q tables |
| `manifest.json` | Completion state, exact members, sizes and SHA-256 hashes |

This list describes `experiment` output. A normal `replay` bundle has results, events, report and manifest; it does not train policies or create new Q tables. The validation script separately records its installed environment.

Choose a new directory for each execution. Never edit an evidence bundle to change its title or appearance: doing so invalidates its recorded hashes. A passing `verify` checks integrity relative to the manifest, not the correctness of scientific claims or the identity of a producer.

## 3. Replay frozen observations

```bash
python -m rl_risk_replay replay --events artifacts/sentinel-demo/events.jsonl --out artifacts/frozen-review --policy all --progress 0.3,0.5,0.7
python -m rl_risk_replay verify --bundle artifacts/frozen-review
```

Each checkpoint is scored separately. Multiple decisions from the same run are not independent trials. `abstain` means the policy did not make a supported binary recommendation; it is not evidence that a run is healthy.

`--evaluation-at` accepts a Unix timestamp to limit observable information. It is an actual cutoff, unlike the historical `--today` output label. The [protocol](RELIABILITY_V2.md) defines historical membership, delayed observations and label revisions.

<a id="integration"></a>
## 4. Connect a trusted observer

Initialize a new store with `python -m rl_risk_replay init --db artifacts/training.sqlite`. To open that existing database from Python:

```python
from pathlib import Path
from rl_risk_replay.storage import EventStore

store = EventStore(Path("artifacts/training.sqlite"))
```

Use `store.record(run_id, kind, payload)` from your observer. The lifecycle is:

| Kind | Record when | Required payload |
| --- | --- | --- |
| `start` | A new attempt starts | `task`, `seed`, `planned_steps` |
| `sample` | An aggregated observation becomes available | `step`; optional supported metrics |
| `finish` | The attempt ends | `status` |
| `label` | Evaluation or review actually provides an outcome | `verdict`, `source` |

Use real observed values, not the controlled experiment's labels. Keep samples strictly increasing in step; a restart or changed budget needs a new run ID. Record finish and label separately. Do not overwrite a label: append a visible revision. The complete allowed fields and value constraints are in [RELIABILITY_V2.md](RELIABILITY_V2.md).

The recorder stamps local availability. Cross-machine clocks must be comparable. A source claiming an event from the future is rejected. The v0.2 event API does not include a ready-made training-framework callback or an automatic stop/restart action.

## 5. Check the installation

For core tests and type checking, install the development tools:

```bash
python -m pip install -r requirements-core-dev.txt
python -m pytest tests_v2/ -q
python -m pyright --project pyright-core.json
```

The historical suite additionally needs `requirements-dev.txt`. The [recorded v0.2 validation](VALIDATION_V2_2026-10-06.md) separates core checks, historical debt and controlled results.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `No module named rl_risk_replay` | Run in the repository root or use an editable install. Do not replace the module name with `RL-Sentinel`. |
| Output directory already exists | Choose a fresh directory; keep the previous evidence intact. |
| No recommendation or skipped checkpoint | Inspect missing samples, nonpositive reward peaks, budget progress and whether finish was already visible. |
| Verification fails | Compare bundle membership and hashes. Do not change the manifest to conceal a failed check. |
| Legacy data cannot enter strict replay | Missing availability evidence is a real limitation; use the read-only legacy audit instead. |

[Back to RL Sentinel](../README.md) · [Event protocol](RELIABILITY_V2.md) · [Evidence record](VALIDATION_V2_2026-10-06.md)
