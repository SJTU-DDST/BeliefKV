# v0.5.20 调度适配边界

状态：2026-09-22，native admission 可显式启用；预测 demand 仅在
目标模型/运行时匹配的合格 artifact 下参与 admission，物理动作仍关闭。
这不是完整 P6 预测调度。
目标配置为 Qwen3.5-35B-A3B BF16、单机、统一 FULL/MAMBA tree、HiCache
cache mode。其他 cache backend、TP/PP、disaggregation 和 speculative
仍需独立验收。

## 固定 v0.5.20 的原生能力

以固定 checkout `94602c9c` 的源码和测试为准，不把后续版本或 RFC
当成已有功能：

- `UnifiedTreeCore` 原生按 token/page 匹配、分裂 radix node 和共享前缀；
  BeliefKV 不重建第二棵共享前缀树，也不能把共享祖先算作一个 agent
  的独占 reclaim 字节。
- `--enable-session-radix-cache` 可给 FULL/MAMBA 维护 session 引用；
  原生驱逐会优先保留这些节点，但这是软优先级而不是 pin。
  必须明确传递 session ID、处理 session generation，并在 context
  结束时关闭 session，否则既不能保证工具等待期保活，又可能造成 Host 污染。
  agent 客户端现有显式 opt-in 的 context/epoch session 桥：工具等待期
  复用同一 session，RETURN/CANCEL 关闭，WORKFLOW_END 清理遗漏 child，
  epoch 变化先关闭旧引用；默认没有接入实验 runner，不能用它宣称
  工具 KV 已在新环境保活。关闭接口同步调用原生 `/close_session`，
  失败保留可重试引用，不隐式继续使用过期 session。
- v0.5.20 **没有**根据 agent 的 `TOOL_START/RETURN` 自动保活或恢复 KV。
  已有 `_prefetch_kvcache` 只针对 storage -> Host；`init_load_back`
  在请求准入时从 Host -> GPU。二者都不等于 BeliefKV 预测的、请求
  reentry 前的 `PREFETCH_GPU`。工具时间/依赖预测和 PREPARE/PREFETCH
  动作时机仍由 BeliefKV 决策。

## 保留的决策

- RCCG 与 Action frontier 保有 agent 因果关系、JOIN 依赖和可见请求资格；
  FrontierBelief 输出 future demand、reentry 和 latest-start 的概率分布。
- P5/P6 JointPlan 保有吞吐优先的执行顺序、beneficiary/victim 选择，以及
  `PREPARE_HOST`、`COMMIT_CPU`、`PREFETCH_GPU` 的价值和 deadline 判断。
  预测决策必须绑定 request/context/epoch、目标 node closure 和生成版本。
- 准入计划只对 *tagged* 请求下发语义顺序/许可。同一安全点中，native
  `policy.calc_priority` 先形成队列；BeliefKV 在 tagged 位置重排有身份
  校验的候选，未授权或过期的 tagged 请求跳过并继续扫描下一候选，
  untagged 请求保持其原生相对位置。当前 staging 已接入
  `--enable-beliefkv-admission` 的在线 plan producer。可选
  `--beliefkv-event-socket-path` 在安全点接收 agent 因果事件，复用
  `CausalFrontierScheduler` 排列与 live context/epoch 匹配的 ready
  invocation；没有事件或没有匹配时沿用 native order。另有可选、
  经 artifact 门禁的 next-output demand 排序（见下节），不授权物理动作。
  事件应用失败
  废弃 RCCG mirror，退回 native order；普通事件不深拷贝整个 RCCG。
  waiting queue 中由事件确认已终止的 tagged 请求在安全点被精确移除，
  通知 tokenizer 并释放原生 handle。首 512 个 tagged 候选受有界
  排序，其余请求保留在队列，等待下轮原生调度机会。
  `compile_native_prefill_plan` 将语义排序在安全点绑定到
  request/context/epoch/attempt 和 native session ID/generation；
  session 在授权与应用之间变化时拒绝 tagged 候选，原生请求不受影响。

## 预测 demand 的 admission 边界

- staging scheduler 将 `--beliefkv-admission-predictor-path` 与
  `--beliefkv-admission-predictor-sha256` 交给 `NativeAdmissionRuntime`；
  预测模式还要求 event socket。启动时必须核对 artifact 文件的精确
  SHA-256、`calibration_status=calibrated`、`online_eligible=true`，
  并核对其中 semantic source contract 的模型 `config.json` 及
  声明的权重索引、tokenizer 文件哈希和
  `sglang_version=0.5.20`。旧 Qwen3-Coder/rc1 的 schema-v5 或
  development-only artifact 不满足新模型准入资格；当前迁移目录
  尚无经 Qwen3.5 标定且可在线使用的 predictor artifact。
- 启用合格 artifact 后，单独的 spawn 进程加载 FrontierBelief，
  每批最多 8 个当前可见且 ready 的 tagged 候选；最多一批在途，
  等待批只保留最新值，安全点仅非阻塞 poll。只使用有 support、
  非 OOD 的 `next_output_tokens` P50；失败禁用本地 worker。
  返回值必须在短有效期内与 live request/context/epoch/attempt、
  session ID/generation 及 invocation revision 一致；未齐备 hint
  时不根据局部预测或 request ID 排序。
- 预测值只在**同一已知因果排序类且该类所有候选均有有效 hint**时
  用于短输出优先的 tagged admission 排序。未配置合格 artifact 时，
  仍只有 observed-causal/native 顺序；即使启用预测，SGLang
  `PrefillAdder` 仍独占实际 FULL/MAMBA 资源验收，不能把 hint
  当容量证明或 `PREPARE_HOST/PREFETCH_GPU` 授权。

