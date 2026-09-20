# BeliefKV 与 SGLang 0.5.2rc1 集成说明

更新日期：2026-09-20。本文描述当前接口契约；具体实验参数以冻结 runtime profile 为准。

## 1. 固定版本

BeliefKV 当前只支持：

```text
SGLang tag:    v0.5.2rc1
SGLang commit: 18f91eb639084825717c0e3c3c7273492812ab71
```

`beliefkv check-sglang SOURCE_ROOT` 会检查 `version.py`、git HEAD（源码目录有
git 信息时）、上游 AST 接口和 BeliefKV patch marker。检查失败时应停止实验，
不能在相近版本上强行运行。

## 2. Patch 的职责

当前正式源契约使用
`patches/sglang-0.5.2rc1-beliefkv-perf-ownership.patch`。它只增加窄接口：

- HTTP/generation input 到 `Req` 的 `beliefkv_metadata` 传播；
- request 始终进入 SGLang waiting queue，BeliefKV 在每个 prefill epoch 编译短期 admission
  ticket，并在 `PrefillAdder` 前后做局部校验；
- abort 时清理 BeliefKV visible side state、当前 epoch ticket 和未完成事务；
- scheduler safe point 驱动 ACK、控制器和迁移 backend；
- SGLang 原生 queue policy 后为 tagged request 提供 causal/ticket candidate view；无 ticket 的
  request 本轮跳过但仍留在原生 waiting queue；
- Radix split/insert/delete/lock 与 HiCache residency 变化触发 observer；
- server flags `--enable-beliefkv` 和 `--beliefkv-config`。

Patch 不把 BeliefKV 策略复制到 SGLang；策略仍位于本仓库。SGLang 继续拥有
allocator、Radix topology、KV tensor 和 DMA queue。

## 3. 请求 metadata

请求 JSON 可带：

```json
{
  "beliefkv_metadata": {
    "root_workflow_id": "wf-42",
    "invocation_id": "coder-call-3",
    "context_id": "coder-session",
    "context_epoch": 7,
    "agent_definition_id": "coder",
    "agent_instance_id": "coder-1",
    "parent_invocation_id": "planner-call-2",
    "parent_context_id": "planner-session",
    "relation_type": "call",
    "context_mode": "resume",
    "execution_mode": "foreground",
    "return_target_id": "planner-call-2",
    "join_id": null
  }
}
```

没有该字段的请求绕过 BeliefKV ticket gate。`relation_type` 只能是
`root/call/spawn/message/handoff`，context 和 execution mode 也会严格校验。

仅靠 request metadata 能恢复 invocation/context 关系，但工具开始/结束、join
和独立消息总线事件仍应由 agent runtime 通过 `RuntimeEvent` hook 上报。通用的
线程安全批处理接口位于 `beliefkv/runtime/agent_runtime_adapter.py`；当前没有
跨进程网络 collector，接具体 agent framework 时需要实现一个薄 adapter。

## 4. 运行时审计

配置中的 `runtime_audit_path` 默认为 `null`，此时不打开文件，也不增加调度器
I/O。设置为 JSONL 路径后，每条记录包含 `run_id + sequence + monotonic ts`，可
验证以下链路：

```text
invocation_created
  -> request_visible_pending
  -> admission_ticket_epoch_started
  -> ticket selected or skipped
  -> request_started
  -> request_finished
```

CALL/SPAWN/MESSAGE/HANDOFF 还会记录 `causal_relation_linked`，迁移会记录
`transfer_dispatched/transfer_acknowledged`。日志只包含 identity、token/byte
计数和状态，不保存 prompt、observation 或生成文本。重复的 admission 拒绝按
状态去重，避免调度循环造成日志风暴。该日志用于正确性与实验审计，不应在正式
性能测量中开启，除非各 baseline 采用等价的观测开销。

## 5. Scheduler safe point 与三层调度边界

`EmbeddedSGLangRuntime.scheduler_step()` 由 SGLang scheduler 主线程同步调用，
入口位于 `get_next_batch_to_run()` 开头；它不是独立周期线程。normal/overlap
event loop 每轮都会到达该入口，空闲时 runtime event socket fd 会加入 idle poller。
入口内部的高开销动作按 5ms policy check、ACK poll、resource telemetry interval、
plan reuse interval 和 watchdog interval 节流。

safe point 的目标不是“异步规划”，而是提供 scheduler-owned consistency boundary：
在同一主线程边界上排定 runtime event、native ACK、Radix mirror、allocator 观测、
admission/retraction/restore 事务和物理 command 的提交顺序。CUDA/native callback
不得直接修改 live scheduler 状态，只能投递 ACK/telemetry，由下一 safe point drain。

固定顺序是：

```text
begin APPLY_EVENTS
  -> drain runtime event datagram（同步提交 RCCG/ledger）
  -> drain HiCache ACK / telemetry
  -> 同步 dirty Radix tree
  -> 上报 allocator/HBM
  -> advance retraction / residency / restore / host cleanup
  -> enforce queue and execution timeouts
  -> begin CAPTURE_AND_PLAN
  -> compact policy snapshot / latest JointPlan result
  -> publish action-local semantic delta to worker
  -> enter TRANSACTIONAL_COMMIT
  -> controller tick / physical command preflight and dispatch
  -> finish safe point
  -> SGLang 原生 queue policy
  -> begin_prefill_epoch 编译 causal/active-set ticket
  -> ticket gate + prefix rematch + PrefillAdder
  -> end_prefill_epoch 提交实际 selection accounting
```

ACK 必须先于 tree sync。否则同步完成的 `COMMIT_CPU` 或 `DROP` 会让控制面先看到
物理新状态，随后又根据 ACK 重复执行状态转换。

