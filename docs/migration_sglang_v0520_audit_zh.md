# BeliefKV -> SGLang v0.5.20 hook 迁移审计

日期：2026-09-22。范围：`third_party/sglang` 的上游基线
`v0.5.2rc1` (`18f91eb639084825717c0e3c3c7273492812ab71`)、
`patches/sglang-0.5.2rc1-beliefkv-dynamic-running.patch` 与独立浅克隆
`third_party/sglang-v0.5.20` (`94602c9c2b7cbdb8efd5c52802dac6a1c180089e`)。
这是源码审计和迁移设计，不是已适配/已跑通声明。旧工作树不作为干净基线：
`third_party/sglang` 有未提交改动；独立的新 checkout 已加入早期迁移 hook，
以 `patches/sglang-v0.5.20-beliefkv-staging.patch` 记录，不替代旧补丁。

## 结论

1. **旧补丁不可直接应用**：在干净 v0.5.20 上执行 `git apply --check`
   失败，涉及的 14 个 diff 文件均报上下文冲突或文件不存在。旧版
   `beliefkv check-sglang` 也返回 `compatible=false`：除了预期的 HEAD、
   hook 差异，`version.py` 已改为动态导出版本，不再有顶层字面量版本赋值。
   **不能**通过放宽版本比较来宣称兼容。
2. **上游原生纯 paged full-attention + HiCache：有实现与回归测试；
   BeliefKV：尚不可运行**。默认 tree factory 使用 `UnifiedRadixCache`
   的 FULL component；其 `init_hicache()` 连接 `HybridCacheController`。
   页大小 >1 的 HiCache 测试存在，但当前 BeliefKV
   `_claim_live_radix_indices` 明确拒绝 `page_size != 1`；旧 backend
   依赖 `TreeNode.value/host_value` 和原来的 allocator token 索引语义。
   因此即使迁移元数据 hook，也不能宣称 BeliefKV 支持纯 paged KV。
3. **上游原生 hybrid Qwen3.5 + HiCache：有实现与专门测试；
   BeliefKV：不支持**。Qwen3.5 的 full KV 与 GatedDeltaNet/Mamba 状态
   在 unified tree 的 FULL/MAMBA components 及 sidecar pools 中协调；
   旧的按单个 Radix node、单一 KV token 数和 `HiRadixCache.load_back()`
   计算的 reclaim/restore/ACK 不覆盖 Mamba 状态和 slot 容量。上游测试
   配置包含 TP4、HiCache、storage、NEXTN、Mamba 参数，但本审计未在 GPU
   上复跑。不能把“上游支持”转述为“BeliefKV 已支持”。

源码定位（以下路径均相对 `third_party/sglang-v0.5.20/`）：
`python/sglang/srt/mem_cache/registry.py` (`default_radix_cache_factory`,
`_create_unified_radix_cache`)、
`python/sglang/srt/mem_cache/unified_radix_cache.py` (`UnifiedRadixCache`)、
`python/sglang/srt/mem_cache/kv_cache_builder.py` (`uses_ssm_state`)、
`python/sglang/srt/models/qwen3_5.py`、
`test/registered/hicache/test_qwen35_hicache.py`、
`test/registered/hicache/test_hicache_variants.py`。
注意其他 factory 分支（pure SWA、ChunkCache、外部 cache backend、实验性
C++ tree）不能套用上述 FULL+HiCache 结论，应在新 flag 入口显式拒绝。

## 最小可运行补丁集（目标拆分，尚未完成）

“最小”指只支持**单机、默认 unified FULL、page_size=1、无 speculative /
disaggregation / streaming session / PP / priority preemption** 的
BeliefKV 闭环；不是只把 HTTP 请求送到调度器，也不等于生产可用。按依赖顺序：

