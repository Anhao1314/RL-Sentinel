> **RETIRED · 历史记录（v1）** 本文描述的是已归档的第一代系统（在线预测 / 建议制止损 / CSV 分析管线），
> 不代表本仓库当前定位。当前定位见 [README](README.md)
> 与 [ADR 0001](docs/adr/0001-from-prediction-to-evidence.md)。
> 代码与数据已归档至 `archive/v1` 分支，见 [ARCHIVE.md](ARCHIVE.md)。

> 当前状态：外部通知已移除；机器参数使用 config.local.json。全程特征基线仅用于事后分析，不能用于在线早停或 ETA 预测；历史指标保留原始口径。

# MATH_LIBRARY：公式与因子口径库（go2w-quant）

> 2026-08-18 全量公式审计结论：1 个关键 bug（screening Config A 数据泄漏）已修复、4 处命名歧义已澄清、1 个缺失因子（eval_neg_ratio）已补齐。
> 2026-08-18 第二轮（P0 后续）：基于 consistency_check 差异统一离线/实时因子口径，新增 4 因子（kl_divergent / current_stall_minutes / restart_count / neg_ratio_current）。
> 2026-08-19：上线 `early_low_reward`（前 25% 步数评估奖励始终低于阈值 → R2 stop，建议制）。基于 19 个历史 run 回测：触发 7 个（6 fail + 1 unknown）、0 pass 误杀；pass 样本仅 2 个，需持续跟踪样本外验证。阈值：traverse*=30、balance*/full_chain*=50。
> 本文是 factors/quant/modeling/label_enrichment/data_screening/backtest_* 全部公式的权威口径说明；代码注释与本文不一致时以本文为准。

## 一、审计状态与修复记录（2026-08-18）

| 类别 | 内容 | 处理 |
|---|---|---|
| 关键 bug | `data_screening.py` Config A 训练集（screened 7 样本）与测试集（full 17 样本）重叠，R2=0.7526 为虚高 | 改为逐样本留一（LOO）：任一测试样本不在自身训练折内；R2 可能显著下降，属正确结果 |
| 命名歧义 1 | `ev_neg_streak` 易被理解为历史最长负值段，实为**尾部连续**为负个数 | 已写清 docstring，不重命名 |
| 命名歧义 2 | `reward_peak_ratio` 易被理解为峰值占比，实为**最终奖励/峰值奖励**，可负可 >1 | 已写清 docstring，不重命名 |
| 命名歧义 3 | `eval_std_recent` 易被理解为 mean_reward 的 std，实为 **std_reward 列（评估内策略输出 std）最近 5 点均值** | 已写清 docstring，并激活为风控规则 |
| 命名歧义 4 | `eval_slope_per_1e6` 原为首末差分，噪声敏感 | 改为最近 N 点最小二乘斜率（默认 N=5） |
| 缺失因子 | 崩溃早期信号缺失：drawdown 需等奖励跌破 50% 峰值才报警 | 新增 `eval_neg_ratio` / `eval_neg_ratio_recent`（负奖励占比），先于回撤报警 |
| 口径统一 1 | `eval_neg_ratio_recent`：实时取全部 history(20) vs 离线 recent_window=5（24/80 不一致） | 实时改为 `history[-recent_window:]`，与离线一致 |
| 口径统一 2 | `approx_kl_last`：实时当前点损坏→None vs 离线回退窗口最后有效值 | 实时状态机新增 `last_valid_kl`（损坏回退）+ `kl_divergent`/`kl_divergent_streak` |
| 口径统一 3 | `stall_minutes`：离线 max-so-far（持久）vs 实时当前（恢复归零），80/80 语义差异 | 实时 `stall_minutes` 改 max-so-far（`max_stall_minutes` 状态）；新增 `current_stall_minutes`（归零口径） |
| 口径统一 4 | `eval_neg_ratio`：实时重启时重置 vs 离线全部尝试混合（38/80 不一致，5 次回退） | 实时新增 `total_neg_count/total_poll_count`（重启不重置）；原计数迁为 `neg_ratio_current`（当前尝试） |
| 新增因子 | 重启/数据损坏信号缺失 | 新增 `kl_divergent`（KL 超界，NaN 计发散）、`restart_count`（规格回退 >100k→<10k）、`neg_ratio_current`（重启分段） |
| 新增因子 | 从未学会型失败信号缺失（低奖励 run 早期不报警） | 新增 `early_low_reward`（前 25% 步数 max(mean_reward) < 任务阈值 → R2 stop，建议制）；实时状态机 eval_history 同规则计算 |