### 5.1 Dynamic working set

输入是 tagged native waiting requests、每个 workflow 的 Action frontier 候选、
effective native HBM capacity（allocator available + native evictable）和 native
running/slot 上限。输出只有 workflow 级决策：

- active workflow IDs；
- throughput/balanced/recovery soft target；
- 本 epoch admission slots；
- effective/gross KV pressure；
- pressure actions 是否开启。

它按 root workflow 聚合 `unlock value * service quantum / HBM envelope`，并保留
starvation/mandatory restore 优先。它不生成 ticket，不改变 request queue，不提交
KV command，也不越过 SGLang native capacity。

### 5.2 AdmissionTicket compiler

输入是本 epoch 的 policy order、visible admission entries、native
`PrefillAdder` 余量、bounded HBM budget、slot/candidate 上限和 restore/admission
reservation credits。输出是一个 immutable ticket epoch：

- tickets：本轮允许尝试 native admission 的 request 与短期 commitment；
- skipped：slot/HBM/token/state 拒绝原因；
- reclaim requirements：bounded HBM 不足时的 beneficiary startup/growth demand。

compiler 是纯计算模块，不 mutate queue/allocator。ticket 在 request prefix rematch
后仍需验证 request/context/invocation/version；SGLang `PrefillAdder` 和 allocator
是最终 authority。大 request 遵守 SGLang 单 retained chunked request 契约：完整
prefill 优先，至多一个 chunked tail。

### 5.3 JointPlan worker

`LatestWinsJointPlanWorker` 是 capacity-one 异步 mirror/规划 worker。safe point
只提交 compact delta：RCCG events、frontier feature/prediction delta、有效 HBM
观测、runnable seed、fairness/control revision、transfer ACK cursor 和 action-local
physical overlay。worker 不访问 live scheduler 对象。

pending delta 会被更新 delta 替换；正在执行的计算不抢占。mirror apply 失败时
fail closed 并要求 full resync。worker 可将 observed JointPlan result 送入独立
predictive risk worker；safe point 只消费 latest result，并在提交前重新验证当前
RCCG、read-set、物理 ownership、容量和 deadline。因此 JointPlan worker 负责“探索
和形成候选计划”，不拥有最终 admission 或 mutation 权。

### 5.4 Action frontier

Action frontier 首先使用 observed RCCG 事实分类：最后一个 JOIN member、唯一剩余
blocking child、message-ready、foreground ready 和 background。当前实现要求
blocking chain 的父节点确实只剩当前 child，否则不给 blocking credit；同类别内
额外统计 active descendant 数和 JOIN waiter 数，并把有界 fanout credit 混入
max-weight utility。预测头只补充 remaining decode/output/prompt growth 和动作
timing；RCCG 确定性 unlock 事实仍优先。

## 6. HiCache 限制

- 只迁移 sealed Radix node extent；
- D2H 遵守 leaf/prefix closure；
- H2D 必须从浅到深选择完整 CPU ancestor closure，禁止由 `load_back()` 隐式
  加载未计费祖先；
- CUDA DMA 一旦提交不假设可抢占，cancel 只停止后续 shadow chunk；
- native HiCache write/load 期间分别镜像为 `MIRRORING/PREFETCHING`；
- cache reset 先生成 `CANCELLED` ACK，再失效 allocation generation。

## 7. Predictor 与 JointPlan

`beliefkv normalize-clawtrace` 和 `beliefkv train-predictor` 仍可用于便携式 predictor
smoke，但正式 P6 路径使用 canonical P6 dataset、FrontierBelief artifact、独立 GPU
service artifact 和 transfer artifact。语义模型只预测 action-local demand、等待存活率、
prompt/output growth 和因果释放；它不得学习 batch size 或旧调度策略下的 GPU wall-clock。

在线流程为：

```text
bounded observed seed
  -> deferred beneficiary + parked victim scope
  -> asynchronous FrontierBelief scenarios
  -> PredictiveIntent
  -> safe-point live rematerialization and validation
  -> JointPlan action or P5 fallback
```

当前 predictive authority 已覆盖非破坏性 `PREPARE_HOST`、真实 deficit 授权的
prepared-victim 消费，以及 latest-start `PREFETCH_GPU` 的 H2D/service lease 归因。
自然 workload 的端到端吞吐收益仍未证明；任何 stale/OOD/物理不可行预测都回退
observed P5。

## 8. 真机验收清单

- 未带 metadata 的请求与 upstream 返回一致；
- patched-disabled 相对 upstream 的吞吐/延迟开销可测且足够小；
- active/shared/locked page 不会迁移；
- abort deferred、abort admitted、cache reset 和 host allocation failure 不泄漏；
- HBM pressure 下 admission 只在实际释放 ACK 后继续；
- D2H/H2D 字节与 HiCache allocator 计数一致；
- safe-point capture、predictive submit 和 physical commit 不超过当前计划中的预算；
- predictor worker 不得阻塞 scheduler，也不得积压或发布 stale intent；
- shadow slowdown 不超过配置预算；
- 长时间混合 workload 不出现 stale handle、location divergence 或死锁。

当前 H200 BF16 系统已经完成 RCCG、visible admission、transactional restore、
generation-aware ownership、CUDA Graph batch 32 和单笔 predictive
`PREPARE_HOST` 的定向门禁。最新 running retraction/ownership 修复仍需一次高压
GPU 回归；自然 predictive throughput 收益尚未建立。当前结论见
[`architecture_status_zh.md`](architecture_status_zh.md)，不要从旧 smoke 报告推断
现有能力。
