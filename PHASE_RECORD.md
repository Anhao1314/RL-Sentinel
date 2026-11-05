> **RETIRED · 历史记录（v1）** 本文描述的是已归档的第一代系统（在线预测 / 建议制止损 / CSV 分析管线），
> 不代表本仓库当前定位。当前定位见 [README](README.md)
> 与 [ADR 0001](docs/adr/0001-from-prediction-to-evidence.md)。
> 代码与数据已归档至 `archive/v1` 分支，见 [ARCHIVE.md](ARCHIVE.md)。

> 当前状态：飞书通知已移除；机器参数使用 config.local.json。全程特征基线仅用于事后分析，不能用于在线早停或 ETA 预测；历史指标保留原始口径。

# go2w-quant 阶段记录

> 记录项目从起源到当前阶段的完整历程、关键决策、经验教训与下一步计划。
> 最后更新：2026-08-19

---

## 一、项目起源

项目起源于一个问题："在三维仿真环境中，量化应该如何体现？"

逐步聚焦到一个具体场景：**宇树 Go2W 机器狗的 MuJoCo 强化学习训练**。训练一直不尽人意（奖励不收敛、策略崩溃、验收失败），希望用量化思路提供优化和决策支持。

### 核心目标

建立一个**量化决策支持系统**：在训练进行中基于可观测数据预测最终水平、剩余时长、失败概率，决定是否止损。不是做机器学习预测模型（样本不足），而是**规则引擎 + 统计回测**。

### 方法论

以**梁文锋/幻方量化思路**为指导：
- 风控优先，不做预测做赔率
- 回测是生命线
- 小样本先规则后模型
- 数据驱动，每条规则都要有回测证据
- 规则不在多，在每条都有独立价值

---

## 二、阶段时间线

### 阶段 1：基础设施建设（2026-08-18）

从零搭建完整的数据管线和规则体系：

| 模块 | 功能 |
|---|---|
| collector.py | 从 Linux 源目录解析数据为 CSV |
| factors.py | 风控因子纯函数计算（30→34→35 因子） |
| quant.py | v2 风控日报引擎 |
| backtest_rules.py | 规则止损历史重放，产出赔率表 |
| backtest_engine.py | 滚动回测框架（leave-one-future-out） |
| curve_fit.py | 幂律/指数学习曲线拟合 |
| label_enrichment.py | 7 维标签富化 |
| data_screening.py | 4 条筛选规则 + LOO 无泄漏对比 |
| realtime_monitor.py | Windows 端 5 秒轮询 webpanel + 本地风险展示 |
| consistency_check.py | 离线 vs 实时因子一致性校验 |
| annotate_alerts.py | 告警日志自动标注 |

### 阶段 2：关键审计发现与修复（2026-08-18）

#### 发现 1：balance/seed00 训练崩溃
- 峰值 99.98 @ 1.9M 步，最终 -18.8 @ 4M 步
- 触发 7 条规则（cpu/drawdown/kl_divergent/neg_ratio/restart/stall/std）
- 根因：训练后期策略灾难性崩溃

#### 发现 2：data_screening 数据泄漏
- Config A（screened→full LOO）假 R²=0.7526
- 修复后 R²=-2.4958（LOO 无泄漏）
- 正确触发 SURVIVORSHIP BIAS WARNING

#### 发现 3：因子口径不一致
consistency_check 发现 4 类因子离线/实时口径差异：
- eval_neg_ratio_recent：实时 history=20 vs 离线 recent_window=5
- approx_kl_last：实时只看当前点 vs 离线取窗口最后有效值
- stall_minutes：离线 max-so-far vs 实时当前归零
- eval_neg_ratio：实时重启重置 vs 离线全部尝试混合

**梁文锋思路的处理**：不消除差异，而是**把差异变成新因子**：
- 新增 kl_divergent / current_stall_minutes / restart_count / neg_ratio_current 四个因子
- 修复后 9 个关键因子 80/80 一致性通过

### 阶段 3：标签数据跃迁（2026-08-18 ~ 08-19）

