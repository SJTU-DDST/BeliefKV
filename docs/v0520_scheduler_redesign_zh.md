# v0.5.20 调度适配边界

状态：2026-09-22，native admission 可显式启用；预测 demand 仅在
目标模型/运行时匹配的合格 artifact 下参与 admission。
action-eligible artifact 门禁下的 pre-admission H2D lease 已接入
staging，但现有 artifact 均非 action-eligible，线上物理动作仍关闭。

## Native reactive 数据采集通道

Qwen3.5/v0.5.20 训练 trace 可以先在 predictor 和 BeliefKV admission
均关闭的 native HiCache (`write_back` 或 `write_through`) 通道采集。
该通道不调用 BeliefKV 的物理 ledger、COMMIT 或 Join H2D ticket，
因此预测路径尚缺的跨 epoch ACK/共享 owner 证书不是采集阻塞项。
使用独立的 `frozen_native_reactive_v0520` 计划，禁止把旧
`frozen_p5_observed` 批次直接改名冒充同一策略数据。
采集脚本必须核验 `0.5.20`、精确模型路径/BF16、Host cache、
物理动作关闭、关键模型文件哈希及 server context，并记录
FULL/MAMBA 混合容量未标定；workflow 没有任意 2 小时截断。
原始 trace 成功不等于旧 rc1 exporter 的 `formal_training_eligible`，
不能直接作为完整 P6 训练集使用。

已冻结 train-only 计划为
`configs/migration/qwen35_native_reactive_train_plan_2026-09-22.json`。
采集前启动相同新环境的 v0.5.20 服务并启用 Host cache。例如：

```bash
HICACHE_SIZE_GB=96 HICACHE_WRITE_POLICY=write_back \
  SGLANG_SOURCE_CHECKOUT=/home/longhao/experiment/BeliefKV/third_party/sglang-v0.5.20 \
  bash scripts/launch_qwen35_native_v0520.sh

/home/longhao/miniconda3/envs/beliefkv-next/bin/python \
  scripts/run_p6_collection_batch.py \
  --collection-plan configs/migration/qwen35_native_reactive_train_plan_2026-09-22.json \
  --batch-id p6-013-train-mixed-r0 \
  --model Qwen3.5-35B-A3B \
  --expected-model-path /srv/ai/models/Qwen/Qwen3.5-35B-A3B
```

2026-09-22 在目标 GPU 上完成短 native smoke：上述 4 GiB
HiCache 服务就绪，采集器的真实 server identity/capacity 校验通过，
单次 chat 返回 HTTP 200；随后已停止临时服务。通用计划生成器
仍只生成旧 P5 策略，native train 计划由专用冻结脚本从原 split
派生并强制核对源计划 SHA-256。真实 train batch 尚未执行；
运行前仍需核对 Docker image lock、日志、宿主 GPU 和工具环境。
取消的是全 workflow 的 2 小时 activation 截止，不是单次模型
调用的默认 `--request-timeout 7200` 安全超时。只有原始 trace
可获得 `raw_trace_eligible`；完整 P6 服务率/传输监督仍需新版
逐请求观测契约与 exporter，不可使用旧 rc1 遥测伪造。

## JOIN 与 pre-admission 协同

- 工具等待的 H2D source 仍需要同一 `WAIT_TOOL`、context/session 与
  revision；JOIN source 独立使用 JOIN ID/mode/成员及 child
  `remaining_to_return_ms`。ALL=max、ANY=min 的边际分位数只是提示，
  不是联合覆盖保证。JOIN H2D 现在采用有界三阶段 ticket：
  (1) calibrated JOIN P10 进入 1s 观察窗口时，尝试一个 action-local
  CPU-backed node；(2) child 模型输出唯一 `ChildCompletion` 后发送
  provisional 意图，只有 ALL 的最后待返回 child 或 ANY 的有效成员
  可提前触发；该信号不证明工具成功或 JOIN 满足；(3) 确认
  RETURN/JOIN_SATISFIED 后，parent READY 且 context/epoch/session、
  JOIN ID/mode/成员不变时，短期 reentry ticket 可继续预取。最多
  两个 node，一笔 H2D ACK 之前不发下一笔；native ACK 排空和
  overlap 安全检查后派发，不占 running slot，也不修改 native 准入。
  取消、超时或身份变化清除 ticket；晚到的 provisional 信号只丢弃。
  1s 是有界试验窗口，尚非目标模型标定的 latest-start/收益判据；
  若旧 epoch 动作的 ACK 晚于 context advance，账本仍 fail closed，
  不能给新 epoch 记账，跨 epoch 接力仍需完善。
