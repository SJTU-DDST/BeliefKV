# Host400 完整对照与 Learned R5

2026-11-02。本记录复用已有非排序 Full 与 Direct Global Oracle，不启动
被用户取消的重复实验。补齐相同配置的 ThunderAgent acting-decay 对照，
并将改进的工作量预测器用于完整 R5 组合准入。两轮 learned 测量均已完成，
相对非排序 Full 均没有取得目标 JCT 改善；后续只改变成本视界的候选
已单独注册。本文件保留各阶段记录，完整结果和新候选边界见末尾。

## 完成的三组结果

三组都使用原 73 个不同项目、相同 token、工具时间、依赖、到达表和固定
18 条测量 workflow。Host 为 400 GB，HBM 为 805198 token，20 条初始
workflow，之后按原 mean70s 泊松表到达；running/decode24、prefill2048、
mixed off。保留测量成员的完整 JCT，18 条全部自然完成后停止非测量背景。

独立比较验证原生测量工作、完整尾部、两个新鲜空闲快照及实际进程退出。
Full 与 ThunderAgent 均为 2991 条测量请求、362157 个输出 token。
原 supervisor 保留固定组停止后的 `-2` 和调查状态；没有改写它的状态，
完成依据为明确的固定测量组证书与独立核验。

| 方案 | 平均完整 JCT（s） | P50（s） | P95（s） | 最大值（s） | 共同窗口全部输出（token/s） |
| --- | ---: | ---: | ---: | ---: | ---: |
| ThunderAgent acting-decay | 1190.928255 | 782.461329 | 3237.703689 | 4094.989289 | 324.579870 |
| Full，工作量排序关闭 | 1081.530918 | 673.016991 | 2838.953199 | 3422.641828 | 338.931253 |
| Direct Global Oracle | 1061.028850 | 638.093381 | 2844.416150 | 3388.554879 | 337.051589 |

Full 相比 ThunderAgent 平均 JCT 降低 9.185888%，18 条中 16 条改善、
2 条回退；共同窗口全部输出吞吐提高 4.421526%。共同窗口 decode 吞吐
分别为 336.160391 与 321.926488 token/s。Oracle 相比 Full 的平均 JCT
改善仍为先前的 1.895652%，不是本轮新增或重复得到的结果。

共同吞吐窗口固定为回放原点后
`[55.6927713505, 1638.9548502657]` 秒，包含背景工作。使用相同 30 秒
墙钟分箱，最后一箱保留实际长度；分布按墙钟时长加权。没有用固定 trace
总量除以最后完成时间替代这个指标。

ThunderAgent 的最后测量完成时间为 4507.933780 秒，最后计划到达为
3966.635036 秒，尾部有 541.298744 秒没有新到达，因此
`background_covers_both_measured_tails=false`。Full 与 Oracle 的尾部覆盖
检查通过。这是需要保留的负载边界：本轮完整 JCT 数值有效，但不能声称
三组都建立了持续到达稳态或通过全部正式负载验收。没有截去该尾部，
也没有因此重跑或修改到达表。

证据位于：

```text
experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  host400_registered_copy_pressure_r1/capacity_review_correction_r1/
    comparison_retry2.json
    thunderagent400_matching_r1/full_comparison.json
    thunderagent400_matching_r1/review/
    thunderagent400_matching_r1/three_arm_figures/
```

图已实际渲染并检查，包含完整 JCT 的经验分布与均值、decode 吞吐趋势及
时间加权分布，另保存 PDF、CSV 与输入哈希。使用固定 Okabe-Ito 配色，
三组各一次执行，不给出统计置信区间，不把 KV 池占用当作有效利用率。

## Learned R5 准备与执行范围

采用新增 16 个独立训练项目后的 59 条训练任务和 `.1` 排序损失候选。
原 12 条校准、18 条开发任务以及工作量分档保持不变。初始配对排序为
101/153；这是开发结果，不是新的盲测或 JCT 证据。首次请求分类仍有明显
误差，详见 `workflow_predictor_accuracy_20261102_zh.md`。

继承现有完整 R5：概率分布参与组合评分，默认六候选，必要时八候选，
0.5 ms 搜索软预算，保留 required JOIN 和已观察阻塞 ALL-JOIN sibling
组合；不会退化成 argmax 分档或单体排序逐个装入。

原预测模型携带旧负载的 v5 成本。新成本只来自本轮已完成非排序 Full：