| 时间点 | pass | fail | unknown | 事件 |
|---|---|---|---|---|
| 初始 | 2 | 2 | 26 | 只有 4 个有标签 |
| 首轮验收后 | 2 | 6 | 22 | Linux 端 4 个 fail 入库 |
| unknown 标注后 | 3 | 14 | 13 | 基于 eval 曲线人工标注 9 个 |
| 数据修正后 | 2 | 15 | 13 | balance report 同步 + curve_v2 作弊暴露 |
| flat/curve 验收后 | 2 | 16 | 12 | flat 正式验收失败 |

#### 数据修正的两个关键问题
1. **balance/seed00 reports.csv 不一致**：runs.csv=fail 但 reports.csv 仍为旧 pass，已修正
2. **traverse_curve_v2 穿墙作弊**：训练期奖励 132+ 但实际是走直线穿墙，物理修复后暴露为假 pass，已修正为 fail（label_source=manual_cheating_exposed）

### 阶段 4：数据字典审计（2026-08-19）

按 7 个条件审计所有数据源：
1. 代表什么
2. 单位是什么
3. 什么时候产生
4. 什么时候可用
5. 有无缺失
6. 有无复权
7. 有无后期修订

发现的问题：
- curriculum_steps/terrain/init_from/n_updates 四个字段 100% 空
- approx_kl 8.7% 缺失+损坏（>1.0 的异常值）
- snapshots 时间只到 8/18（collector 需源机运行）
- 奖励函数跨版本口径不一致

产出：DATA_DICTIONARY.md（7 条件数据字典）

### 阶段 5：early_low_reward 规则上线（2026-08-19）

#### 规则定义
前 25% 训练步数内，如果 mean_reward 始终低于任务阈值（traverse=30, balance=50, full_chain=50），判定为"从未学会型"失败，触发 R2 stop。

#### 回测验证
- 触发 7 个 run（6 fail + 1 unknown）
- 0 pass 误杀
- 新增命中 4 个（与现有规则重叠 3 个）
- 阈值 30 有安全 margin（traverse_flat_slope_v1 early_max=30.05 刚好不触发）

#### 规则审计
上线后做了 11 条规则的独立贡献审计，发现：
- **early_low_reward 是唯一有独立贡献的规则**（4 个 run 只有它能触发）
- 其他 10 条规则在当前样本下高度重叠，但不建议删除（小样本下删除风险大）
- neg_ratio_current 是 neg_ratio 的完全子集
- std_reward_collapse 是 std 的完全子集

产出：MATH_LIBRARY.md 新增"规则独立贡献审计"章节

---

## 三、当前状态（2026-08-19）

### 量化端

| 指标 | 值 |
|---|---|
| 总 runs | 30 |
| 标签分布 | pass=2, fail=16, unknown=12 |
| 因子数 | 35（6 大类） |
| 风控规则 | 11 条（R0~R3） |
| stop 事件 | 11 个（首次触发） |
| 误杀率 | N/A（pass 仅 2 个，no pass samples） |
| 因子一致性 | 9 个关键因子 80/80 通过 |
| 测试 | 202 passed |
| 实时监控 | 运行中（PID 8664，5 秒轮询） |

### 训练端（Linux）

| 任务 | 状态 | 说明 |
|---|---|---|
| balance/seed00 | ✅ 完成，fail | 峰值后崩溃，正式验收失败 |
| full_chain/seed00 | ✅ 完成，fail | 启动即倒 |
| traverse_curve/seed00 | ✅ 完成，fail | 航向惩罚 2.5，原地即倒 |
| traverse_flat_slope/seed00 | ✅ 完成，fail | 训练期奖励一度 37，最终不会上坡 |
| traverse_slope/seed00 | 🔄 训练中（41%→8M） | 奖励 +8.72 已转正，当前最有希望 |
| full_chain_simple | ⏳ 等待中 | 等 slope 完成后启动 |

#### curve 失败根因
"活着就给分" + 早停结构让"不动"成为最优解。航向惩罚 2.5 压制转向，机器狗选择原地站立 3 秒拿生存分，而不是冒险绕弯。

