> **RETIRED · 历史记录（v1）** 本文描述的是已归档的第一代系统（在线预测 / 建议制止损 / CSV 分析管线），
> 不代表本仓库当前定位。当前定位见 [README](README.md)
> 与 [ADR 0001](docs/adr/0001-from-prediction-to-evidence.md)。
> 代码与数据已归档至 `archive/v1` 分支，见 [ARCHIVE.md](ARCHIVE.md)。

> 当前状态：外部通知已移除；机器参数使用 config.local.json。全程特征基线仅用于事后分析，不能用于在线早停或 ETA 预测；历史指标保留原始口径。

# go2w-quant v2：量化风控与训练决策体系

> 依据：`data/datasets/` 六张表（runs / eval_points / tb_points / snapshots / reports / costs）
> 方法论参考：梁文锋 / 幻方量化的量化思路（风控优先、多因子、概率与分布、自动化流水线、成本效率）

## 一、梁文锋量化思路 → 本方案落地

| 思路 | 在本仓库的落地 |
|---|---|
| 风控优先：先防亏再求胜 | 把训练过程当"组合"管理：先定义 R0-R3 风险分级与止损规则，再谈继续训练 |
| 多因子可计算 | 训练质量拆成 收敛/稳定/健康/验收/资源/成本 六类因子，全部由 CSV 可算，不拍脑袋 |
| 概率与分布思维 | 一律用窗口统计量（峰值回撤、窗口斜率、连续计数、占比），不用单点值 |
| 自动化流水线 | 采集 → 因子 → 风控 → 决策 → 报告 → 推送，全部定时自动执行 |
| 成本效率 | token 成本、缓存率、资源利用率纳入风控与日报，算清单位成本 |

## 二、体系架构

```
data/datasets/*.csv ──> factors.py（因子 + 规则 + 分级） ──> quant.py（日报）
       只读           纯函数，可单测                   Markdown + JSON 入库推送
                                                           │
                                          cron 每日一次（Linux） / 手动（Windows）
```

- `factors.py`：纯函数引擎，无 I/O。`compute_all(cfg, tables, today) -> QuantResult`。
- `quant.py`：CLI，只读 `data/datasets`，写 `data/reports/quant_YYYY-MM-DD.md` 与同名 `.json`。
- 建议制：只输出建议，不自动干预训练；阈值全部在 `config.json` 的 `risk` 段，可随时调整。

## 三、标签表 labels.csv（v3 建模标签的权威来源）

- 每行一个 (task, seed)，与 runs.csv 一一对应（含全部人工锁定行）。
- 列：`task / seed / completed / verdict / success_rate / duration_seconds / label_source / label_updated_at`。
- `label_source`：`auto`（采集自动刷新）或 `manual`（人工锁定）。人工在 Excel 中把某行改为 `manual` 并修正标签值后，后续采集**不会覆盖**该行；其余 auto 行每次采集按最新验收数据刷新。
- 来源：verdict/success_rate 来自验收报告（summary.json 优先、metrics.csv 兜底），duration_seconds 来自快照推算，completed 由 `.completed` 标记或验收报告推定。
- v3 建模时以本表为标签（y）来源；特征矩阵（X）由 modeling.py 固化到 data/modeling/。
## 四、因子定义（每 task/seed）