| 组 | 必须做的最小改动 | v0.5.20 落点 |
| --- | --- | --- |
| 1. 入口和契约 | 增加 `beliefkv_metadata`、单/批量及 parallel sample 展开与校验；OpenAI chat -> `GenerateReqInput` -> tokenizer -> tokenized IPC -> `Req`；session 路径保持同一身份；增加 opt-in flags 和明确的 cache/feature gate；重写源码契约检查（tag/commit + 新 AST 和行为 smoke）。 | `entrypoints/openai/protocol.py`, `serving_chat.py`; `managers/io_struct.py`, `tokenizer_manager.py`, `scheduler.py`, `schedule_batch.py`; `session/session_controller.py`; `arg_groups/fields/` 和 `server_args.py`; 本仓库 `beliefkv/runtime/sglang_adapter.py` |
| 2. 生命周期和 safe point | 请求先入原生 waiting queue，tagged request 注册/abort/requeue；在每轮决策前按 ACK -> tree sync -> plan 顺序调用 runtime；在 scheduler 生命周期内关闭 runtime，idle 时保证外部事件能唤醒/被轮询。保持无 metadata/disabled 路径原生语义。 | `managers/scheduler.py` 的 `ingest_requests`, `handle_generate_request`, `_add_request_to_queue`, `abort_request`, `event_loop_normal/overlap`, `get_next_batch_to_run`, `run_scheduler_process` 及 idle sleeper |
| 3. admission | 原生 `policy.calc_priority` 后编译 ticket；**跳过无 ticket 的 tagged request，但继续考虑后续候选**；`init_next_round_input` 前后重验版本/匹配、失败撤销；`PrefillAdder.add_one_req` 成功后记账；retained chunked req 每轮单独处理。 | `managers/scheduler.py::_get_new_batch_prefill_raw`; `managers/schedule_policy.py::PrefillAdder` |
| 4. tree/ownership observer | 在 unified tree 的 insert/split/delete、GPU/CPU residency、lock receipt、cache finished/unfinished、reset 处记录增量与 request 生命周期；必须覆盖 native eviction/load、session、组件侧修改；必要时 full resync，但不能把仅供 router 的 KV events 当成完整 lock/owner 流。 | `mem_cache/unified_radix_cache.py`, `mem_cache/unified_cache/` 下的 tree core/components；本仓库 mirror/backend |
| 5. 原子物理动作和 ACK | 在 unified FULL 的有界 node closure 上实现可校验的 D2H、H2D、drop/cancel，复用 controller 的 pool transfer/ack，记录每 pool 的 bytes、node generation、来源；host/device 分配失败回滚；只在 native ACK 后提交 page 状态。不要绕开原生分配器/lock receipt。 | `mem_cache/unified_radix_cache.py`, `mem_cache/hybrid_cache/hybrid_cache_controller.py`, `mem_cache/hybrid_cache/hybrid_pool_assembler.py`; 本仓库 `beliefkv/runtime/sglang_v052rc1.py` backend |
| 6. selective running retraction | 仅在 overlap pipeline 排空并对结果达成各 rank 一致后，提供可选的“选定 req 集合”释放/重排；释放必须走新版 `release_req`/`release_kv_cache` 和 `Req.reset_for_retract`，保护 shared/full pages 和 request slot；若不能保证屏障，**关闭此能力且禁止声称 dynamic-running 等价**。 | `managers/scheduler.py::get_next_batch_to_run`/`event_loop_overlap`/`update_running_batch`; `managers/schedule_batch.py::ScheduleBatch` |

