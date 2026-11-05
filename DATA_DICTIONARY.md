> **RETIRED · 历史记录（v1）** 本文描述的是已归档的第一代系统（在线预测 / 建议制止损 / CSV 分析管线），
> 不代表本仓库当前定位。当前定位见 [README](README.md)
> 与 [ADR 0001](docs/adr/0001-from-prediction-to-evidence.md)。
> 代码与数据已归档至 `archive/v1` 分支，见 [ARCHIVE.md](ARCHIVE.md)。

> 当前状态：飞书通知已移除；机器参数使用 config.local.json。全程特征基线仅用于事后分析，不能用于在线早停或 ETA 预测；历史指标保留原始口径。

# go2w-quant 数据字典

> 本文档定义项目所有数据源的字段含义、单位、产生时机、可用性、缺失情况、口径一致性（复权）和后期修订记录。
> 最后更新：2026-08-19

## 一、数据源概览

| 数据源 | 文件 | 行数 | 覆盖 run 数 | 说明 |
|---|---|---|---|---|
| runs | data/datasets/runs.csv | 30 | 30 | run 元数据与验收标签 |
| eval_points | data/datasets/eval_points.csv | 1346 | 19 | 评估曲线（eval 时记录） |
| tb_points | data/datasets/tb_points.csv | 9015 | 19 | TensorBoard 训练指标 |
| snapshots | data/datasets/snapshots.csv | 16021 | 18 | 实时快照（webpanel 轮询） |
| reports | data/datasets/reports.csv | 15 | 7 | 验收报告（多场景） |
| labels | data/datasets/labels.csv | 30 | 30 | 标签汇总（含来源追踪） |

## 二、runs.csv（run 元数据）

| 字段 | 代表什么 | 单位 | 产生时机 | 可用时机 | 缺失率 | 口径/复权 | 后期修订 |
|---|---|---|---|---|---|---|---|
| task | 任务名 | - | run 创建时 | 始终 | 0% | v1/v2/v3 后缀代表不同版本，奖励函数和物理环境可能不同，跨版本不可直接比较 | 无 |
| seed | 随机种子 | - | run 创建时 | 始终 | 0% | - | 无 |
| owner | 运行方(cloud/local) | - | run 创建时 | 始终 | 50% | - | 无 |
| status | 运行状态 | - | run 结束时 | 结束后 | 50% | - | 无 |
| attempts | 尝试次数 | 次 | run 结束时 | 结束后 | 50% | - | 无 |
| completed | 是否完成 | bool | run 结束时 | 结束后 | 0% | 回测时强制设 False（模拟在线） | 无 |
| total_steps | 目标训练步数 | 步 | run 创建时 | 始终 | 73% | 影响 p(t)=timesteps/total_steps 因子 | 无 |
| envs | 并行环境数 | 个 | run 创建时 | 始终 | 73% | 影响训练速度对比 | 无 |
| curriculum_steps | 课程步数 | 步 | run 创建时 | 始终 | **100%** | 全空，字段待补全或删除 | 无 |
| terrain | 地形类型 | - | run 创建时 | 始终 | **100%** | 全空，字段待补全或删除 | 无 |
| init_from | 初始化来源 | - | run 创建时 | 始终 | **100%** | 全空，字段待补全或删除 | 无 |
| verdict | 验收结果 | pass/fail | 验收后 | 验收后 | 43% | 多场景验收取综合判定 | **有**：balance/seed00 从 pass→fail（正式验收退化）；traverse_curve_v2/seed00 从 pass→fail（穿墙作弊暴露） |
| success_rate | 验收成功率 | 0~1 | 验收后 | 验收后 | 43% | traverse 多场景取综合值 | 随 verdict 修订 |
| duration_seconds | 训练时长 | 秒 | run 结束时 | 结束后 | 40% | - | 无 |

## 三、eval_points.csv（评估曲线）

