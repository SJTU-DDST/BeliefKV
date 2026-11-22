# R5 剩余路径预测与 sibling 联合准入校验

日期：2026-11-18 UTC，对应美国西部 2026-11-17。
本文继续[上一轮路径审计](r5_completion_path_audit_20261117_zh.md)；
所有新结果均为 CPU 同状态选择，不是 GPU 服务或 JCT 结果。

## 1. 联合目标和本轮结论

R5 应先按整个 workflow 的预测剩余模型＋工具关键路径排序，再让同一
真实阻塞 ALL-JOIN 的 ready sibling 共同获得可行的准入。sibling 关系、
ready 状态和容量来自 runtime，不能交给模型猜测。

保留上一轮已经实现的跨恢复类别分组：轮到某个 workflow 时，当前可见
ready sibling 整组放得下就共同选择；整组放不下则推进可行子集，
不等待尚未 ready 的成员。required JOIN 权利和 native 最终安全检查保留。
普通窗口、工具替换使用同一排序单位，不增加等待老化、fanout 效用加分、
成本比排序或为凑齐集合主动等待容量。

本轮新增工作量先验输入和联合校验训练入口。四折项目校验选出的路径头，
相对上一版路径头，将全程集合偏差从 227 次降至 180 次，多 ready sibling
组的漏选从 3 次降至 1 次。但测量 workflow 参与的集合偏差从 70 次
增至 74 次，共同吞吐窗口从 20 次增至 21 次。因此它是一个有明确
取舍的 CPU 候选，尚未证明完整测量 JCT 改善。

## 2. 偏差发生在哪里

复用原 sealed 数据：133 workflow、117,357 个因果进度快照，以及
3,134 个非空选择摘要，其中 3,127 个完整，七个保持未知。

- 完整边界中 983 个存在 JOIN 候选，166 个有多个 ready-paused sibling。
- 同一个 workflow 同时出现多个候选组仅一个边界；JOIN 组与其他组同时
  出现的边界为零。当前证据不足以支持增加同 workflow 内的组间评分。
- 给定日志原权重，旧契约审计没有发现可行短优先反序或可整体容纳却被
  拆散的可见 sibling 组。主要已证实问题仍是预测目标和排序偏差。
- 相对真实路径顺序漏选整个 JOIN frontier，与 selector 将本来放得下的
  sibling 拆开，是不同问题。单纯统计“部分成员入选”会混入容量不足，
  不能把这类记录全部当成 bug。

存在 1,150 个多 workflow 各自可容纳、合起来不能全部容纳的边界；
其中 316 个有测量 workflow 候选。444 个竞争边界有 JOIN 候选，
共 546 个可见 JOIN 组，其中 100 个有多个 ready sibling。
这些都是重复观察的规划边界或组次数，不是独立 workflow 数。

## 3. 预测实现

可选 `conditional_remaining_completion_path_with_work_prior_v2` 保留
`remaining_model_tool_critical_path_seconds` 目标，在原因果进度、
委派语义表示和已观察工具时钟之外，复用同一次推断已经得到的：

- `predicted_log_remaining_model_work`；
- `predicted_log_work_sigma`。

不再要求路径头从原隐藏表示独自重新学出模型工作量。没有第二次工作量
推断，也没有同步模型调用；最终候选仍是有界浅树。v2 工件绑定实际
模型工作预测器和连续读出的 SHA256，底层预测契约变化后不能静默复用。
原 v1 工件兼容。

还保留显式 residual v3 离线实验支持：预测相对冻结模型秒数的 log 残差。
它没有通过本轮联合选择，不启用该工件。模型秒数转换使用稳定的 log
运算，避免极大工作量溢出。不同单位之间仍通过冻结服务率转换。

新测试覆盖同一次工作量计算复用、工件绑定拒绝、共享 runtime 装配、
真实预测排序下跨恢复类别 sibling 共同准入，以及容量不足时的部分推进。

## 4. 训练选择和负结果

先固定原训练 103、校准 12、测量 18 的划分，比较三种工作量先验绝对
路径头和三种残差头。模型选择先检查真实竞争集合及 JOIN frontier，
再看排序和数值误差，不能只凭回归误差下降替换工件。

| 初次固定方案 | 全程集合偏差 | 测量参与集合偏差 | 全程多 ready sibling 组漏选 |
| --- | ---: | ---: | ---: |
| 原路径头 | 227 | 70 | 3 |
| work prior，64 轮 | 252 | 90 | 2 |
| work prior，128 轮 L2 | 190 | 79 | 1 |
| work prior，128 轮 L1，校准组选中 | 192 | 75 | 1 |
| residual，64 轮 | 203 | 67 | 5 |
| residual，128 轮 L2 | 169 | 66 | 1 |
| residual，128 轮 L1，校准组选中 | 186 | 79 | 2 |

