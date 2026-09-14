# RL Training Risk Replay · 强化学习训练风险回放

面向强化学习训练过程的实验基础设施：采集训练运行遥测，**按时间顺序回放（chronological replay）**训练过程，给出可配置的 **R0–R3 风险决策**，并在采信结果前校验实验数据质量。

`时序回放` `R0–R3 风险` `数据质量` `实验基础设施`

**领域：** 机器人（Go2W）训练日志与资源遥测，不是金融行情数据。**技术栈：** Python、pandas、NumPy、SciPy、scikit-learn、TensorBoard。**质量：** 文档记录的本地运行 265 项测试通过；完整 Pyright 当前仍有错误，作为技术债公开跟踪（见[已知限制](#已知限制)）。

[English](README.md) · [核验与声明边界](docs/portfolio-validation.md)

![架构图：训练日志与遥测经采集进入 CSV schema，与人工标签合并后分流到数据质量检查、缓存的事后因子、按进度过滤的时序回放，再进入预测器插件与 R0–R3 决策，用于实验复盘。本图为系统结构，不是实验结果。](docs/assets/replay-pipeline-architecture.svg)

## 关键结果

| 已核验事实 | 证据与范围 |
| --- | --- |
| **265 项测试通过** | Python 3.12.14 / macOS，2026-09-12，本次无跳过（耗时 71.26 s），见[核验记录](docs/portfolio-validation.md) |
| **4 个风险等级 R0–R3** | `factors.py`：可配置的 RiskItem 累积与决策矩阵 |
| **6 类数据质量 / 20+ 检查** | 缺失、离群、时间连续性、重复、标签一致性、跨表完整性；`scripts/data_quality_check.py` 及其测试 |
| **3 个内置回放预测器** | 规则引擎、always-continue、always-stop；`tests/test_backtest_engine.py` 确定性 fixture 测试 |

上述为已实现的结构能力，不代表预测准确率；不存在任何提速倍数或“成功样本零误杀”的结论。

## 这是什么 / 不是什么

把评估曲线、TensorBoard 标量、资源快照与验收报告整理成结构化 CSV，分析失败信号、回放本地建议、比较事后基线。算法重点是时间可见性、规则决策与实验数据可靠性，而非仪表盘界面。

这是**训练运行分析，不是行情、下单或交易收益回测系统**：没有资金、没有订单簿、没有投资收益目标。在线回放只实现了部分可见性防护，**尚未做到端到端无前视（look-ahead safe）**。

## 为什么重视前视偏差

T 时刻的决策不应看到实验的最终结论或更晚的遥测。`online_view` 按时间截断快照、按已观测进度截断评估/TensorBoard 行、排除报告并强制 `completed=False`；滚动训练集选择要求每个被选历史运行的估计结束时间早于目标开始时间。

**剩余边界：** 该视图仍会整张复制 `runs` 表（包含最终结论/时长以及无关的未来运行）；结束时间估计与标签可得性尚未按 T 时刻跟踪；现有 `test_no_time_leakage` 只校验训练集成员关系，不校验预测器输入的完全隔离，因此不能据此宣称端到端保证。

## 时序回放（Chronological Replay）

回放引擎所在模块仍保留历史文件名 `backtest_*.py`；这只是历史命名，**不代表金融回测**。

- `backtest_rules.py`：按快照轴的规则回放，以及估计的反事实训练成本。
- `backtest_engine.py`：按时间顺序排列目标运行、可用历史训练运行与决策进度检查点。
- 预测器接口：`(train_runs_info, target_online, cfg) -> {stop, confidence, reason}`。
- 输出记录被跳过的样本与结束时间估计方式（`actual` / `step_rate` / `task_mean`）。

规则置信度来自严重程度，不是校准概率；缺少快照可能让成功运行无法进入滚动评估。记录中 0.3/0.5 进度有 4 个已标注失败、**0 个成功样本**，误杀率为 N/A，其表观准确率不适合作为标题结论。规则的确切数量取决于统计口径（因子代码 / 阈值分支 / 决策条件），因此不声称某个固定规则总数。

## 风险引擎

`run_factors` 提取奖励回撤、低奖励窗口、KL/value-loss 信号、停滞、重启与资源压力；`run_risk_items` 累积阈值触发的风险项；`decide` 把 R0–R3 映射为 `continue / watch / stop / tune / resize`。这些都是**本地建议**：不发送外部通知，也不会自动停止训练。

## 数据质量

只读检查器输出 Markdown/CSV 与可选 JSON 问题，覆盖六类；退出码：0 无严重问题、1 存在严重问题、2 运行失败。`data_screening.py` 另用 4 条筛选规则把实验记录分为 good / insufficient / anomalous。文档记录的隔离只读运行把已提交数据集评为 **D 级、128 个问题（21 个严重）、退出码 1**——这是“检测出数据问题”，不是检查器实现失败，也不代表已提交数据集没有问题。

## 性能与缓存

`run_pipeline.py` 只加载一次表，在各分析阶段之间传递 DataFrame 与逐运行因子缓存；`tests/test_pipeline.py` 校验缓存/直算结果等价。CLI 支持 `--verbose` 分阶段计时。不声明端到端提速倍数；未来基准必须固定输入提交、环境、阶段与输出等价标准。

## 离线快速开始

离线分析不需要训练服务器，当前验证/CI 目标为 Python 3.12。

```bash
git clone https://github.com/Anhao1314/rl-training-risk-replay.git
cd rl-training-risk-replay
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell 使用 .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python summary.py
python run_pipeline.py --today 2026-09-12 --out data/modeling/local_review --verbose
python backtest_engine.py --today 2026-09-12 --out data/modeling/local_replay --decision-progress 0.3,0.5,0.7
python scripts/data_quality_check.py --today 2026-09-12 --out data/quality/local_review --json
```

输出目录与历史报告分离；`--today` 只用于命名输出，不会截断输入。Windows 入口见 [WINDOWS.md](WINDOWS.md)。

## 数据采集端与本地监控

复制 `config.example.json` 为被忽略的 `config.local.json`，填写本机训练源路径；公开默认没有采集源。`python collector.py` 只读训练源并写入本仓库数据目录，单文件原子替换、多表并非整批事务，读取与采集不应并发。本地监控：`python realtime_monitor.py --once`（默认读 localhost:8787），只输出本地表格、风险建议与日志，不发飞书消息，也不自动停止训练；ETA 是进度估算，不是已验证预测模型。

## 测试与质量

```bash
python -m pytest tests/ -q
python -m pyright --pythonpath .venv/bin/python --outputjson
```

本地核验：**265 通过**；**Pyright 36 个文件、1,645 个错误、0 警告**。GitHub Actions 在 Linux/Python 3.12 上以测试作为门禁，并把完整 Pyright 作为**建议性（advisory）**检查、上传诊断报告；工作流成功不代表类型检查通过。没有伪造 tests/CI 徽章，当前未选择开源许可证。

## 已知限制

- 预测器输入尚未与最终元数据完全隔离，暂不能宣称无前视。
- 标签稀疏/不均、快照覆盖有限，限制了误报、跨任务与样本外验证。
- 快照时长是观测跨度而非有效计算时长，节省量是反事实估计，不是已实现降本。
- CSV 为单文件原子替换而非多表事务；在线采集与跨平台采集未经离线 CI 验证。
- 完整 Pyright 仍有未解决问题（1,645 错误）；依赖未完全锁定。
- 不声明生产交易、真实资金、收益率、用户/客户或自动早停。

后续优先：as-of 元数据与标签可得性、未来数据变异测试、压缩类型债、可比基准、多表发布一致性。[研究历程](PHASE_RECORD.md)仅为历史背景，不是当前性能证据。

## 文档导航

[数据字典](DATA_DICTIONARY.md) · [数学定义](MATH_LIBRARY.md) · [开发约定](AGENTS.md) · [Windows 指南](WINDOWS.md)

数据同步脚本（`scripts/sync_github.sh`）可能提交/推送数据，不属于离线复现范围；安装定时任务前先手动验证采集与离线管线。
