# BeliefKV 最新架构与实现状态

更新日期：2026-09-11

## 2026-09-11：Future-Growth 首次产生正收益，Canary 仍关闭

64-root H200 predictor-only 高压运行首次产生 48 个正收益 PREPARE 候选、43 个 eligible
候选，planner 选择 35 次 PREPARE。beneficiary/value 路径已经成立；本轮不再是“高压但
没有预测机会”。完整记录见
`docs/experiments/beliefkv_p6_future_growth_top4_shadow64_2026-09-10_zh.md`。

在线门槛仍未通过。旧 validator 将 action-local overlay 对照只更新过一次的全局
`PolicyInput`，导致 54/54 certificate 被错误报告为 stale。当前改为 package-local causal
read-set 对 live RCCG 验证，并以 victim context-local revision 验证 compact physical
evidence；真正提交仍在 safe point 重物化完整 bundle 并执行资源、ownership 和 transfer
门禁。

时序是独立问题：42 个 finite latest-start result 中，hint 42/42 及时发布，但 worker
completion 和 validation 各只有 1/42 及时。Joint worker 现在允许有效 risk-only result
在无关 semantic delta pending 时立即发布，同时继续处理新 delta；action-projected
k-medoids 复用距离矩阵，overlay 复用 context-revision-valid 的 closure preview。同一 trace
的 12-snapshot CPU replay planning P50/P95 为 37.20/49.51 ms，但这些实验后改动尚未经过
GPU 验证。

PREPARE canary 继续关闭。下一门槛是一次短 64-root predictor-only 回归同时满足：worker
failure 为 0、fresh-positive 大于 0、validation 早于 latest-start。若第三项仍失败，下一
步是隔离 Predictive worker 的 GIL/调度延迟，而不是降低风险阈值。

## 2026-09-10：多 Beneficiary Future-Growth Probe 与局部物理失效

对 2026-09-07 高压 trace 的 680 次 beneficiary probe 完成 2 秒后验审计：57 个
唯一 request 中，44 次 probe 后 request 获得 service，532 次处于 slot 饱和且没有
service，104 次缺少直接证据；没有观察到 native `NO_TOKEN`/capacity rejection 意义下
的严格 HBM false negative。该结果只说明旧 trace 没有反驳首候选分类，不能证明没有
漏报：旧日志未保存 bounded seed 排名第 2--4 的 deferred request，因而无法评估它们
是否具有更高 action-unlock value。审计产物为同一 run 目录下的
`beneficiary_lookahead_audit.json`。

在线 probe 已拆分 `immediate_admission_fit` 和 `future_growth_deficit`。bounded seed
按原 execution 优先级保留前 4 个 deferred request，safe point 仅做常数规模 byte
需求筛选，并只为最佳一个 beneficiary 捕获最多两个 parked victim。future demand 使用
精确 remaining prefill、Frontier p90 decode demand 和当前 running demand；具体
`predicted_block_time` 由异步 graph32 GPU service timeline 推导，不再以固定 2 秒和
历史平均 decode rate 作为硬门禁。

`ActionLocalPhysicalOverlay` 新增 context-local physical revision。victim 的 generation、
lock、owner 或 topology 在同一 64 MiB HBM bucket 内变化时会使 retained overlay 失效并
局部重建，不恢复全局 PageIndex。PREPARE canary 仍关闭；开放条件是出现 fresh positive
projected package，且 safe-point validation 早于 latest feasible start，而不是等待
beneficiary 已经真实 HBM-blocked。

CPU 定向门禁为 266 passed、8 个 subtest passed。另有两个原有 SGLang import 测试因
本机未设置 `CUDA_HOME` 失败，与本轮代码路径无关。下一步只运行一次短 64-root
predictor-only 高压 shadow，验证多候选覆盖、局部 revision、worker 活性和同步开销。

## 2026-09-02：Bounded-Hint 高压 Gate 未达到 Canary 门槛

64-root H200 predictor-only shadow 持续约 41.4 分钟，HBM 峰值 99.9995%，高压窗口
约 13.79 分钟，停止前保持 32 running / 95 waiting。bounded observed-seed hint 共
发布 2,039 次，Predictive worker 1,199/1,199 terminal，受控 shutdown 无遗留事务。

本轮没有产生 positive、fresh-positive 或 timely-positive package，因此没有运行
`PREPARE_HOST` canary。475 次 risk worker failure 可复现为 compact RCCG 缺少可选
execution slot witness；修复后 snapshot replay 能正常生成一个 PREPARE 候选，但
4/4 scenario 均为 `projected_beneficiary_hbm_block_unavailable`，候选仍为零收益。

当前实现将 beneficiary 与最多两个 victim 作为必需 BeliefScope 节点并 fail closed，
可选 slot witness 仅在 mirror 中存在时加入；worker 同时刷新 candidate-local RCCG
closure。hint 的 seed generation 更新继续同步 mirror，但只有
request/context/epoch/startup/growth 变化才触发新 RISK_EVAL，避免重复评估同一动作。

高压路径的控制面仍未合格：safe-point capture P95 为 20.00 ms，risk event
materialization P95 为 60.21 ms；`no_live_victim_bundle=807`，enqueue physical mirror
age P95 为 1.36 秒。下一步仅实现 `1 beneficiary x 2 victims` action-local physical
overlay，不恢复全局 PageIndex。只有再次获得 fresh-positive 且 validation 早于
latest-start，才允许单笔 PREPARE canary。完整记录见
`docs/experiments/beliefkv_p6_bounded_hint_high_pressure_shadow64_2026-09-02_zh.md`。

## 2026-09-01：bounded observed seed beneficiary 已接入 Risk worker

Predictive worker 不再只依赖可能已经过期的异步 full-plan beneficiary。safe point
现在保留最新 bounded observed seed 的首个 deferred engine-waiting request，并以紧凑
hint 发布 `plan_id`、request/context identity、context epoch 和 startup/growth demand。
worker 必须逐字段与最新 runnable frontier 匹配后才能构造
`ProjectedReclaimRequirement`；任何身份或 demand 变化均 fail closed。PREPARE package
使用 bounded seed plan ID 作为 causal generation，safe point 仍重新物化物理 bundle，
预测器没有获得提前 COMMIT 权限。

冻结 replay 也已停止重新选择不同 victim，并使用与在线 builder 相同的 transfer estimate
字段契约。对 2026-09-01 的 4 个旧高压 snapshot 重放仍为 0 positive/eligible，
latest-start 仍落后约 0.91--0.98 秒。这些旧 snapshot 不包含新增 bounded hint，不能
反事实验证提前发布效果；按预注册门槛本轮不启动 GPU、不开放 canary。定向回归为
JointShadow/Predictive `44 passed`，SGLang adapter `2 passed`。

## 2026-09-01：事件驱动 Risk GPU gate 通过机制门槛，价值门槛未通过

提交 `0c86eb6` 补齐独立事件驱动 RISK_EVAL：`TOOL_START`、WAIT_CHILD/JOIN、
RETURN/JOIN_SATISFIED/TOOL_RETURN 不再依赖 pressure crossing 或 30 秒 watchdog，
也不重新触发全局 JointPlan。64-root H200 高压运行形成 192 invocation/context、
HBM 峰值 99.9998% 和最大 68.73 GB migratable KV；1,538 次 risk trigger 进入
Joint worker，Predictive worker 8/8 完成且无积压。

同步控制面继续合格：safe-point capture P95 为 0.314 ms、predictive submit P95
为 0.023 ms。完整 plan 仅 9/5,923=0.152%，但其 snapshot/compute P95 仍约
292/603 ms。异步 risk P50/P95 为 336/636 ms，trigger-to-validation P95 为
2.61 秒。

预测价值门槛未通过。在线 6 个 PREPARE_HOST 候选全部为负收益；收窄错误的全局
transfer-epoch freshness 后，4 个冻结高压 snapshot 重放仍为 0 positive/eligible，
其 latest feasible D2H start 已落后约 0.91--0.98 秒。当前第一阻塞项转为让最新
bounded observed seed 更早发布 projected beneficiary，而非继续优化 semantic-only
safe point。predictive physical action 保持关闭。完整记录见
`docs/experiments/beliefkv_p6_event_driven_risk_shadow64_2026-09-01_zh.md`。

## 2026-09-01：P6 Semantic-Delta GPU 控制面门槛通过

提交 `deac101` 将普通 RCCG 事件改为真正的 semantic-only publication：
safe point 不再复制 PageIndex、allocator、fairness、transfer telemetry 和完整
control state，page/topology/telemetry cursor 留待下一次完整 JointPlan 一次性追赶；
PageIndex full capture 同时减少为一次 mutation-journal 扫描。

相同 64-root H200 配置形成 64 workflow、192 invocation/context、32 running 和
96 waiting。575/575 个 worker publication 完成，零失败和积压；capture
P50/P95/P99 为 0.167/0.336/0.488 ms，enqueue P95 为 0.030 ms，完整规划仅
1/1,513=0.066%。冻结高压 snapshot 的 16 次 candidate-local replay planning
P95 为 85.23 ms。六项控制面门槛全部通过。

该 GPU 运行的 KV pool 峰值仅 27.31%，因此只证明真实高基数 RCCG/GIL 下的控制面，
不证明在线 predictive risk、physical action 或策略收益。predictive physical action
继续关闭；下一步返回 beneficiary-bound positive-package 验证。完整记录见
`docs/experiments/beliefkv_p6_control_plane_semantic_delta_gpu_gate_2026-09-01_zh.md`。

## 2026-08-31：P6 控制面 CPU/replay 门槛通过

P6 预测路径已重新接入 Performance-First 架构。worker mirror 对可信 safe-point
delta 使用 non-atomic apply，失败时丢弃 mirror 并 fail closed；运行时显式区分
SEMANTIC_DELTA、JOINT_REPLAN 和 RISK_EVAL，普通 agent 事件不再默认触发完整
JointPlan。predictor 继续使用 compact snapshot，只为一个 beneficiary 和最多两个
victim 局部物化 closure 与 transfer estimate；eligibility 已全部移入 predictive
worker；frontier feature 使用 changed-invocation delta；seed-only/no-action plan 跳过
全量在线 validation。

192-invocation CPU gate 的 safe-point capture P95 为 0.181 ms、predictive submit
P95 为 0.014 ms、RCCG delta apply P95 为 0.040 ms。4,096-page/192-runnable 的
compact observed seed P95 为 5.943 ms。冻结高压 trace 上 16 次 candidate-local
predictive replay 的 planning P95 为 88.201 ms。按 pressure crossing 与 30 秒高压
watchdog 重放，full plan 预计为 8/5,599 scheduler steps，即 0.143%。既定门槛全部
通过。

该结论只关闭控制面第一阻塞项，不代表预测策略已有收益。冻结 replay 仍没有 positive
package，predictive physical action 继续关闭；下一步需一次短 GPU control-plane gate
确认真实 GIL 干扰、worker backlog 和事件驱动 full-plan 比例。完整记录见
`docs/experiments/beliefkv_p6_control_plane_cpu_replay_gate_2026-08-31_zh.md`。

## 2026-08-30：Beneficiary-bound 机制闭环，当前高压窗口为 slot-only

Observed JointPlan 过去只发布截断后的 16 个 candidate ID；bounded seed 将这些
请求全部 ADMIT 后，risk planner 看不到其余 74--110 个 DEFER 请求。提交 f78e33c
现在使用同一 observed 排序额外发布一个 seed-excluded waiting request，避免建立
第二套 beneficiary scheduler。真实 64-root 运行中 1,040/1,040 个 risk result
均完成，产生 2,079 个 `1 beneficiary x 2 victims` PREPARE_HOST package，
`no_projected_hbm_beneficiary=0`。

本轮仍未通过价值门禁。26 个 HBM >=80% result 中有 52 个 candidate，但 positive、
fresh-positive 和 eligible 均为 0。16 个冻结高压快照的 HBM 余量为
12.99--15.42 GiB，beneficiary startup+growth 仅约 8.3--433.2 MiB；slot 可用时请求
可以直接 admission。离线 replay 的 256/256 scenario 均为
`projected_beneficiary_hbm_block_unavailable`，说明当前窗口主要受
`max_running_requests=32` 限制，而非 HBM deficit。系统不会把高 watermark 或
waiting age 人工转换成 saved stall，因此不开放 PREPARE canary。

replay 脚本已支持从冻结 `frontier_features` 和显式 `--predictor-model` 复用在线模型
推理。完整 planning 仍过慢：predictive planning P50/P95 为 0.91/1.84 秒，高压
P50/P95 为 1.47/2.15 秒；safe-point capture P95 为 24.13 ms。后续若继续该方向，
先将停止条件改为预注册的 action-specific projected deficit，并实现 compact semantic
snapshot 与 candidate-local physicalization；固定 80% watermark 不再等价于
predictive KV opportunity。完整记录见
`docs/experiments/beliefkv_p6_beneficiary_bound_shadow64_2026-08-30_zh.md`。

## 2026-08-29：Projected beneficiary overlay 已实现，等待有界 replay

P6 现只从 observed JointPlan 的候选顺序中选择第一个可见、GPU-ready/deferred 且
尚未产生真实 P4 ReclaimRequirement 的请求，构造
ProjectedReclaimRequirement。每次风险评估固定为一个 beneficiary 和最多两个
parked victim；timeline 在下一 execution slot 上进行 startup/growth HBM what-if。
slot-only 等待且容量可容纳时不会产生 projected deficit，也不会人为生成收益。

PREPARE_HOST 比较同一组 victim、beneficiary 和 physical closure 的 reactive
D2H 与提前 shadow 两条路径。预测路径只允许建立 Host shadow；COMMIT_CPU 仍必须
由真实 P4 ReclaimRequirement 触发。safe point 会重新验证 beneficiary identity、
context epoch、demand 上界、causal package generation、真实 reclaim 状态和 D2H
deadline。

旧 closure smoke 没有保存完整 PolicyInput，无法严格离线 replay 新模型。运行时现
只在 HBM >=80% 且出现 projected beneficiary 时，通过现有有界异步 writer 保存最多
20 个精确 snapshot；正收益 snapshot 继续独立保留。下一轮短 predictor-only shadow
将同时完成 replay 数据采集和自然正收益判定。

## 2026-08-29：64-root Closure Smoke 通过，P6 转向 beneficiary-bound value

同一冻结 64-root predictor-only workload 已完成一次短高压 closure smoke。运行时
形成 64 workflow、192 invocation/context，HBM 峰值 90.88%；1,003 个 risk result
中 closure_prediction_incomplete=0，证明提交 6b56736 的 closure-local
prediction 修复已覆盖真实高基数 RCCG。全程产生 6,012 个 PREPARE_HOST 候选和
4,045 个新鲜证书；高压区间 258 个候选中仍有 54 个证书新鲜，因此当前失败不是
“全部 stale”。

价值门禁仍未通过：6,012 个候选全部为负收益，最大 expected benefit 为 -4.19 ms。
本轮只证明旧价值模型没有发现 recourse；scenario failure 聚合是在实验后实现的，
不能把零收益唯一归因于 beneficiary。独立的代码审计确认旧 PREPARE package 只有
victim、没有绑定明确 beneficiary，这是后续必须修复的设计缺口。实际高 HBM 与
waiting backlog 不能单独证明迁移可以解锁 GPU work，尤其本轮同时有 32 running
和 95 waiting。

下一修改点已收敛为 beneficiary-bound recourse：由 observed
execution/admission/reclaim seed 提供真实 beneficiary、startup/growth deficit 和
victim reclaim envelope，比较 reactive 与 proactive 两条 admission/service 路径。
在该价值语义完成前不开放 PREPARE canary，也不先投入完整 planner 性能重构。

本轮后已将 predictive package 限制在 closure-complete BeliefScope，移入 OTHER 的
invocation 不再产生无效 local_prediction_missing；同时聚合已计算的 recourse
failure counts/credit，便于下一轮直接定位价值拒绝原因。完整记录见
docs/experiments/beliefkv_p6_closure_smoke64_2026-08-29_zh.md。

## 2026-08-29：P6 64-root 高压 Shadow 与闭包局部推理修复

已从既有 H200 v6/performance trace 重建 graph32 GPU service artifact，共包含
10,535 个唯一 batch sample，覆盖 decode/prefill 和 batch 1-32。该 artifact 绑定 v6
profile 与硬件 key，但仍明确为 `shadow_only`，不替代受控 GPU service calibration。
launcher 会同时 fail-fast 校验 GPU service 与 transfer service 的路径及 hardware key。

预注册 64-root predictor-only shadow 达到 100% KV pressure，并在高压后完成 208 次
risk result 后按停止规则结束。PageIndex 未再断言；predictive worker 1,176/1,176
terminal、无 failed/dropped/pending；shutdown 无遗留 transaction、command、lease 或
obligation。该轮 1,176 个结果全部因 `closure_prediction_incomplete` 跳过，未运行
canary。根因是 scheduler 只为任意前 64 个 invocation 生成 prediction，而运行时存在
64 parent + 128 child；BeliefScope 对 JOIN/child 原子闭包的要求与该截断冲突。

现已删除 risk shadow 的 scheduler-path 全局模型推理：safe point 仅冻结全部 active
invocation 的轻量 `LocalFrontierFeatures`，异步 worker 从实际 KV 候选扩展完整 RCCG
闭包，并只对闭包成员推理。旧 `frontier_predictions` metadata 仍可用于冻结 trace
回放。该改动修复了高基数闭包正确性，也避免将全局模型评估重新放回 scheduler。
定向与运行时回归共 218 passed、8 subtests passed。

P6 仍未开放 canary。完整 snapshot build、plan compute 和 validation 的高压 P95 分别
为 1.58 s、1.70 s 和 132.8 ms；下一门槛是 compact semantic snapshot、候选局部
physicalization 和 bounded scenario evaluation，而不是调整 workload、shape bucket
或风险阈值。完整记录见
`docs/experiments/beliefkv_p6_action_aligned_high_pressure_shadow_2026-08-29_zh.md`。

## 2026-08-29：Performance Patch Shape-Aware Transfer Artifact 重建

已从当前 performance patch 的既有 GPU telemetry 重建 v6 transfer artifact。导出器
现在可合并多个 telemetry 文件，并且只接受具有 HiCache submit 边界的 bundle 级
`offload_context/prefetch_context`；per-extent callback、native demand-load 和
write-back 不再重复计权。当前 artifact 包含 36 条 bundle terminal record，其中
35 条为完成样本，形成 14 个 D2H/H2D shape bucket。已覆盖 1-extent 的 4 MB 至
402 MB 实际动作，以及两次 4-extent、6.437 GB TransferEngineV2 传输；超出 bounded
size/extent 邻域的形态继续 fail closed。

v6 profile 已切换到：

`artifacts/p6/h200_bf16_v6/transfer_service_qwen3coder30b_bf16_h200_perf_v1.json`