## 二、因子公式总表（六类 35 因子 + 富化标签）

### 收敛/稳定（eval_points，每 50k 步评估）
| 因子 | 公式 | 说明 |
|---|---|---|
| eval_points | len(eval 点) | 评估点总数 |
| eval_last_timesteps | 末点 timesteps | 观测窗口末端 |
| eval_last_reward / eval_peak_reward | 末值 / 全局最大 mean_reward | 单点值 |
| eval_drawdown | min(1, (peak-last)/peak)，peak<=0 时 0 | 裁剪到 [0,1]，末值为负不再出现 >1 伪回撤 |
| reward_peak_ratio | last / peak | **最终奖励/峰值奖励**，可负可 >1 |
| eval_slope_per_1e6 | polyfit(timesteps, mean_reward, 1)[0] × 1e6（最近 N=5 点） | 最小二乘斜率，抗单点噪声；有效点 <2 或步长无变化时为 0 |
| eval_std_recent | mean(tail(5, std_reward)) | **std_reward 列均值**（评估内策略输出 std），非 mean_reward 的 std |
| eval_neg_ratio | mean(mean_reward < 0)，全部有效点 | 负奖励占比 |
| eval_neg_ratio_recent | mean(mean_reward < 0)，最近 5 点 | 崩溃早期信号，先于回撤报警 |
| neg_ratio_current | mean(mean_reward < 0)，最后一次规格回退之后的 eval 点 | 当前训练尝试占比（重启感知）；无回退时 = eval_neg_ratio |
| progress_ratio | eval_last_timesteps / total_steps | 训练进度 |
| early_low_reward | max(早段点 mean_reward) < 任务阈值；早段点 = timesteps ≤ total_steps×0.25 的有效 eval 点（≥3 点） | 从未学会型失败 → R2 stop（建议制）；阈值 traverse*=30 / balance*=50 / full_chain*=50 / 默认 30；total_steps 优先 runs 行，缺失回退最后 eval timesteps |

### 健康（tb_points，TensorBoard rollout）
| 因子 | 公式 | 说明 |
|---|---|---|
| approx_kl_last | 过滤 (0,1] 后最后一个有效 approx_kl | 脏值（<=0 或 >1，如日志损坏 122376）置空 |
| std_last | 最后一个 rollout 策略输出 std | 与 eval_std_recent 不同源（rollout vs 评估） |
| ev_neg_streak | 从尾部开始连续为负的 explained_variance 个数 | **非历史最长** |
| value_loss_divergent | 尾部连续上升 ≥10 点且末值 > 前 20 点均值 ×2 | 发散判定 |
| kl_divergent | 当前 tb 点 approx_kl 不在 (0,1]（含 NaN）→ True | 数据损坏/发散标志；按 eval 点对齐（step<=eval 的最后 tb 行原始值） |
| kl_divergent_streak | 尾部连续发散的 eval 点数（无对齐时按 tb 行数） | NaN 也计发散，与实时逐轮口径一致 |

### 验收（reports）
report_rows / verdict_fail_ratio / success_rate_mean / max_dev_max / min_clear_min / falls_mean / nan_count（NaN 行数）。

### 资源（snapshots，30s 采样）
snapshot_count / time_span_minutes（起止时间差）/ timesteps_growth / stall_minutes（窗口内最大停滞 max-so-far，持久）/ current_stall_minutes（当前连续停滞，恢复归零）/ restart_count（规格回退 >100k→<10k 次数）/ idle_minutes（CPU 低于阈值持续分钟）/ cpu_percent_max / mem_percent_max / swap_percent_max。

