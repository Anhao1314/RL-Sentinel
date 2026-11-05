> **RETIRED · 历史记录（v1）** 本文描述的是已归档的第一代系统（在线预测 / 建议制止损 / CSV 分析管线），
> 不代表本仓库当前定位。当前定位见 [README](../README.md)
> 与 [ADR 0001](adr/0001-from-prediction-to-evidence.md)。
> 代码与数据已归档至 `archive/v1` 分支，见 [ARCHIVE.md](../ARCHIVE.md)。

# Portfolio validation — 2026-09-12

Audited source: 2207ccd85486bcba9382cd8cc63287e4a25270bc. Domain: Go2W RL training logs; not financial-market data.

## Local execution

Python 3.12.14 / macOS; pytest 9.1.1; Pyright 1.1.408.

- `python -m pytest tests/ -q`: 265 passed, no skips, 71.26 seconds. Suite duration is not a pipeline performance benchmark.
- `python -m pyright --pythonpath <isolated-env>/bin/python --outputjson`: 36 files, 1,645 errors, 0 warnings. Runtime dependencies were installed; this is not the initial incorrect-interpreter import-failure run.
- Rolling rule replay at progress 0.3/0.5: four labeled failures, zero passes. At 0.7: three failures, zero passes. False-kill rate N/A. Do not promote apparent accuracy or estimated savings as predictive/realized performance.

- Read-only data-quality check on an isolated copy of the committed dataset: grade D, 128 issues, 21 critical, exit code 1. This is a detected-data-issues result, not a failed checker implementation. No source dataset was modified.

## Temporal boundary probe

Using `tests/test_backtest_engine.py:make_tables`, `online_view(tables, "a", "s0", 50)` includes snapshots only through T=50 but exposes the whole runs table. The future s2 run (starts at 150) is visible with final verdict pass and duration 50. Target s0's final fail verdict is also visible. Reports are omitted, completed is false. This proves predictor input access to final/future metadata, not that the current rule predictor necessarily consumes every leaked field.

`test_no_time_leakage` asserts historical training-set membership only. End-time/label availability also needs as-of tracking. No business-logic correction was performed in this portfolio change.

## Metric decisions

| Claim | Status | Evidence |
|---|---|---|
| 17 composable rules | PARTIALLY SUPPORTED | Configurable accumulated risk items exist; 20 literal factor codes in run_risk_items, no definition of exactly 17. |
| R0–R3 | SUPPORTED | factors.LEVEL_INDEX / run_risk_items / decide and risk tests. |
| Six categories / 20+ quality checks | SUPPORTED | Six check functions, 28 literal diagnostic types excluding check_error, additional dynamic issue types; test_data_quality.py. |
| 97s→21s | UNSUPPORTED | No controlled timing artifact; b413fa4 implements cached pipeline only. |
| 4.6x | UNSUPPORTED | No verified comparable timing baseline. |
| 197 tests | UNSUPPORTED | Current execution has 265 passed. |
| Pyright 0 errors | UNSUPPORTED | Current complete execution has 1,645 errors. |
| Six passes / zero false kills | PARTIALLY SUPPORTED | Limited historical per-rule evidence; current chronological replay has zero pass samples. Cannot establish independent successful online samples or general false-positive control. |

## CI policy

Tests gate on Linux/Python 3.12. Full type checking is explicitly advisory with diagnostic artifact and summary; overall success never means Pyright passed. Record the actual published CI outcome separately. Runtime dependency ranges are not an exact lockfile.

## Reproducibility limits

Committed sample CSVs support offline regression. Full-run baselines are post-hoc, not online predictions. `--today` names outputs and does not apply a time cutoff. Generated evaluation outputs were isolated outside historical data. No license was added; no rename, release, credential-history rewrite or live collection was performed.