| 类别 | 因子 | 来源表 | 计算方式 | 用途 |
|---|---|---|---|---|
| 收敛 | eval_last_reward / eval_peak_reward | eval_points | 末点、历史最大评估奖励 | 进度与回撤基准 |
| 收敛 | eval_drawdown | eval_points | (峰值-末值)/峰值 | 回撤风控 |
| 收敛 | eval_slope_per_1e6 | eval_points | 最近 5 点奖励/步数斜率 ×1e6 | 收敛速度 |
| 收敛 | progress_ratio / reward_peak_ratio | eval_points+runs | 末步数/总步数、末奖励/峰值 | 停滞判定 |
| 稳定 | eval_std_recent | eval_points | 最近 5 点 std_reward 均值 | 波动观察 |
| 健康 | approx_kl_last | tb_points | 最近 rollout 的近似 KL | PPO 稳定性 |
| 健康 | std_last（塌缩/发散） | tb_points | 策略输出标准差 | 探索性退化 |
| 健康 | ev_neg_streak | tb_points | 尾部连续负 explained_variance 个数 | 价值网络失效 |
| 健康 | value_loss_divergent | tb_points | 连续 10 点上升且末值 >2× 前期均值 | 损失发散 |
| 验收 | verdict_fail_ratio / success_rate_mean | reports | 场景级 verdict/成功率 | 验收风险 |
| 验收 | max_dev_max / min_clear_min / falls_mean | reports | 场景极值/均值 | 物理指标越界 |
| 验收 | nan_count | reports | nan=True 行数 | 数据污染 |
| 资源 | cpu/mem/swap_percent_max | snapshots | 采样峰值 | 过载 |
| 资源 | stall_minutes | snapshots | timesteps 连续不变分钟数 | 训练停滞 |
| 资源 | idle_minutes | snapshots | CPU<20% 连续分钟数 | 疑似卡死 |
| 成本 | daily/weekly/monthly_cost | costs | 近 1/7/30 天 cost_yuan 合计 | 预算风控 |
| 成本 | cache_rate | costs | cached/(input+cached) | 缓存效率 |

## 五、风控分级与默认阈值（config.json `risk` 段）

| 规则 | R1 观察 | R2 警告 | R3 严重 |
|---|---|---|---|
| 峰值回撤 | >15% | >30% | — |
| 训练停滞 | 进度≥60% 且奖励<峰值 60% → R2 | | |
| std 塌缩 / 发散 | <0.05 | >1.5 | — |
| value_loss 发散 | — | 连续 10 点上升且 >2× 均值 | — |
| approx_kl | >0.05 | >0.10 | — |
| explained_variance | — | 连续 ≥10 点为负 | — |
| 验收 NaN | — | ≥1 次 | ≥3 次 |
| 物理指标 | 全 fail / max_dev>0.15 / min_clear<0.05 → R1 | | |
| Swap 使用率 | >50% | >80% | — |
| 内存 / CPU | CPU>95% → R1；内存>95% → R2 | | |
| timesteps 停滞 | ≥30 分钟 | ≥60 分钟 | — |
| 空闲疑似卡死 | — | CPU<20% ≥60 分钟且未完成 | — |
| 日/周/月预算 | ≥80% | ≥100% | ≥150% |
| token 缓存率 | <30% → R1 | | |

## 六、决策矩阵（建议制）

| 风险等级 | 建议 | 触发 |
|---|---|---|
| R0 | continue | 无风险项 |
| R1 | watch | 单项临界，下次快照复核 |
| R2 | stop | 回撤>30%、停滞、NaN≥1 |
| R2 | tune | value_loss / approx_kl / explained_variance |
| R2 | resize | swap / mem / cpu / stall / idle |
| R3 | stop | NaN≥3 等严重项 |

## 七、运行方式

- Linux（数据源机器）：`scripts/cron_cycle.sh` 每 15 分钟采集；量化日报**每日一次**（`data/.quant_last_date` 标记防重复），随 `data/reports/` 一起推送 GitHub。
- Windows：`run_windows.bat --quant`（或 `.venv\Scripts\python.exe quant.py`）手动生成当天日报；`run_windows.bat` 默认仍为 `summary.py`。
- 命令行：`python quant.py --config config.json [--today 2026-08-17] [--out data/reports]`。

## 八、B 方案：特征固化 + 基线模型（进行中）