| 已完成 Full 的成本观测 | 数值 |
| --- | ---: |
| batch/context 加权 decode 请求步代理 | 25.485950 ms |
| substantial prefill 的中位每新增 token 代理 | 0.064556 ms |
| substantial H2D 的中位每 token 代理 | 0.004496 ms |
| 五个上下文区间的单请求 substantial prefill 批次数 | 650 / 3574 / 1178 / 877 / 399 |

这些是 worker 区间代理，可能包含等待与提交开销，不是精确 GPU 秒数。
保留 16 步成本视界、完整 batch decode 成本和固定安全约束。

使用显式 `r5_frozen_batch_cost_proxy_capacity_v6`：组合层按实际完整 FULL
池的几何容量规划，当前 free/cold 仍如实记录。规划不提供空闲页，不保证
即时服务；原生分配、Host 副本、共享引用、锁、在途 DMA 与最终输入准入
检查保持权威。该变化与模型、组合选择合并评价，不称为单独排序收益。

派生模型只更换成本 profile 与溯源，预测权重、概率语义、分档、支持
范围和训练划分不变。派生 evaluation 将原 `expanded_split` 明确规范为
部署接口要求的 `split_contract`，绑定新模型哈希；原评价和结果保持不动。

## 复用已有服务资格

选中的模型是原有格式的普通 R5 MLP，不依赖本轮试验新增的语义编码器、
委派文本或初始状态变换。对 89 条任务的全部 63988 个已导出因果快照，
当前代码与本轮 Full/Oracle/ThunderAgent 所用冻结提交 `cd56c385` 的
预测概率及支持判断逐项一致；六个组合与工作集控制模块源码也逐字节一致。

冻结版本的实际 runtime 构造确认：

- `workflow_work_source=learned`，工作量分布与组合准入均启用；
- JOIN/sibling 支持及预测工具卸载保留；
- 工具预取与逐层 handoff 关闭。

新增 154 项针对性冻结版本 CPU 检查通过；另核对保留的 650 项数据平面
检查与源绑定没有变化，二者不是一次新的 804 项完整执行。准备层另有
69 项 CPU 检查通过。复用同一 `cd56c385/434c35dd` 服务与原生版本的
position、采样、400 GB 分配注册传输证据，没有再次执行这些 GPU 资格实验，
NUMA 不要求均匀分布。

新配置与资格证据：

```text
experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  r5_host400_learned_combination_r1/
    cost_calibration/
    frozen_serving_compatibility_r2.json
    frozen_constructor_and_tests.json
    serving_ready/{model,evaluation,full}.json
    launch.json
    registration_result.json
```

已经注册一次 learned R5，端口 18990：

```text
/home/longhao/experiment/ops/
  workflow_original73_host400_R5_learned_20261102_r1.registration.json
```

该注册使用新的同提交冻结目录，未修改旧三组源树或结果。注册时此前
ThunderAgent 全部实际 owner 已退出，GPU 空闲。服务已经实际进入回放。
独立 live prefix 核验记录：

- supervisor 471775、server 472264、driver 473807 的 PID/start_ticks 匹配；
- 唯一 GPU scheduler 为 472337，服务 owner 均为 nice0；
- 固定 18 成员观察器 473829 实际运行于 nice19；
- 原生已完成 1503 条精确请求、132535 个输出 token，position 采样与
  原生 sampler 保留，输出 self-hash 独立核对；
- 1599 次组合入口中有 155 次有效候选概率观测，21 次集合变化；
- 实际 profile identity 与新模型一致，worker error 为空，容量与对照一致。

这是运行前缀，不是 1599 次有效竞争、155 个独立 workflow，也不证明
21 次集合变化已全部保留授予、获得有界有效服务或改善 JCT。完整性能
仍待固定组自然完成后核验。live prefix 的第一次只读检查错误地把可省略
的 `oracle_information` 当作必需字段，修正为允许省略或 null、继续拒绝
实际 oracle 信息；原运行未重启，失败记录保留。没有登记 GPU 后继。

## 后续 CPU 修复：等待服务时的预测有效性

运行前缀中同时有有效预测和缺失预测。缺失观测并不全是同一种原因，
元数据不足、输入版本变化和缓存过期需要分开处理；不能把全部缺失或
当前性能变化归因于下面这项问题。

