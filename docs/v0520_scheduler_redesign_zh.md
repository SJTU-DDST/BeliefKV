# v0.5.20 调度适配边界

状态：2026-09-22，设计与未激活的 admission 切片；不是 BeliefKV 已在新版本运行。
目标配置为 Qwen3.5-35B-A3B BF16、单机、统一 FULL/MAMBA tree、HiCache
cache mode。其他 cache backend、TP/PP、disaggregation 和 speculative
仍需独立验收。

## 保留的决策

- RCCG 与 Action frontier 保有 agent 因果关系、JOIN 依赖和可见请求资格；
  FrontierBelief 输出 future demand、reentry 和 latest-start 的概率分布。
- P5/P6 JointPlan 保有吞吐优先的执行顺序、beneficiary/victim 选择，以及
  `PREPARE_HOST`、`COMMIT_CPU`、`PREFETCH_GPU` 的价值和 deadline 判断。
  预测决策必须绑定 request/context/epoch、目标 node closure 和生成版本。
- 准入计划只对 *tagged* 请求下发语义顺序/许可。同一安全点中，native
  `policy.calc_priority` 先形成队列；BeliefKV 在 tagged 位置重排有身份
  校验的候选，未授权或过期的 tagged 请求跳过并继续扫描下一候选，
  untagged 请求保持其原生相对位置。此切片已写入 staging，但 runtime
  plan producer 尚未接线，服务仍 fail closed。

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
   当前只读 observer 不包含原子 revision，不能授权迁移。
2. 为每个提交的动作建立独立 command identity，区分 *提交* 与 *DMA
   完成*。原生 merged ACK 只有 node IDs 与 pool 总数，缺少 per-node
   bytes/command ID；不能按 ACK 数量猜测 BeliefKV 的完成证书。
   分配失败、拆分、部分成功和未知 ACK 一律 fail closed。
3. 等动作级 D2H/H2D 和 native ACK 双向对账通过后，再开放预测性
   PREPARE/PREFETCH。只有真实 beneficiary deficit 才能授权 COMMIT；
   SELECTIVE RETRACTION 需另行验证 overlap drain/TP 一致性。

安全点顺序固定为 native chunk abort -> HiCache ACK 排空 ->
BeliefKV 状态同步/决策 -> native prefill admission -> GPU batch。
现阶段不开启 `enable_beliefkv`，不能用新版原生 smoke 代替 A/B 实验。