同时，预测 worker 的触发签名由精确 Radix bundle generation 改为：

`action candidates + context epoch + causal state + 64 MiB resource/byte buckets + log2 extent bucket`

仅 generation、bundle ID 或同一 shape bucket 内的轻微物理变化不会再次启动后台
risk evaluation；精确 generation 仍由 action certificate 在 safe point 校验。
H2D 查询也开始携带 live extent count，和 D2H 共用 shape-aware 服务模型。定向
回归为 62 passed。固定 w4 trace 已完成：新 artifact contract 通过，候选的
shape-unsupported 比例由旧 trace 的 100% 降至 7.60%；generation-only 变化的重复
评估抑制率由 13.91% 提升到 27.94%，完整 risk result 相对业务事件数由 67.35%
降至 50.16%，certificate stale 比例由 48.96% 降至 38.67%。

该 trace 的 resident KV 峰值只有 17.79%，没有形成 HBM recourse；因此 1,854 个
PREPARE_HOST 候选仍为 0 positive/0 eligible。shape 支持扩大后更多候选进入完整
scenario timeline，planning P50/P95/P99 反而为 600/1,078/1,469 ms。代码已进一步
加入 deterministic-infeasible fast reject：物理硬约束已失败的 package 不再重复模拟
baseline/candidate timeline。该 fast reject 需要在一次预注册 64-root 高压 shadow 中
验证，不据此宣称已有在线延迟收益。

64-root 高压验证随后两次在启动后约 33–46 秒触发 PageIndex workflow charge
一致性断言，均未形成有效性能样本。CPU 复现确认增量 cache 与全量重算的最大差异
仅为 0.000406 byte，根因是共享页按 workflow 浮点分摊后仍使用 0.000001 byte
绝对容差，不是 ownership 丢失。修复后 non-accounting 元数据不再重写 resident
charge，workflow charge 校验采用 1 byte 的舍入容差；resident GPU/CPU 总字节仍保持
整数精确校验，超过 1 byte 的注入错误仍会失败。相关回归为 94 passed。高压 GPU
结果仍待一次独立复验，本轮不继续实验循环。

## 2026-08-27：Action-Aligned P6 首轮事件驱动 Shadow 完成

使用固定的 4-workflow `native_subagent_2to3` train gate 完成一次 predictor-only
GPU shadow。四个 parent 共创建 8 个 FRESH child，12/12 invocation RETURN，4/4
JOIN_SATISFIED；296 次 LLM 和 439 次工具调用均闭合。预测动作权限保持关闭，最终
无 pending transaction、restore debt、lease 或 command，shutdown correctness gate
全部通过。

预测 worker 完成 661 个任务、无 failed/dropped/pending；495 个 risk result 共评估
1,869 个 PREPARE_HOST 候选。结果为 0 个正收益、0 个 eligible、495/495 选择
observed baseline。该结果不能解释为 causal-slack 模型无效：本轮实际 resident KV
pressure 最高仅约 11%，future HBM overflow 为 0，所有候选都没有 pressure-time
recourse credit，最大 expected benefit 仍为 -18.13 ms。

本轮同时确认三个上线阻塞：

- 1,191/1,869 个候选得到 action timing，678 个 timing unavailable；可用样本的
  `required_wait_ms` P50/P95/P99 为 79.08/157.91/246.69 ms；
- 当前 runtime profile 仍引用 superseded、`recalibration_required=true` 的 transfer
  service artifact，1,869/1,869 个候选均为 `shape_unsupported`，不能开放物理动作；
- 后台 planning P50/P95/P99 为 527/895/1,196 ms，1,869 个候选证书中 915 个在结果
  返回前已 stale。预测 worker 必须继续只发布 semantic intent，并在 safe point 使用
  live shape 重物化，不能通过延长 TTL 接受旧物理证书。

因此 schema-v4 artifact 保持 `online_eligible=false` 和
`predictive_action_eligible=false`。下一步先重建 performance-patch/TransferEngineV2
对应的 shape-aware transfer artifact，并降低 background risk planning 的重复候选开销；
随后在预注册、持续 root backlog 且能形成 HBM pressure 的 trace 上再次运行 shadow。
只有出现 fresh、正收益 package 后，才依次开放单笔
`PREPARE_HOST -> COMMIT_CPU -> PREFETCH_GPU` canary。完整记录见
`docs/experiments/beliefkv_p6_action_aligned_frontier_v3_2026-08-27_zh.md`。

## 2026-08-27：P6 恢复，FrontierBelief 改为 Action-Aligned Schema-v4

Oracle 开发继续暂停。Performance-First P5 已完成控制面压缩和批量 transfer
正确性门禁，当前主线转为将 FrontierBelief 以单一 predictive overlay 接入现有
Causal Package Planner。

本轮复用冻结的 64 train + 16 held-out calibration workflow，未访问 test_id。
WAIT_TOOL 现按 role/tool family/backend/command class 分层；WAIT_JOIN/WAIT_CHILD
继续由 RCCG 组合 child completion，不拟合统一 external-wait。模型直接回答：

```text
PREPARE_HOST: P(release after live D2H p95 + guard)
PREFETCH_GPU: P(reentry within live H2D p95 + guard)
```

`COMMIT_CPU` 仍由 Causal Package Planner 将 beneficiary readiness、HBM deficit、
reclaimable bytes 和 expected saved stall 组合成原子 replacement package，不新增
独立分类头或迁移策略源。

held-out operational-tau Brier 为 PREPARE 0.0641、PREFETCH 0.1222；required tool
head availability 为 99.72%，但支持以 command/family/state backoff 为主。旧
40.86% composite OOD 已废弃，在线门禁只检查 action x state 所需预测头。

由于旧冻结语义行没有 live extent morphology，当前 tau 使用 performance patch
transfer anchor 按 bytes 缩放。schema-v4 artifact 继续保持
`online_eligible=false`，下一步只运行一次 predictor-only event-driven shadow；
不会开启预测性物理迁移。完整记录见
`docs/experiments/beliefkv_p6_action_aligned_frontier_v3_2026-08-27_zh.md`。

## 2026-08-26：主线切换为 Performance-First JointPlan

Oracle v2 暂停，当前不再扩展 Oracle action space 或 CPU simulator。正确性基线冻结为
`perf-baseline-h200-20260826`，后续只修改性能关键路径。

已完成 bounded transfer telemetry、Performance Mode 和 context-summary JointPlan 输入。
在 16,384 pages、32 runnable、384 changed pages 的 CPU 基准上，no-action snapshot
P99 为 0.248 ms，single-lock snapshot P99 为 0.234 ms，worker delta apply P99 为
1.875 ms，semantic JointPlan wall P99 为 3.646 ms。控制面 CPU 门槛已通过。

TransferEngineV2 logical lane、bundle-level D2H 和 observed Causal Package Planner
已完成首版。单笔 6.437 GB D2H/commit/H2D GPU gate 通过，D2H 为 249.8 ms，且
修复后没有整笔 H2D retry。双向 PCIe overlap 尚未验证，backend capability 仍为 1，
正式实验不得开启 transfer_engine_v2。完整结果见
`docs/experiments/beliefkv_extreme_performance_p3_p4_gpu_gate_2026-08-26_zh.md`。


## 2026-08-26：Frozen-Demand GPU O0/O3 首组配对完成

基于 18 个完整 native-subagent workflow 导出 FrozenAgentDemand v2 和 physical
sidecar，并在同一 H200/Qwen3-Coder-30B BF16/v6 profile 上完成有效 O0/O3 配对。
两臂均完成 18/18 workflow、1,208/1,208 request，token demand 完全一致，所有
事务和 shutdown 正确性门禁通过。

O0 makespan 为 2,803.65 秒、23.11 workflow/h；当前有限动作空间 O3 candidate
makespan 为 3,355.66 秒、19.31 workflow/h，吞吐下降 16.45%。O3 完成 18 次
COMMIT_CPU、13 次 PREFETCH_GPU 和 36 次 DROP，但出现 16 次方向反转；相同输出
demand 下累计 decode service interval 增加 24.83%，batch mean 则基本不变。

因此 whole-run no-op-dominant finite-candidate Oracle 选择 O0，当前 gain 为 0%。本轮
没有达到继续 O1/O2 的门槛，也不能主张 execution-KV joint synergy。后续先做逐动作
beneficiary/stall 归因及 sequence-length-aware execution package evaluation，不重复相同
GPU 实验。完整报告见
`docs/experiments/beliefkv_gpu_oracle_o0_o3_native18_2026-08-26_zh.md`。

## 2026-08-22：Gate C 长跑暴露 Ordinary Admission 饥饿与 Deadline Wakeup 缺口

prefix-rematch 修复版完整运行至 7,200 秒 deadline。543 次局部 ticket
重认证全部随后获得 physical start，说明 rematch correctness 已关闭；但 1,780 个
可见 request 中仍有 88 个 ordinary native-fallback request 从未 physical start，
最长等待约 674 秒。它们已在 server queue 中，问题不是 client backlog，而是 stale/
partial JointPlan 没有给已排除 durable restore priority 的普通 native miss 提供最终
admission floor。

已新增 bounded ordinary-fallback aging：超过 30 秒后每 epoch 最多提升一条请求进入
bounded seed；native allocator 保持容量权威；NO_TOKEN 后按 allocator capacity 和
1 秒冷却退避，容量变化立即重试。该路径不创建 obligation、lease、funding 或全局
barrier。实现提交为 `946be88`。

本轮 64/64 workflow 最终 cleanup 完成，但只有 17/64 在 5 秒内 server-terminal，
P50/P95 为 12.51/65.56 秒。根因是 idle scheduler 未监听 BeliefKV event UDS，且
deadline cancellation 曾串行执行。`96b358a` 已将 event fd 接入 SGLang idle poller，
合并 child cancellation control batch，并并发启动 request/task/command cancellation；
`h200_bf16_v6` 在 `2f2a8bc` 冻结该 SGLang patch。

修复后 CPU control-plane gate 为 283 passed + 8 subtests，Deep Agents gate 为 149 passed。
该长跑原先报告的 73 条 child RETURN 已修正为 64 条 root RETURN 与 9 条自然 child
RETURN；另有 119 条 child cancel、1 个 JOIN_SATISFIED、63 个 JOIN_TIMEOUT，且没有
自然 JOIN 后的再次 SPAWN。0 个 workflow 自然完成，因此不进入训练集、Frozen GPU
Replay、O0/O3 或性能 A/B。后续 Gate C 独立输出 System Gate 与 Agent Coverage Gate：
coverage 不再影响系统活性裁决，但 coverage 不足的 trace 仍不能进入 O0/O3。完整报告见
`docs/experiments/beliefkv_p5_gate_c_native64_rematch_fixed_2026-08-22_zh.md`。

## 2026-08-22：P5 Gate C 复验暴露 prefix-rematch 活性缺陷

使用 `0986398` 与同一 64-root/v5 冻结配置完成约 60 分钟复验。前一轮
ordinary restore debt 和 gross-pressure 误判已经消失：ordinary obligation、
capacity block、barrier 均为 0；17 个唯一 CPU-only prefix 直接交给 native
PrefillAdder；完整 JointPlan 从 2,188 次降为 1 次，4,301 个 epoch 走 apply-only。

复验仍未通过 Gate C。满池后出现 4,582 次
`prefix_rematch:prefix_demand_increased`，478 个已签发 ticket 的 epoch 最终
native batch 为 0。17 个 ordinary fallback request 在停止前均未再次 physical
start，running 从 32 降至约 20--21。根因是 ticket 编译后 SGLang 重新匹配
Radix/HiCache prefix，需求增长时整张 ticket 被拒绝，但增长后的 demand 未写回
side index，下一 epoch 继续签发 stale ticket。

已修复为局部重认证：纯 demand growth 在 request/context/bundle generation
仍有效时先写回 side index；当前 HBM/prefill budget 可行则原子重签局部 ticket，
prefill budget 不足则下一 epoch 按新 demand 编译，HBM 不足则进入统一
ReclaimRequirement/replacement/rescue 状态机。bundle 或 identity 变化仍严格拒绝。
受影响 CPU 回归为 249 passed、8 subtests passed。

本轮 trace 另有 64 parent、128 child、64 JOIN_WAIT，但自然 RETURN/JOIN 均为 0，
因此只用于 P5 高压活性 characterization，不进入 Frozen GPU Replay 或 O0/O3。
完整证据见
`docs/experiments/beliefkv_p5_gate_c_native64_rematch_2026-08-22_zh.md`。


## 2026-08-22：P5 Gate C 首轮失败并完成针对性修复

首轮 64-root Gate C 在 60 分钟检查点受控停止，不进入训练集、A/B 或 Frozen
GPU Replay。workload 形成 64 parent、128 FRESH child、64 JOIN_WAIT、1,218 次
LLM submit 和 3,037 次工具调用，但停止时仍有 97 个 request 从未 physical start，
physical-start wait P95 为 819.25 秒，GPU 平均利用率仅 4.22%。

根因有两层：

- 9 个 ordinary CPU-only prefix 被错误写入 durable RestoreObligationIndex，9/9
  fallback 后未再次获得 service；前 8 个占满槽位，随后 ordinary miss 被容量拒绝。
- physical resident HBM 接近 100%，但 SGLang non-evictable pressure 均值/最大仅
  13.80%/32%。DynamicWorkingSet 和 full-plan trigger 使用 gross residency，导致
  native evictable cache 填满时错误收缩 active set，并对低有效压力下的每个因果
  事件执行完整 JointPlan。

已修复：

- durable restore debt 只由 BeliefKV running retraction 创建；ordinary prefix 直接
  交给 PrefillAdder/native load-back，不创建 obligation、transaction、lease 或
  restore priority；当前 Radix path 的 ownership rebind 仍保留。
- working-set/admission emergency 使用扣除 native evictable capacity 后的 effective
  pressure；gross residency 只表示物理 cache/victim 空间。
- predictor-off observed P5 在低 effective pressure 下将 TOOL/SPAWN/RETURN 等
  因果事件降为 apply-only；有效 pressure、beneficiary、transfer 和 restore/
  retraction 变化仍触发完整计划，predictive worker 启用时保持因果 full planning。

验证更新为 adapter/restore/admission 175 passed；core 分组 771 passed、2 skipped；
agent/runtime/collection 分组 160 passed；语法和 whitespace 检查通过。下一步使用
相同 v5 profile 与冻结 64-root manifest 只复验一次 Gate C，不改变 KV/Host pool。

完整证据见
`docs/experiments/beliefkv_p5_gate_c_native64_2026-08-22_zh.md`。

## 2026-08-22：P1-P4 GPU Gate B 已通过

P1-P4 已通过分层 GPU correctness gate：

- 8-root `native_subagent_2to3` 观察到 16 个动态 child、644 次 LLM 和
  1,119 次工具调用；deadline workflow 的服务端清理延迟最大 561.17 ms，最终
  无请求或控制事务残留。
- running-retraction micro 完成 3.00 GiB D2H、beneficiary service、同量 H2D、
  victim service 和 durable obligation satisfaction。
- Host lifecycle micro 完成 3.01 GiB 原子 D2H、generation-safe CPU_ONLY Host
  drop、`recompute_required`、native demand-load 和 uncached prefill service。
- 所有最终 correctness gate 为真。test hook 与 Host lifecycle 分别标记为
  `test_hook`/`lifecycle`，不冒充 JointPlan 动作。

CPU 回归更新为 core 782 passed、agent/runtime 117 passed、runtime profile
15 passed。下一步是 predictor-off 64-root Gate C；不以显式迁移次数为通过标准。
完整证据见
`docs/experiments/beliefkv_native_trace_p1_p4_gate_b_2026-08-22_zh.md`。

## 2026-08-22：Native Trace P1-P4 正确性与活性修复

针对 2026-08-21 64-root trace 暴露的 admission starvation、deadline
泄漏、Host 饱和和调度目标冲突，已完成以下修改：

- Workload 支持真实 parent 在 JOIN 后按剩余独立问题动态发起下一轮 2--3 个
  child，不固定轮数；唯一 ActivationDeadline 传播到 parent、child 和
  summary，截止时向 SGLang abort 并等待服务端清理闭环。
- Admission ticket 只为当前 prefill chunk、16-token decode quantum 和
  allocator guard 计算即时 HBM envelope；HBM 不可行时发布持久
  ReclaimRequirement，JointPlan 将 victim reclaim 与 beneficiary admission
  绑定。
- 新增全局至多一笔 admission rescue。它只暂停新的普通 admission，不停止已有
  running batch；回收容量后由 allocator-backed reservation 保护 beneficiary，
  直到首个真实 GPU service quantum。
- Host pool 保持 96 GiB，使用 95%/85% 水位。DUAL_CLEAN 优先删除冗余 Host
  shadow；CPU_ONLY 仅在所有 owner 都有 owner+epoch 绑定的 full-prompt replay
  证据、处于 parked 状态、无锁/reader/pin/transfer 且是 Radix leaf 时执行
  generation-safe Host drop，并标记 recompute_required。
- 正常 admission/working-set/JointPlanner 统一为短片段 causal MaxWeight：
  causal class 优先，同级按 unlock-weighted GPU work / immediate HBM envelope
  排序；workflow fairness 只保留 30 秒 starvation floor 和最终 tie-break，不再
  因 fairness 排名变化拒绝已选计划。

CPU Gate A 已通过：核心控制面 777 passed、agent/runtime 117 passed、runtime
profile 14 passed；另有 8 个 subtest。两个 SGLang 测试依赖本机完整 CUDA
toolkit，继续由真实 GPU server 启动路径覆盖。当前只关闭 P1-P4 CPU
correctness gate，尚未主张 GPU utilization、JCT 或 workflows/hour 改善。下一步
是 4--8 root Gate B，分别覆盖 replacement service、Host drop/recompute 和短
deadline cleanup；通过后才运行 predictor-off 64-root Gate C。

实现记录见
docs/experiments/beliefkv_native_trace_p1_p4_gate_a_2026-08-22_zh.md。

## 2026-08-20：GPU-First Native-Subagent Oracle（当前）

CPU Counterfactual Oracle 已退出正式收益门禁，只保留契约测试和调试用途。当前权威路线见 [GPU-First Native-Subagent Oracle 计划](beliefkv_gpu_native_subagent_oracle_plan_2026-08-20_zh.md)。

正式 workload 使用 native_subagent_2to3：同一个 Deep Agents parent 通过原生 task 发起 2--3 个 FRESH child，parent 进入 JOIN_WAIT，child reports 作为 ToolMessage 回到同一 parent 对话，随后同一 context_id 在下一 context_epoch 继续。旧 parallel_analysis_2to3 的外部 planner、独立 child orchestration、新 supervisor，以及 64K--160K context pack/two-wave workload 全部降级为 diagnostic。