源码与真实 producer 线程的 CPU 复现确认一个覆盖缺口：ready workflow
没有继续获得模型服务，也没有新事件时，数值特征保持不变；worker 会
等待新观测，已有概率却在三秒后过期。即使 worker 健康且全部观测已经
消费，也会返回 None，使这个较早排队的候选失去有效排序预测。

新增查询时的有界有效性核对：

- 仅适用于输入不包含 elapsed time 的 R5 剩余模型工作分布；
- 原 producer 线程必须仍然存活，无停止或错误状态；
- 预测对象、已消费/已提交观测版本和结构版本必须完全一致；
- 不得有 dirty、pending 或未消费观测，终态工作仍立即撤销；
- 使用非阻塞锁，锁忙则沿用无预测回退，不等待推断；
- 成功时只延长有效期，保留原概率、版本和真实推断时间；
- 新观测、SPAWN/RETURN/JOIN 等结构变化仍要求新的有效推断。

这不增加定时扫描、模型推断或物理准入权限。若 producer 已退出或原
预测不受支持，过期预测仍不可使用。它改善的是在线预测可用性，不改变
分类或相对排序的离线数值。

修复前 CPU 报告为一项目标失败、五项通过，复现健康 ready workflow
的过期预测丢失；修复后 153 项相关检查全部通过。用选中的真实模型和
实际后台线程检查 18 条任务，初始概率与离线一致；1024 次模拟过期查询
全部通过核对，推断次数始终为 18。核对线程 CPU 的 P50/P99 约为
6.14/7.85 微秒，保留原始推断时间。这是组件成本，不是整轮调度或 JCT。

证据位于 `r5_host400_learned_combination_r1/cache_validity_cpu/` 的
`before.xml`、`after.xml` 和 `actual_worker_and_cost.json`。

该修复只在主线 CPU 候选中，未修改正在运行的 `cd56c385` 源树，也没有
把其收益计入当前 GPU 实验。后续验证必须冻结包含此 worker 修复的新
源码并核对实际状态协议，不能仅凭旧服务版本的概率数值一致证明缓存
有效性行为也已更新。

## Learned R5 完整结果

固定 18 条测量 workflow 已全部自然完成，独立重新核对了原始完成
证书、2991 条请求及 362157 个输出 token、两次新鲜 idle 快照和
实际 owned-process 退出。原 supervisor 保留 `stopped_for_investigation`
及 driver `-2`；这是完成测量组后停止背景的原始状态，不改写为全部
73 条完成。背景到达表覆盖了本轮测量组尾部。

| 方法 | 完整平均 JCT（秒） | 共同窗口输出 token/s |
| --- | ---: | ---: |
| 非排序 Full | 1081.530918 | 338.931253 |
| Learned R5，原冻结 worker | 1097.311075 | 339.857821 |

JCT 回退 1.459057%，吞吐提高 0.273379%。按用户的 1% JCT 改善与
98% 吞吐门槛，JCT 项失败，吞吐和背景覆盖项通过。这不是新的 oracle
结果，也不包含后续缓存有效性修复或新起始分支。

新增 `review_learned_work_ranking.py` 严格检查 Full→learned 的八个
环境变量变化、原生资源和完整测量执行；明确将条件分布、组合选择及
成本规划作为联合变化，不标成纯排序。原有 oracle 审计仍保留其独立
入口。66 项相关 CPU 检查通过，先前五项失败来自新测试 fixture 使用了
错误的成本字段名，修正为真实 dataclass 字段，没有放宽对照约束。

完整组合审计保留 8618 次入口、457 次有枚举元数据的搜索和 87 次集合
变化，其中 45 次严格缩小参考集合，42 次含参考集合外成员，共 44 个
新增选择 episode。周期路径的 15 个新增 episode 均有精确授予、压力后
资格和后续原生服务，14 个有下一控制/干预前的有效完成。后续补齐的
`complete_combination_service_with_free_scoped.json` 又验证 29 个直接
free 授予，全部有精确后续服务，22 个有下一控制/干预前的有效完成。
因此 44 个新增 episode 全部有授予及后续服务，其中 36 个有有界有效
完成。独立的 A→B 工具替换授予不计入 free 组合的服务归因；缺失元数据
仍为未知。原 `complete_combination_service.json` 保留其较窄审计范围。