当前 staging 补丁完成组 1 的 request metadata 传递、组 2 的部分
scheduler hook 和 tagged request 生命周期回调，以及 unified cache
在 cache-mode 原生 D2H/H2D ACK 提交后的可选只读通知。buffer-only
路径没有可用的相同 post-commit 边界；该通知对合并 ACK 只能报告整体
pool 数量，不能把字节拆分归因给单个 node。safe point 现先执行原生
chunk abort 和 HiCache ACK 排空，再执行 BeliefKV mirror/plan，避免在同一轮
根据 ACK 之前的状态发布动作。启用 BeliefKV 时仍明确抛错。
组 3 现有一个尚未激活的同步排序/许可切片：在新版 `policy.calc_priority()`
后、`Req.init_next_round_input()` 及 `PrefillAdder.add_one_req()` 前按
request/workflow/invocation/context/epoch/attempt 身份选择 tagged 候选；
过期或无授权者被跳过，未标记请求保持原生位置，且跳过不会结束后续
候选遍历。FULL/MAMBA 共享容量、Mamba slot 和 Host load-back 的最终
资源验收仍由原生 `PrefillAdder` 执行。该接口不复用 rc1 的单值
`kv_bytes_per_token` admission ticket；也没有完成可运行的 runtime
plan producer、physical ACK 对账或预测动作，不能视作 group 3 已验收。
74 项迁移定向测试（含本仓库 observer/selection 测试）通过，
**不能**据此声称 BeliefKV physical KV
操作、admission、动作 ACK 对账或预测调度已迁移。
本仓库的 `beliefkv/runtime/sglang_v0520_observer.py` 另提供 FULL-token、
MAMBA-slot 和 Host pool 的**只读静态容量上限和占用计数**；两个 device 子池
共享同一字节 buffer，所报上限不能相加。它不估计可立即调度的空闲字节
或可回收量。新增针对 Python `UnifiedTreeCore` 的指定 node 及祖先链
有界只读快照，记录 FULL/MAMBA 驻留、锁与 pending 状态，不复制物理
index；Rust tree 的 `node_by_id` 尚未实现，该路径明确 fail closed。
这些值不提供原子 revision、可迁移性判定或动作授权；mock 测试通过，
尚未在真实 GPU cache 上验收。
后续固定 checkout 审计确认：`UnifiedTreeCore` 的 page-aligned radix
共享与 node split 已原生实现；可选 session refs 为驱逐软优先级，
不是 tool-call-aware 保活；storage prefetch 是 storage -> Host，
admission load-back 是请求到达后的 Host -> GPU，均不是提前预测
恢复。`beliefkv_metadata` 只是传输契约，开启 BeliefKV 仍 fail closed。
有界准入编译器现将 native session ID/generation 一并纳入候选授权
身份；只读 FULL/MAMBA node 快照记录 session 引用计数/叶标记数，
**不得**由此推断独占 ownership 或 physical action 已实现。
后续 staging 增加了 opt-in 原生 session 生命周期桥（同 context/epoch
复用、终态关闭、失败可重试），但默认 agent runner 尚未启用。
cache/controller 现为可选 tagged D2H/H2D 子传输保留原始 command ID、
FULL/MAMBA pool 计数与 bytes，merged ACK 仍包含 untagged 子操作
以检查总账。cache 只有在 ACK 同步及 tree finish 后才输出匹配的
`child_commits`；H2D 入队不构成提交或完成。完整动作的跨子操作
原子性、context/epoch 和物理 ownership 仍未迁移，启用 flag 必须
继续 fail closed。
另外固定新环境默认运行 wheel (`source_is_active=false`)；
`scripts/launch_qwen35_native_v0520.sh` 仅在显式设置
`SGLANG_SOURCE_CHECKOUT` 后才校验固定源码和 staging patch 并使用
该 checkout。必须记录实际加载的 `sglang.__file__`，不能仅凭
包版本 0.5.20 声称运行了 BeliefKV 补丁。
组 1-5 + 改写本仓库 runtime/contract 才能定义为“基础 BeliefKV 可运行补丁集”；
要复现题设 `dynamic-running.patch` 的行为还需组 6。**没有现成可运行的
v0.5.20 可运行补丁文件**；只搬运上游 diff 或只加入口字段不构成完整补丁集。

## 需要替代的 hook / 主要风险

| rc1 hook 或旧假设 | v0.5.20 替代位置 / 处理 |
| --- | --- |
| `managers/session_controller.py::Session` | 文件迁到 `session/session_controller.py`；检查 session 恢复、`session_id` 和 `session_params` 两条分支的 metadata 继承，不能只改普通 `Req(...)`。 |
| `Scheduler.get_next_batch_to_run()` 无参、返回 batch；旧 safe point 在函数入口 | 新签名 `(running_batch, last_batch) -> NextBatchPlan`；`_process_hicache_events()` 已在函数开头并含 TP consensus。ACK/drain/plan 必须在正确的 rank 同步边界，不能重复或越过 collective；normal/overlap/idle 分支都要验证。 |
| 旧 `get_new_batch_prefill` 直接遍历队列并在 `add_one_req` 后补 gate | 新实际候选循环在 `_get_new_batch_prefill_raw`；`PrefillAdder.add_one_req` 内含 `_select_prefill_admission`、host load-back 和 `_commit_prefill_admission`。前置 gate 必须防止被拒绝请求触发 native H2D/Mamba slot 变更；后置验证需放在原生不可逆提交之前或设计 rollback。不能继续按旧的 `req.extend_input_len`/`fill_ids` 推断 retained chunk：新代码使用 `full_untruncated_fill_ids`/`extend_range`。 |
| 旧 `ScheduleBatch.retract_selected` 和 `running_batch.is_hybrid` | 上游只有 `retract_decode()`（按 native order）、`release_req()`，且可能中止请求；`Scheduler` 分别使用 `is_hybrid_swa/is_hybrid_ssm`。需重新设计 selective release、overlap barrier、PP/TP consensus；不能简单调用 native retract_decode 替换。 |
| `RadixCache/HiRadixCache` 的 `_beliefkv_notify`、`TreeNode.value/host_value`、`_evict_backuped` 等 | 默认 FULL 和 Qwen3.5 已走 `UnifiedRadixCache` 与 tree core，tree node ID、component state、lock receipt 及 eviction action 都变了；原 patch 到旧两个类即使强行移植也不覆盖默认路径。`take_events()`/KV router events 不含完整锁、物理 owner、提交/撤销时序。 |
| 旧 `HiCacheController.write_batch`、`HiRadixCache.write_backup_batch/load_back(force,allow_eviction,beliefkv_source)` | unified 的 `load_back(node_id, mem_quota, req) -> bool`，写入及 ACK 用 component/pool-transfer 模型；普通 `HiCacheController.write` 会立即启动写入；不能把多个 node 串行写入冒充“原子批次”。在提交前核对每 pool 容量、host ancestor closure，并处理 ack.node_ids 和失败回滚。 |
| 旧 `ServerArgs` 直接新增字段及手工 argparse | v0.5.20 字段按 `arg_groups/fields/` 汇聚，`ServerArgs` 是拼装/解析层；flag、解析、配置 bags、子进程传播与 validation 需统一。 |
| 旧 Qwen3-Coder tool-call 容错修复 | v0.5.20 的 `qwen3_coder_detector.py::has_tool_call` 仍仅检查 `<tool_call>`，one-shot 在缺失该标记时直接返回普通文本。此修复与 BeliefKV hook 正交：非 BeliefKV 场景不列入“最小补丁”，若实验依赖无开标签的 Qwen3-Coder 工具调用，应单独移植并加 streaming/one-shot 回归。 |