冻结输入为 configs/p6/oracle_v2_native_subagent_v1/collection_plan.json：64 个 formal-train root 同时提交、client in-flight=64、SGLang max running=32、KV pool=850K、Host=96 GiB，无事件驱动放量、无 outcome replacement。GPU 空闲并得到指令后，先执行 4-root 首 JOIN semantic gate；通过后才采 64-root native trace，并以 trace-driven GPU replay 先比较 O0/O3。

2026-08-21 semantic gate 已验证 4/4 首个 JOIN 均为 2-child；其中 3 个完整 workflow 通过全部 parent continuation/ToolMessage/prefix-reuse 检查，parent prefix retention 均为 100%。第 4 个 pytest child 在 642 次工具调用后仍未 RETURN，被人工取消，因此不能声称 4/4 clean completion。报告见 [H200 Native-Subagent 语义门禁](experiments/beliefkv_h200_native_subagent_semantic_gate_2026-08-21_zh.md)。下一步等待指令后采集冻结 64-root trace。


## 历史：CPU Oracle v2 有限候选与压力 Workload（已降级）

该段记录已停止的 CPU-first 路径。V2-0 schema v2、V2-1 truth/physical sidecar exporter 和 V2-1.5 CPU 离散事件估计器已经完成；普通 P5/P6 路径不导入 Oracle provider。

CPU estimator 已补齐最低限度的 no-op dominance：C1 将 C0 与四种固定 execution policy 分别完整模拟，C3 同时比较 C0、C2、全部 C1 及四种 execution+C2 组合，并按真实 whole-run makespan 取最优。因此有限候选集合内严格满足 `C1 >= C0` 和 `C3 >= max(C1,C2)`；它仍是有限候选 lower bound，不是全局最优 Oracle。

Opportunity 统计现区分 eviction、stall-free round-trip 和 net-positive，并分别记录唯一 victim-beneficiary pair、按 victim 去重的 byte-time 和按 beneficiary 去重的 blocked-work。资格 gate 固定读取同一条 `C0 + NOMINAL + measured-fastpath` row，禁止跨 service/overhead row 拼接最大值。

旧 32-root trace 的完整复跑包含 60 个 whole-run rollout。C0 reconstruction 的 makespan 误差仍为 2.98%/2.70%；C1/C3 相对 C0 的 gain 区间由旧的负数修正为 `[0, 0.523%]`，C2 与 joint synergy 均为 0。资格 row 仍无 eviction/stall-free/net-positive window，因此该 trace 只证明 workload 机会不足，不能否定 KV future，也不能启动 GPU O0--O3。

新的冻结输入位于 `configs/p6/oracle_v2_workloads_v5/`：

- `natural_opportunity_collection_plan.json` 是 train-only 自然机会 prevalence pool，不再称为 Representative。所有任务都进入 prevalence 分母；censor 前完整局部区间可用于 opportunity 统计，只有 clean trajectory 进入 whole-run Oracle/JCT truth。
- `kv_pressure_execution_plan.json` 是可执行 mechanism stress workload：32 个固定实例、64K/96K/128K/160K parent prompt、真实 2--3 child、两批各 16 root、间隔 30 秒、850K KV pool 和 96 GiB Host，不注入 synthetic wait、不按结果替换任务。
- 32 个 context pack 已按 frozen base commit、固定 seed 和 Qwen tokenizer 离线构造；运行时仅校验并读取 pack，不新增 tokenizer 依赖。
- collection launcher 现在直接消费冻结 arrival schedule；CLI 只能断言相同值，不能静默覆盖清单。

历史计划曾要求先采集自然池和压力 C0，并仅在固定 `NOMINAL + measured-fastpath` row 通过 stall-free exact joint gate，才计算 CPU C1--C3；只有 C3 相对 C0 约 10% 且明显优于 C1/C2，才进入真实 GPU O0--O3；该前置门槛现已取消。

完整报告：`docs/experiments/beliefkv_cpu_counterfactual_oracle_estimate_2026-08-20_zh.md`。

## 2026-08-17：P5 v4 Restore-Isolation 结果

- `66b7fb7` 的 ordinary restore 隔离完成 64-root 正式长跑；全局 restore barrier 为 0，backlog 下未复现 running 排空。
- 峰值 resident pressure 仅 60.09%，没有 running retraction 或 replacement，因此本轮仅作为活性与控制面 characterization，不进入正式 offload A/B。
- JointPlan 开销已成为首要阻塞：delta capture P95 34.70 ms、snapshot build P95 2.09 s、plan validation P95 171.84 ms、plan age P95 2.49 s。
- v4 的 31/32-way decode 全部未命中 CUDA Graph。下一版本为 `h200_bf16_v5`，目标捕获到 batch 32；v5 baseline/treatment 必须同配置配对。
- 完整报告：`docs/experiments/beliefkv_p5_restore_isolation_formal_v4_2026-08-17_zh.md`。

## 2026-08-17：JointPlan 事件驱动快路径与 H200 BF16 v5

P5 v4 长跑表明完整 JointPlan 的 delta capture、snapshot materialization、planning 和全局 validation 已显著超过 safe-point 预算。当前实现改为事件驱动的两档路径：普通 decode service 和非关键 queue revision 以 100 ms 合并，只向 worker 提交 apply-only delta；HBM pressure crossing、SPAWN、TOOL RETURN、CHILD RETURN、JOIN、transfer ACK、beneficiary deficit 与 restore/retraction revision 才触发完整规划。低压在线路径继续使用 bounded work-conserving seed，不等待异步完整计划。

物理输入改为紧凑 owner delta；RCCG 和 consumer snapshot 按 revision 复用；低压 apply-only 路径不物化 PolicyInput；完整规划仍构建全量 bundle summary 以选择 victim，但 observed 模式不再生成 transfer estimate，目标 closure 在 safe point 才重新物化；snapshot ID 使用 revision tuple，完整内容不再在关键路径哈希。在线提交只校验选中动作的 invocation/dependency/allocator/lease read-set，无 residency 动作的异步计划不接管 bounded admission seed。fast no-action 路径和稀有 physical action 分别使用 1 ms 与 5 ms 预算。

新增 `configs/p6/h200_bf16_v5/frozen_runtime_profile.json`，仅将 `cuda_graph_max_bs` 从 16 扩展到 32，KV pool 仍为 850,000 tokens。短 GPU gate 已成功捕获 `[1, 2, 4, 8, 16, 24, 32]`，capture 后 SGLang 可用显存为 3.28 GB，31/32-way 均命中 CUDA Graph，63/63 请求成功且无 OOM、NaN 或 replay failure。相同 32-way 固定 decode 下，graph 32 与 graph 16 的稳定吞吐中位数分别为 5,112.48 和 661.12 token/s；该结果只作为 CUDA Graph 配置门禁，不代表完整 agent A/B。v5 可以作为后续 baseline/treatment 的共同 profile；GPU service 与 decode-contention transfer artifact 仍需在 graph 32 下重新校准。

CPU 回归为 191 passed、2 deselected、6 subtests passed；两个 deselected 测试依赖本机完整 CUDA toolkit，将由 GPU server 启动覆盖真实导入路径。

短 gate 中 941 个 scheduler step 产生 551 次 progress coalescing、46 次 apply-only delta 和 1 次完整规划。safe-point delta capture P50/P95/P99 为 0.114/0.413/0.806 ms，完整规划没有 failed、dropped 或 superseded。该结果通过低压 no-action 快路径门槛，但没有覆盖高压 physical-action validation；完整 bundle summary 的 target-only 构建仍是后续优化项。报告见 `docs/experiments/beliefkv_jointplan_fastpath_cuda_graph32_gate_2026-08-17_zh.md`。


## 2026-08-16：正式 Treatment 暴露 Ordinary Restore 全局 Barrier

一次冻结配置的 64-root predictor-off P5 treatment 已运行约 200 分钟。物理 PageIndex KV 平均占用
80.99%，71.47% 时间高于 80%，最终 HBM/Host 分别约为 99.47%/99.99%；因此该 workload 已形成物理
KV 高压。`sglang:num_used_tokens` 最大仅 55.99% 是因为该指标扣除了 Radix evictable cache，不能继续
称为全部 resident pressure。

本轮未通过 liveness gate：40 笔 restore obligation 全部来自 native HiCache 的
`ORDINARY_WAITING_PREFIX`，其中最老债务因 Host 已满无法 funding 后被错误提升为全局
`restore_debt_barrier`。随后 48 个 waiting request 被阻塞，running 从 14 降到 4，GPU 平均利用率仅
2.46%。29 笔 debt 恢复并获得 service，11 笔在受控停止时取消；没有形成 semantic
victim-to-beneficiary replacement，因此不运行 baseline，也不把该轮计入 A/B。

restore 语义现已拆分：BeliefKV 主动 `RUNNING_RETRACTION` 继续保留 durable lease、service grace 和
全局 overdue barrier；普通 waiting-prefix miss 在显式 H2D 不可立即满足时退回 SGLang PrefillAdder
和 native demand-load，不再重复申请 allocator lease，也不能冻结无关 admission。

审阅后新增独立容量与优先级隔离：restore_obligation_max_active=8 只限制普通槽位，另有 2 个槽位仅供 RUNNING_RETRACTION；ordinary native fallback 不再进入 restore-ready priority 或 dynamic working-set mandatory，NO_TOKEN 后恢复普通 native waiting 排序。

相关回归为 162 passed、6 subtests passed；两个需要完整 CUDA toolkit 的导入测试未计入。完整证据见
`docs/experiments/beliefkv_p5_work_conserving_formal_treatment_2026-08-16_zh.md`。

## 2026-08-16：Replacement Liveness 与 64-Root Predictor-Off Smoke

beneficiary replacement priority 不再在 admission ticket 签发时清除。ticket 只是进入 native admission
检查的资格，prefix rematch、allocator、prefill token budget 或 generation 变化仍可能使请求未进入
batch。priority 现在只在 beneficiary 获得首个真实 GPU service quantum、请求终止/取消，或
request/context identity 失效时清除。

`--saturated-root-backlog` 改为在等待任意 workflow 完成前提交全部冻结 root。64-root workload 使用
64 个 client worker，server 仍由 `max_running_requests=32` 和 JointPlan 控制 active set。JOIN/CHILD/
MESSAGE 的 causal slack 也不再复用 resource feasibility；它直接对 RCCG scenario 中 dependency-release
时刻统计 `P(release > transfer_p95 + guard)`，OTHER/unresolved 质量按零 slack 保守处理。默认
`resident_service_window_ms` 从 1 秒调整为 5 秒，并记录同一 context 的短时 D2H/H2D 反转。

一次 development-only predictor-off H200 smoke 已实际提交 64/64 root：平均 28.15 running、68.50
waiting，且没有 `queue > 0 && running = 0` 的 metrics sample；admission native batch 平均 1.62、最大
15、无 native rejection。该轮最大 SGLang non-evictable pressure 为 15.43%，最大 physical HBM
pressure 为 30.97%，因此没有策略性 transfer，也没有覆盖 beneficiary service 链。GPU 平均利用率仅
5.04%，说明消除隐藏 client backlog 仍不足以让该短时 agent workload 饱和。该结果只能关闭
root-submission/work-conserving 正确性项，不能关闭 migration 或性能 gate。完整证据见
`docs/experiments/beliefkv_p5_work_conserving_smoke64_2026-08-16_zh.md`。

## 2026-08-14：WaitBelief、Causal Slack 与 Work-Conserving JointPlan

本轮修复了两个真实的架构断层：训练/校准曾把工具、JOIN/child 等异质等待重新合并成
`remaining_external_wait_ms`；working-set 在 0.8 HBM pressure 时收缩 GPU-ready 集合，但 residency
通常要等 admission deficit 或 0.98 emergency pressure 才迁移，造成 resident KV 占空间却没有获得
service。

预测接口现收敛为：

```text
WaitBelief
  ToolWaitBelief    -> tool-family/backend-class competing-risk survival
  Join/ChildBelief  -> RCCG child completion + JOIN_ALL/JOIN_ANY composition
  MessageWaitBelief -> producer dependency composition
  UnknownWaitBelief -> OOD，不执行预测性物理动作

KV action query:
  tau = Q95(transfer | live physical shape) + commit_guard
  P(causal wait remains open beyond tau | current observed state)
```

`WAIT_JOIN` 不再训练 wall-clock empirical model，也不再与工具时间共享 conformal slack。工具 survival
在 held-out calibration 上使用右删失安全的 binary logit calibration；只有 censor 时刻晚于 `tau` 才能
作为已知 survived 标签。旧 `remaining_external_wait` 字段仅用于 schema-v1/v2 artifact 反序列化，不能
驱动 schema-v3 策略。

新 H200 schema-v3 artifact 为
`experiments/models/frontier_belief_h200_bf16_v2_wait_slack_calibrated.json`。LOPO 目标已加入 tool
causal-slack Brier；正式 calibration 使用 30,096 个 slack 标签，scale/offset 分别为 0.9216/-0.1188。
在冻结 calibration split 上，10/100/1K/10K ms 的 Brier 分别为 0.0028/0.2070/0.2242/0.0080。
这些数值说明中间时长仍较难预测，因此 artifact 继续保持 `online_eligible=false`，只用于 shadow/replay。
`test_id` 未访问。

旧报告中的 40.86% composite OOD 表示“五个预测头中任意一个不可用”，即使该 head 与当前状态或
动作无关也会计数；它不是 40.86% workload 未见。新指标只统计
`action x invocation_state x required_head`，并分别发布 availability/OOD，不能再用全局 composite
指标一票否决 KV 动作。

observed JointPlan 改为 throughput-first、work-conserving：HBM pressure 只开启 replacement/reclaim，
不再主动降低 GPU-ready target。planner 先选择 execution set，再计算 startup + restore + projected
growth deficit，随后生成带 beneficiary 的 `COMMIT_CPU(victim) -> ADMIT(beneficiary)` replacement
事务。每个 victim 只证明自己实际承担的 reclaim 份额；safe point 重新物化 live Radix bundle，ACK
后 beneficiary 获得持久 admission priority，native allocator 仍是最终容量权威。

新增不变量：

```text
KEEP_GPU(context)
  => selected now OR receives real GPU service within resident_service_window_ms
otherwise
  => residency lease expires and context becomes a replacement victim candidate
```

fairness 不再决定主排序，只保留 30 秒 starvation floor 和最终 tie-break。此前
`engine_waiting` request 的 startup growth 被错误视为 0，本轮也已修复；该错误会直接隐藏 beneficiary
deficit。workload runner 和正式 P6 collection launcher 新增 `--saturated-root-backlog`；client
in-flight root window 必须大于 server JointPlan active set，才能在现有 root 全部 parked 时仍提供
待准入计算。尚未进行 GPU A/B，因此当前只主张控制面正确性，不主张
GPU utilization 或 workflows/hour 已改善。完整实现记录见
`docs/experiments/beliefkv_wait_slack_work_conserving_jointplan_2026-08-14_zh.md`。

## 历史基线：H200 FrontierBelief schema-v2 Held-out Calibration

Astropy/Sphinx 两个预冻结 calibration shard 已完成，共 16 个 workflow。canonical coverage audit
覆盖 24,394 个 decision row，natural/parallel 与两个项目均覆盖全部训练 target；15/15 个 eligible
JOIN closure-complete。`CALL_CENSORED` 与 `JOIN_TIMEOUT` 现作为显式右删失 reentry endpoint，
重导出后的 1,284 个 eligible reentry 全部获得 observed 或 right-censored 归因。censor 不再被误当作
成功 terminal/wait 样本。

本节记录已被上节 schema-v3 WaitBelief 替代的首个 H200 BF16 模型。该模型曾在 held-out
calibration split 上完成概率和 local-episode conformal 校准，且
没有重新 fit 训练计数。四个连续目标的 local-episode interval coverage 分别为 90.15%、93.90%、
90.17% 和 90.13%；boundary accuracy 为 94.96%，tool terminal accuracy 为 86.82%。但工具等待
区间仍极宽，旧统一 external-wait workflow-macro coverage 仅 87.40%，因此该历史 artifact 保持
`online_eligible=false`、`predictive_action_eligible=false`，只允许 shadow/replay。`test_id` 未访问。

exact incremental action boundary 仍为 0%，不阻塞基于完整 `LLM_RESULT` 的最终 action/demand
预测，但继续阻塞 early dispatch 与 run-to-action 主张。完整证据见
`docs/experiments/beliefkv_h200_bf16_canonical_train_and_calibration_2026-08-13_zh.md`。

## 2026-08-13：H200 Canonical Train 与 Calibration 边界

64 个 H200 BF16 train workflow 已通过 replacement-aware exporter 合并为 canonical dataset。三条
recovery 只替换同一预冻结 instance，最终保持 64/64 instance 一一对应。训练资格已拆分为
`formal_local_training_eligible` 与 `clean_trajectory_eligible`：前者允许 target/horizon censor 后的
局部自然标签进入 Frontier fit，后者仍独占 terminal/JCT/完整 trajectory。正式 test loader 没有
放宽。

当前 coverage gate 通过：83,712 个 decision point 可训练，其中 35,038 行来自 clean workflow，
48,674 行是受 intervention workflow 中保留下来的局部标签，13,013 行完全被 censor。exact
incremental action boundary 仍为 0%，所以不支持 early-dispatch/run-to-action 主张。

首个 H200 FrontierBeliefModel 已完成 7-project LOPO 和 fit，但 artifact 明确保持
`uncalibrated`、`online_eligible=false`、`predictive_action_eligible=false`。已冻结 16-workflow
calibration plan：Astropy/Sphinx 各 8，natural/parallel 各 8，固定 seed 且不允许用于 fit 或模型
选择；16 个 image 已锁定为 RepoDigest。`test_id` 继续封存。完整证据见
`docs/experiments/beliefkv_h200_bf16_canonical_train_and_calibration_2026-08-13_zh.md`。

## 2026-08-12：批量 Admission 与动态 Working Set

H200 pressure trace 暴露出两个相互关联的控制问题：prefill 平均 batch size 仅约 1.02，且
固定 workflow active window 无法同时满足低压填满 GPU 与高压控制 KV footprint。当前实现已改为：

```text
safe point
  -> 汇总 workflow 级 GPU-ready 数量、fairness rank、RCCG action-unlock value
  -> 低压 GPU_FILL：扩展 active set 并在同一 JointPlanEpoch 中批量提升 admission
  -> AdmissionTicketCompiler：先装入可完整 prefill 的短请求，再放至多一个 chunked tail
  -> prefix rematch 后累计验证整批 token/HBM certificate

HBM pressure >= enter watermark
  -> HBM_PRESSURE_REPLACEMENT：保持 work-conserving ready target
  -> execution set 的 startup/restore/growth deficit 触发 replacement reclaim
  -> 允许 observed PREPARE/COMMIT/DROP 与 running retraction
HBM pressure <= exit watermark
  -> hysteresis 退出 replacement 模式，继续 GPU_FILL
```