候选预测观测有 769 次正常支持、1296 次缺失、26 次超出训练支持。
这些是重复观测，不能当成独立请求；缺失也可能来自新输入、结构撤销、
初始推断等原因，不能全部归因于三秒过期。组合元数据与选择累计线程
CPU 2.635 秒，后台 worker 累计线程 CPU 27.051 秒；二者都不等于完整
控制增量成本，也不能直接与平均 JCT 相减。

证据位于同一 learned 实验目录：

```text
complete_comparison.json
complete_combination_service.json
analysis_cpu.xml
review/{review,cohort,workload_decision}.json
```

下一候选将使用已修复的输入有效性协议及 CPU 合格的起始分支，保留
本轮 capacity-v6/full-batch 成本、相同 73 条原始任务、20 初始＋平均
70 秒到达、400 GB Host 和固定 18 成员。需要新的冻结预测器源码，
但原生、采样、position 和 400 GB 组件没有变化，应复用已有 GPU
资格与完整 Full/Oracle/ThunderAgent 对照，不重跑这些对照。该候选
尚需 source-bound CPU 和实际 runtime 构造检查，不能从本节推断
其已运行或取得改善。目标继续保持 active。

## 修正版的独立注册

修正版随后在干净冻结提交 `d94fe0de` 上完成了 1051 项 source-bound
CPU 检查，保留原来的全部 650 项数据平面 case；实际 runtime 构造和
真实 worker 的 18 条发布、1024 次无新推断有效性核对通过。只改变
预测器及 worker，原生 hook、准入与组合策略、回放采样均未改变，
capacity-v6/full-batch 成本逐项复用上一轮。

新注册为
`ops/workflow_original73_host400_R5_valid_predictions_20261102_r1.registration.json`，
端口 18992，实际执行目录为
`beliefkv-causal-d94fe0de2fd5-4d8549103075`。证据父目录为
`r5_host400_valid_predictions_r1/`，包括 `qualification.json`、
`frozen_cpu.xml`、`frozen_sources.json`、`frozen_worker_and_cost.json`、
`serving_ready/{model,evaluation,full}.json`。

首次注册在 GPU 前因 canonical patch 路径的派生 state hash 不同退出。
`native_path_rebinding.json` 证明仅路径与派生哈希改变，native tree、
patch 字节及其余字段全部相同；原 `registration_result.json` 保留失败，
有效注册见 `registration_retry_result.json`。后台启动观察器先于比较
状态文件发布，产生一次 CPU `FileNotFoundError`，原状态已另存
`observer_bootstrap_first_failure.json`，只重新挂接观察器，没有重启 GPU。

启动阶段已核对 supervisor 502046、server 502543、scheduler 502624 均实际
存活并为 nice0；CPU bootstrap 502949 为 nice19，等待正式回放后启动
原固定 18 条结束协议。该次观察仍在 400 GB Host 分配/注册阶段，不能称作
已经完成 workload 回放或修复获得 JCT 改善。NUMA 均衡不作为启动门槛，
未注册其他 GPU 后继。

上一轮 Full 与 learned 的完整 JCT、共同窗口 decode 趋势及时间加权
分布图已生成并目视检查，位于
`r5_host400_learned_combination_r1/completed_figures/complete_JCT_and_decode.{png,pdf}`；
同目录保留 CSV 与来源哈希。新候选未加入该图。

随后已独立核对正式 `running_full`：driver 504077 实际运行，固定 18 条
结束观察器 504095 已启动且为 nice19；GPU 仍仅由 scheduler 502624
占用，所有服务 owner 为 nice0。`live_prefix.json` 核对 63 条精确请求、
2705 个输出 token、实际容量、真实 server 环境、原生 sampler、position
及输出 self-hash，worker 无错误。

`live_predictor_protocol.json` 进一步核对真实执行源码与注册哈希一致，
实际 worker 已使用新模型指纹和起始分支权重 1，线上出现 119 次
`unchanged_input_validity_revalidations`。这是实际回放中的修复生效证据，
不是纯 CPU 模拟；仍不能归因 JCT。该前缀有 87 次组合入口、18 次正常
支持的预测观测、1 次集合变化，不与旧实验不同长度前缀直接比较。
完整测量 JCT、共同时间窗口及后续服务审计继续待完成，没有登记 GPU
后继，目标保持 active。

## 相同时间前缀的预测可用性

