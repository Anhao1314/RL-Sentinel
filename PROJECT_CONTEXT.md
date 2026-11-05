> **RETIRED · 历史记录（v1）** 本文描述的是已归档的第一代系统（在线预测 / 建议制止损 / CSV 分析管线），
> 不代表本仓库当前定位。当前定位见 [README](README.md)
> 与 [ADR 0001](docs/adr/0001-from-prediction-to-evidence.md)。
> 代码与数据已归档至 `archive/v1` 分支，见 [ARCHIVE.md](ARCHIVE.md)。

> 当前状态：飞书通知已移除；机器参数使用 config.local.json。全程特征基线仅用于事后分析，不能用于在线早停或 ETA 预测；历史指标保留原始口径。

# go2w-quant 项目上下文

> 本文件供 Codex/AI 助手快速理解项目，避免扫描全量代码。修改项目前先读本文件。

## 项目目标

宇树 Go2W 机器狗强化学习训练的**量化决策支持系统**：在训练进行中基于可观测数据预测最终水平、剩余时长、失败概率，决定是否止损。核心是规则引擎 + 统计回测，不是机器学习预测模型（样本不足）。

方法论：梁文锋/幻方量化思路——风控优先、不做预测做赔率、回测是生命线、小样本先规则后模型。

## 环境

- 项目路径：`D:\lianghua\go2_lianghua`（Windows 端）
- Linux 训练端：`<本地训练仓库路径>`，webpanel `http://127.0.0.1:8787`
- Python 3，依赖见 `requirements.txt`

## 核心文件职责

| 文件 | 职责 | 不可随意改 |
|---|---|---|
| `factors.py` | 34 因子纯函数计算，所有下游的输入 | 因子公式、阈值逻辑 |
| `quant.py` | v2 风控日报引擎 | — |
| `collector.py` | 从 Linux 源目录解析数据为 CSV | verdict 空串兜底 `if not labels["verdict"]` |
| `backtest_rules.py` | 规则止损历史重放，产出赔率表 | 重放协议（reports 排除、completed 强制 False） |
| `backtest_engine.py` | 滚动回测框架（leave-one-future-out，插件协议） | 时间不泄漏原则 |
| `label_enrichment.py` | 7 维标签富化（collapsed/best_step/stop_justified 等） | — |
| `data_screening.py` | 4 条筛选规则 + 异常归档 + LOO 无泄漏对比 | Config A 逐样本留一折 |
| `realtime_monitor.py` | Windows 端 5 秒轮询 webpanel，本地风险展示 | 状态与风险展示 |
| `consistency_check.py` | 离线 vs 实时因子一致性校验 | — |
| `curve_fit.py` | 幂律/指数学习曲线拟合 | — |
| `baseline.py` | duration 回归基线（LOO） | — |
| `modeling.py` | 建模数据集构建 | — |
| `annotate_alerts.py` | 告警日志自动标注（是否真崩溃） | — |
| `config.json` | 所有阈值和参数 | risk/screening/monitor/compute 段 |
| `MATH_LIBRARY.md` | 34 因子公式总表、阈值、命名澄清 | 公式定义的唯一来源 |
| `schema.py` | 数据表定义 | — |

## 因子体系（34 个，6 大类）

**收敛类**：eval_drawdown(clip[0,1]), eval_slope_per_1e6(最近5点最小二乘), reward_peak_ratio(final/peak), eval_neg_ratio, eval_neg_ratio_recent(最后5点), ev_neg_streak(尾部连续负值)

**稳定类**：eval_std_recent(mean(tail5,std_reward)), approx_kl_last(清洗到(0,1]), tb_value_loss_last, tb_std_last, kl_divergent(bool, 当前点不在(0,1]), kl_divergent_streak(连续发散eval轮数)

**健康类**：stall_minutes(max-so-far 持久), current_stall_minutes(当前连续,恢复归零), nan_count, restart_count(>100k→<10k回退次数)

**验收类**：success_rate, verdict

**资源类**：cpu_percent, mem_percent, swap_percent