重要边界：

- batch-fill 是原 `JointPlanEpoch` 的 admission action 扩展，保留 `plan_id`、residency intent 和
  retraction transaction，不是第二个 admission planner；
- active set 只控制当前 ticket eligibility，不从 RCCG 删除 inactive workflow。排序优先 GPU residency、
  startup cost、action unlock 和可形成的 batch；fairness 只提供 starvation floor/tie-break；
- `TICKET_READY` restore obligation 是 mandatory，可临时越过 active-window hard cap，避免 restore
  debt 被工作集收缩饿死；
- 低压只抑制新的 destructive observed actions。`PREFETCH_GPU` restore、terminal cleanup 和已提交
  transaction 的 ACK/commit 不受影响；预测性 `PREPARE_HOST` 仍由其 future-pressure 证书管理；
- SGLang 0.5.2rc1 只保留一个 `chunked_req`。普通情况下先批量完整短请求再放一个长请求
  chunk；若 policy order 的队首本身是 oversized prompt，则为其保留至多 1/4 token budget 和一个
  slot，避免持续短请求流造成长 prompt 饥饿。

新增 H200 `h200_bf16_v4` profile 将 `chunked_prefill_size` 与 `max_prefill_tokens` 同时冻结为
16,384。历史 trace 中 815 个 uncached prompt 的 P50/P95 分别约 272/3,677 tokens，16K 可容纳约
4 个 P95 prefill，同时不超过原生已有的 16K `max_prefill_tokens`。v3 与历史结果保持不变。

当前证据仅为 CPU correctness：focused 与扩展 runtime suite 已通过。尚未运行
修复后的 GPU trace，因此不能声称 batch size、GPU utilization 或 workflows/hour 已提升。下次 GPU
gate 必须同时报告 issued/native prefill batch histogram、平均 batch size、GPU-ready/running 数、HBM
pressure mode 占比、迁移/抢占仅在高压发生的比例和 workflow service lag。

状态基线：P5G system correctness gate 已通过并冻结架构；最新版 P6 R0--R5 代码路径和实验
基础设施已经实现，GPU gate 尚未全部执行。2026-08-10 的 GPU0 受控矩阵表明，相同 2.659 GB KV 从
7 个 extents 增加到 106 个 extents 时，D2H 均值由 185.69 ms 增至 765.17 ms。当前模型首版
只以 bytes 和 extent count 为条件。后续冻结 Xarray w8 characterization 发现
`PREPARE_HOST` 被错误施加 future-HBM gate；修正后曾在 post-hoc development replay 中出现
promotion/veto，但这不能通过预声明的 M5 gate。随后完成 veto-only 在线 treatment：7/8 workflow 完成，
P5 shutdown/事务守恒通过，但发现在线 `min_samples=8` 错误覆盖了 artifact 的 3-run 校准门槛，
使两个 arm 都回退静态带宽并产生 16 个伪 veto。修复后重放 14 个可用源 snapshot，byte-only 与
shape-aware 均选择 2 个 PREPARE，promotion/veto/selected-action change 全为 0。按预声明 gate，
morphology 降级为 transfer cost/OOD safety 辅助模型；M6 在线收益仍未证明。

2026-08-11 已修复该实验暴露的 service contract：runtime 在线样本门槛与 artifact 校准门槛
独立，启动时验证 warm-start 至少支持一个代表性 query，并把契约摘要写入初始化审计。进一步
审计发现历史 1,922 次 native D2H 的 owner 均为空，根因是完成回调才查询已变化的 ownership；
现在改为 submit 时冻结 owner context/epoch、extent size 和 ownership revision。旧 trace 无法
追溯恢复这些标签。最新执行路线不再把独立 causal useful-action oracle 设为在线动作的前置 gate，
而是在端到端 A/B 中同步记录 useful/wasted/too-late/censored 动作归因。

当前 P6 权威实施顺序见
[`beliefkv_p6_predictive_joint_execution_plan_2026-08-11_zh.md`](beliefkv_p6_predictive_joint_execution_plan_2026-08-11_zh.md)。
该计划删除 morphology 独立策略，增加受控 2--3 child fan-out workload，并把 FrontierBelief 接入
现有 admission 与 selective running retraction；历史 P6 段落只用于追溯。

本文回答三个问题：

1. 当前线上真正执行的是哪条代码路径；
2. 哪些模块已经实现，但仍然只用于 shadow、replay 或 oracle；
3. 哪些结论已经有真实 GPU 证据，哪些仍然只是研究计划。

“存在代码”不等于“已经接入在线系统”，而“通过机制测试”也不等于“已经证明性能提升”。
本文使用以下状态：

- **在线完成**：已进入真实 SGLang 控制路径，并通过对应正确性验证；
- **机制完成**：代码和测试完整，但性能、泛化或真实负载退出条件未闭合；
- **部分完成**：核心代码存在，仍缺少关键接入、信号或实验；
- **Shadow/Replay**：可以生成和比较决策，但不能改变真实请求队列或 KV residency；
- **未实现**：仅存在设计和接口计划。

## 0. 2026-08-11 技术主线与状态覆盖

本节覆盖本文后续历史段落中的旧 P6 优先级；P5/P5G 的实现记录仍保留作为追溯依据。

```text
Runtime events + RCCG + FrontierBelief
  -> EXPAND / CLOSE / HOLD causal frontier
  -> reentry / future-pressure / demand scenarios
                         +
SafePointPhysicalSnapshot + Radix ownership
  -> live PhysicalBundle + calibrated transfer cost
                         |
                         v
JointPlan
  -> execution priority + admission
  -> bounded PREPARE_HOST
  -> optional selective retraction + replacement
  -> existing P5 transaction / ACK / restore path
```

物理可行性仍使用以下判据，但它只是 transfer cost 约束，不是当前核心创新主张：

```text
transfer_slack = min(pressure_deadline, reentry_deadline)
                 - Q90(T_transfer | bytes, extent_count, contention)
                 - safety_guard
```

`transfer_slack` 为负、transfer cost unsupported、future HBM 不可行或 certificate 失效时，
预测 overlay 不发布动作，系统保持 P5 observed seed。该机制不引入第二个 KV planner，
也不改变现有 restore transaction。

当前模块状态：

| 模块 | 状态 | 下一项产物 |
|---|---|---|
| P5 observed JointPlan/restore | 在线完成并冻结 | 仅修 correctness bug |
| predictive intent/rematerialization/ACK 路径 | 机制完成 | 单个自然正收益动作 canary |
| GPU0 同 bytes、不同 shape 测量 | development evidence 完成 | 7/106 extents 为 185.69/765.17 ms |
| 自然 agent trace 形态审计 | 完成 | 13 个稳定 parked episode；5 个高碎片 episode 分布在 3/5 个 context |
| extent-count-aware transfer model | development artifact 完成 | 仅以 bytes/count 为条件，完整 morphology 尚未闭合 |
| transfer service contract | 在线完成 | runtime/artifact 独立门槛、hardware key、supported query fail-fast |
| native HiCache ownership telemetry | 机制完成 | submit-time owner/epoch/extent/revision；等待新 trace 验证 coverage |
| morphology 独立策略 | 已删除 | 仅保留统一 bytes/extent-count/contention transfer cost/OOD guard |
| bytes-only 对照 replay | 实现完成，稳定 decision gate 未通过 | 修正 service contract 后 14 snapshot 中 0 action flip |
| R3 单动作 PREPARE canary | 机制完成 | 新统一 analyzer 已完成；自然 GPU 动作待验证 |
| 受控 2--3 child fan-out workload | 机制完成 | fake-backend 通过；短 GPU smoke 待执行 |
| Frontier-Aware Retraction | 机制完成 | 默认 shadow，最多一笔在线变化；GPU gate 待执行 |
| 在线动作归因 | 机制完成 | useful/wasted/too-late/censored/failed ledger 已接入 |
| R5 配对 A/B | 基础设施完成 | v9 run plan 冻结为 A-B/B-A/A-B；六次 GPU run 待执行 |
| 端到端收益 | 未证明 | 以 clean workflows/hour 和完整归因链共同判断 |

GPU1 crossover、small-size 完整矩阵、progressive slicing、KV compaction、自定义 DMA、
PREFETCH_GPU 和 P8 baseline 适配均暂缓。当前只实现对现有 selective retraction 的轻量预测注解，
不新增物理抢占机制。

## 1. 当前最重要的架构结论

BeliefKV 保留 P2 reactive 和 P4 shadow 作为独立实验模式；启用 P5 时，在线决策已收敛为单一
JointPlan authority。reactive transfer planner 在 P5 下不能重新成为第二个 victim/迁移策略源。

```text
在线执行主路径（P2）
RuntimeEvent / request metadata / allocator observation
  -> RCCG + PageOwnershipIndex
  -> admission / fairness / prediction
  -> bundle-aware reactive or shadow planner
  -> ControlCommand queue
  -> RadixArbiter
  -> SGLang scheduler safe point
  -> HiCache physical action
  -> actual-byte ACK
  -> residency commit + telemetry

统一策略路径（P2.5--P5）
RCCG + consumer index + PageOwnershipIndex + resource observation
  -> PolicyInputSnapshotBuilder
  -> latest-wins AsyncSemanticJointPlanner
  -> SemanticPlan(execution/admission/context-tier/retraction)
  -> scheduler safe point local validation
  -> current PhysicalBundle materialization (at most one transfer transaction)
  -> JointPlanEpoch -> ticket / residency / retraction

预测联合路径（P6，默认关闭）
FrontierBelief scenarios + RCCG causal deadlines
  + live PhysicalTransferShape + calibrated transfer model
  -> action-specific risk/benefit under causal slack
  -> ScenarioRiskPlanner
  -> semantic PredictiveIntent merged into the same JointPlan
  -> safe-point live-shape rematerialization
  -> existing P5 transaction / ACK / restore path

延后比较路径（P8，默认关闭）
immutable PolicyInput + frozen trace
  -> 按届时论文接口重新实现的有效 baseline
  -> common-denominator native systems when compatible
```

P5 的异步 planner 不再绑定 page、extent generation 或 closure handle。它只输出较慢变化的语义
顺序和 context 级目标；safe point 对最大有效 action slice 做局部校验，并只物化下一批请求和至多
一个迁移事务。`BOUNDED_SEED/OPTIMIZED/EMERGENCY/NO_ACTION` 使用同一 epoch 合约；running
retraction 必须有显式 `RetractionIntent` 和 `source_joint_plan_id`。

第一条路径现在同时记录 BeliefKV 显式 command 与 SGLang native demand-load/write-back 回调。
两者统一进入 transfer timeline，并用 `telemetry_origin` 区分；native 路径仍绕过 BeliefKV
command queue，因此控制归因与数据面完成事件必须分开解释。

native HiCache callback 的 attribution 语义已改为：submit 时从 `PageOwnershipIndex` 冻结
generation-aware `PageHandle`、owner context、context epoch、extent size 和 revision，complete 时
只消费该快照。telemetry 显式记录 generation/owner/extent coverage；旧记录没有 submit snapshot 时
才使用 `completion_lookup` 兼容路径。该修改解决 context 在 DMA 完成前解绑或 node ID 重用导致
归因漂移的问题。

transfer artifact 加载后执行 service-contract preflight。合法状态允许
`runtime_min_samples=8`、`artifact_min_samples=3`；关键是 warm-start bucket 按 artifact 自身门槛
保持 supported。若加载样本为零或所有代表性 query 都 unsupported，server 在 workload 前失败，
而不是回退静态带宽后继续发布预测结果。

因此，当前版本可以描述为“P5 在线 JointPlan 已通过系统正确性 gate，P6 预测 overlay 的
控制路径已实现并默认关闭”。不能描述为“形态感知策略已在线启用”或“预测策略已经产生
端到端收益”；后续历史段落中关于 P5G 尚未通过的表述只记录当时状态，以本页第 0 节为准。

2026-07-28 增加 P5E running-retraction restore obligation。此前 retraction 只保证 victim D2H 和
replacement admission，没有持久保证被 requeue 的 victim 能再次完成 H2D 并获得 GPU service；真实
w4 trace 中两个 victim 因此在 requeue 后停留 900 秒并触发 execution timeout。修复后的状态机为：

```text
RETRACTION_PREPARED -> D2H_INFLIGHT -> PARKED_WAIT
  -> EVICT_FOR_RESTORE -> H2D_INFLIGHT -> RESTORE_ACKED
  -> TICKET_READY -> SATISFIED
```

每笔 committed running retraction 现在必须先通过 fail-closed obligation capacity/path 检查，并在
retraction 前保存 request Radix path extent。obligation 不随异步 JointPlan stale/invalidation 消失；
容量不足时 safe point 先选择不包含目标 context 的物理可行 D2H funding bundle，收到 ACK 且
allocator/page revision 前进后再提交目标 H2D。cooldown 只限制再次成为 retraction victim，不再覆盖
restore dependency。相同 page/topology/allocator/transfer stamp 下不会重试失败命令；超过 2 秒启用
restore-debt admission barrier，所有 blocked obligation 都带具名 blocker 和状态 stamp。

bounded/emergency JointPlan seed 也会保留 restore requirements，所有命令携带
`source_joint_plan_id`。H2D ACK 后重新匹配 request prefix，首次重新获得 GPU service 才将 obligation
标记为 `SATISFIED`；abort、cache reset 和 shutdown 分别进入显式终态。CPU 集成测试覆盖
`requeue -> D2H funding -> ACK -> H2D -> ACK -> ticket -> service`，当前全量结果为
`431 passed, 8 skipped`。

同日固定 w4 trace 进一步暴露 ordinary-waiting restore 缺口：两个从未 physical start 的 child
request 在 waiting 期间被 native HiCache write-back，ticket gate 发现 CPU-only prefix 后只设置
`WAIT_RESTORE`，却没有创建可驱动 H2D 的 obligation。当前修复将该事件归因为
`ordinary_waiting_prefix`，直接创建独立于 JOIN/readset plan freshness 的 durable restore debt；H2D
ACK 后 `TICKET_READY` debt 以创建时间顺序进入 admission liveness priority。确定性测试已覆盖
`ordinary waiting -> CPU path detection -> H2D -> ACK -> TICKET_READY`。2026-07-28 固定 w4 GPU
trace 进一步真实触发 6 个 `ordinary_waiting_prefix` debt，6/6 均在 H2D ACK 后重新获得 GPU
service；同轮 66 个 running-retraction debt 也全部进入 `SATISFIED`，且 HBM funding 分支真实释放
4,419,256,320 bytes。214 次 dispatch 全部收到 ACK，无 orphan transaction。

该 GPU trace 同时暴露了 workload 终止性缺口：cyclic persistent peer 在 1,800 秒 activation
deadline 后仍继续提交到 context epoch 74，也超过 48-call stuck guard，最终需要人工中止。根因是
旧实现把 deadline 和 guard 限制在单次 persistent-peer activation，外层 `graph.invoke()` 与 runner
future 没有共享的绝对 deadline 和 cancellation supervisor。

2026-07-28 已完成 CPU 侧修复：一个绝对 workflow deadline 由 root、persistent peer 和所有 child
共享；到期后统一 abort 活跃 SGLang request、取消 child 与 JOIN，并终止该 workflow 的隔离 sandbox。
普通工具错误原样返回模型并规范为 `ToolMessage.status="error"`，runtime 记录 error class、参数签名和
写工具前后 workspace digest。物理失败与 `duplicate_suppressed` 意图分开计数，并以 failure episode ID
关联。后续审阅将在线边界进一步收缩：重复调用、错误率和无进展模式只记录 telemetry，不再禁用工具、
修改 prompt 或强制返回 `BLOCKED`；历史 `NORMAL/SUSPECT/RECOVERY/FINALIZE` 状态机仅保留为显式
回归开关。相同参数的成功工具调用若连续两次既无有效输出也不改变 workspace epoch，第三次起由 circuit
breaker 阻断物理执行。默认路径只有 512 superstep 和绝对 workflow deadline 能触发进程级安全收尾，
其中 graph step 481 起保留 32 step 做有界终态收尾。该逻辑位于独立的
`runtime/agent_safety.py` 与 `runtime/langchain_tool_safety.py`，KV policy 只消费终态事件，不参与判断
工具或任务是否成功。CPU 回归为 core/server `431 passed, 8 skipped`，agent runtime 聚焦集
`73 passed`。

2026-07-29 已执行一次固定 w4 clean-completion gate。99/99 command 收到 ACK，25/27 restore
obligation 恢复 service，退出前 request/transaction 全部清空；但 `restore-23` 的 3.426 GB H2D
ACK 后没有保留约 600 MB admission 空间，native admission 随后返回 `NO_TOKEN`。overdue debt
barrier 最终形成 `0 running + 7 waiting`，直到 900 秒 execution timeout 取消该请求。最终 0/4
workflow clean completion，且 Ctrl-C 只把 shutdown summary 写到 `preparing`。因此完整 P5 gate
仍未通过，也不能执行性能比较。详见
`docs/experiments/beliefkv_p5e_clean_completion_w4_2026-07-29_zh.md`。

同日已完成针对该失败的 CPU 侧修复。新增 request-indexed `RestoreLease` 状态机，并以真实
SGLang KV allocator token 预留 admission 容量；lease 在 H2D 前建立，H2D ACK 后额外 pin
restored Radix prefix，prefix rematch 后仅向 owner ticket 发放 reservation credit。native
`PrefillAdder` 尝试前临时释放预留 token，失败则立即重新获取，成功则由 request lock 接管
prefix，直到首次 GPU service 或 terminal/cancel/reset/shutdown 才结束 lease。全局 debt barrier
改为“lease 保护剩余容量可继续工作”；lease 无法建立且 engine idle 时只允许一次 bounded small
bypass。全量 CPU 回归为 `435 passed, 8 skipped`；真实 w4 GPU clean-completion 复验尚未执行。

随后一次固定 w4 复验确认了另一类 restore liveness 缺口：`restore-18` 的逻辑 debt 仍引用 15 个
CPU-only extent，但这些 extent 已不再是当前 context owner 集合的一部分，因而无法生成
`PREFETCH_CONTEXT` physical preview。当前 CPU 侧修复只针对 `ordinary_waiting_prefix`：每次恢复先
以 waiting request 的当前 Radix 路径校验 handle generation，并用 `replace=True` 原子重绑该 context
的 physical ownership；若重绑后仍无 preview，则先以同一个 `RestoreLease` 预留完整 native admission
容量，再把 debt 标记为 `native_admission_fallback`，交给 SGLang HiCache 原生 load-back，失效时允许
按 raw prompt 重算。native admission 返回 `NO_TOKEN` 时仍走既有 rollback/reacquire，不会绕过 HBM
容量约束；running-retraction obligation 不允许使用该 fallback。focused restore/admission/retraction
回归为 `123 passed`，全量 CPU 回归为 `437 passed, 8 skipped`。