`matched_availability_prefix.json` 使用相同的回放时间前缀
`[55.6927713505, 910.8536377009]` 秒。上一轮 learned 的受支持预测观测
为 298 次、缺失 682 次，支持比例 30.4082%；修正版为 1151 次受支持、
67 次缺失，支持比例 94.4992%。这比较的是实际候选观测的预测可用性，
不是分类准确率。两个运行的候选人群不同，重复观测不等于独立请求；
缓存修复与起始分支联合变化，也不能单独归因或推出 JCT 改善。

同一时段的选择集合变化且含新成员分别为 16 和 23 次。窗口外的最新
worker 累计计数另列，不参与该比例。修正版另一个已封闭的服务前缀
`combination_service_scoped_prefix.json` 记录 26 个新增 episode：
10 个周期、16 个直接 free；全部有精确授予和后续服务，分别有 9 和
14 个有界有效完成。它与上一轮完整实验时长不同，不用次数直接比较收益。

## 工作量准确率审计修正

旧预测审计把恢复类别零当作 JOIN 保护；零类别也包含普通连续推理。
修正版按实际 global rank 零识别 JOIN，已通过 CPU 检查并在不重跑
GPU 的前提下重新审计封闭 Full 日志。普通有效配对由旧误算的 195 次
修正为 1542 次；两侧均为开发 workflow 的配对由 9 次修正为 89 次。
旧报告保持不变，但不再使用其错误排除人群描述准确率。

修正版中，原 43 训练模型、59 训练排序模型、再加起始分支的开发组
边界等权排序分别为 79.3056%、80.0000%、82.0833%；不同 workflow
对等权分别为 80.9921%、79.5635%、80.7540%。自然源初始 153 对的
结果不受影响。这是同一开发组的探索评价，没有新增盲测或 JCT 证据。
完整人群、工具路径和输入边界见 `workflow_predictor_accuracy_20261102_zh.md`
及 `live_prediction_quality_r1/corrected_global_JOIN_rank.json`。

## 修正版完整结果与原因诊断

修正版的 18 条测量 workflow 已全部自然结束，保持 2991 条请求、
362157 个输出 token、原始任务与工具时间。独立核验完成证书、两次
新鲜 idle 快照和所有实际 owner 退出通过。400 GB Host 的结束阶段
scheduler 子进程晚于主 server 退出，原清理协议完成后才审计，没有
重启实验或改写 supervisor 的 `stopped_for_investigation`/driver `-2`。

| 方法 | 完整平均 JCT（秒） | 共同窗口输出（token/s） |
| --- | ---: | ---: |
| 非排序 Full | 1081.530918 | 338.931253 |
| 首轮 learned R5，旧缓存协议 | 1097.311075 | 339.857821 |
| 修正版 R5，缓存修复＋起始分支 | 1097.776414 | 337.908049 |

修正版相对 Full 的平均 JCT 回退 1.502083%，吞吐下降 0.301891%；
9 条改善、9 条回退。最后测量完成为回放后 4044.046844 秒，背景最后
到达为 3966.635036 秒，存在 77.411808 秒未覆盖尾部。完整 JCT 保留
该尾部；吞吐仍为原共同窗口，不用最终完成时间作分母。目标 JCT 和
背景覆盖项未通过，吞吐 98% 非劣门槛通过，不能将本轮提升预测可用性
当作性能验收通过。

封闭前缀 `fixed18_JCT_lower_bound_prefix.json` 在尚有一个成员未结束
时，按所有 18 个固定成员、已完成 JCT 加未完成者已流逝时间计算，
已经证明平均 JCT 至少回退 0.448306%。该文件只用于确定本轮不可能
达到 1%，没有用它提前停止或替代最终结果。

完整组合服务审计记录 7140 次入口、638 次可重放搜索、128 次集合
变化和 56 个新增选择 episode。其中周期 23 个、直接 free 33 个，
全部有精确授予及后续原生服务；分别有 22 和 23 个在下一控制或干预
之前完成有效批次。独立 A→B 工具替换不计入 free 归因。选择元数据
和搜索累计线程 CPU 3.435 秒，不等于整个控制增量或可消除 JCT。

新的 `audit_learned_combination_prediction.py` 对同一封闭选择历史
分别代入实际缓存权重、由当时可见输入重算的模型权重、真实剩余模型
工作量的三档权重和连续权重。固定容量、成本和 required JOIN；只比较
590 次完整搜索，48 次未完整搜索另列，6502 次缺少可重放搜索元数据
的入口不猜测。连续权重为 `1/(1+remaining/short_threshold)`，
不等于包含工具关键路径的原 Direct Global Oracle。

