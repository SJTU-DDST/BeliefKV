# Host400 完整对照与 Learned R5

2026-11-02。本记录复用已有非排序 Full 与 Direct Global Oracle，不启动
被用户取消的重复实验。补齐相同配置的 ThunderAgent acting-decay 对照，
并将改进的工作量预测器用于完整 R5 组合准入。Learned R5 的性能尚未完成。

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