2026-07-29 固定 w4 GPU 复验真实触发 32 笔 running-retraction 和 17 笔 ordinary-waiting
obligation，49/49 全部 `SATISFIED`；0 次 `physical_preview_unavailable`，因此上一轮 parent restore
永久停滞未复现。170 个 command 全部收到 ACK，0 missing/orphan/order violation。该修复同时产生
160 次 ownership rebind，说明 liveness 已恢复但 context owner 仍被物理同步反复改写。完整
clean-completion gate 仍失败：4/4 workflow 达到 1,800 秒绝对 deadline，主要剩余问题转为 parent
语义终止、late dynamic spawn 的剩余预算，以及 2 个瞬时 Host/page-index mismatch、1 个 physical-start
checkpoint 缺口和未完成的 shutdown summary。详见
`docs/experiments/beliefkv_p5e_restore_rebind_w4_2026-07-29_zh.md`。

2026-07-30 的下一次固定 w4 压力复验中，物理迁移仍保持 125/125 command/ACK 完整，52 个 restore
obligation 中 51 个恢复 service；唯一失败的 `restore-52` 在前一 restore 满足后仅 1.77 秒便再次
成为 running-retraction victim。其 H2D 已完成，但后续 7 次 funding 释放的 11.89 GB capacity
没有归属于该 debt，同时 active lease 使 normal-admission barrier 过早关闭，最终持续 blocked
423.96 秒。该轮是 liveness characterization，不用于性能结论，详见
`docs/experiments/beliefkv_p5e_restore_funding_fix_w4_2026-07-30_zh.md`。

当前 CPU 修复增加两项闭环机制：`RestoreServiceGrace` 从首次恢复 service 开始按已完成 batch 后
`output_ids` 的真实增量累计，默认完成 32 个 decode token 或正常结束当前 request 前禁止再次
retraction；funding ACK 则把实际 reclaim 中用于弥补 allocator deficit 的部分立即分配成该
obligation 独占的 allocator escrow，随后在同一 safe point 转换为 owner lease 与 H2D headroom。
active lease 不再关闭 overdue debt barrier，cancel/cache reset/shutdown 会统一回滚 escrow、lease
和 grace。全量 CPU 回归为 `442 passed, 8 skipped`；固定 w4 GPU 复验尚未执行。

2026-07-31 根据 restore-30 的失败证据增加 P5G `Transactional Restore Coordinator`。该次故障中，
request 已从 running 回到 native waiting，但历史 `_active_request_ids` 仍使四次 H2D 被错误判定为
`ENGINE_BUSY`；随后 lease grant/rollback 改变 allocator revision，自身写入又触发 963 轮无效恢复
尝试。P5G 不再把历史 active 集合当作 ownership，而是在 scheduler safe point 从 queue location、
`req_pool_idx`、Radix lock、native load 和 explicit transfer 重建正交
`NativeRequestPhysicalSnapshot`。

restore 提交改为 `preflight -> reservation/pin prepare -> enqueue-or-adopt -> commit`，失败逆序回滚。
controller 返回类型化 `EnqueueOutcome`；相同 context/epoch/kind/bundle generation/closure/target
residency 的命令共享 canonical command，并由 ACK 多订阅表唤醒全部 restore transaction。失败重试
使用 `ExternalProgressToken`，只响应 engine owner、closure、capacity threshold、command ownership、
guard 或 native load 的相关变化，不响应无关 allocator revision 和本事务自己的 lease 写入。

最老 restore 超过活性门槛时显式进入
`NORMAL_JOINT -> RESTORE_DRAIN_REQUESTED -> RESTORE_DRAIN_ACTIVE`；ACTIVE 状态使当前在线计划失效，
coordinator 成为唯一 admission/residency authority，普通 JointPlan 只保留 shadow 能力。当前已完成
CPU 机制与故障注入，包括 stale active ID、guard-blocked 零 lease allocation、canonical ACK 多订阅、
partial/rejected/stale ACK、native/explicit load 冲突、context epoch 冲突和 drain 权限切换；固定 w4
GPU clean-completion 当时尚未执行。

2026-07-31 进一步执行了固定 w4 ownership-overhead characterization。首轮真实暴露 Host-only
Radix leaf 在 H2D 前被错误 `inc_lock_ref()` 的崩溃；现已改为 H2D 前只持有 capacity reservation，
由 HiCache 保护 native loading path，H2D ACK 后再建立持久 GPU prefix pin。修复后的单次复验覆盖
35 次 deferred-pin/H2D/post-ACK pin、14 次 running retraction 和 89 条 transfer ACK，受控 shutdown
后 0 pending transaction。105 次 native ownership rebuild 的 P50/P95/P99 为
0.052/0.156/0.523 ms，最大 5.454 ms，每次最多扫描 12 个 request。w4 量级下开销可控，但 P99
略高于暂定 0.5 ms 门槛，且该轮达到样本数后主动停止，不是 clean-completion gate。w24/w32 前需
改为每个相关 safe point 构造一次、按 context 索引复用的 immutable physical snapshot。详见
`docs/experiments/beliefkv_p5g_ownership_snapshot_overhead_w4_2026-07-31_zh.md`。

2026-07-31 随后只运行了一次完整固定 w4 P5G gate。数据面 246/246 lifecycle cleanup command/ACK
完整，HBM/Host page-index 一致，953 个 request 均完成 physical start/finish，运行中没有 API timeout、
queue timeout、OOM 或 admission stall；但 workload 只有 1/4 semantic complete、0/4 clean JCT，失败
分别来自 self-handoff runtime error、LangGraph recursion limit、7,200 秒 workflow timeout，以及一次
`repeated_failed_tool_call` 使已完成 workflow 的 `guard_valid=false`。其中 `native_protocol_valid` 只是
严格诊断字段，不参与当前 clean-JCT 判定。该轮虽记录 2,923 次 native D2H 与 20 次 native H2D，却没有创建 restore obligation
或显式迁移，因此 lazy ownership snapshot `call_count=0`，P5G transactional restore 未被覆盖。shutdown
prepare 时物理事务为空，但 SIGINT 在 `SHUTDOWN_ACK` 和 audit flush 前终止进程，summary 停在
`preparing/final=false`，独立 telemetry 少 1 条 D2H。详见
`docs/experiments/beliefkv_p5g_clean_completion_w4_2026-07-31_zh.md`。

准确说法是：

> P2 reactive、P4 shadow 和 P5 unified JointPlan 是显式隔离的实验模式；P5G transactional
> restore 已通过全量 CPU 与 fault-injection gate；普通压力下旧 restore 路径已有 GPU 证据，但
> P5G fixed-w4 ownership reconstruction 已有 GPU characterization；完整 fixed-w4 已执行但因 runtime
> 终止性、零 restore-transaction coverage 和 shutdown 未 ACK 而失败，P5 接口尚不能冻结。

BeliefKV 不是 metadata-free：它要求 invocation/context identity 和已经发生的 spawn、wait、return、
message、handoff 等最小在线因果事件。其定位是**无需先验完整 DAG、在线发现动态 workflow**，
而不是从完全不透明请求中推断 workflow。

2026-07-30 增加独立的 agent runtime context lifecycle。parent 和 Deep Agents child 均以动态消息
历史 32,768 token 为压缩触发点，压缩后保留最近约 8,192 token，并用独立的 2,048-token 摘要调用
保留任务目标、计划、文件修改、测试、错误以及 child RETURN/JOIN 状态。32K 不包含静态 system
prompt 和工具 schema，也不是 SGLang 的硬 context length；服务端保留更大的窗口，为静态 schema、
摘要请求和故障回退预留空间。

摘要 LLM 使用 `runtime_internal` 的临时 FRESH context，不计入业务 subagent 和 LLM 强度。摘要成功
应用到下一次 parent/child 请求前，runtime 发出 `CONTEXT_COMPACT(old_epoch, new_epoch)`；控制面原子
解除旧 context 到 Radix page 的 ownership、清除旧 terminal-node 绑定并推进 epoch。旧 GPU KV 由
正常 residency 策略按压力回收，旧 Host-only 副本进入 cleanup；共享页只有在没有其他 owner 时才会
失去保护。摘要调用显式传播 runtime callback；摘要模型失败或返回空 checkpoint 时 fail-closed，不会
发布 compaction。单个不可切分工具轮次超过摘要预算时，摘要输入有界保留最早目标和最新状态。摘要
游标在单次 graph invocation 内作为 `ContextLifecycleState` 私有状态传递，并标记为 LangGraph
`PrivateStateAttr`；跨 peer activation 的续接由该 parent 独占的 lifecycle middleware 保存和恢复，
不经过公开 graph input/output。本地 subagent middleware 同时在 child 输入和 child `Command.update`
输出边界剥离该游标，避免并发 child 在 JOIN 时把各自 compaction 状态合并回 parent；该字段不能使用
reducer 合并，因为 child 的压缩位置对 parent 没有语义。没有启用通用 LangGraph
checkpointer，避免 loop guard、旧 structured response 和多代完整 checkpoint 污染下一次 activation。
摘要内容和触发策略仍属于 runtime，BeliefKV 仅处理 KV 生命周期事件。未增加新的语义 progress
detector，既有工具错误 circuit breaker 与 loop guard 保持不变。

## 2. 当前端到端架构

```text
┌──────────────────────────────────────────────────────────────────────┐
│ A. Agent 与请求事件源                                                │
│ Deep Agents / Codex / Responses / LangGraph / ClawTrace / SGLang    │
│ RuntimeEvent、request metadata、structured action、tool/message 事件 │
└──────────────────────────────┬───────────────────────────────────────┘
                               v
┌──────────────────────────────────────────────────────────────────────┐
│ B. 已观测状态层                                                       │
│ RuntimeCausalContextGraph     ObservedDataConsumerIndex              │
│ ActionFrontierObserver        ContextPrefixAffinityIndex             │
│ PageOwnershipIndex            RuntimeResourceObservation             │
└──────────────────────────────┬───────────────────────────────────────┘
                               v
┌──────────────────────────────────────────────────────────────────────┐
│ C. BeliefKVController                                                │
│                                                                      │
│ 在线 P2：                                                            │
│ causal frontier / fairness / admission / causal lease               │
│ residency / predictor / transfer service curve                      │
│ PhysicalBundleBuilder / ReactiveTransferPlanner / TransferGuard     │
│                                                                      │
│ 研究 P2.5/P3：                                                       │
│ PolicyInputSnapshotBuilder -> B0 / what-if / joint oracle           │
└──────────────────────────────┬───────────────────────────────────────┘
                               v
┌──────────────────────────────────────────────────────────────────────┐
│ D. 物理桥接与执行                                                     │
│ TransferCommandQueue -> RadixArbiter -> SGLangSchedulerBridge       │
│ -> HiCacheNodeCommandBackend -> GPU/Host KV -> ACK/Telemetry        │
└──────────────────────────────────────────────────────────────────────┘
```

BeliefKV 与 SGLang 的职责边界没有改变：

```text
BeliefKV：已观测因果状态、策略、物理 bundle 意图、审计和实验
SGLang：allocator、Radix topology、KV tensor、engine lock 和实际 DMA
```

`PageOwnershipIndex` 只是带 generation 的 CPU mirror，不能替代 SGLang 物理真相源。

## 3. 四个必须分开的状态视图

最新版不再尝试用一棵 workflow 图同时解释所有关系，而是维护四个正交视图。

### 3.1 因果控制关系

[`RuntimeCausalContextGraph`](../beliefkv/control/causal_graph.py) 记录已经发生的
`CALL/SPAWN/RETURN/JOIN/MESSAGE/HANDOFF/TOOL/LLM/REACTIVATE` 事件。它回答：

- 谁创建或阻塞了谁；
- 哪个 return、join、message 会唤醒谁；
- 哪些 invocation 当前为 `READY/RUNNING/WAITING/TERMINAL`；
- 谁位于当前 causal frontier。

RCCG 新增单调 `graph_version`。策略快照可以引用确定版本，避免把基于旧图生成的决策应用到
新状态。重复 `event_id` 保持幂等，原子批处理失败时回滚。

### 3.2 数据消费者关系

[`ObservedDataConsumerIndex`](../beliefkv/control/data_consumers.py) 单独记录 producer-consumer
事实，包括 `RETURN/MESSAGE/BROADCAST/WORKSPACE/HANDOFF`。它回答“谁会读取谁的结果”，而不是
“谁是谁的 parent”。

该索引只保存已观测关系。预测 consumer 不得写入 observed index，也不得伪装成 RCCG 事实。

### 3.3 物理 Prefix 共享关系

[`ContextPrefixAffinityIndex`](../beliefkv/runtime/prefix_affinity.py) 只根据真实共享
`PageHandle` 计算 byte Jaccard 和共享字节。因果 parent-child 不能直接推导 prefix affinity。

真实 P2 workload 已经显示，FRESH parent-child 的公共 prefix 很小，显著复用主要来自相同
template 的 parent-parent 或 sibling child-child。因此当前策略不能把 causal edge 直接转换成
cache-affinity edge。

### 3.4 Action Frontier

[`ActionFrontierObserver`](../beliefkv/runtime/action_frontier.py) 关联：

- structured action 何时成为合法 tool/spawn/handoff/final action；
- 合法 action 出现前后 runnable frontier 如何变化；
- tool-start gap、active-to-waiting KV 和后续 reentry；
- parser 状态为 valid、invalid、incomplete 还是 unknown。

Deep Agents 当前只能上报运行时已经解析完成的 action，不能伪造原生 incremental boundary
token。Action frontier 目前只用于观测和 characterization，不改变 decode order。

## 4. 在线 P2 控制路径

### 4.1 Controller 是组合根

[`BeliefKVController`](../beliefkv/control/controller.py) 在初始化时连接：

```text
RCCG + consumer index + page index
causal frontier + residency + fairness + admission
causal lease + physical bundle builder
predictor + transfer service curve
reactive planner + shadow controller + retry guard
command queue + RadixArbiter
PolicyInput snapshot builder
```

事件入口依次更新 RCCG、observed consumer index、predictor feature、context epoch 和 retry guard。
context 被唤醒时，过时的 shadow 会被取消，并用实际唤醒时间更新 prediction calibration。

### 4.2 当前 `tick()` 的真实顺序

```text
1. 释放 terminal non-persistent context 的语义 ownership
2. 更新 HBM/Host/engine/telemetry 信号
3. 生成可选 remaining-time prediction
4. 检查 pending admission 的 liveness 和 reservation
5. 根据 authoritative HBM 与 native reclaim capacity 作 admission
6. 若无 in-flight command，执行 shortage -> prefetch -> shadow 规划
7. 将命令加入 urgent/shadow queue
8. retry guard 判断当前物理快照是否允许重新尝试
9. RadixArbiter 在派发前重建并校验 bundle
10. 有效命令成为 in-flight；无动作命令生成结构化 local ACK
```

当前 admission、transfer planner 和 SGLang waiting queue 仍各自包含局部排序。这正是 P4/P5
要用统一 JointPlan 消除的问题。

### 4.3 Causal lease 与资源保护

[`CausalLeaseProjector`](../beliefkv/policy/leases.py) 将 RCCG 状态投影为有限资源承诺：

```text
RUNNING > READY > CONDITIONAL_RESUME > SPECULATIVE > DEAD
```

- `RUNNING_LLM` owner 禁止策略迁移；
- `READY` owner 应保留或恢复；
- `WAIT_TOOL/WAIT_CHILD/WAIT_JOIN/WAIT_MESSAGE` 可 shadow，压力下可 commit；
- 未知 context 默认保守保护，不能被当作 dead；
- 共享 bundle 取所有 owner 中的最强 lease。

Lease 不是新的 cache coherency 协议。真正的 lock、位置和 generation 仍来自 SGLang 和
`PageOwnershipIndex`。

### 4.4 Admission 与公平

[`AdmissionController`](../beliefkv/policy/admission.py) 使用：

- 未缓存 prompt 与预计 output 的增量 KV；
- authoritative HBM、已保留 reservation 和 native reclaim capacity；
- root-workflow soft share、有界借用和 attained service；
- workflow 内 causal frontier；
- admission liveness timeout 与 force-progress 条件。

请求只有在真实空间或经过 scheduler 验证的 reclaim 能力满足时才进入 SGLang。依赖 H2D 的
请求需要等待 terminal ACK，并在进入 engine 前重新匹配 authoritative prefix。

## 5. Physical Bundle 与三重校验

P2 的核心变化是把“迁移一个 context”改成“验证并执行一个版本化 physical bundle”。

[`PhysicalBundleBuilder`](../beliefkv/runtime/bundles.py) 负责 preview：

- D2H/COMMIT 包含必须共同处理的 GPU descendant closure；
- H2D 包含 CPU target 到 GPU anchor 的 ancestor closure；
- 共享 extent 只计一次物理字节；
- 区分 `EXCLUSIVE_SUFFIX` 和 `SHARED_SUBTREE`；
- 计算 unique、copy、reclaim、locked 和 foreign-owner bytes；
- 生成覆盖 topology、generation、owner、lease、lock 和 residency 的 fingerprint。

一个迁移命令需要经过：

```text
Planner preview
  -> PhysicalBundleIntent 冻结 handles/actions/fingerprint
  -> RadixArbiter 按 PageOwnershipIndex 二次重建
  -> HiCache backend 按 authoritative allocator 三次 preflight
  -> DMA / COMMIT / rollback
  -> actual-byte ACK
```

任何 generation、parent/children、owner、lock、capacity、closure 或 action bytes 不一致都会
fail closed。Blocked preview 仍进入审计，但不能转换成可执行 intent。

## 6. Command、ACK 与 Residency 状态机

公共协议位于 [`runtime/protocol.py`](../beliefkv/runtime/protocol.py)。

```text
PageHandle = page_id + allocation_generation

GPU_ONLY --START_D2H--> MIRRORING
MIRRORING --shadow ACK--> DUAL_CLEAN
MIRRORING --reactive ACK--> CPU_ONLY
DUAL_CLEAN --COMMIT_CPU--> CPU_ONLY
CPU_ONLY --START_H2D--> PREFETCHING
PREFETCHING --ACK--> DUAL_CLEAN
失败或未完成 action --> 回滚 started transfer
```

`CommandAck` 是正确性边界；`TransferTelemetry` 是性能观测，二者不能混用。只有 ACK 中明确
完成的 handles 才能改变 `PageOwnershipIndex`。Telemetry 只有在对应 correctness ACK 已提交后
才能训练 [`TransferServiceCurve`](../beliefkv/policy/service_curve.py)。

Blocker 已结构化为 closure、capacity、engine busy、lock/loading、inflight、semantic pin、
unsealed、stale generation、extent mutation 和 unknown backend 等类型。