十二条校准 workflow 的选择没有稳定迁移到测量组；没有依据测量组的
66 次结果倒选 residual L2。保留这些结果，改用其余 115 个完整项目的
四折校验，fold 为原项目名 SHA256 模四，大小为 32/33/27/23。
同一项目始终同折；含任一测量 workflow 的完整竞争窗口不参加选择。

每次选择按可行的 held-out workflow 计一次，避免同组多个 sibling
或频繁出现的 workflow 主导平均误差。需同时满足 workflow 选择误差
和 JOIN 选择误差不劣于同样交叉拟合的原路径头，再做候选选择。
工具特征支持范围只从各折训练成员获取；偏移量由非测量成员的 OOF
残差校准，最终仅在 115 个非测量 workflow 上重拟合。

| 四折方案 | workflow 等权选择误差 | workflow 等权 JOIN 误差 |
| --- | ---: | ---: |
| 原绝对路径结构 | 0.152804 | 0.122362 |
| 工作量先验绝对路径，选中 | 0.142365 | 0.104898 |
| 工作量先验残差 L2 | 0.159482 | 0.135442 |
| 工作量先验残差 L1 | 0.148682 | 0.116867 |

另验证直接面向相对路径顺序的 LambdaRank，每个 query 内一个 workflow
只出现一次，不给 fanout 加权。使用真实竞争 query，以及真实竞争和
4,096 个观测状态采样 query 的平衡方案；采样不读取目标，合成 query
仅用于训练，不作为实际竞争证据。

| 排序训练方案 | workflow 等权选择误差 | workflow 等权 JOIN 误差 |
| --- | ---: | ---: |
| 按 query 权重，仅真实竞争 | 0.152265 | 0.150505 |
| 按 query 权重，真实＋采样平衡 | 0.130738 | 0.119589 |
| 按 workflow 权重，仅真实竞争 | 0.167719 | 0.149473 |
| 按 workflow 权重，真实＋采样平衡 | 0.144674 | 0.143114 |

按 query 的平衡排序虽然改善普通选择，JOIN 误差仍高于选中的
0.104898；修正训练与评价的 workflow 权重后也未过联合门槛。
四个排序头均未推广，没有为追求单项误差更低而改变 oracle 或分组规则。

## 5. 同状态结果及边界

选中的绝对路径头为 128 轮、最多 16 叶、深度五，工件 SHA256：
`c86cc5d53a21e1da5c7ff18289b8fb02d4f212360e3d6acf557719a5caf288ef`。

| 相同记录和容量下的指标 | 上一版路径头 | 联合校验路径头 |
| --- | ---: | ---: |
| 全程集合偏差 / 1,150 边界 | 227 | 180 |
| 测量 workflow 参与集合偏差 / 316 边界 | 70 | 74 |
| 全程 JOIN frontier 不同 | 57 | 48 |
| 测量参与 JOIN frontier 不同 | 9 | 13 |
| 全程 oracle 完整可见 JOIN frontier 漏选 | 22 | 16 |
| 测量参与 oracle 完整可见 JOIN frontier 漏选 | 7 | 6 |
| 全程 oracle 多 ready sibling 组漏选 / 100 组 | 3 | 1 |
| 测量参与 oracle 多 ready sibling 组漏选 / 36 组 | 2 | 0 |
| 共同吞吐窗口集合偏差 / 95 边界 | 20 | 21 |

旧 Full 实际集合相对真实路径为 347/96 次偏差，因此两版路径头都比
旧 Full 的同状态记录更接近该参考；新头没有在所有评价范围内优于 v1。
“测量参与”表示候选中含测量成员，并不表示每次偏差都改变了测量成员
自身的选择。三个表的不同范围不能互相替代。

整个数据仍来自复用开发负载，原表示也复用了开发预测器；固定十八条已
被反复诊断。即使头部标签留出，也不是未见项目的独立泛化证据。
同状态重放不重算未来队列、原生恢复、实际选批或 workflow JCT。

局部 CPU0/nice19、单线程串行测量中，原模型工作量推断线程 CPU
P50/P99 为 151.259/157.661 微秒，加入选中路径头为
258.601/274.244 微秒。推断位于既有后台 worker，不在同步调度器等待。
六/八候选 ready-sibling 选批墙钟 P99 为 192.411/258.620 微秒，
样本中没有触发截止；该成本不含 native 前缀元数据、日志或完整控制。