| 相同完整搜索中的权重替换 | 改变实际选择集合次数 | 严格缩小参考集合次数 |
| --- | ---: | ---: |
| 实际缓存权重 | 0 | 50 |
| 可见输入同步重算 | 5 | 51 |
| 真实剩余工作量三档权重 | 31 | 55 |
| 真实剩余工作量连续权重 | 19 | 43 |

其中 155 次有普通跨 workflow 的规划容量竞争。实际缓存与同步模型在
开发组竞争中的边界等权排序均为 80.8333%，来自 38 次配对、20 个边界、
18 个不同对；不同对等权均为 69.4444%。该人群不同于 Full 对照上
89 次配对的准确率审计，不能直接用两个百分比解释模型回退。

这些结果说明，修复缓存之后仅消除观察滞后对本地集合的作用有限，
纯模型工作量排序完美也仍受当前组合目标限制。但 5/19/31 次变化
可能包含重要决策，不能据其次数推出整轮 JCT 上界，更不能证明成本
评分是唯一根因。未来标签只用于 CPU 诊断，不进入线上模型或调度器。
初次新增检查的两项失败来自 fixture 的 workflow 名与共享字典别名，
修正 fixture 后 39 项相关检查通过，没有放宽实际身份或 JOIN 检查。

完整证据位于 `r5_host400_valid_predictions_r1/`：

```text
complete_comparison.json
complete_combination_service_scoped.json
prediction_model_objective_diagnostic_r1.json
fixed18_JCT_lower_bound_prefix.json
review/{review,cohort,supply,periodic_decisions,demand,workload_decision}.json
```

`completed_figures/complete_JCT_and_decode.{png,pdf}` 已加入修正版 R5，
同时保留相同 Full、Global Oracle、ThunderAgent 和旧缓存 R5。图中
JCT 使用全部固定 18 成员，decode 趋势使用同一墙钟窗口，分布按各箱
实际时长加权；另保存 CSV 与输入哈希，PNG 已实际渲染检查。旧、新
R5 使用同一蓝色的虚线和实线，其他方法继续使用已有 Okabe-Ito 配色。
每个配置只有一次执行，没有推断统计置信区间，没有将占满 KV 池画成
有用 HBM 利用率。

资源压力和供给仍存在：共同窗口 ready-paused 样本比例约 72.35%，
27 个常规需求样本的最大可复用状态并集占组合容量约 60.88%。最终
同上下文已服务前缀丢失代理为零，但 Host-block 归因的额外重算为
16746 FULL 单位，不能把两类计数相加，也不能声称完全零重算。
当前没有 GPU 后继，目标保持 active。

## 按已完成调用校准成本视界

进一步发现一个需要实验验证的建模不对称：现有组合评分一次计入完整
恢复与 prefill 成本，却只计 16 步 decode。已完成相同 Full 对照的
10405 个完整调用，输出 P25/P50/P75 为 47/59/78 token，均超过
16 token，均值为 126.201153。选择 64 步作为中位数向上取整后的
候选，128 步仅做 CPU 敏感性检查。该选择在重放前声明，不根据候选
的 JCT 或最佳重放效果选择参数，也不读取线上当前请求的未来输出长度。

在相同 590 次完整搜索上，仅改变 `CombinationCosts.decode_steps`，
保持实际缓存概率、候选、容量、输入预算、锁与 JOIN 权利不变：

| 成本视界 | 改变实际集合 | 比实际集合更大 | 严格缩小参考集合 | 缩小且仍有可行扩展 |
| --- | ---: | ---: | ---: | ---: |
| 16 | 0 | 0 | 50 | 49 |
| 64 | 44 | 39 | 19 | 18 |
| 128，敏感性 | 59 | 50 | 9 | 8 |

64 步有 9 次新选择涉及 3 条测量 workflow；其余变化也保留，没有按
是否有利于测量组筛选。各表计数包含重复决策，不等于新增独立 agent、
实际服务次数或 JCT 改善。输入与缺失前缀计数同样是重复规划量，不能
把其差值当作新增真实 H2D 或重算。该修正没有增加最大化集合大小约束，
仍使用原组合目标，并保留 prefill 对 active 计算的成本。

新模型只修改成本 profile 的 `decode_steps: 16→64` 和派生 profile
identity；概率权重、初始分支、工作量门槛、支持范围、worker、controller
及全部原生代码相同。64 只进入规划成本，控制周期仍为 2 秒、事件兜底
为 500ms，原生 running/decode24 和 prefill2048 保持不变。