| 字段 | 代表什么 | 单位 | 产生时机 | 可用时机 | 缺失率 | 口径/复权 | 后期修订 |
|---|---|---|---|---|---|---|---|
| task | 任务名 | - | eval 时 | 始终 | 0% | 同 runs | 无 |
| seed | 随机种子 | - | eval 时 | 始终 | 0% | - | 无 |
| timesteps | 训练步数 | 步 | eval 时 | eval 后 | 0% | - | 无 |
| mean_reward | 平均奖励 | 奖励分 | eval 时 | eval 后 | 0% | **奖励函数改过**（航向惩罚 1.5→2.5、墙旋转修复、走廊宽度变化），跨版本不可直接比较 | 无（原始数据） |
| std_reward | 奖励标准差 | 奖励分 | eval 时 | eval 后 | 0% | - | 无 |
| mean_ep_len | 平均 episode 长度 | 步 | eval 时 | eval 后 | 0% | - | 无 |

## 四、tb_points.csv（TensorBoard 训练指标）

| 字段 | 代表什么 | 单位 | 产生时机 | 可用时机 | 缺失率 | 口径/复权 | 后期修订 |
|---|---|---|---|---|---|---|---|
| task | 任务名 | - | 训练时 | 始终 | 0% | - | 无 |
| seed | 随机种子 | - | 训练时 | 始终 | 0% | - | 无 |
| step | 训练步数 | 步 | 训练时 | 训练后 | 0% | - | 无 |
| ep_rew_mean | 滚动平均奖励 | 奖励分 | 训练时 | 训练后 | 0% | 同 eval_points.mean_reward，奖励函数口径问题 | 无 |
| ep_len_mean | 滚动平均 ep 长度 | 步 | 训练时 | 训练后 | 0% | - | 无 |
| std | 策略输出标准差 | - | 训练时 | 训练后 | 0.2% | - | 无 |
| value_loss | 值函数损失 | - | 训练时 | 训练后 | 0.2% | - | 无 |
| approx_kl | 近似 KL 散度 | - | 训练时 | 训练后 | 8.7% | 有 >1.0 的损坏值，用 kl_divergent 因子标记 | 无 |
| explained_variance | 值函数解释方差 | -1~1 | 训练时 | 训练后 | 0.2% | - | 无 |
| learning_rate | 学习率 | - | 训练时 | 训练后 | 0.2% | 全为 0.0003（固定学习率） | 无 |
| n_updates | 更新次数 | 次 | 训练时 | 训练后 | **100%** | 全空，字段待补全或删除 | 无 |

## 五、snapshots.csv（实时快照）

| 字段 | 代表什么 | 单位 | 产生时机 | 可用时机 | 缺失率 | 口径/复权 | 后期修订 |
|---|---|---|---|---|---|---|---|
| time | 时间戳 | Unix 秒 | 快照时 | 始终 | 0% | - | 无 |
| task | 任务名 | - | 快照时 | 始终 | 0% | - | 无 |
| seed | 随机种子 | - | 快照时 | 始终 | 0% | - | 无 |
| timesteps | 当前步数 | 步 | 快照时 | 快照后 | 0% | - | 无 |
| reward | 当前奖励 | 奖励分 | 快照时 | 快照后 | 44% | webpanel 5 秒轮询但奖励非每次更新 | 无 |
| cpu_percent | CPU 使用率 | % | 快照时 | 快照后 | 10.5% | - | 无 |
| mem_percent | 内存使用率 | % | 快照时 | 快照后 | 10.5% | - | 无 |
| load1/5/15 | 系统负载（1/5/15分钟） | - | 快照时 | 快照后 | 10.5% | - | 无 |
| mem_available_mb | 可用内存 | MB | 快照时 | 快照后 | 10.5% | - | 无 |
| mem_total_mb | 总内存 | MB | 快照时 | 快照后 | 10.5% | 仅 3 个值（不同机器） | 无 |
| swap_used_mb | 已用交换内存 | MB | 快照时 | 快照后 | 10.5% | - | 无 |
| swap_total_mb | 总交换内存 | MB | 快照时 | 快照后 | 10.5% | - | 无 |
| swap_percent | 交换内存使用率 | % | 快照时 | 快照后 | 10.5% | - | 无 |