### 成本（costs）
daily_cost / weekly_cost / monthly_cost / total_cost（元）、cache_rate = cached/(input+cached) token 缓存率。

### 富化标签（label_enrichment.py）
| 标签 | 公式 | 说明 |
|---|---|---|
| training_collapsed | 峰值后存在 reward < 0.5×peak，且其后至末尾 max < 0.8×peak，且塌缩段跨度 ≥20% 观测窗口 | V 形恢复不算塌缩 |
| best_step | 全局最大 mean_reward 的 timesteps | |
| best_step_ratio | best_step / 最后 eval timesteps（无 eval 回退 total_steps） | balance/seed00 = 1.9M/4M = 0.475 |
| collapse_step | 首个 reward < 0.5×peak 的 timesteps | 未塌缩为 None |
| collapse_ratio | collapse_step / 最后 eval timesteps | |
| stop_justified | verdict=fail 或（塌缩 且 best_step_ratio<0.7 且 collapse_ratio<0.8） | 末段塌缩不可早停 |
| data_quality | data_screening.classify() | good / insufficient / anomalous |

## 三、关键公式定义

- **回撤裁剪**：`eval_drawdown = clip((peak - last) / peak, 0, 1)`；末值为负时旧公式产出 >1 的伪回撤（1.1884、1.735），已裁剪。
- **最小二乘斜率**：对最近 N 点 (timesteps, mean_reward) 做 1 阶多项式拟合，斜率 ×1e6；比首末差分抗噪（审计案例：首末 7.5e6 → LS -1e6）。
- **KL 合理性过滤**：`0 < approx_kl <= 1`（PPO 域内合理区间）；日志损坏值（122376 等）置空不入库。
- **KL 发散**：当前 tb 点（按 eval 点对齐）approx_kl 不在 (0,1]（**含 NaN**）即 `kl_divergent=True`；`kl_divergent_streak` = 尾部连续发散计数，≥3 个 eval 点 → R2，≥5 → R3。实时状态机在每轮轮询维护 last_valid_kl 与 streak，当前点损坏时 `approx_kl_last` 回退 last_valid_kl。
- **当前停滞**：`current_stall_minutes` = 窗口/轮询末端仍在持续的停滞段分钟数（timesteps 变化即归零）；与 `stall_minutes`（max-so-far，持久）并存，规则复用 watch=30/warn=60。
- **重启计数**：`restart_count` = timesteps 从 >100k 回退到 <10k 的次数（规格定义，如 1384448→12288 不计）；≥2 → R2，≥4 → R3。离线从 snapshots 序列检测，实时状态机在回退轮 +1 并重置当前尝试计数器（total_* 不重置）。
- **当前尝试占比**：`neg_ratio_current` = 最后一次重启点之后 eval 点的负奖励占比（离线以最后回退行的截面时间为分段边界）；无回退/无快照时 = `eval_neg_ratio`。
- **从未学会型（early_low_reward）**：早段点 = `timesteps <= total_steps × 0.25` 的有效 eval 点；`len >= min_early_points(3)` 且 `max(mean_reward) < 任务阈值`（traverse*=30、balance*/full_chain*=50、其他 30）→ True → R2 stop（建议制，不自动止损）。实时侧以状态机 eval_history（按奖励值变化去重）同规则计算。基于 19 个历史 run 回测：触发 7 个（6 fail + 1 unknown）、0 pass 误杀；pass 样本仅 2 个，需持续样本外跟踪。
- **负奖励占比**：`eval_neg_ratio = mean(reward < 0)`；recent 版取最近 5 点。balance/seed00 = 37/80 = 0.4625（R1），recent = 1.0（R2 → stop）。
- **停滞判定**：进度 ≥60% 且 最终奖励 < 峰值 ×60% → R2 stagnation。
- **stall/idle**：timesteps 无增长持续 ≥30/60 分钟（watch/warn）；CPU <20% 且训练未完成持续 ≥60 分钟疑似卡死。
- **fail_score（软标签代理，backtest_rules）**：`0.2×(1-completed) + 0.3×(verdict=='fail') + 0.2×stagnation_score + 0.2×divergence_score + 0.1×(1-reward_rank)`；仅辅助指标，不进命中率分母。
- **t_end 外推**：completed run 用实际结束时间；incomplete 优先 `total_steps / (触发时 timesteps / 触发时已耗时)`（step_rate），缺失回退同 task completed 平均时长；`t_end_method ∈ {actual, step_rate, task_mean}`。
- **成本拆分**：`saved_yuan_compute = 节省小时 × gpu_count × gpu_hourly_price`；token 成本仅做项目级日趋势，不逐 run 归因。
- **筛选规则（data_screening）**：① eval 点数 ≥10；② max(末 timesteps, total_steps) ≥500k；③ factors 清洗后 approx_kl_last <1.0 且 eval 无 NaN；④ std(mean_reward) >1.0（n<2 视为通过）。rule3/4 任一失败 → anomalous（异常优先），仅 1/2 失败 → insufficient。
- **Config A 无泄漏 LOO**：full 中每个样本 i 的训练集 = screened \ {i}（i∈screened）或全部 screened（i∉screened）；逐折独立填充/标准化/拟合，汇总 full 全部样本的 R2/MAE 与 accuracy；verdict 折内门禁：训练 ≥4 标签且正负各 ≥2。