- 特征矩阵（X）：`modeling.py` 每日固化 `data/modeling/dataset_YYYY-MM-DD.csv`——每行一个 run，数值因子（收敛/稳定/健康/验收/资源类）为 X；`completed` 不进特征（决策时刻不可知）。
- 标签（y）：来自 labels.csv（verdict / success_rate / duration_seconds，附 label_source 溯源）。
- 基线模型（`baseline.py`）：verdict 分类用 LOO 逻辑回归 vs 多数类 Dummy；duration/success_rate 回归用 LOO 线性回归 vs 中位数 Dummy；另输出单变量相关性 Top 供特征筛选。报告写入 `data/modeling/baseline_YYYY-MM-DD.md`。
- 数据门槛（诚实基线）：verdict 正负样本各 ≥2 才训练分类；回归有效样本 ≥6 才训练。当前（2026-08-17）24 runs：verdict 仅 1 个标签、duration 17 个样本（LOO R2 0.681）——结果只作管线验证，不作训练决策依据。
- v3 演进：样本达标后（建议 verdict 正负各 ≥30、duration ≥50 且含 completed 样本）升级为概率化早停：模型输出 stop 概率与置信区间，替换 R2/R3 静态阈值；耗时回归用于资源规划与预算。

## 九、实施路线

- v2（已完成）：因子 + 风控 + 建议制日报：`factors.py`、`quant.py`、`tests/test_factors.py`、`config.json risk 段`、cron/sync 集成。
- B 方案（当前）：标签表 labels.csv（auto/manual 锁定）+ 特征固化 `modeling.py` + 基线 `baseline.py` + 学习曲线拟合 `curve_fit.py`（幂律/指数、25%/50% 前缀外推 R∞、跨 seed 置信带、`--validate` 对比实际末值），随采集累积样本。
  - B 方案 P0（已完成）：规则止损回测 `backtest_rules.py`——按快照时间轴（默认 10 分钟粒度）重放 R2/R3 规则，输出 `data/modeling/rule_backtest_YYYY-MM-DD.csv`（级别×因子赔率表：命中率、误杀率、代理误杀率、期望净节省、t_end 方法分布）与同名 `.md`（含逐 run 止损明细与免责声明）。四项修正理由：
    1. 零 pass 处理：当前 n_pass=0，`false_kill_rate` 输出 `N/A (no pass samples)` 而非 0；另算 `fail_score` 软标签（0~1）与 `potential_false_kill_rate` 代理误杀；
    2. 反事实外推：incomplete run 的结束时间用 step_rate 外推（total_steps/(触发时进度/已耗时)），缺失回退同 task 平均时长，`t_end_method` 列标注 actual/step_rate/task_mean；
    3. 成本拆分：`saved_yuan_compute` = 节省小时 × gpu_count × gpu_hourly_price（`config.json compute` 段，估算）；token 成本仅报告项目级日趋势，不做逐 run 归因；
    4. 误杀惩罚：`expected_net_saving = hit_rate × avg_saved(fail) − false_kill_rate × avg_wasted(pass)`。
  - B 方案 P1（已完成）：滚动回测框架 `backtest_engine.py`——按开始时间排序的 leave-one-future-out（训练集 = 结束时间早于目标开始时间且有标签的 run，时间不泄漏）；决策时刻 = 训练进度达 `--decision-progress`（支持 0.3,0.5,0.7 列表扫描）；插件协议 `predictor_fn(train_info, target_online, cfg) -> {stop, confidence, reason}`，内置 `rule` / `always_continue` / `always_stop`；输出 `data/modeling/backtest_YYYY-MM-DD.csv`（逐 run 决策行）与同名 `.json`（各决策进度点聚合指标）。未来 v3 模型按同一协议接入。