## 六、reports.csv（验收报告）

> 多场景验收：traverse 任务有 bump/bumpy/flat/narrow/slope 五个场景，每个场景一条记录，verdict 为综合判定。

| 字段 | 代表什么 | 单位 | 产生时机 | 可用时机 | 缺失率 | 口径/复权 | 后期修订 |
|---|---|---|---|---|---|---|---|
| task | 任务名 | - | 验收时 | 始终 | 0% | - | 无 |
| seed | 随机种子 | - | 验收时 | 始终 | 0% | - | 无 |
| label | 验收场景名 | - | 验收时 | 始终 | 0% | 多场景(bump/flat/slope/narrow/curve) | 无 |
| verdict | 综合验收结果 | pass/fail | 验收后 | 验收后 | 0% | 综合判定（有场景失败即 fail） | **有**：balance/seed00 从 pass→fail（与 runs.csv 同步） |
| success | 该场景是否成功 | bool | 验收时 | 验收后 | 0% | - | 无 |
| success_rate | 该场景成功率 | 0~1 | 验收时 | 验收后 | 0% | - | 无 |
| max_dev | 最大俯仰偏差 | rad | 验收时 | 验收后 | 0% | - | 无 |
| min_clear | 最小离地高度 | m | 验收时 | 验收后 | 87% | 仅 balance 任务有意义 | 无 |
| dual_hold | 双轮保持时长 | s | 验收时 | 验收后 | 0% | 仅 full_chain 任务有意义 | 无 |
| recovered | 是否恢复站立 | bool | 验收时 | 验收后 | 0% | 全为 False | 无 |
| settle_seconds | 稳定耗时 | s | 验收时 | 验收后 | 93% | 仅 1 条有值 | 无 |
| distance | 行走距离 | m | 验收时 | 验收后 | 13% | balance/full_chain 无距离 | 无 |
| time_to_goal | 到达目标时间 | s | 验收时 | 验收后 | 40% | 仅 traverse 有 | 无 |
| falls | 摔倒次数 | 次 | 验收时 | 验收后 | 0% | - | 无 |
| total_reward | 验收总奖励 | 奖励分 | 验收时 | 验收后 | 0% | 同奖励函数口径问题 | **有**：balance/seed00 从 99.94→-18.8337 |
| mean_base_reward | 基础奖励均值 | 奖励分 | 验收时 | 验收后 | 0% | - | 无 |
| nan | 是否有 NaN | bool | 验收时 | 验收后 | 0% | 全为 False | 无 |

## 七、labels.csv（标签汇总）

| 字段 | 代表什么 | 单位 | 产生时机 | 可用时机 | 缺失率 | 口径/复权 | 后期修订 |
|---|---|---|---|---|---|---|---|
| task | 任务名 | - | 标注时 | 始终 | 0% | - | 无 |
| seed | 随机种子 | - | 标注时 | 始终 | 0% | - | 无 |
| completed | 是否完成 | bool | 标注时 | 始终 | 0% | - | 无 |
| verdict | 验收结果 | pass/fail | 标注时 | 标注后 | 43% | 应与 runs.csv 保持一致 | **有**：多次手动修正，label_source 追踪 |
| success_rate | 成功率 | 0~1 | 标注时 | 标注后 | 43% | - | 随 verdict 修订 |
| duration_seconds | 训练时长 | 秒 | 标注时 | 标注后 | 40% | - | 无 |
| label_source | 标签来源 | - | 标注时 | 始终 | 0% | auto=自动采集, manual=手动补录, manual_curve=曲线推断, manual_cheating_exposed=作弊暴露 | 修订追踪字段 |
| label_updated_at | 标签更新时间 | 时间 | 标注时 | 始终 | 0% | - | 修订时间戳 |