[`TransferAttemptGuard`](../beliefkv/policy/transfer_guard.py) 使用 bundle ID、fingerprint、context
epoch 和 blocker release event 抑制相同失败在每个 scheduler tick 重复提交。它不是永久屏蔽
context；匹配的 allocator、lock、generation、engine 或 runtime 事件到达后才重新放行。

## 7. SGLang/HiCache 数据面

当前只支持：

```text
SGLang tag:    v0.5.2rc1
SGLang commit: 18f91eb639084825717c0e3c3c7273492812ab71
```

[`EmbeddedSGLangRuntime`](../beliefkv/runtime/sglang_v052rc1.py) 在 scheduler safe point 中执行：

```text
drain RuntimeEvent
-> drain ACK and retire H2D dependency
-> drain telemetry/callback errors
-> sync dirty Radix mirror
-> allocator/Radix consistency check
-> report HBM/Host and workflow charges
-> release ACK-satisfied admission
-> Controller.tick()
-> cancel or submit at most one transfer command
-> emit audit, bundle preview, blocker and timing records
```

真实后端的物理单位是 sealed Radix node extent，不是任意固定大小 page。当前 capability 为：

```text
operation_merge = false
layer_completion_events = false
max_inflight_operations = 1
physical_unit = node_extent
```

H2D 使用 `load_back(force=True, allow_eviction=False)`：允许绕过旧版小 closure 阈值，但不能
绕过容量检查或隐式驱逐其他 KV。带 bundle 的 D2H 先完成全部 copy，再按 closure 顺序释放
GPU；H2D 失败时回滚已经恢复的 extent。

## 8. P2.5 统一策略与追踪 Contract

[`PolicyInput`](../beliefkv/policy/reference/base.py) 将所有策略放在同一数据平面：

```text
RuntimeGraphSnapshot
+ runnable frontier
+ disjoint PhysicalKVSnapshot
+ ResourceSnapshot
+ typed optional metadata
+ identity mappings
+ runtime capability report
```

[`PolicyOutput`](../beliefkv/policy/reference/base.py) 统一表达：

```text
ExecutionIntent
+ AdmissionIntent
+ ResidencyIntent
+ TransferDependencies
```

[`PolicyInputSnapshotBuilder`](../beliefkv/policy/reference/snapshot_builder.py) 合并 RCCG、consumer
index、admission queue、physical page mirror、allocator observation、lease 和 service curve。未被
PageOwnershipIndex 跟踪但被 allocator 占用的字节会成为不可迁移 protected bundle，不能被
策略误当成空闲空间。

当前维护代码只保留 B0 same-data-plane policy：

| ID | 实现 | 元数据模式 | 当前状态 |
|---|---|---|---|
| B0 | Reactive baseline | Online | 默认 Shadow/Replay；当前核心 baseline |

`ReferencePolicyAdapter` 会隔离 metadata：online 模式不能读取 hindsight，oracle metadata 只能在
replay 中使用，unsupported action/capability 必须显式输出。当前所有 reference output 强制
`shadow_only=true`，不会改变真实 admission、D2H、H2D 或 waiting queue。
`PolicyReplayRunner()` 和 CLI 只支持 B0；运行时只保存中立物理快照。

2026-07-22 维护清理删除了 B1-B4 具体 policy、B1/B2 hindsight enricher、stateful replay 和
运行时 `program_phase/congestion_feedback` producer。旧实验 JSONL 保留，通用 contract 仍能
校验其中的 decision fingerprint；历史报告不作为当前可执行 baseline。

## 9. P3 动态观测、What-if 与 Joint Oracle

### 9.1 动态 workload 与 instrumentation

当前工作区已实现：

- observed producer-consumer index；
- 单调 RCCG graph version 和 `REACTIVATE`；
- 基于真实 PageHandle 的 context prefix affinity；
- structured action frontier observer；
- Coder/Reviewer/Tester 循环 handoff，并可嵌套 FRESH subagent 的 LangGraph workload；
- 持久 peer context、真实 repository tool loop 和 Deep Agents 动态 task backend；
- topology、cycle、handoff、consumer fan-out 和 action coverage characterization。

CPU/fake backend 已能重复完成 spawn、join、handoff 和 reactivation。真实模型 cyclic/mixed A/B
中的 workload 机制已经跑通：一次 12-workflow 全 mixed run 完成 100 个 LLM call、48 个
FRESH child、40 次 handoff 和 17 次 reactivation。它仍不是配对 A/B，且原生 incremental
boundary-token 覆盖率为 0。

2026-07-22 审计后，上述 12-workflow run 因 leaf one-shot 且 tool call 为 0，已降级为
topology/pressure smoke。新版 `agentic_peer_backend.py` 让 peer 在 handoff 后 RESUME 同一
context，让 FRESH child 在 task 内执行多轮 LLM/tool，并以模型实际 task call 决定 fan-out。
单 workflow GPU gate 已覆盖 253 LLM、239 个真实工具、3 个有效多轮 child、闭合 join 和
cyclic reactivation；另有两条 28/41-request tool-rich run 正常 semantic complete。正式并发
A/B 尚未执行，详见
[P3 真实工具型 Agentic Workload](experiments/beliefkv_p3_agentic_workload_2026-07-22_zh.md)。

### 9.2 Scenario physicalizer 与 What-if

[`ScenarioPhysicalizer`](../beliefkv/policy/scenario_physicalizer.py) 将 blocking、nonblocking、
FRESH、handoff、multi-consumer 和 cyclic reactivation 场景转换成物理需求。尚未创建的预测
request 不能进入实际 execution frontier；consumer readiness 与 physical ownership 分开计算。

[`WhatIfPacker`](../beliefkv/policy/whatif_packer.py) 无副作用地组合 execution order、admission、
required restore 和 victim bundle，并检查 closure、capacity、fairness、liveness 和 handoff
hysteresis。缺少 extent identity 或 closure 重叠时必须 fail closed。

### 9.3 历史 O0-O3 Joint Oracle（Legacy）

本节记录 2026-07 的离线 counterfactual 实现和负结果，仅用于说明旧方法为何不能作为当前 GPU oracle。其静态拓扑、hindsight eviction 和 rolling resimulator 语义已由 Perfect-Future Action-Space Oracle v2 取代；下述代码与结果不得直接用于新的 O0--O3 性能结论。

[`JointPlanOracle`](../beliefkv/policy/joint_oracle.py) 定义：

```text
O0 current agent scheduling + current KV policy
O1 oracle agent scheduling + current KV policy
O2 current agent scheduling + oracle KV policy
O3 oracle joint scheduling + admission + KV policy
```

Joint synergy gap 定义为：

```text
min(cost(O1), cost(O2)) - cost(O3)
```

Oracle 只有在外部 evaluator 确认重新计算了 queue、service、residency 和 physical action 后才接受
JCT。固定原策略的 wall-clock physical trace 只能比较决策，不能报告反事实 JCT。

当前 O0-O3 数据结构、冻结 request DAG、queue/service resimulator、token-exact tiered Radix 和
rolling allocator 已实现。候选顺序会逐 quantum 重算 cache hit、active lock、unique growth、
GPU/Host residency、D2H/H2D/drop 和 HBM peak。trace-order oracle 对小 DAG 穷尽合法拓扑顺序，
超过预算时显式标记 bounded search。

一个 1,000-token HBM 的真实模型 mixed 单-workflow trace 穷尽 120 个顺序后得到 O0/O1
38,859.629 ms、O2/O3 38,853.685 ms，synergy gap 为 0。该结果是负例而不是收益证据；trace 为
semantic-race-sensitive、没有 workflow fairness 竞争，PCIe 使用配置值。

正式动态并发 GPU trace 已采集并冻结，但旧 rolling O0 与真实 run 严重不对齐：真实 mean
JCT/D2H/H2D 为 589.08 s/0.715 GB/0.189 GB，rolling O0 为 115.66 s/8.814 GB/0。全 mixed
run 又发现 native demand-load 未进入 BeliefKV telemetry，因此当前不能继续报告反事实性能。

## 10. 模块与代码落点

| 层 | 主要代码 | 当前状态 |
|---|---|---|
| Identity/Event | `core/events.py`、`core/ids.py`、`core/config.py` | 在线完成 |
| RCCG | `control/causal_graph.py` | 在线完成 |
| Consumer facts | `control/data_consumers.py` | 机制完成，P3 新增 |
| Online controller | `control/controller.py` | P2 reactive 在线；提供统一 snapshot/control generation，P4 plan 不在此执行 |
| Frontier/Residency/Fairness | `policy/causal_frontier.py`、`residency.py`、`workflow_fairness.py` | 在线完成 |
| Admission | `policy/admission.py` | 在线完成 |
| Lease/Bundle | `policy/leases.py`、`runtime/bundles.py` | P2 在线完成 |
| Transfer policy | `transfer_planner.py`、`shadow_controller.py`、`transfer_guard.py` | Reactive 在线；预测收益待证 |
| Predictor | `predictor/frontier_belief.py`、legacy `predictor/` | P6 closure-complete scope、belief schema 和 finite horizon 已实现；模型未训练 |
| Predictive risk | `policy/predictive_joint.py` | A0/PREPARE/PREFETCH 离线 Benefit/CVaR 选择已实现；未接在线数据面 |
| Common policy contract | `policy/reference/`、`policy/resource_snapshot.py` | P2.5 完成，Shadow/Replay |
| What-if/Oracle | `scenario_physicalizer.py`、`whatif_packer.py`、`joint_oracle.py` | P3 离线机制完成 |
| Joint runtime | `policy/joint_scheduler.py`、`policy/online_joint.py`、`runtime/joint_shadow.py`、`runtime/sglang_v052rc1.py` | P4 worker mirror 与 P5 validated online compiler 已接入；默认关闭，GPU gate 待完成 |
| Physical mirror/arbitration | `runtime/page_index.py`、`runtime/radix_arbiter.py` | 在线完成 |
| SGLang backend | `runtime/sglang_adapter.py`、`runtime/sglang_v052rc1.py`、`patches/` | 显式 command 与 native demand-load/write-back telemetry 已接入；autonomous w4 统一覆盖 1,535 条物理 telemetry，shutdown ACK 与 command integrity 通过 |
| Agent event adapters | `runtime/agent_safety.py`、`langchain_tool_safety.py`、`event_channel.py`、`deepagents_adapter.py`、`codex_adapter.py` | Deep Agents 工具错误、断路、结构化终态和 workflow 级取消已通过 CPU 测试；GPU clean gate 与更多 framework 待接 |
| Action/prefix observation | `runtime/action_frontier.py`、`prefix_affinity.py` | P6.0 revision 与训练前 coverage 已实现；固定 w4 trace 的 runtime-only action/reentry coverage 已采集，exact boundary 仍为 0% |
| Trace | `traces/normalizer.py`、`runtime_validation.py`、`characterization.py` | 标准化/验证完成；P3 characterization 新增 |
| Simulator | `simulator/queue_service.py`、`token_radix.py`、`rolling_physical.py`、`rolling_queue_service.py` | rolling token/allocator 机制完成；真实 extent/PCIe/fairness gate 待闭合 |
| Experiments | `experiments/deepagents_swebench.py`、`langgraph_peer_workflow.py`、`policy_replay.py` | 12-workflow mixed characterization 已跑；配对 A/B 待跑 |
| Metrics | `metrics/transfer_timeline.py`、`transfer_validation.py` | 显式/native DMA 统一时间线，物理 lock/closure/migratable/dual-resident 及 100/500 ms locked-but-not-served 下界曲线已接入；autonomous w4 验证 0 个 Host/page-index mismatch |

## 11. P0-P8 实施状态

| Phase | 状态 | 已完成 | 仍缺少 |
|---|---|---|---|
| P0 Correctness baseline | 在线完成 | RCCG、admission、page mirror、ACK、trace/audit 基线 | 持续回归 |
| P1 Telemetry | 部分完成 | HBM/Host snapshot、显式与 native demand-load/write-back telemetry、统一 timeline | compute wait、copy-engine/PCIe/GPU utilization 的原生观测 |
| P1.5 Retry guard | 机制完成 | typed blocker、event-gated release、retry storm 消除 | 同 manifest 性能配对 |
| P2 Physical bundle | 可靠性 gate 通过 | lease、preview、fingerprint、atomic preflight/commit/rollback | P1.5/P2 配对性能、service curve 尾部、controller timing |
| P2.5 Common policy contract | 完成 | immutable PolicyInput/Output、metadata 隔离、B0 replay | P8 按需增加有效 baseline |
| P3A Dynamic instrumentation | 部分完成 | consumer/action/prefix/topology、required-range fan-out、固定时长 GPU-ready probe | 稳定 ready 并发、配对 A/B、fanout 多样性、boundary-token coverage |
| P3B Jointness analysis | 部分完成 | B0、what-if、bounded search、rolling Radix/allocator、HTML timeline | 从真实 pressure snapshot 做局部 Jointness Audit；完整动态 O0-O3 后置 |
| P4 JointPlan shadow | CPU 修复完成，GPU 复验待执行 | visible ticket、无 reservation restore、增量 Page/RCCG journal、lossless-coalescing worker mirror、root-workflow 公平快照、per-action current-state validation、anytime best-prefix、分阶段 planner budget、增量 bundle/lease/closure rebuild、bounded snapshot/audit、native DMA telemetry、locked-but-not-served observer、workflow absolute deadline；Host pool 默认扩到 96 GB，Host 生命周期含 terminal cleanup、Host-copy eviction 与容量饱和淘汰 | 真实 GPU safe-point/stale/coverage/终止性、Host 字节一致性和 lock-service 归因覆盖率复验；run-to-yield 留到 P5 |
| P5 Online observed JointPlan | P5G system correctness gate 通过，接口可冻结 | semantic/physical 两阶段 planner、统一 anytime modes、显式 RetractionIntent、持久 restore debt；`APPLY_EVENTS -> CAPTURE_AND_PLAN -> TRANSACTIONAL_COMMIT`、epoch 内惰性单快照、commit read-set 复验、typed enqueue/canonical ACK 多订阅、ExternalProgressToken、prepare/rollback 和 exclusive restore authority；确定性 micro-gate 完成 4.40 GiB D2H/H2D；autonomous w4 完成 4/4 system JCT、一次生产 retraction/restore/service 闭环与 ACK/shutdown 守恒；无效 overlap barrier 从 493 request/drain 降为 1/1 有效事务 | P6 物理动作开放前补短时 w8 smoke；agent-native clean JCT 0/4 和工具恢复质量单独处理，不回滚 P5 架构 |
| P5.5 Action-unlock gate | 未执行 | observer 基础存在 | 相对 B0/O1/O2 的真实 coverage/gap |
| P6 Predictive JointPlan | P5G system gate 后可恢复训练前开发，预测性物理动作仍关闭 | closure-complete scope、global scenario/OTHER、finite horizon、P5E revision、ActionGroup、离线 A0/PREPARE/PREFETCH risk selector、第一轮固定 trace coverage | 补 service/external model、rollout cache、predict-plan shadow；短时 w8 correctness smoke 后再开放在线物理动作 |
| P7 New HiCache portability | 未实现 | 固定旧版本 contract | 新版 adapter 与能力协商 |
| P8 Deferred competitors | 未执行 | related-work 与通用 trace contract 已建立 | 按稳定接口实现 metadata 分层、same-data-plane 与原生系统公平对比 |

## 12. 已有真实证据

### 12.1 当前代码测试

2026-07-31 P5G transactional restore 修复后执行完整 CPU 回归：

```text
conda run -n beliefkv pytest -q
493 passed, 10 skipped, 3 subtests passed
```

该结果覆盖 P6 训练前契约，并新增 P5G 惰性 physical snapshot、epoch 阶段转换、commit read-set
复验、request ID 重用、native/explicit operation 变化、typed enqueue/canonical ACK 多订阅、
ExternalProgressToken、RestoreTransaction 和 exclusive authority 故障注入；不代表 P5G GPU
clean-completion、预测模型或性能已验证。

2026-07-22 对当前工作区执行：

```text
conda run -n beliefkv pytest -q
326 passed, 7 skipped

conda run -n beliefkv-agents pytest -q \
  tests/test_deepagents_adapter.py \
  tests/test_deepagents_swebench.py \
  tests/test_multi_agent_runtime.py \
  tests/test_counterfactual_trace.py
60 passed

conda run -n beliefkv-agents pytest -q tests/test_multi_agent_runtime.py
15 passed
```

源码同时通过 `py_compile` 和 SGLang source contract；固定版本为 `0.5.2rc1`、commit
`18f91eb639084825717c0e3c3c7273492812ab71`。

### 12.2 P2 真实 GPU 可靠性复验

2026-07-21 的 Qwen3-Coder-30B-A3B-Instruct-FP8、RTX 6000 Ada、SGLang 0.5.2rc1
高压复验得到：

| 检查项 | 结果 |
|---|---:|
| workflow system terminal state | 8 / 8 |
| request started / finished | 848 / 848 |
| transfer dispatch / ACK | 1502 / 1502 |
| missing/orphan/order/byte violation | 0 / 0 / 0 / 0 |
| watchdog / scheduler exception | 0 / 0 |
| identical failed/zero-byte retry | 0 / 0 |
| dispatch without matching preview | 0 |
| HBM mirror exceeds allocator | 0 / 38,594 snapshots |
| Host page-index mismatch | 0 / 38,594 snapshots |
| offload planned/actual reclaim | 52,851,376,128 / 52,851,376,128 bytes |
| reclaim realization | 100% |

该结果证明 **P2 物理可靠性 gate 已通过**，不证明性能提升。

### 12.3 尚不能从该实验得出的结论

- 只有 3/8 workflow 通过本地任务 correctness gate，其余为 blocked/no-patch-needed；
- 没有同代码、同 manifest、同语义路径的 P1.5/P2 配对 run；
- admission P95 仍为 61.46 s，不能归因或宣称改善；
- service-curve 总体低估率为 13.48%，D2H 为 23.53%，尾部仍不够保守；
- SIGINT 路径未写出 controller timing summary；
- 真实 mixed multi-agent characterization 已执行，但没有配对 A/B 和重复实验；
- 单-workflow rolling O0-O3 的 synergy gap 为 0，尚无多-workflow 正结果；
- 12-workflow rolling O0 与真实 JCT/transfer 明显不一致，不能进入性能表；
- SGLang native demand-load 不在现有 transfer telemetry 中，当前 H2D timeline 是下界；
- action-unlock synergy 尚无真实 gap 结果。

### 12.4 P3 Queue/Service 标定

同模型、同 SGLang 配置的 39-request microbenchmark 覆盖单/多 chunk prefill 和 decode batch
1/2/4。`episode_piecewise_isotonic_v1` 在独立 holdout 上得到总体 P95 relative error 20.09%，
prefill 4.51%，decode 23.49%，通过 25% 门槛。该结果只关闭 GPU service-time 子问题；旧
agent trace 的 policy-dependent cache hit 与 future physical growth 原先不满足 timing gate。
rolling replay 已能在完整 token trace 和空 Radix epoch 下重算候选 cache hit 与 growth，但
尚未替代真实 page/node extent、PCIe holdout 和多-workflow fairness 证据。