- v3（预留）：概率化早停分类器与训练耗时回归器，把 R2/R3 阈值升级为模型输出。

  - B 方案 标签富化 + 数据筛选（已完成）：多维标签与分层建模数据。
    - `label_enrichment.py`：每 run 计算 7 个新标签——`training_collapsed`（峰值后跌穿 0.5×peak 且未恢复 0.8×peak、塌缩段跨度 ≥20% 观测窗口）、`best_step` / `best_step_ratio`（分母=观测窗口末 timesteps，无 eval 回退 total_steps）、`collapse_step`（首个 <0.5×peak 的点，未塌缩为 None）/ `collapse_ratio`、`stop_justified`（verdict=fail 或塌缩且 best_step_ratio<0.7 且 collapse_ratio<0.8）、`data_quality`（单向引用 data_screening.classify）；输出 `data/modeling/enriched_labels_YYYY-MM-DD.csv`（UTF-8 BOM，schema 校验）。
    - `data_screening.py`：4 条客观筛选规则（① eval 点数≥10；② max(末 timesteps, total_steps)≥500k；③ **factors 清洗后** approx_kl_last<1.0 且 eval mean_reward 无 NaN——原始 122376 类解析异常不误杀；④ std(mean_reward)>1.0），`classify` 异常优先：rule3/4 任一失败→`anomalous`，仅 rule1/2 失败→`insufficient`，全过→`good`；输出 `screened_dataset_*.csv`（good×28 因子×富化标签）、`anomaly_archive_*.csv`（含 fail_reasons）、`screening_summary_*.md`。
    - 全量 vs 筛选对比（防幸存者偏差）：Config A = screened 训练 + **full 测试**；Config B = full 训练 + full 测试；A 差于 B → SURVIVORSHIP BIAS WARNING；有标签样本 <10 → 标注“框架验证，不具统计显著性”。筛选=分层非删除：clean 训练、anomaly 做鲁棒性测试、full 做最终回测。
    - 集成：`config.json` 新增 `screening` 段（9 键全可配）；`schema.py` 新增 `enriched_labels` 表；`modeling.py --screened`、`baseline.py --screened`、`run_windows.bat --labels/--screen`；cron 每日追加两模块（复用 `.quant_last_date`）。
    - 当前数据（2026-08-18，25 runs）：good 14 / insufficient 11 / anomalous 0；塌缩 8 / 早停合理 9；balance/seed00 集成达标（collapsed=True, best_step=1900000, best_step_ratio=0.475, collapse_step=2100000, stop_justified=True, data_quality=good）；duration 对比 A（screened→full）R2=0.7526 优于 B（full→full）R2=-0.497，无幸存者偏差警告。

  - 公式审计修复（已完成，2026-08-18）：
    - P0：`data_screening.py` Config A 原为 screened 训练 + full 测试且训练/测试重叠，R2=0.7526 为虚高；
      改为逐样本留一（LOO）后 A R2=-2.4958（B=-0.627），正确触发 SURVIVORSHIP BIAS WARNING。
    - P1：新增 `eval_neg_ratio` / `eval_neg_ratio_recent`（负奖励占比，recent>50% → R2 stop，整体>30% → R1）；
      `eval_slope_per_1e6` 由首末差分改为最近 5 点最小二乘（config `risk.eval_slope.window`）；
      激活孤儿因子 `eval_std_recent`（<0.01 且 eval 点数≥5 → R1 std_reward_collapse，config `risk.std_reward.collapse`）；
      补齐歧义因子 docstring（ev_neg_streak / reward_peak_ratio / eval_std_recent，不重命名）。
    - P2：新建 `MATH_LIBRARY.md`——公式与口径权威库（审计记录、30 因子公式总表、关键公式、阈值总表、命名澄清）。
    - 当前数据（2026-08-18）：balance/seed00 触发 R2/neg_ratio（recent=100%）决策仍为 stop；
      rule_backtest 赔率表新增 R2/neg_ratio（4 触发）与 R2/std_reward_collapse（1 触发）。

  - 实时监控（已完成，2026-08-18）：`realtime_monitor.py`——Windows 侧建议制监控守护。
    - 每 5 秒轮询 webpanel（/api/state + /api/viewers），因子字典适配器复用 factors.run_risk_items（不重实现阈值）；
    - stale/NaN 直接触发（stall>60min 带 stale 标志 / nan 标志 → R2）；新 run 检测（步数回退或 alive 翻转）整体重置状态；
    - config.json 新增 `monitor` 段；requirements.txt 追加 requests；run_windows.bat 新增 --monitor。

## 十、假设与边界

- 服务训练过程管理（非投资交易），范围限定本仓库数据。
- 只读 `data/datasets`，不修改采集逻辑与 schema；新增 `data/reports` 入库推送。
- 建议制：报告不自动执行早停/调参，人工确认后执行。
- `data/.quant_last_date` 与 `data/quant.log` 已加入 `.gitignore`。
- 阈值均为默认值，按实际训练节奏在 `config.json` 调整后重新运行即可生效。