## 四、风控阈值总表（config.json risk 段）

| 配置键 | watch | warn/severe | 说明 |
|---|---|---|---|
| drawdown | 0.15 | stop 0.30 | 峰值回撤 |
| neg_ratio | 0.30 | severe 0.50（recent_window=5） | 负奖励占比，R1/R2 |
| std_reward.collapse | 0.01 | - | 评估内策略输出 std 塌缩（R1，eval 点数 ≥5） |
| std (tb) | collapse 0.05 | explode 1.50 | rollout 策略 std |
| approx_kl | watch 0.05 | warn 0.10 | 过滤后末值 |
| explained_variance | - | neg_points 10 | 尾部连续为负 |
| stagnation | progress 0.60 | reward_ratio 0.60 | 高进度低奖励 |
| stall_minutes | 30 | 60 | timesteps 无增长 |
| resources | cpu 95 / mem 95 / swap 50→80 / idle 60 分钟 | | 过载与卡死 |
| nan | warn 1 | severe 3 | 验收 NaN 行数 |
| budget | 80% | 100% / 150% | 日/周/月预算 |
| cache_rate_watch | 0.30 | - | token 缓存率 |
| eval_slope.window | 5 | - | 斜率窗口（P1b） |
| kl_divergent | - | r2 3 / r3 5 | KL 超界连续 eval 点数 |
| restart_count | - | r2 2 / r3 4 | 规格回退次数 |
| neg_ratio_current | 0.30 | severe 0.50 | 复用 neg_ratio 阈值（当前尝试） |
| current_stall_minutes | 30 | 60 | 复用 stall_minutes 阈值（当前停滞） |
| early_low_reward | traverse 30 / balance 50 / full_chain 50 / 默认 30 | min_early_points 3 | 前 25% 步数奖励始终低于阈值 → R2 stop |

## 五、命名歧义澄清表（不重命名）

| 因子 | 实际含义 | 易误解为 |
|---|---|---|
| ev_neg_streak | 尾部连续为负个数（非历史最长） | 历史最长负值段 |
| reward_peak_ratio | 最终奖励/峰值奖励，可负可 >1 | 峰值占比 |
| eval_std_recent | std_reward 列（评估内策略输出 std）最近 5 点均值 | mean_reward 的 std |
| eval_slope_per_1e6 | 最近 N 点最小二乘斜率 ×1e6 | 首末差分斜率 |
| best_step_ratio | best_step / 观测窗口末 timesteps | best_step / total_steps |
| collapse_ratio | collapse_step / 观测窗口末 timesteps | 塌缩段长度占比 |
| stall_minutes | 窗口内最大停滞（max-so-far，持久） | 当前停滞（恢复归零）——后者为 current_stall_minutes |
| eval_neg_ratio | 全部尝试混合负奖励占比（重启不重置） | 当前尝试占比——后者为 neg_ratio_current |
| kl_divergent | 当前 tb 点 KL 不在 (0,1]，NaN 也计发散 | 仅 >1.0 才发散（NaN/缺失同样发散） |
| restart_count | 规格回退（>100k → <10k）次数 | 任意步数回退（12288 等小回退不计） |