---

## 四、关键决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| 规则 vs 模型 | 先规则后模型 | 30 runs 小样本，任何模型都会过拟合 |
| 自动止损 vs 建议制 | 建议制 | 误杀率未验证（pass 仅 2 个），自动干预风险大 |
| 因子口径差异处理 | 变差异为新因子 | 不消除差异，而是把 max-so-far/current、全部尝试/当前尝试等差异显式化为独立因子 |
| early_low_reward 上线 | 上线 | 回测 0 误杀 + 4 个独立命中，符合上线条件 |
| 删除冗余规则 | 不删 | 小样本下删除风险大，冗余规则有诊断价值 |
| curve 补训 4M | 暂缓 | 根因是奖励结构不是步数，补训浪费算力 |
| ep_len_stall 规则 | 暂缓 | 边际价值低（3 触发中 2 个已被覆盖） |

---

## 五、经验教训

1. **回测是生命线**：每条规则上线前必须回测验证，data_screening 的假 R²=0.75 就是因为没做 LOO 无泄漏验证
2. **数据一致性比规则多更重要**：离线/实时因子口径不一致会导致回测结果不可信，consistency_check 是必要的
3. **标签质量决定一切**：balance report 不一致、curve_v2 作弊暴露，说明数据治理是持续工作，不是一次性的
4. **小样本下耐心比勤奋重要**：不要急于加规则、做模型，等数据积累
5. **训练端的根因分析比量化端加规则更有价值**：curve 的"不动最优"问题，量化端再加规则也解决不了，必须改奖励结构
6. **规则的独立贡献要定期审计**：11 条规则中只有 1 条有独立贡献，说明规则体系需要持续精简

---

## 六、下一步计划

### P0：等 slope 结果做样本外验证
- slope 进度 41%，奖励 +8.72 已转正
- 如果成功 → pass 2→3，误杀率开始有意义
- 如果 early_low_reward 误杀 → 调阈值或撤规则
- 这是 early_low_reward 的第一次样本外验证

### P1：curve 下一轮方案（训练端）
- **奖励结构改革**：取消纯生存分，改为"前进才给分" + 原地惩罚 + 早停惩罚
- **航向惩罚调整**：2.5 → 1.5 或动态调整
- **课程学习**：如果奖励改革仍学不会，先直道后弯道、渐进收窄走廊
- **Imitation 预热**：如果课程学习仍不行，用专家轨迹行为克隆预热

### P2：数据持续治理
- collector 在 Linux 源机运行，snapshots 数据更新到最新
- 继续标注 unknown run（还有 12 个）
- approx_kl 损坏值处理策略优化

### P3：规则体系精简（等 pass ≥5）
- 合并完全子集规则（neg_ratio_current → neg_ratio，std_reward_collapse → std）
- 每条规则的独立贡献持续跟踪
- 考虑缩短 early_low_reward 判定窗口（25% → 15%？）

### P4：监督模型（等样本足够）
- pass ≥5, fail ≥20 后，尝试 duration 回归、R∞ 外推等轻量模型
- 仍以规则为主，模型为辅

---

## 七、待验证事项

- [ ] early_low_reward 在 slope 上的样本外表现（是否误杀）
- [ ] slope 验收结果（pass or fail）
- [ ] full_chain_simple 训练表现
- [ ] curve 奖励结构改革后的效果
- [ ] pass 样本积累到 5 个后的误杀率计算
- [ ] 规则精简后的回测对比

---

## 八、文档索引

| 文档 | 内容 |
|---|---|
| PROJECT_CONTEXT.md | 给 Codex/AI 的技术上下文 |
| MATH_LIBRARY.md | 35 因子公式总表、阈值、命名澄清、规则审计 |
| DATA_DICTIONARY.md | 7 条件数据字典（字段定义/单位/口径/修订） |
| QUANT_PLAN.md | 项目路线图（B 方案 P0~P3） |
| AGENTS.md | 开发约定 |
| README.md | 项目简介和目录树 |