- `--beliefkv-admission-prefetch` 必须与 admission predictor 和
  action-eligible 的 pinned artifact 同时存在。仅对被选中的 READY
  session request 启动有界 lease：先核验 native running slot，
  再抓 action-local CPU-backed node，单 node 原生 H2D 入队；
  ACK 之前 request 留在 waiting。每笔最多两个 node，失败回归
  native 准入。`init_next_round_input` 和 `PrefillAdder.add_one_req`
  仍负责最终 prefix match、FULL/MAMBA 物理门禁和 running 成员资格。
- 此设计不是把等待 H2D 的 request 直接标为 running；也不是对全部
  waiting 请求无差别预取。当前没有经 Qwen3.5 校准的动作 artifact，
  因此线上只执行原本 admission-only，JOIN 物理派发默认关闭，
  物理收益未验证。
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
- 该 worker 现在也接受 `TOOL_START` 后的工具剩余时间请求，产出
  P10/P50/P90；须有已完成请求留下的 context/session 绑定、live
  `WAIT_TOOL` 和模型中非 OOD 且有 support 的 tool-wait head。
  返回时重验 workflow/context/epoch、invocation revision、模型
  SHA 和有效期；无效或过期结果不用于调度。工具推断可覆盖等待中的
  admission 任务，而新的 admission 请求不能抹掉已经在途的工具
  结果。scheduler idle poller 监听 worker 结果 fd，以便结果到达时
  被唤醒。当前工具预测只为只读候选检查提供信号，不派发物理命令；
  admission-only 晋升并不证明 tool-wait head 的动作时机准确率。
- `scripts/promote_qwen35_admission_predictor.py` 提供有证据才晋升的
  admission-only 路径：检查同一 Qwen3.5/v0.5.20 模型/运行时哈希、
  相互隔离的 train/calibration/test_id 数据、冻结拆分以及重放
  的 demand 可用率、误差和区间覆盖。输出仍明确标记
  `predictive_action_eligible=false`；目前缺少目标数据与正式 artifact，
  脚本不能将旧模型数据转换成新模型的预测动作资格。

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
   原生 session tracker 新增按 session ID/generation 有界查询 FULL/
   MAMBA leaf anchor 的接口，runtime 可在请求已完成但仍处于
   `WAIT_TOOL` 时保留 context/session 绑定并查询 anchor；换 session、
   终止、取消或 epoch 变化使查询失效。此查询依赖实际启用
   session radix cache；默认 runner 尚未开启该能力。leaf anchor
   是候选来源，不是节点独占所有权或 DMA 授权。
   工具预测结果通过后，安全点最多读取一个 session 的 FULL/MAMBA
   祖先闭包（默认上限 64 个 node），计算尚未在 Host 备份的 FULL
   token/MAMBA node；有 pending transfer、祖先缺失、anchor generation
   不一致时放弃检查。候选仅保留只读快照，随后过期或 session 失效
   即清理；没有原子 ownership/可迁移字节证明，不能视为 PREPARE
   证书。调用动作前必须重新读取 live closure。
   本地根优先步骤选择器从 FULL session leaf 的祖先路径选一个
   尚未备份的 node；每次只建议部分 KV，不把 MAMBA-exclusive leaf
   当作 FULL 路径的来源。工具 hint、上下文和 closure 在动作安全点
   重新读取。步骤只是给 native 的候选 ID，不是可执行动作。
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
   closure、方向、context epoch/session generation 和冻结的 FULL/MAMBA 每 token
   字节数核对 native child receipt，只有整笔 children 对账才记录
   completion；过期、未知/重复、缺失或不匹配的 receipt 不给部分
   credit，对账错误会禁用后续物理 credit。PREPARE 的精确 pool
   token 数量及含 sidecar 的 DMA 字节数现可从 native 子操作在入队
   前取得并登记；账本输入仍不能自行证明共享 owner、独占 reclaim、
   锁与原子 generation。scheduler 尚未启用预测动作策略。
4. PREPARE 与 PREFETCH 的单 node native 入队前预留和 ACK 对账已接线，
   但尚未取得动作策略资格，因此不开放在线预测性派发。
   只有真实 beneficiary deficit 才能授权 COMMIT；
   SELECTIVE RETRACTION 需另行验证 overlap drain/TP 一致性。
   下一步仍需为 COMMIT 的全部共享 owner/独占 reclaim 与 predictive
   action 建立独立资格，覆盖 child/parent reentry 及收益与 latest-start
   判断。异常发生在 native
   已接收操作之后时不能释放可能被 DMA 持有的 Host slot，只能等待
   ACK/到期并报告失败。已接线的账本不等于在线 P6 物理调度。