**成本/进度类**：progress, duration_seconds, cost_yuan(compute段估算)

**注意命名歧义**（已在 MATH_LIBRARY.md 澄清，不重命名）：
- eval_std_recent = std_reward 列的均值，不是 mean_reward 的标准差
- ev_neg_streak = 尾部连续负值，不是历史最长
- reward_peak_ratio = final/peak，不是 peak/final
- stall_minutes = max-so-far（持久），当前停滞用 current_stall_minutes
- eval_neg_ratio = 全部尝试混合，当前尝试用 neg_ratio_current

## 风控规则（R0~R3）

- R0：正常
- R1：预警（如 neg_ratio>0.3, current_stall>30min, std_reward_collapse）
- R2：严重（如 drawdown>30%, neg_ratio_recent>50%, kl_divergent≥3, restart≥2）→ decide=tune 或 stop
- R3：紧急（如 drawdown>50%, kl_divergent≥5, restart≥4）→ decide=stop

规则定义在 `factors.py` 的 `run_risk_items` 和 `decide`，阈值在 `config.json` risk 段。

## 数据结构

```
data/datasets/          # collector 产出的原始 CSV
  runs.csv              # 每个 run 一行（task,seed,verdict,total_steps,...）
  eval_points.csv       # eval 奖励曲线（task,seed,timesteps,mean_reward,...）
  tb_points.csv         # TensorBoard 标量（task,seed,step,approx_kl,std_reward,...）
  snapshots.csv         # 快照（time,task,seed,timesteps,reward,cpu,mem,...）
  reports.csv           # 验收报告（task,seed,verdict,success_rate,...）
  labels.csv            # 标签

data/modeling/          # 建模和回测产物
  dataset_YYYY-MM-DD.csv
  rule_backtest_YYYY-MM-DD.csv/md
  backtest_YYYY-MM-DD.csv/json
  enriched_labels_YYYY-MM-DD.csv
  screened_dataset_YYYY-MM-DD.csv
  anomaly_archive_YYYY-MM-DD.csv
  consistency_check_YYYY-MM-DD.csv
  curve_fits_YYYY-MM-DD.csv
  plots/

data/monitor/           # 实时监控日志（.gitignore）
  realtime_log.csv
  alert_annotations.csv
```

## 数据现状（截至 2026-08-19）

- 30 runs 参与回测，标签：pass=3, fail=14, unknown=13（含 9 个基于 eval 曲线的人工标注）
- 9 个 run 检测到训练塌缩，14 个早停合理
- 数据筛选：good=17, insufficient=13, anomalous=0
- duration 回归 LOO R²：小样本下波动大（-3.96~+0.18），持续跟踪中
- 规则止损回测：7 个 stop 事件中 5 fail / 2 unknown / 0 pass，所有规则命中率 100%
- 因子一致性：9 个关键因子 80/80 通过，2 个结构性 N/A（eval_std_recent, eval_slope_per_1e6 实时不可用）

## 常用命令

```bash
# 全量测试
pytest tests/

# 单模块运行
python quant.py --today 2026-08-18
python backtest_rules.py --today 2026-08-18
python label_enrichment.py --today 2026-08-18
python data_screening.py --today 2026-08-18
python modeling.py --today 2026-08-18
python consistency_check.py --task balance --seed seed00 --today 2026-08-18
python realtime_monitor.py --once          # 单轮（本地表格与日志）

# Windows 入口
run_windows.bat --backtest
run_windows.bat --monitor
```

## 管线重跑顺序（数据更新后）

collector → label_enrichment → data_screening → backtest_rules → quant → modeling → consistency_check（回归验证）

## 不做的事（硬约束）

- 不做 verdict 二分类（pass 太少）、success_rate 回归（0 样本）、28+ 特征非线性模型（必过拟合）
- 不自动止损（建议制，误杀率未验证）
- 不重命名现有因子（下游连锁修改风险大，用 docstring + 新增因子替代）
- 不改 backtest_engine.py 架构
- token 成本不逐 run 归因（无因果数据）