## 交还给上游的机制

- 不移植 rc1 的 `HiRadixCache.write_backup_batch`、`load_back(force=...)`、
  `_evict_backuped`、逐 token allocator reserve 或 `TreeNode.value` 映射。
  新版 cache 管理 FULL token 与 MAMBA slot，两者共享设备字节 buffer；
  老的单值 `kv_bytes_per_token` ticket 无法当成物理容量证书。
- `PrefillAdder.add_one_req` 负责原生 tile/chunk、FULL+MAMBA 资源门禁和
  原生 Host load-back。BeliefKV 的候选选择在 `Req.init_next_round_input`
  与 `add_one_req` *之前*完成，拒绝候选不会在该 prefill 循环中触发
  prefix match 或 H2D；上游入队阶段独立的 storage-prefetch 路径不在
  此保证内，需要关闭或单独验证。
  native 接受后，BeliefKV 只观察结果，不事后否决已提交的请求。
- `UnifiedRadixCache`、`HostPoolGroup` 和 `HybridCacheController` 保有
  node split、共享 ownership、锁、Host/device 分配、实际 DMA 与 ACK。
  不实现第二套通用 PCIe 队列，也不把 router KV events 当完整 ownership 流。
  物理命令只允许调用经认证的 action-local native API。

## 尚需实现的物理契约

1. 将 native pool 的共享字节占用、FULL/token、MAMBA/slot，以及 node
   本地锁和 split 后的 closure 映射进 BeliefKV 的有界 physical view。
   当前只读 observer 已暴露 FULL/MAMBA 的 session 引用计数及叶
   标记数，但没有原子 revision、全部共享 owner 或独占 reclaim 证明，
   不能据此授权迁移；session 引用也不等同于锁。
2. staging native D2H/H2D 入口现可传递可选 command ID，controller
   在真正提交子操作时记录 anchor、pool token counts 和总 bytes；
   合并 ACK 同步且 tree finish 后，只有全部子 receipt 与 ACK
   的 node IDs、pool totals 和总 bytes 对账，才输出 tagged
   `child_commits`。未标记 native 子操作只参加合并账目；
   split 后 node ID 属于原子操作的 published closure，不是新 command。
   `load()` 成功或 ACK 数量都不是 DMA 完成/动作证书。分配失败、
   未提交的 H2D 或对账不符均不得获得子 receipt。
3. staging scheduler 在 admission 启用时已将
   `UnifiedRadixCache.on_hicache_transfer_commit` 接到
   `NativeAdmissionRuntime.on_native_transfer_commit`；runtime 持有
   有界 `PhysicalTransactionLedger`。`register_physical_action`
   只接受与 live causal context/epoch 和可见非终止请求匹配的预期
   动作，**仅注册对账期望，不派发迁移**。ledger 按预期 node
   closure、方向、context epoch 和冻结的 FULL/MAMBA 每 token
   字节数核对 native child receipt，只有整笔 children 对账才记录
   completion；过期、未知/重复、缺失或不匹配的 receipt 不给部分
   credit，对账错误会禁用后续物理 credit。此接线只用于 ACK
   accounting；当前没有能产生可信预期的 ownership certificate，
   也没有预测动作 dispatch，账本输入不能自行证明共享 owner、
   独占 reclaim、锁与原子 generation。
4. 等动作级 D2H/H2D 和 native ACK 双向对账通过后，再开放预测性
   PREPARE/PREFETCH。只有真实 beneficiary deficit 才能授权 COMMIT；
   SELECTIVE RETRACTION 需另行验证 overlap drain/TP 一致性。
   下一步需在动作提交前后重验 request/context/epoch、全部共享
   owner、split closure、锁与 pool 容量，形成 action-local 原子
   revision/可迁移证明；在此基础上实现安全的动作 dispatch，
   将逐子操作提交/失败（包括部分成功）、取消和资源回收绑定到
   runtime 事务，再验证 ACK 与动作期望的守恒。即便已接线的
   ledger 能对账已完成子操作，也不等于
   `PREPARE_HOST/PREFETCH_GPU` 已可执行。

新环境默认仍加载预安装 wheel；使用 staging 源码启动时必须显式传
`SGLANG_SOURCE_CHECKOUT` 给 `scripts/launch_qwen35_native_v0520.sh`，
脚本校验实际加载路径和固定 checkout。带源码启动但不启用 BeliefKV
也只验证原生禁用路径，不能代替物理动作 gate。

安全点顺序固定为 native chunk abort -> HiCache ACK 排空 ->
BeliefKV 状态同步/决策 -> native prefill admission -> GPU batch。
现阶段只有单机单 rank、非 disaggregation、无 speculative、unified cache
的 admission-only 模式可显式开启。**目前已验证的排序来自 observed
causal frontier；代码中的异步 demand worker 需要新 artifact 门禁，
尚不能作为已验证的 Qwen3.5 在线预测结果；没有预测 D2H/H2D。**旧 BF16
GPU/transfer 服务率不能作为 Qwen3.5 FULL/MAMBA 的物理容量或
latest-start 证书。下一步先生成/校准新模型 eligible artifact，
验证预测 hint 的 stale/OOD 回退和 admission GPU gate；再完成物理
owner/closure、动作事务和 D2H/H2D 归因，重做容量/服务率标定与
冻结 baseline/P6 高压 A/B。不能用新版原生 smoke 替代这些 gate，
也不能宣称 P6 完成。