## 六、规则独立贡献审计（2026-08-19）

> ⚠️ 本审计基于 11 个首次 stop 事件的回测分析，当前 pass 样本仅 2 个，结论待 slope、full_chain_simple 等样本外数据持续验证。不构成规则精简依据。

### 审计方法
对每个 stop 事件的触发因子集合，计算「删除某规则后该 run 是否仍被其他规则覆盖」，以此衡量每条规则的独立贡献。

### 核心结论

| 规则 | 触发数 | 独立命中数 | 评估 |
|---|---|---|---|
| early_low_reward | 7 | 4 | ✅ 不可替代（唯一覆盖「从未学会型」） |
| drawdown | 6 | 0 | ⚠️ 无独立命中 |
| stall | 6 | 0 | ⚠️ 无独立命中 |
| cpu | 5 | 0 | ⚠️ 无独立命中 |
| neg_ratio | 5 | 0 | ⚠️ 无独立命中 |
| neg_ratio_current | 4 | 0 | ❌ neg_ratio 的完全子集 |
| restart | 2 | 0 | ⚠️ 无独立命中 |
| std | 2 | 0 | ⚠️ 无独立命中 |
| approx_kl | 2 | 0 | ❌ cpu/drawdown/stall 的完全子集 |
| kl_divergent | 1 | 0 | ❌ 6 条规则的完全子集 |
| std_reward_collapse | 1 | 0 | ❌ 7 条规则的完全子集 |

### early_low_reward 的 4 个独立命中
删除 early_low_reward 后，以下 4 个 run 不再被任何规则触发：
- traverse_curve_v1/seed00（fail）
- traverse_curve_v2/seed00（fail）
- traverse_curve_v3/seed00（fail）
- traverse_v1/seed00（unknown）

→ early_low_reward 是当前唯一能覆盖「从未学会型」失败模式的规则。

### 冗余关系
- neg_ratio_current ⊆ neg_ratio（4 个触发全部重叠）
- std_reward_collapse ⊆ std（1 个触发全部重叠）
- approx_kl ⊆ cpu ∩ drawdown ∩ stall（2 个触发全部重叠）
- kl_divergent ⊆ 6 条规则的交集（1 个触发全部重叠）
- drawdown ↔ stall：Jaccard=0.71（高度相关）
- cpu ↔ drawdown：Jaccard=0.57（高度相关）
- cpu ↔ stall：Jaccard=0.57（高度相关）

### 为什么不删除冗余规则
1. 小样本（11 个 stop 事件）下删除风险大，冗余规则可能在未来数据上有独立价值
2. 规则有诊断价值：即使触发重叠，不同规则指向不同失败原因（cpu=资源、kl=发散、stall=停滞），对训练端调参有参考意义
3. 等 pass 样本 ≥5 且 stop 事件 ≥20 后，再做规则精简

### 待验证事项
- slope（当前 41%，奖励 +8.72）验收结果：early_low_reward 是否误杀？
- full_chain_simple 启动后：规则在新任务上的表现
- 持续跟踪每条规则在样本外数据上的独立命中数

### 样本外验证记录

#### traverse_slope/seed00（2026-08-19，第一次样本外验证）
- 训练：从 best 断点续训到 8M
- 前 25%（2M）内最大奖励：-1.78（@1.4M），远低于 traverse 阈值 30
- 首次可判定步数：80 万步
- early_low_reward 触发：是
- 最终验收：fail（0% 成功率，平均距离 0.87m）
- 判断：正确命中（非误杀）
- 若 80 万步早停，可省约 7M 步（约 5-6 小时算力）
- 结论：规则在第一个样本外数据上表现符合预期，继续跟踪
