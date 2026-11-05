# 归档索引

【事实】本文件记录 v1 物理归档的范围与检索方式。仓库为 `Anhao1314/RL-Sentinel`（公开仓库，
owner `Anhao1314`）。第一批改造（PR #10）已合并，main 合并提交 `0e5cf3a`；本批次（第二批）
在 `refactor/archive-v1` 分支完成 v1 归档。

## 归档范围

【事实】main 上移走、归入 `archive/v1` 的内容，按类别：

- 根目录 18 个 v1 模块（采集、因子、规则、回测、日报等 Python 文件）。
- `legacy/` 目录。
- `tests/`（265 个 v1 测试）。
- `data/` 下 142 份产物：66 csv / 39 md / 24 png / 13 json，合计约 9.5 MB。
- `config.json`、`run_windows.bat`、`WINDOWS.md`、`requirements.txt`、`requirements-dev.txt`。
- 5 个 v1 专用 scripts。

【事实】main 保留：`rl_risk_replay/`、`research/`、`audit/`、`contracts/`、`tests_v2/`、
`tests_research/`、`docs/`、`scripts/check_documentation.py`、
`scripts/validate_reliability_v2.py`，以及治理文件与下列历史文档。

【事实】`config.example.json` 保留在 main，但**没有任何消费者**：当前参考实现不需要配置文件。
它作为凭据事件的历史物证保留（v1 配置模板），与 `config.json` 内容相同且不含 webhook 值，
状态见 SECURITY.md。

## `archive/v1` 分支与基线

【事实】`archive/v1` 是从 main `0e5cf3a` 建立的完整快照分支，已创建并推送；该分支含被移走
文件的完整内容，可整体检出。归档是移动而非删除，main 与 `archive/v1` 合起来仍覆盖全部历史资产。

## 检出与校验

```bash
git checkout archive/v1
python -m audit data --root <archive/v1 检出路径>
```

【事实】`python -m audit data --root <path>` 以 `data/manifest.json` 为基准，按 SHA-256
与字节数校验归档检出内容，确认 142 个产物与清单一致。`data/manifest.json` 保留在 main，
因此校验不依赖归档分支上的清单副本。

## `data/manifest.json` 的作用与边界

【事实】`data/manifest.json` 记录全部 142 个产物的路径、SHA-256 与字节数，作为内容寻址锚点
（provenance anchor）。这是把项目自身的「内容哈希证据包」机制用在仓库自身：归档后 main 不再
持有产物本体，仍可用哈希清单定位并校验 `archive/v1` 上的内容。

【事实】边界：清单只证明「哪些内容被归档、内容未变」，**不表示**归档 CSV 可用于严格回放。
历史 CSV 无 `available_at` 字段，严格回放可用的历史运行数为 0；清单是 provenance 锚点，
不是数据可用性或结论可复现的声明。

## 保留在 main 的历史文档与 RETIRED

【事实】以下历史文档保留在 main 原位，仅在文件开头加 RETIRED 横幅，正文一字不改：
`PROJECT_CONTEXT.md`、`AGENTS.md`、`PHASE_RECORD.md`、`QUANT_PLAN.md`、
`DATA_DICTIONARY.md`、`MATH_LIBRARY.md`、`docs/portfolio-validation.md`。

【事实】RETIRED 表示：文档描述的是已归档的第一代系统（在线预测 / 建议制止损 / CSV 分析管线），
不代表本仓库当前定位；当前定位见 [README](README.md) 与
[ADR 0001](docs/adr/0001-from-prediction-to-evidence.md)。它们是历史证据，删除会破坏
可追溯性与文档相对链接校验，故保留不删。

## 边界

【事实】归档不删除、不改写历史：`archive/v1` 分支可完整检出，main 以 `data/manifest.json`
保留内容寻址锚点；本批次**未**改写 git 历史、**未**改写已归档文件正文。
历史中的凭据事件与不改写历史的决策，见 [SECURITY.md](SECURITY.md) 与
[ADR 0002](docs/adr/0002-no-git-history-rewrite.md)。