### 12.5 P3 动态并发 GPU Characterization

12 个全 mixed workflow 在 163,840-token KV pool 下全部 semantic complete，峰值 HBM 为
96.93%。trace 含 48 次 SPAWN、12 次 JOIN、40 次 HANDOFF 和 17 次 REACTIVATE。BeliefKV
显式路径的 14 次 D2H、8 次 H2D 全部完成，且没有 partial/reject/retry storm。

该 run 同时否决了两个过强假设：一笔 join 后无消费者 H2D 占显式 H2D 的 37.52%；三个已
offload parent 通过 SGLang native demand-load 恢复，却没有 BeliefKV H2D telemetry。因此该
实验支持统一 observed-state control 的研究动机，但不支持当前性能结论。完整报告见
[P3 动态并发 GPU Characterization](experiments/beliefkv_p3_dynamic_gpu_validation_2026-07-21_zh.md)。

### 12.6 P3A 固定时长 GPU-ready 探针

2026-07-22 只执行了一次 240 秒 required-range 探针：4 个 mixed workflow 分两批到达，初始
coder 必须运行时创建 2--4 个 child。实际 4 个 workflow 各创建 4 个 child，系统瞬时达到
16 个 running request；但按 snapshot 时间区间积分后，平均 running request 仅 4.03，
`running <= 2` 占 68.13%，`running >= 8` 仅占 24.36%。同一窗口 GPU 利用率平均 26.77%，
低于 20% 的时间占 64.17%。

低 ready 并不等于低显存压力：`running <= 1` 的区间占 65.09%，其中 HBM KV 平均仍为
95.62%。267 个 SGLang batch log 观察点的 queue 均为零。因此 required-range 机制 gate
已通过，但 simultaneous fan-out 只能形成 burst，**稳定 GPU-ready 并发 gate 未通过**。
稳定 GPU-ready workload gate 仍停留在 P3A；P4 只能推进无副作用 runtime shadow 机制，
不得据此进入 P5 或报告性能收益。完整边界与数据见
[P3A GPU-ready 并发探针](experiments/beliefkv_p3_gpu_ready_probe_2026-07-22_zh.md)。

### 12.7 P4 Runtime Shadow 接入

2026-07-23 已将原混合异步路线改为增量 mirror：scheduler safe point 只复制自上次发布以来的
RCCG event、PageIndex replacement record、当前 waiting request/resource/fairness/control 值；容量
1 的 worker 顺序合并所有 delta，并在独立 RCCG、consumer、PageIndex mirror 上构造 immutable
`PolicyInput` 和 observed `JointPlan`。pending sequence 可以 latest-wins，但其 delta 被合并而非
丢弃，审计分别报告 `coalesced_pending_count` 与真正的 `dropped_pending_count`。

P4 对 fully-fresh plan 只记录 `joint_plan_would_apply`；部分 action 仍有效时记录
`joint_plan_shadow_partial`，全 stale、过期或 worker failure 均不触发同步完整规划，也不修改
admission、waiting queue、transfer queue 或 physical residency。source snapshot 使用完整
`PlanReadSet`；结果返回后按 execution、每条 admission、每条 residency 和每条 dependency 定点读取
当前 request/invocation/join/transition/touched extent 并独立校验。graph/topology/allocator 全局 stamp
变化仅作为 `strict_global_stale` 对照指标；实际公平顺序未变、无关 request/extent/transfer 变化不会
淘汰整份计划。目标 extent 会重算 lease、物理 blocker 和 descendant closure，缺失或无法解析时
fail closed。审计已覆盖每类 component 的 valid/invalid 数量与原因，以及 delta capture、worker
snapshot build、queue/compute、publish、validation、plan age、coalesce/drop 和 coverage。

全量 CPU 回归为 `344 passed, 7 skipped`，其中测试显式禁止增量 runtime 调用 live controller
完整 snapshot builder，覆盖 worker busy 时连续 delta 无损合并、单 request/bundle 局部失效、
fairness revision 前进与实际 priority 翻转，以及目标 extent bundle 与完整 snapshot 一致性。该节点
只证明接入正确性。真实 GPU
safe-point P99、GIL 干扰、plan age、drop/stale rate 与 would-apply coverage 尚未测量，因此 P4
实验 gate 仍未通过。

### 12.8 Locked-but-not-served 观测

2026-07-23 已加入只读的 `RequestServiceLedger`。SGLang batch 被选中仅建立 request 身份和
首次观测时间；只有 `process_batch_result` 完成后才记为一次真实 GPU service，因而不会把
waiting/running 状态本身误当成获得 token service。资源采样时，adapter 从每个 tagged running
request 的 `last_node` 沿父链走到 Radix root，并与 `engine_lock_ref > 0` 的物理 extent 做精确
关联。共享 prefix 在物理指标中只计一次，另行保留按 request 重复的 logical bytes 供诊断。

观测分别报告 100 ms 和 500 ms 窗口。只有 extent 的全部 `engine_lock_ref` 都能由当前运行
request 路径解释，并且所有 blocker 均已超过窗口未完成 service 时，才计入
`locked_but_not_served_gpu_bytes_*`。部分归因、缺失路径、额外 engine lock 均归入 unknown；初次
进入 batch 但尚未超过窗口的 request 归入 warming。因此该数字是保守的物理字节下界，不是
“当前可立即迁移字节”。PageIndex 复用同 revision 的 physical breakdown 缓存，新增热路径只遍历
当前 running request 的 Radix 祖先链和已缓存的 locked extent，不增加第二次全树扫描。

该 observer 不生成 ticket，不重排 waiting queue，不 retract running batch，也不改变 KV
residency。时间线新增两个下界曲线、完整归因覆盖率和 stale/engine-lock sample ratio。全量 CPU
回归为 `371 passed, 7 skipped`；尚未运行 GPU characterization，因此该节点自身不能证明锁策略
过于保守。

### 12.9 Observed Active-Set Admission

2026-07-25 已完成 P5A 的 admission-only 在线切片。该路径不消费异步 JointPlan，也不运行
physicalizer/packer；它在 SGLang 每个 batch-construction epoch 使用当前 observed state 决定哪些
tagged waiting request 获得短期 ticket。当前 active KV footprint 定义为：

```text
PageIndex 中 engine-lock/active-reader 保护的 Radix 物理唯一字节
+ running/chunked request 中尚未进入 matched prefix 的私有 KV 字节
```

策略在 `(KV pool - reserve) * active_kv_high_watermark_ratio` 上建立 active-set 高水位。普通新增
request 的 `uncached prompt + remaining max output` KV 上界必须放入剩余 headroom，并继续接受
SGLang `PrefillAdder` 的原生 token、slot 和 allocator 校验。若 running request 数低于
`observed_admission_min_active_requests`，只允许补足该固定 floor，且仍不超过原生可用 HBM；这是
唯一可绕过高水位的 work-conserving 路径。等待超时只改变候选顺序，不绕过容量 gate。

候选排序只使用已观测 causal class、unblock depth、workflow virtual runtime、workflow 内
frontier round、等待时间和增量 KV；不预测 tool duration、下一 agent 或剩余 workflow。公平是
软排序，同一 workflow 可在一个 epoch 获得多个 ticket。无 metadata request 完全绕过该 gate；
`WAIT_RESTORE`、terminal 和 transition-open 继续由 side state fail closed。active-set 计算异常时
审计 `observed_admission_fallback`。在独立 P5A 模式可回退原 reactive compiler；启用统一 P5
JointPlan 时只能回退同一 planner 的 bounded/emergency epoch，显式 blocker 不被解除。

配置项为 `observed_admission_scheduling_enabled`、
`observed_admission_active_kv_high_watermark_ratio` 和
`observed_admission_min_active_requests`，默认关闭。审计记录每个 epoch 的 active budget、footprint、
headroom、Radix lock、running-private bytes、policy/native HBM budget、mode 和 ticket 结果。全量
CPU 回归为 `377 passed, 7 skipped`。

该切片只阻止产生新的 lock owner，不能释放已经运行的 request 或其 8--14 GiB 锁路径。因此其
预期结果是降低后续 lock-footprint 墾殖，不是单独解决既有 convoy。尚未执行真实 GPU gate，当前
不能宣称 JCT、GPU utilization 或 admission tail 改善；高水位 `0.8` 只是初始实验参数，必须用
固定 trace 做 sensitivity sweep，而不能作为经验常数写入论文结论。

### 12.10 Observed Selective Running-Batch Retraction

2026-07-26 已完成默认关闭的 P5B CPU/接口切片。固定版 SGLang 新增
`ScheduleBatch.retract_selected(request_ids)`；它只在 scheduler safe point 释放被选请求的
request-private KV 和 `last_node` lock，不调用额外的 Radix LRU，也不替策略选择其他 victim。原生
allocator shortage retraction 保持不变，仍作为最终 liveness fallback。

BeliefKV 只在 observed admission 持续无 ticket、active KV 超过高水位或最高优先级 replacement
超过当前 `available + evictable` 容量时考虑 retraction。planner 从 100/500 ms service ledger、RCCG
causal class、root-workflow virtual runtime 和完整 lock provenance 构建候选；一个 extent 只有
`engine_lock_ref == |blocker set|` 且全部 blocker 同时被选择时才计入 expected unlock。选择过程按
blocker package 求解，不把共享 extent 重复计费，并至少保留一个 running request。任一 blocker
路径含 semantic pin、active reader、in-flight transfer 或 unsealed extent 时，该 request fail closed，
不能成为主动 retraction victim。

执行使用版本化事务：

```text
observed admission stall
  -> blocker-set plan
  -> selective native retraction
  -> force Radix/PageIndex rematch
  -> measure allocator available delta (not available + evictable)
  -> if insufficient, rematch exact physical closure
  -> explicit OFFLOAD_CONTEXT or DROP_CONTEXT
  -> physical ACK and allocator-free confirmation
  -> confirmed: one-epoch replacement priority
  -> partial/rejected/stale/timeout: release barrier without priority
```

被 retract 的请求以 `retraction_cooldown` 留在 SGLang 原生 waiting queue，避免同一 epoch
re-admission；其逻辑 output token 保留，但未缓存 private KV 在恢复时需要重算。只有实际
allocator `available` 增量达到 reclaim target，且当前绝对 free bytes 足以覆盖首个 replacement 时，
replacement 才获得一次性优先级。`evictable_size` 只用于 retraction 机会识别，不能作为事务提交证据。

2026-07-26 的 P5C 增量把新解锁 closure 编译为版本化 physical bundle。事务只豁免该次被 retract
context 的逻辑 RUNNING lease；engine lock、active reader、semantic pin、in-flight、foreign active owner
仍 fail closed。优先选择 exclusive suffix；共享 bundle 仅在全部 owner 都属于同一 blocker set 时可选。
Host 足够时执行显式 D2H/COMMIT_CPU；`DROP_CONTEXT` 保留 dual-clean Host copy，GPU-only drop/recompute
默认关闭，只有显式配置才允许。所有 tagged waiting request 在 physical ACK 前受 transaction barrier
约束；ACK partial/rejected/stale 或 5 秒事务超时后一次失败退出，不做盲目重试。全量 CPU 回归为
`389 passed, 7 skipped`；真实 GPU correctness、recompute、thrash、JCT 和 utilization gate 尚未执行。

2026-07-27 增加只读 `TentativeUnlockPreview`。PageOwnershipIndex 接受临时 engine-lock ref override，
在不修改 page、revision、Radix topology 或物理 breakdown cache 的情况下重新计算 descendant closure，
输出 lock-ref 归零字节、首次变为 migratable 的物理唯一字节和 closure amplification。request blocker
映射只在 `engine_lock_ref == |完整 blocker set|` 时应用；路径错误、缺失/重复 extent 和部分归因均
标记 `provenance_incomplete` 并保守保留原 lock。barrier 前记录所有 observed-stale blocker 的
unconstrained upper bound，安全点选出 plan 后记录 selected-set preview，并在真实 retraction callback
记录 realized delta 和误差。两条 preview 当前均为 `shadow_only`，不跳过 barrier、不改变 victim、
admission 或迁移事务；汇总记录 reason/exactness 和计算开销 P50/P95/P99。全量 CPU 回归为
`403 passed, 7 skipped`，GPU 外部有效性尚未验证。

## 13. P6 训练前实现状态

截至 2026-08-04，P6 已完成 development-only 模型管线和正式数据采集契约，但尚未产生可用于
论文评价的跨项目 train/calibration 模型，也没有预测动作进入在线 safe point。已经实现的部分为：

```text
ActionFrontierObserver
  -> revision + boundary/reentry/demand coverage report

RCCG + P5 active-window seeds + physical blocker sets
  -> BeliefScopeBuilder
  -> closure-complete CausalAtom
  -> included atoms / whole-atom OTHER

RestoreObligation / RestoreLease / RestoreServiceGrace
  -> PersistentLivenessRevisionTracker
  -> obligation_revision / lease_revision / grace_revision

FrontierBeliefSnapshot contract
  -> global joint demand scenarios + OTHER + finite horizon + evidence read set

candidate JointPlan + safe-point physical snapshot
  -> Radix physicalization + complete batch composition
  -> conditional service distribution + TimedScenario
  -> child completion then RCCG JOIN/message resolution

frontier_decision_points + explicit project split
  -> local conditional Structured Frontier model
  -> RCCG particle composer
  -> held-out temperature/conformal calibration

A0/PREPARE_HOST/PREFETCH_GPU offline evaluation
  -> ScenarioRiskPlanner
  -> expected benefit + CVaR regret + deterministic/chance/liveness gates
```

`BeliefScopeBuilder` 的预算限制原子组数量和估计建模成本，不限制 invocation 数组长度。JOIN 的全部
未完成成员与 waiter、blocking-child chain、完整 physical blocker set 和已观测 message producer-
consumer 会先求闭包；超预算时整组进入 OTHER，不能截断后错误声称 parent 即将 unlock。

`policy/online_joint.py` 已定义 `ActionGroup`、dependency DAG、resource certificate、compensation 和
`ALL_OR_NOTHING/PREFIX_COMMITTABLE`。当前 P5 `ActionSlice` 会生成 dependency-connected group 供审计，
但 P5 在线执行语义未改变；P6 第一版只允许 `ALL_OR_NOTHING`，且必须在当前 topology/allocator、
HBM/Host、physical generation 和 P5E revision 上重验整组。

`ScenarioRiskPlanner` 当前是离线纯函数，不模拟完整 RCCG，也不发送命令。候选动作仅包括
`PREPARE_HOST/PREFETCH_GPU`；predictive retraction、run quantum、COMMIT_CPU、DROP/RECOMPUTE 均未
实现。

2026-08-01 使用通过 P5 system gate 的固定 autonomous w4 完成第二轮 P6.0：575/575 request 通过
native request/workflow/invocation 严格关联，ordinal fallback 已删除；575/575 request 同时具有
prefill/decode service，45,201 个 batch sample 展开为 108,344 个 request interval；600 个 external
wait、5 个完整 JOIN 和 1,535 个条件字段完整的 transfer operation 已导出。exact incremental action
boundary 仍为 0/575，故只允许训练 remaining-decode-demand fallback；reentry 为 539/559，20 个缺失 action
没有逐 call suppression/censor event；direct DMA timestamp 为 0/1,535，只能建模 submit-to-complete。
全部数据来自 SymPy，当前只可用于 schema/训练代码开发。完整报告见
`docs/experiments/beliefkv_p6_0_autonomous_w4_training_evidence_2026-08-01_zh.md`。

2026-08-03 已补 `CALL_CENSORED` 逐调用事件，duplicate suppression、LLM timeout/abort 等携带
request/tool-call 和 invocation identity，不再从 aggregate count 反推。decision schema v2、数据导出
schema v3 新增事件
采样的 `frontier_decision_points.jsonl` 与 `censor_events.jsonl`；采样只发生在 LLM/tool/runtime
transition、每 32 decode token、HBM threshold crossing 和有 owner 的 transfer completion。

显式 split manifest 已按 project 冻结：SymPy 整体为 development-only，其余 SWE-bench Verified
项目按 train/calibration/test-ID 隔离，同一 repository/task/base commit/重复 rollout 不跨 split。
`configs/p6/collection_v3/collection_plan.json` 固定 65 个唯一任务、91 个 workflow、11 个 repository，
predictor 和 predictive action 均关闭；calibration/test batch 默认由 runner 封存，必须显式授权。

`Structured Conditional Particle Model` 已实现 variable-order boundary context tree、tool competing-risk、
log-binned decode/output/prompt demand distribution。RCCG composer 只保留 closure-complete JOIN/member、
producer 和 blocking-chain 依赖，不再对 raw demand 计算 JOIN max/min。W4 的 decision point
仅生成 development model，用于序列化 sanity check；它不参与最终模型选择或结果报告。正式 fit 先在
local episode 内归一化，再在同一 workflow rollout 的 episode 间归一化，避免长 workflow 以采样次数
获得更大权重。calibration 只学习 boundary/tool temperature 和按 local
episode 最大 nonconformity 的 split-conformal interval，不重拟合 train counts；离线评价输出 NLL、
Brier、ECE、区间覆盖/宽度和 OOD fallback。下一项工作是完成分批跨项目 train 数据采集、独立硬件
service curve 和一种 non-coding OOD workload，再执行 calibration/test。

首个 `p6-009-train-mixed-r0` 采集尝试已被标记为 development diagnostic，不能进入正式 fit。该轮
8/8 workflow 虽完成并产生 820 次 LLM、1,108 次工具调用和 12,796 个 decision point，但默认的两次
correctness repair 在模型已经提交有效 `WorkflowCompletion` 后继续启动独立 LLM repair agent，使
采集时长和状态分布不再代表原始 agent workflow；另有两条 runtime event 因 1 秒 ACK 窗口未及时
确认。修复后的正式 collector 使用 model-terminal semantics、关闭 harness LLM repair、将 event ACK
窗口设为 10 秒，并在 batch 前后校验 runtime source fingerprint。无效 run 的 manifest 被强制标记
`formal_training_eligible=false`，全部样本降为 `development`；fit/calibration/test loader 同时拒绝
无效 run、重复 run 和重复 decision，防止静默数据泄漏或重复计权。