staging 的 `UnifiedRadixCache.prepare_host_shadow` 已有单 node 原语：
仅在 cache mode、`write_through`、session radix cache 启用，且
FULL leaf 的 generation/创建代数和祖先 Host 连续性仍成立时，
向原生 controller 提交带 command ID 的 D2H。Host 空间不足则拒绝，
不得为影子备份驱逐其他 Host KV；MAMBA-only 子操作若同处 FULL leaf
祖先路径可提交。该方法**保留 GPU KV**，返回 `issued` 而非完成凭证。
controller 现在有可选的入队前 callback：在主/辅助 Host slot 确定后、
DMA 入队前将真实 `CacheOperation` 交给 runtime，冻结单 child 的
FULL/MAMBA token 数量与含 derived sidecar 的 DMA 字节数并注册账本；
拒绝时释放本次分配的 slot，不排队。runtime 的
`issue_shadow_backup_step` 在同一安全点重新检查 live `WAIT_TOOL`、
session、node，并调用该 callback；native 明确拒绝入队时才撤销账本
预留，返回的 command ID 是 issued 标识，不是 ACK 或正收益凭证。
**scheduler 尚未调用该事务入口**：当前 Qwen3.5 预测 artifact 只有
admission-only 资格，不能据此授权 PREPARE；还需要收益/时序和
物理资格，再在 safe point 接入动作选择。
更没有 beneficiary-bound COMMIT_CPU 或提前 H2D 执行闭环。

staging 的 `UnifiedRadixCache.prefetch_gpu_session_node` 新增单 node H2D
入口：重验 FULL session leaf、context 对应的 node creation、祖先在
GPU、无 pending transfer，并要求 native FULL load-back 只涉及该
node；MAMBA-only（零 FULL token）仍允许。与响应式
`load_back` 不同，此入口遇到 FULL 或辅助设备池不足时直接退出，
不能通过 `evict_for_alloc` 或 side-pool reclaim 抢占其他请求。
controller 仅在实际设备索引及 sidecar 解析完毕、操作入队前调用
BeliefKV callback，拒绝时只释放本次分配的设备槽；runtime 以原生
pool 几何和真实操作内容登记 `PREFETCH_GPU` 预期，native 明确未
入队才撤销预留。**回调成功不等于 H2D ACK，也不等于首次服务**。
该入口可为有动作资格的 JOIN ticket 和 admission lease 读取一个
FULL session leaf 路径；尚不提供动作收益、shared owner/reclaim
证明、完整 execution-KV JointPlan 或跨 epoch ACK 接力。scheduler
只在安全点尝试 JOIN ticket 派发，默认动作门禁仍保持关闭。

新环境默认仍加载预安装 wheel；使用 staging 源码启动时必须显式传
`SGLANG_SOURCE_CHECKOUT` 给 `scripts/launch_qwen35_native_v0520.sh`，
脚本校验实际加载路径和固定 checkout。带源码启动但不启用 BeliefKV
也只验证原生禁用路径，不能代替物理动作 gate。
staging 单测同样需将 checkout 的 `python/` 和 BeliefKV 根目录显式
加入 `PYTHONPATH`；直接运行环境中的 `pytest` 可能导入 wheel，
报缺少新增 session anchor 方法，并不代表 staging 源码失败。
`scripts/launch_qwen35_native_v0520.sh` 默认仍为 `write_back` 且
不开 session radix cache；显式设置 `HICACHE_WRITE_POLICY=write_through`
与 `ENABLE_SESSION_RADIX_CACHE=1`（还需 `HICACHE_SIZE_GB>0`）才满足
单 node 原语的运行前提。该配置本身不会启用 BeliefKV 物理动作，
也不自动启用 agent-native-session 桥；从 write_back 切换后的服务率
与 Host 驱逐行为须独立测量，不能复用旧 A/B 基线。

安全点顺序固定为 native chunk abort -> HiCache ACK 排空 ->
BeliefKV 状态同步/决策 -> native prefill admission -> GPU batch。
现阶段只有单机单 rank、非 disaggregation、无 speculative、unified cache
的 admission-only 模式可显式开启。**目前已验证的排序来自 observed
causal frontier；代码中的异步 demand worker 需要新 artifact 门禁，
尚不能作为已验证的 Qwen3.5 在线预测结果；没有预测 D2H/H2D。**旧 BF16
GPU/transfer 服务率不能作为 Qwen3.5 FULL/MAMBA 的物理容量或
latest-start 证书。下一步采集隔离的新模型数据，生成/校准
action-eligible artifact 并验证 stale/OOD 回退和 admission GPU gate；
仍须完成动作价值/时序、物理 owner/closure、COMMIT 与完整 JointPlan、
跨 epoch ACK 接力及 D2H/H2D 归因，重做容量/服务率标定与
冻结 baseline/P6 高压 A/B。不能用新版原生 smoke 替代这些 gate，
也不能宣称 P6 完成。