## 八、数据质量问题汇总

### 严重问题（已处理或需处理）

| 问题 | 状态 | 说明 |
|---|---|---|
| balance/seed00 标签不一致 | ✅ 已修正 | reports.csv 旧 pass 已同步为 fail |
| traverse_curve_v2 假 pass | ✅ 已修正 | 穿墙作弊，verdict 改为 fail，label_source=manual_cheating_exposed |
| 奖励函数口径不一致 | ⚠️ 需注意 | 航向惩罚 1.5→2.5、墙旋转修复、走廊宽度变化，跨版本 reward 不可直接比较 |

### 中等问题（建议处理）

| 问题 | 状态 | 说明 |
|---|---|---|
| curriculum_steps/terrain/init_from/n_updates 全空 | ⏳ 待处理 | 4 个字段 100% 缺失，建议删除或补全 |
| total_steps 73% 缺失 | ⏳ 待处理 | 影响训练进度因子 p(t) |
| snapshots 时间只到 8/18 | ⏳ 待处理 | 当前训练数据未入库，需启动 collector |
| approx_kl 8.7% 缺失+损坏 | ✅ 已缓解 | 用 kl_divergent 因子标记损坏值 |

### 轻微问题（可接受）

| 问题 | 说明 |
|---|---|
| snapshots.reward 44% 缺失 | webpanel 轮询机制导致，不影响核心分析 |
| reports 部分字段仅特定任务有意义 | min_clear/dual_hold 等是任务差异，正常 |
| learning_rate 全为 0.0003 | 固定学习率，正常 |

## 九、复权（口径一致性）说明

在本项目中，"复权"等价于**数据口径一致性**。以下因素会导致数据不可直接比较：

1. **奖励函数版本**：航向惩罚系数（1.5 vs 2.5）、到达奖励、速度上限等参数变化
2. **物理环境版本**：墙是否旋转、走廊宽度（0.40 vs 更宽）、地形参数
3. **模型初始化**：从零训练 vs 从 balance best 初始化 vs 从 full_chain_simple best 初始化
4. **训练步数**：4M vs 8M，不同总步数的 run 不能直接比最终奖励

**处理原则**：
- 回测和建模时按 task 版本分组，不跨版本混合
- 曲线拟合（curve_fit）在同一 task 版本内进行
- 因子计算（factors.py）不依赖绝对奖励值，只依赖相对变化（回撤、斜率、负值占比），因此跨版本仍可用

## 十、后期修订记录

| 日期 | 修订内容 | 影响字段 | 原因 |
|---|---|---|---|
| 2026-08-18 | balance/seed00 verdict pass→fail | runs.csv, labels.csv | 正式验收发现策略退化（奖励 -18.8, ep_len 80） |
| 2026-08-18 | traverse_curve/seed00 空→fail | runs.csv, labels.csv | 手动补录验收结果（0% 成功率） |
| 2026-08-19 | 9 个 unknown run 曲线推断标注 | labels.csv | 基于 eval 奖励曲线人工标注（+7 fail, +2 pass） |
| 2026-08-19 | balance/seed00 reports.csv 同步 fail | reports.csv | 与 runs.csv 对齐，total_reward 改为 -18.8337 |
| 2026-08-19 | traverse_curve_v2/seed00 pass→fail | runs.csv, reports.csv, labels.csv | 穿墙作弊暴露，label_source=manual_cheating_exposed |

---

*本文档随数据变更持续更新。新增字段或口径变化时，请同步更新本文档。*

## 观测与标签边界

`duration_seconds` 当前为同一 task/seed 快照的最大时间减最小时间，包含可能的暂停与观测缺口，不等同于实际计算时长。人工标签文件位于 data/datasets/manual_labels.csv；键必须唯一，success_rate 在 [0,1]，verdict 为 pass/fail/unknown 或缺失。最终验收因子仅可用于事后分析。