实际 CPU runtime 构造接受新 profile，模型加载确认原起始分支权重仍为
1。复用原冻结提交 `d94fe0de` 的 1051 项 source-bound CPU 报告及
position、采样、400 GB 组件证据，没有重跑这些未变化资格；不把复用
计成新执行的 1051 项检查。新 artifact 的完整参数、源树、原报告及
原实验完成证书均重新核对，证据为 `r5_host400_horizon64_r1/qualification.json`。

已注册一次新候选：

```text
ops/workflow_original73_host400_R5_horizon64_20261102_r1.registration.json
port: 18994
execution: .worktrees/beliefkv-causal-d94fe0de2fd5-78794b07ee43
supervisor: 532037 / start_ticks 783013704
server: 532577
sole GPU scheduler: 532662
CPU observer bootstrap: 532038
```

这些 owner 已实际核对存活，服务 owner 均为 nice0，bootstrap 为 nice19。
启动观察器处理比较状态文件尚未发布的阶段，不复用上一轮失败启动的
观察器。GPU 仅由本候选占用，尚未完成测量。复用相同 Full、Oracle、
ThunderAgent 和已完成 R5 对照，不重复这些 GPU 实验。保留原 73 个
项目、400 GB Host、20 初始＋mean70 到达、固定 18 个完整 JCT 成员，
仍在全部 18 个自然完成后停止背景；不截尾，不修改工具时间或任务。

此候选是否改善 JCT、decode 效率、迁移和重算仍未证明。没有登记
128 步或其他 GPU 后继，目标保持 active。

随后已实际进入 `running_full`。driver 534124 和固定测量组 observer
534157 均已核对存活，服务 owner 为 nice0，observer 为 nice19。
独立 `live_prefix.json` 核对 84 条精确原生回执、4104 个输出 token、
唯一 GPU owner 532662、实际完整容量、server 环境、position sampler
与输出 self-hash。实际 worker 无错误，出现 130 次无新推断有效性核对。

`live_horizon_protocol.json` 进一步核对实际执行源码、worker artifact
和组合成本指纹，确认加载的规划视界为 64，实际 profile identity 为
`b714ad0765df989d1c3acd4c5823ef278381b3f379a1250a536d055a3a9deceb`。
该封闭前缀有 92 次入口、31 次正常支持预测观测、3 次集合变化；
不同长度的前缀不能直接与上一轮比较，更不能作为完整 JCT 收益。

## 64 步保留参考成员的实际服务

新候选的一部分作用是避免旧 16 步评分缩小 S0，并不一定选择 S0 之外
的 agent。旧组合服务审计只关联 `selected - S0`，因此不能覆盖这些
被保留下来的成员。本轮新增显式的 `include_all_selected_service`
分析开关，默认关闭；启用时同时关联全部已选成员的周期与 free 授予，
原参考集合外新成员的人群和计数保持不变。该变化只影响离线审计，
运行中的冻结 `d94fe0de` 没有修改。

`audit_decode_horizon_service.py` 在每个实际 64 步现场，用同一候选、
概率、资源、成本率和 required JOIN 重放 16 步。仅比较完整搜索，
对两者的新增差集验证真实授予、完全相同的请求/context/attempt/session
和后续原生服务；不把局部 16 步重放当成一轮实际执行。

首份封闭 `horizon_service_prefix.json` 记录：

| 前缀中的项目 | 数量 |
| --- | ---: |
| 全部入口 | 4258 |
| 有元数据的完整搜索 | 259 |
| 不完整搜索，另列 | 25 |
| 64 与 16 选择集合不同 | 9 |
| 64 选择更多成员 | 6 |
| 64 新增的成员 episode | 9 |
| 其中属于原 S0 的保留成员 | 7 |
| 其中属于固定测量组 | 4 |
| 真实直接授予且有后续精确服务 | 9 |
| 下一控制或实际干预前的有效完成 | 4 |

其余 3974 次入口缺少可重放的搜索元数据，保持未知，不计成无效
调度；没有单独 A→B 工具替换被归入这 9 次直接授予。所有 9 个已有
后续服务，但只有 4 个满足更强的有界完成条件，不能将二者混为一谈。
周期路径验证压力处理后的资格；free 路径验证发布时已授予的资格，
不为 free 路径编造 `active_after_pressure`。未闭合观察窗口的有效
完成继续记为未知，真实暂停与计划暂停仍分别处理。