第二次完整采集尝试进一步发现 autonomous `create_deep_agent()` 路径没有安装 BeliefKV 的
`ContextLifecycleMiddleware`：7/8 workflow 已完成，但最后一个 child 在持续获得 GPU service 的同时
增长到约 66.8K sequence token，超过预定的 32K dynamic-history 生命周期，因此整轮以
`COLLECTION_INVALID.json` 作废。修复后 autonomous runtime 不再叠加 Deep Agents 默认 170K summarizer，
而是显式构造等价的 filesystem/execute/task/todo/patch-call 栈；parent 和四类 child 各持有独立的
32K/retain-8K lifecycle，child 返回时剥离 `_summarization_event`，正式 collector 的普通与终态输出
预算均为 4096，summary 为 2048。CPU/graph 回归通过；后续 startup failure 的原因分别包括外部 GPU
占用、GPU1 上异常大的权重 footprint 导致无 KV pool，以及旧 frontend 残留占用 18000 端口，均未
提交 workload、不构成正式数据。旧 frontend/scheduler 的受控关闭路径已经补充 PID start-time 校验和
显式 scheduler 退出等待。导出器现统一拒绝
`PILOT_INVALID.json`、`COLLECTION_INVALID.json` 和 `STARTUP_FAILED.json`。

使用旧的 development-only diagnostic 数据重新执行了训练/序列化 sanity：12,796 个 event-sampled
decision point、828 个 request/runtime episode、959 个 local episode 均可拟合并写入
`training_summary`，模型 metadata 明确为 `development_only=true`。这不是正式训练；只有完成至少
两个 train project（当前顺序为 `p6-009` 后接 `p6-010`）后才允许执行 project-macro LOPO。

2026-08-04 的 image-identity 复核发现旧 `p6-009-train-mixed-r0/20260803T100537Z` 可能按
repository/version 复用错误的 SWE-bench task image。raw run 与历史报告保留供审计，但两个 processed
dataset manifest 均已强制为 `formal_training_eligible=false`，正式 loader 会拒绝它。

当前可接受的 local-label evidence 包括 `p6-010` GPU0 的四个 workflow、`p6-012` GPU1 的八个
workflow，以及对 `p6-010`/`p6-011` 失败任务的两次独立定向复跑，共 11,491 个 fit-eligible decision
point、5 个项目、14 个不同任务、14 个 rollout 来源。其中 requests recovery 仅保留首次 runtime 干预前
且 horizon 不跨界的局部标签，不能用于 JCT、终态或完整轨迹。原始 `p6-011` shard 中一个 workflow 将输入
膨胀到约 812K token，超过 262K 服务端窗口，仍只按 `development_diagnostic` 导出；修复后复跑是
source fingerprint 稳定的独立正式数据源。正式 fit 的 provenance gate 排除所有固定 P5 w4 和无效
collection；diversity gate 另要求至少 5 个项目、40 个不同任务和 40 个 workflow。当前训练命令按预期以
`task_count=14<40, workflow_count=14<40` 拒绝执行，因此尚无正式 Frontier 模型。40/40 只是最小防误
训练门槛，论文级 fit 目标仍为全部 7 个 train project 上 80--120 个 workflow，并使用封存的
calibration/test project 评价泛化。完整收尾见
`docs/experiments/beliefkv_p6_multproject_collection_2026-08-04_zh.md`。

失败任务修复新增三条数据边界：每轮工具 observation 总量受版本化预算约束，避免并行工具结果把单次
prompt 推到服务端窗口之外；当前 LangGraph 以 384-step 记录 telemetry，512-step 为硬保险丝；
repository/image-specific preflight 在 agent 启动前验证测试依赖、pytest plugin 与 fixture。
runtime 首次注入
loop-guard、graph-budget 或 terminal-repair prompt 之后及跨越该时刻的 decision point 会保留供审计，
但标记为 `training_eligible=false`，防止 Frontier 模型学习 runtime 干预后的特殊行为。定向复跑分别
保留 65 和 402 条自然 decision point。

train-project 内的超参数选择已实现为 leave-one-project-out（LOPO）：每个 project 等权，目标由可用的
boundary/tool NLL、按 held-out project 中位目标尺度归一化的连续值 MAE 和 OOD fallback 惩罚组成。
selection manifest 必须与最终 fit 的 project 集合完全一致；它只选择同一结构化模型的 context order、
minimum support 和 smoothing，不新增 predictor 或动作决策源。

硬件服务模型已与 agent 语义模型分离。`run_queue_service_calibration.py` 覆盖 prompt 1K/4K/16K/约
32K、cache hit 0/0.5/0.9 和 batch 1/2/4/8/16；导出器按唯一 `sample_id` 将 tagged case 还原为完整 batch，
记录每个 request 的 token delta、sequence length、cache hit，以及 chunk/mixing/PCIe/HiCache 条件。
`GPUServiceCurveModel` 只接受 `controlled_microbenchmark` batch 行并输出 P50/P90/P95；agent runtime
overlap interval 标记为 `runtime_validation`，只能评估，不能进入 fit。client elapsed 和逐 request 复制的
batch elapsed 均不能作为 GPU service 标签。PCIe 继续使用 direction、bytes、extent/page count、Host
pinned/copy 状态、command kind、native HiCache contention、allocator 和 callback 分段字段。

Frontier 正式 loader 对顶层 `batch_size/elapsed_gpu_service_ms/observed_gpu_service_ms` 和旧
`remaining_gpu_service_ms/next_gpu_service_ms` fail-closed；共享 batch elapsed 只能放在 `diagnostics`。
runtime 的 `LLM_SUBMIT` 记录消息、工具 schema、模型采样配置的 `prompt_semantic_sha256` 与独立
`sampling_seed`。`audit_p6_load_invariance.py` 可在 w1/w4/w8 间按相同语义 prompt、seed 和重复 occurrence
配对；没有显式 seed 的结果只作 diagnostic，不能声称 demand 已与负载解耦。该审计只检验标签污染，
不会把 message race、JOIN_ANY winner 或 timeout 导致的真实调度因果差异强行消除。

`TimedScenario` 将 dependency release 与 invocation completion 分开：child 的 candidate-specific GPU
completion 先解析 JOIN reentry，parent 后续 prefill/decode 还必须等待候选 H2D completion 和 batch
service。这样 JOIN 只对完成时刻取 max/min，不会对 raw token demand 取 max/min，也不会把 parent
恢复时刻误写成 parent 最终完成时刻。

## 14. 必须维持的系统不变量

1. RCCG 只保存已观测因果事实，不拥有或释放物理 KV。
2. causal edge、consumer edge 和 physical prefix owner 必须分开维护。
3. SGLang 是 allocator、Radix topology、KV tensor 和 DMA 的唯一物理真相源。
4. shared physical extent 只计费一次，并由最强 owner lease 保护。
5. admission 使用实际 HBM、reservation 和 ACK，不把计划释放当作已释放。
6. context epoch、allocation generation、graph/topology/allocator version 防止 stale action。
7. bundle preview 只是版本化意图，arbiter 和 backend 必须在执行前重新验证。
8. active reader、engine lock、semantic pin、in-flight 和 closure blocker 不能被预测绕过。
9. ACK 前不提交 residency；telemetry 不能替代 correctness ACK。
10. online policy 不能读取 hindsight metadata；B0 replay 保持 shadow-only。
11. 未创建的预测 agent/request 不能被实际调度或获得物理资源。
12. predictor/OOD 失败必须退化到 observed-state JointPlan，而不是切换到第二策略源或破坏 liveness。
13. 无 `beliefkv_metadata` 的请求必须保持上游 SGLang 行为。
14. cache reset 和 abort 必须先清理 in-flight bookkeeping，再失效 page handle。
15. BeliefKV command telemetry 与 native HiCache operation telemetry 必须分源记录并统一计费；
    command integrity 通过不能替代全系统 DMA coverage。
16. 每笔 committed running retraction 必须对应持久 restore obligation；普通 waiting request 的
    matched Radix path 一旦出现 CPU-only extent，也必须创建 obligation 或记录明确的 obligation
    capacity blocker。任何非终态 obligation 必须具有 in-flight H2D、已提交的 D2H funding、带状态
    stamp 的具名 blocker 或可发放 ticket，禁止无 durable debt/blocker 的 `WAIT_RESTORE`。
17. `_active_request_ids` 不得作为物理 ownership；`ENGINE_BUSY` 必须来自 safe-point physical
    snapshot 中的 req-pool/lock/native-load/explicit-transfer 事实。
18. guard 与 command ownership preflight 必须早于 allocator reservation；一次 canonical command
    的 terminal ACK 必须通知所有 restore subscribers。
19. `RESTORE_DRAIN_ACTIVE` 期间 coordinator 是唯一 admission/residency authority，普通 JointPlan
    不得提交在线动作。
20. transfer estimate 必须绑定候选的真实 closure shape，不能只按 context bytes 计费。
21. safe point 必须重建 live shape；它超出 intent 收益包络时，预测动作 fail closed。
22. shape model unsupported/OOD 时必须退化到 P5 observed seed，不得跨 extent bucket 乐观外推。

## 15. 当前关键缺口与优先级

以下先记录已经闭合的 P5 基线；它不是下一阶段待办。

P5G full CPU gate 已完成。旧固定 w4 的失败被拆成两个独立问题：人工 peer orchestration 的
self-handoff/终态语义，以及没有覆盖 restore 事务的 workload 偶然性。当前主 gate 已改为原生
Deep Agents autonomous root 与 runtime 动态 FRESH subagent；`system_jct_eligible`、
`native_agent_jct_eligible` 和 SWE-bench task correctness 分开报告。可选 peer self-continuation 不再
伪造 HANDOFF。scheduler shutdown 现在使用 PID start-time 校验、事务 drain、最终 summary 和显式
ACK；abort/finish checkpoint 通过 tombstone 保留物理起点。ownership snapshot 仍只在需要物理状态的
safe-point epoch 内惰性构建一次，不按 scheduler tick 无条件构建。确定性 restore micro-gate 已在
代码中完成：专用 victim 必须先获得真实 GPU service，测试 retraction 与 replacement admission 位于
同一原子 ActionGroup，后续完全复用生产 D2H/obligation/H2D/admission/service-grace 状态机。修复后的
test-only pair barrier 已在 `2 running / 1 waiting` 下通过 GPU gate：一次事务完成 4.40 GiB D2H 与
等量 H2D，obligation 在 shutdown 前 SATISFIED，restore 后真实 decode service 可观测，physical
snapshot/read-set 与 command/ACK 守恒均通过。随后修复将 restore authority、overdue restore debt 和
test-only private-KV readiness 检查前移到 barrier request 之前。2026-08-01 单次 autonomous w4
`experiments/raw/p5g_autonomous_w4/20260801T115617Z` 完成 4/4 `system_jct_eligible`，只发生 1 次
barrier request/drain，且该次创建有效 retraction；68 次 active restore debt 候选在 drain 前被抑制。
一次 6,396,641,280-byte restore obligation 在 4.81 秒后因真实 GPU service 进入 SATISFIED；shutdown
前 command/ACK、lease、funding、pin、transaction、allocator 与 Host page index 全部守恒。因此 P5
system correctness gate 已关闭，接口可以冻结，P6 预测性物理动作仍保持关闭直到短时 w8 smoke。
本轮 0/4 `native_agent_jct_eligible` 与 2/4 task measurement valid 单独归因于工具错误和 runtime guard，
不用于性能/JCT 结论。完整报告见
`docs/experiments/beliefkv_p5g_autonomous_w4_system_gate_2026-08-01_zh.md`。

M1--M6 本轮执行结果如下：

1. **M1 自然形态审计，完成**：57 个候选 epoch 对应 51 个 physical generation，但稳定 parked
   episode 只有 13 个；5 个高 extent-count WAIT_JOIN/WAIT_TOOL episode 分布在 3/5 个 context。
   physical generation 不是独立 workload 样本，该结果只证明问题存在，不估计 prevalence。
2. **M2 extent-count-aware 首版，完成 development artifact**：使用 GPU0 7/106-extents 结果 warm-start，
   以 `bytes + extent_count` 邻域给出 P50/P90；删除会跨 extent bucket 抹平形态的
   bytes-only fallback，样本不足返回 unsupported。extent-size 与 closure depth 尚未进入模型。
3. **M3 精确 shape plumbing，完成**：从候选真实 Radix closure 构造 shape，PolicyInput 携带
   immutable service snapshot，risk 使用被选 closure 的 bytes/count。
4. **M4 JointPlan 接入，完成机制与测试**：在 PREPARE recourse 中使用
   `causal slack - morphology debt`，继续复用 P5 certificate、transaction、ACK 和 fallback。
   不修改 Frontier predictor、RCCG、fairness、restore protocol，也不增加动作类型。
5. **M5 初始固定 trace 对照，只通过 timing gate**：124 个配对 snapshot、229 个候选
   epoch 对应 58 个 context-physical-shape key（不是独立 workload 样本）；87 个 epoch/24 个 key
   有 extent-count support。timing
   estimate 与 feasibility reason 分别变化 87/147 次，但 promotion/veto/selected-action 均变化 0 次。
6. **M6 单动作 canary，基础设施完成**：已实现 run-level 单 PREPARE 上限、
   safe-point shape/cost envelope，以及 commit/queue/telemetry/ACK/terminal 五段 ID 守恒归因。
   初始 trace 未开放 GPU；后续 Xarray characterization 曾出现 3 promotion/17 veto，但 veto
   treatment 证明其 service sample-gate 不一致，修复后的同源 replay 为 0 action flip。一次
   morphology-aware autonomous w8 虽然 8/8 正常结束，也未自然产生 PREPARE。因此 M6 在线
   收益和 decision-relevance gate 均未通过。

下一阶段不再为 morphology action flip 扩大 GPU matrix。extent-count-aware curve 作为统一
transfer service/OOD safety 模型保留；P6 核心回到 FrontierBelief 对 agent causal slack、reentry
demand、future pressure 和 action boundary 的预测，并检验它与 admission、KV 和 selective
retraction 联合后能否提高 workflow throughput。任何新在线 arm 必须先验证 runtime 与 artifact 的
service-contract 一致性。当前实施顺序以 2026-08-11 P6 执行计划为准：

1. 删除 morphology 独立策略，并补齐 generation-aware attribution 与 observer fail-open；
2. 实现 `parallel_analysis_2to3` fan-out workload，第一轮保持模型冻结并在 SPAWN 后预测；
3. 直接完成一笔自然 PREPARE_HOST canary，同时记录 useful/wasted/too-late/censored；
4. 用 EXPAND/CLOSE/HOLD 注解现有 running retraction，先 shadow 后完成一笔 canary；
5. 在相同 fan-out profile 上比较 P5 observed 与完整 predictive JointPlan 的 workflow throughput。

独立 causal useful-action oracle 不再是前置 gate；动作结果归因随 canary 和 A/B 一起完成。

2026-08-11 的 R5 v7 第一笔 observed A arm 完成 8/8 workflow，但第二笔 predictive B arm 在
高压 selective retraction 后触发 allocator/Radix duplicate device-index fail-closed，0/8 完成。
B arm 在退出前没有选择 predictive physical action，因此该轮只暴露 P5 物理释放边界缺陷，不能
用于比较 A/B。修复后 retraction suffix 必须排除 live Radix、already-free、invalid 和 duplicate
page；确定性 high-fragment micro-gate 完成 2.659 GB D2H/H2D，14/14 correctness checks 通过。
提交前完成可移植性修订后，新的 v9 A/B 已按相同 workload/artifact 和实验参数重新冻结，source
tree SHA-256 为 `b95b5e4e7b91dce4698af5a964258698f25a19251ba8df411c548b9027a48a41`；v7/v8
不再参与性能汇总。
详见 `docs/experiments/beliefkv_p6_r5_retraction_ownership_repair_2026-08-11_zh.md`。

当前最重要的研究问题已经不是继续调整 FrontierBelief，而是：

> 在冻结真实 agent demand 并完全知道未来的条件下，当前 action space 中的 agent execution、
> admission 与 KV residency 联合控制，能否在真实 H200 数据面上显著提高 workflows/hour，
> 且 O3 是否优于 O1/O2 中更好的单侧 oracle。

只有该上界成立，后续才继续使用 FrontierBelief 逼近 oracle；否则应收缩或调整 BeliefKV 的核心动作空间。

长上下文的语义总结、tool-output 压缩和 checkpoint 由 agent 业务层负责。固定 SGLang 仅提供
sliding-window、长度限制和物理 KV eviction，不提供 agent-aware 自动压缩；BeliefKV serving 层
只负责 context-growth admission 与到 yield/terminal 边界的调度。

## 16. 文档权威顺序

当前 Oracle v2 实施的权威文档为
[Perfect-Future Action-Space Oracle v2 执行方案](beliefkv_perfect_future_oracle_v2_execution_plan_2026-08-17_zh.md)。
除该紧急主线外，建议按以下顺序理解系统设计与历史状态：

1. [`beliefkv_design_2026-07-14_zh.md`](beliefkv_design_2026-07-14_zh.md)：
   当前规范设计、算法主线和实施顺序；
2. 本文：当前代码与阶段状态总览；
3. [`experiments/beliefkv_p6_d2h_overlap_characterization_2026-08-09_zh.md`](experiments/beliefkv_p6_d2h_overlap_characterization_2026-08-09_zh.md)：
   形态测量协议、GPU0 development evidence 与证据限制；
4. [`experiments/beliefkv_p6_service_contract_and_native_ownership_2026-08-11_zh.md`](experiments/beliefkv_p6_service_contract_and_native_ownership_2026-08-11_zh.md)：
   sample-gate 根因、修复契约、native ownership 标签边界和下一阶段 gate；
5. [`beliefkv_hicache_joint_control_improvement_plan_2026-07-18_zh.md`](beliefkv_hicache_joint_control_improvement_plan_2026-07-18_zh.md)：
   P0-P8 历史实施方案和 Go/No-Go 条件；
6. [`experiments/beliefkv_p2_physical_bundle_2026-07-19_zh.md`](experiments/beliefkv_p2_physical_bundle_2026-07-19_zh.md)：
   P2 bundle 实现与 2026-07-21 真实复验；
7. [`experiments/beliefkv_p3_dynamic_gpu_validation_2026-07-21_zh.md`](experiments/beliefkv_p3_dynamic_gpu_validation_2026-07-21_zh.md)：
   P3 12-workflow mixed characterization、迁移反例与 telemetry 边界；
8. [`beliefkv_dynamic_agent_workflow_considerations_2026-07-20_zh.md`](beliefkv_dynamic_agent_workflow_considerations_2026-07-20_zh.md)：
   动态 workflow、consumer 和 prefix 关系边界；
9. [`related_work_comparison_2026-07-21_zh.md`](related_work_comparison_2026-07-21_zh.md)：
   B0、P8 候选 baseline 和竞争边界；
10. [`architecture.md`](architecture.md)：基础控制面/数据面原则。

`figures/beliefkv_architecture_status.*` 和 `figures/beliefkv_phase_status.*` 仍是
2026-07-15 的历史图，未覆盖 physical bundle、retry guard、P2.5 contract 和 P3。重新生成新版
图源之前，不应再将这些图片作为当前状态依据。