本仓库还需要迁移 `beliefkv/runtime/sglang_v052rc1.py` 的 backend：
它要求 `write_backup_batch`、旧版 `load_back` kwargs、`_evict_backuped`、
`tree_cache.root_node`/`node.value` 和 token-granular reconciliation。
必须引入按新版 component/allocator 的适配，保留 generation、ACK、
no-active-page-transfer、shutdown/abort 约束；不是仅修改上游 hook。
现有 `scripts/check_sglang_contract.py` 通过
`beliefkv/runtime/sglang_adapter.py` 检查 exact rc1 commit 和旧 AST，
故新版本应有独立 contract/profile，不覆盖旧实验指纹。

## 验收顺序与边界

1. 静态：固定 v0.5.20 commit、清洁 checkout、版本检查改读构建后的
   `sglang.__version__`/release tag；针对两种 cache backend 校验具体类型
   和 API。新补丁 `git apply --check`、编译、契约测试和 disabled 路径回归。
2. FULL page_size=1：单/批量与 session metadata、tagged/untagged 混跑；
   原生 prefill order、chunked continuation、abort/reset、host OOM、
   D2H/H2D ACK 与 node ownership 双向对账；normal/overlap + TP 多 rank
   一致性；再验证 selective retraction 的 drained barrier。
3. FULL page_size>1：优先移除 BeliefKV token-granular reconciliation 限制，
   用 page-aligned 索引/容量/ownership 重新验证 split、reuse、eviction、
   last-page replay、预算和 host load-back；未通过前 flag 必须 fail closed。
4. Qwen3.5 hybrid：单独验证 FULL/MAMBA pool bytes、Mamba checkpoint/slot
   恢复、prefill retained chunk、decode retraction、TP collective、可选
   NEXTN sidecar 和 storage ACK；默认关闭 BeliefKV，不能复用纯 FULL
   成功结果作为准入证明。

初次审计已执行：旧补丁 `git apply --check`（失败，预期）、`python3
scripts/check_sglang_contract.py third_party/sglang-v0.5.20`
（`compatible=false`，预期）。随后已导出 staging 补丁，并确认
`git apply --cached --check` 可应用于固定的 v0.5.20 index，
`git apply --reverse --check` 可从本地 checkout 撤销；
2026-09-22 追加原生 GPU smoke：H200 + 已安装的 v0.5.20 wheel +
Qwen3.5-35B-A3B BF16，在不带 HiCache 和启用 4 GiB HiCache 时，
chat、解析后的工具调用和工具结果续写均通过。启用 HiCache 的启动日志
显示 FULL/MAMBA Host pool 已挂载；小 Host pool 不代表迁移验收，
本次请求未证明真实 D2H/H2D 或 BeliefKV physical action。日志还提示
`sglang-kernel` 缺少 `kvcacheio.get_device_accessible_ptr` 而使用原始
Host 地址作为 kernel pointer；需在真实传输 gate 单独验证。
同日使用带 staging patch 的独立 checkout，开启 4 GiB HiCache，
并发的原生 chat/tool 请求与携带 metadata 的请求都通过 disabled-path
smoke。默认 FlashInfer sampling 首次 JIT 编译失败：环境中的 `nvcc`
为 CUDA 13.4，而 `cuda_runtime_api.h` 标记 CUDA 13.0，CCCL 报 compiler 与
toolkit headers 不兼容。改用原生 `--sampling-backend pytorch` 后通过，
但不能以此后端做正式吞吐对比；实验前必须对齐工具链并复验默认采样。
原 rc1 的正式 patch/profile 是
`perf-ownership` 而非本次作为对照的 `dynamic-running`；不可混用旧实验结论。