72 项相关 CPU 检查通过，保留旧周期/free 审计行为，并覆盖原 S0
成员、缺授予、错误 attempt、零进度、真实暂停、开放窗口、JOIN
权利、单独工具替换及元数据回退。开发中两项 fixture 错误和一次
读取 free/periodic 不同字段的错误保存在
`horizon_service_reader_development_failures.json`；未因此重启或修改
GPU 实验。实现提交 `3e0abe27`。

该前缀证明成本视界可以改变实际服务，尚未证明完整 JCT 或吞吐改善。
它与上一轮完整结果的时长、人群不同，不直接比较总次数。

## 完整 16 步回退的每 Token 成本

对已完成 Full 和修正版 16 步 R5 分别运行现有
`audit_useful_token_cost.py`，完整测量请求和输出仍逐项守恒。使用同一
固定墙钟窗口，其 decode 成本如下；区间是 worker 观测，包含计算、
依赖等待和 CPU 提交，不是独占 CUDA kernel 时间。

| 共同窗口 decode 观察 | Full | 修正版 16 步 R5 |
| --- | ---: | ---: |
| 输出 token | 532230 | 530611 |
| worker ms / output token | 1.859606 | 1.869577 |
| worker 时间加权有效宽度 | 14.057610 | 14.145285 |
| 平均上下文 token | 37803.71 | 37753.63 |
| 配置 padding 比例 | 0 | 0 |

表中有效宽度是正输出请求数按 worker 区间加权，不能把它当作同等
上下文与 kernel 成本的 batch 效率上界。按实际宽度和原 mean-context
分箱，在每侧至少 30 个 forward 的共同支持上对输出做相同权重标准化：

- 原始 decode ms/token 高 0.536229%。
- 标准化后为 Full 1.860835、R5 1.865331 ms/token，高 0.241583%。
- 共同支持覆盖 Full 99.837288%、R5 99.929892% 的 decode 输出。
- 对称分解严格重建 `+0.009971736 ms/token`：箱内
  `+0.004486444`，构成 `+0.009179683`，不支持部分 `-0.003694392`。

该描述性分解没有控制精确上下文向量、NUMA、并行传输或 CPU 争用，
不能独立归因 JCT，也不能把 worker 区间覆盖整段窗口解释成 GPU
利用率 100%。目前没有发现大幅底层执行成本恶化的证据，不能因这组
小差异断言控制路径是 1.50% JCT 回退的唯一或主要原因。

整段执行的非测量背景完成量不同，不用整段输出总数作为相同工作量
比较。证据分别为 Full 结果目录和 `r5_host400_valid_predictions_r1/`
下的 `useful_token_cost.json`，以及后者的
`decode_cost_decomposition.json`。分析复用现有分箱与分解函数，用
Full/learned 的真实配对校验，没有伪装成 ThunderAgent 配对。

## 64 步已闭合吞吐窗口

64 步运行的原生日志已越过原固定窗口末端，可以独立统计该窗口，
但不提前统计 JCT。`closed_common_window.json` 使用原
`[55.6927713505, 1638.9548502657]` 秒、全部 workflow（包含背景），
按原生 batch 完成时刻分配输出；总输出保留 prefill 首 token。
原生输出序号连续、forward 唯一、单一时钟域和健康 writer 已核对。

| 同一已闭合墙钟窗口 | Full | 64 步 R5 |
| --- | ---: | ---: |
| 全部输出 token | 536617 | 541263 |
| decode 输出 token | 532230 | 536836 |
| 全部输出 token/s | 338.931253 | 341.865701 |
| decode token/s | 336.160391 | 339.069575 |

64 步全部输出提高 0.865794%，decode 提高 0.865415%。其 decode
worker ms/token 为 1.842539，有效宽度约 14.103061；这些仍是观测
成本，不能归因为单独 kernel 提速或推导平均 JCT。窗口没有随运行
完成数、吞吐峰值或新策略结果移动。

该报告显式标为 `complete_measured_cohort_verified=false`，没有改变
固定测量组、选择最先完成者或输出部分完成组的 JCT。最终自然完成、
请求回执、排空与退出审计仍须通过，且最终 cohort 审计要逐项复核
541263/536836 的窗口计数；这不是提前完成性能验收。