第一次同核并行 CPU 测量的墙钟 P99 约四毫秒，八候选新分组有一次
软截止回退，原结果完整保留；后续串行测量不能覆盖这项负结果，
0.5ms 是算法的软预算，不能当成操作系统调度下的硬墙钟保证。
集成回归 594 项通过；此前 41 项聚焦检查与其重叠，不叠加计数。

## 6. 复现与运行隔离

证据位于 `experiments/reports/r5_completion_path_20261117/`：

- `joint_error_breakdown_r1.json`：完整 JOIN 边界与组统计。
- `joint_path_r1/`、`joint_path_r2_residual/`：固定校准选择及负结果。
- `joint_path_r3_crossfit/`：四折计划、各折指标、选中工件和完整评价。
- `joint_path_r4_rank/`、`joint_path_r5_equal_workflow/`：两种权重的排序
  负结果与未推广判断，复用缓存特征，不重新扫描 native audit。
- `joint_selected_choice_audit_r1.json`：独立按原候选与秒数目标核对。
- `joint_selected_cpu_cost_r1.json`、`joint_selected_cpu_cost_serial_r2.json`：
  并行和串行局部成本，均保留。
- `joint_prediction_integrated_r1.xml`：594 项集成回归。

新增入口为 `optimize_r5_join_path.py`、`crossvalidate_r5_join_path.py`、
`rank_r5_join_path.py`；继续复用 `audit_r5_completion_path.py` 和
`benchmark_r5_completion_path.py`。CPU 命令统一隐藏 CUDA、单线程、
CPU0、nice19。实验工件由已有 gitignore 忽略，代码和本文进入提交。

运行中的 repaired_r4 GPU 配对继续使用冻结 `ee1c166d`，仍是
133 项目、Host200 GB、initial20/mean55 秒，顺序为 Full+R5 对照、
同配置 Full+R5+D3；唯一实验差异是 D3 开关。没有热改该配对，
没有为这些小步 CPU 修改新增 GPU 实验。新 R5 工件需在一致代码集成后
验证完整 JCT、吞吐及服务顺序，不能将当前配对结果归因于本文候选。

## 7. 2026-11-22 复核

重新以 115 个非测量项目做四折评估。每个竞争者使用留出其自身项目的
预测，同一决策内的竞争者可以来自不同折。含固定 18 条候选的边界排除在
模型选择之外。候选同时通过全程和共同吞吐窗口的 workflow 与 JOIN 误差
门槛后，按留出误差选中 `prior_residual_l1`。

| 指标 | 原路径头 | 本轮残差路径头 |
| --- | ---: | ---: |
| 全程集合偏差 / 1,150 边界 | 227 | 185 |
| 共同窗集合偏差 / 95 边界 | 20 | 18 |
| 测量候选参与的集合偏差 / 316 边界 | 70 | 70 |
| 固定测量成员自身的选集偏差 | 50 | 56 |
| 共同窗固定测量成员自身的选集偏差 | 10 | 9 |
| 固定测量成员多 ready sibling 整组漏选 | 2 | 0 |

固定测量成员自身的选择共纠正 12 次，引入 18 次偏差，全部真实路径分数、
资源和候选窗口取自同一冻结诊断。残差路径头保留为 CPU 候选。
[交叉拟合结果](../../experiments/reports/d3_reclaim_r5_20261118/scheduling_screen/path_crossfit_oof_common_r1/evaluation.json) ·
[固定成员复核](../../experiments/reports/d3_reclaim_r5_20261118/scheduling_screen/path_crossfit_oof_common_r1/fixed_member_comparison.json)

修复并发进度事件引起的预测延迟误判。线程发布预测后，延迟从这份预测尚未
纳入的第一条观测起算。确定性用例先复现新预测被旧时间戳拒绝，再核对有
两条新观测排队时仍按较早观测实施超时回退。

历史 `repaired_r4` 的 D3-off 臂自然完成。D3-on 臂因高重算被终止，固定
测量组完成 17/18，该组没有完整平均 JCT。另一组已完成的 55 秒同配置
三臂 `5bfe45ad`，R5 固定 18 条平均 JCT 增加 2.51%，Oracle 下降
0.26%，共同窗输出分别下降 2.17% 和 1.42%。
[55 秒三臂完整读数](../../experiments/reports/d3_reclaim_r5_20261118/rotation_prepared/rotation_5bfe45ad/report_zh.md)

已用同一 55 秒到达表、tool overlay、固定成员和 cap-1 装配 Full、精确路径
Oracle 与预测 R5。两条排序臂使用 D3 动态门控和仅续接排序，采用此前已
装配的 guarded 路径头。三臂实际 runtime CPU 构造通过。
[CPU 装配](../../experiments/reports/d3_reclaim_r5_20261118/scheduling_screen/mean55_guarded_path_current_source_cpu_r1/arms/preparation.json)
