# 原生需求恢复与驻留 decode 流水线

2026-10-28 初始记录。此候选继续处理完整 FULL 中关键请求被其他 prefill 区间覆盖
的问题。默认关闭 `BELIEFKV_DEMAND_PREFILL_PIPELINE`；CPU 实现、来源绑定
及真实 GPU 组件验证在建档时已完成，当时尚未建立模型并发推进或 JCT 收益。

## 2026-10-29 待尝试：prefill 预算与 mixed chunk

状态：仅记录方案，尚未实现或启用新的组批策略，也未登记实验。已有的
需求 H2D 与驻留 decode 流水线已经完成模型资格及完整 FULL R1/R2；
ACK 准入修复版平均 JCT 为 1617.896789s，全程输出为
238.405436token/s，详见
[最新完整结果](../research/demand_restore_readmission_result_20261028_zh.md)。
下文原有资格和实现记录保留其历史时点；本节提出的 prefill 计算与 decode
组批优化不能算作已有流水线的完成项。当前剩余 baseline 的适配和资格
顺序保持不变，本方案留待后续单独验证。

### 工作负载依据

参考冻结应用 `5cf3bb8b` / native tree `729aa0caa7a1` 的完整运行：
Qwen3-Coder-30B-A3B-Instruct、FULL-only、单卡、18 个不同项目的 workflow，
共 3674 个请求。以下结合完整报告与原始 trace 的离线复核：

| 观察项 | 完整运行结果 | 对方案的启示 |
| --- | --- | --- |
| 完整输入长度 P50/P90 | 21010/120780 token | 历史上下文长，不能只看本轮新增 token 判断成本。 |
| 每请求实际 prefill extend 总量 P50/P90 | 145/1329 token | 大量请求是有缓存的短续接，完整 prompt 长度不等于重做 prefill 的工作量。 |
| 每请求输出长度 P50/P90 | 60/378 token | 短生成容易受首次准入和长 prefill 插入影响。 |
| 单请求 prefill 批次 | 3280/3603，约 91.04% | 值得检查 ready 短续接的组批机会，但不能证明这些请求当时都可合并。 |
| 恰好 4096 extend token 的批次 | 472 批，约占 prefill 批次的 13.10%，占 prefill worker 累计区间的 47.27% | 少量大块可能造成较长 decode 服务间隔。 |
| 上述 4096-token 批次的 worker 区间 P50/P90 | 335.94/670.87ms | 应验证缩小单次 prefill 工作是否改善服务连续性。 |
| 不超过 256 extend token 的批次 worker 区间 P50/P90 | 18.18/38.44ms | 缩块会增加小批与调度次数，不能只追求更短的单批。 |
| 实际 decode 宽度 | 均值 5.88，按 worker 时间加权 8.37，最大 17；running 上限 24 | 需要区分 ready 需求、agent 资格和物理 KV 容量，不能直接上调请求上限。 |

实际 extend 共 3770870 token，约为累计完整输入 145565966 token 的
2.59%；这不是缓存命中率，不能用来断言其余输入都在 HBM。
prefill worker 累计 372.62s，decode worker 累计 1591.37s。worker 区间
包含运行时和依赖等待，不是纯 GPU kernel 时间，也不是可直接扣除的 JCT。

当前有效配置为 `chunked_prefill_size=4096`、
`max_prefill_tokens=16384`、`max_running_requests=24`、
`enable_mixed_chunk=false`、`enable_dynamic_chunking=false`，
原生 overlap schedule 已开启。decode 请求步平均历史约 55730 token，
Device 容量为 805198 token；若 24 个上下文彼此独立且均达到该长度，
按每 token 98304 字节估算，单 KV 就需要约 131.5GB。因此不能把
running 上限 24 当作当前容量下一定可以维持的 decode 宽度；共享前缀、
长度分布与增长预留仍需按真实页核算。

证据入口：

- [完整性能与 decode geometry 报告](../../experiments/reports/prefill_pipeline_20261028/readmission_complete/complete_review.json)。
- [实际服务参数](../../experiments/raw/demand_prefill_ack_retry_20261028_r1/full/server/server_info.json)。
- [逐请求长度](../../experiments/raw/demand_prefill_ack_retry_20261028_r1/full/replay/native_requests.jsonl)及
  [原生服务样本](../../experiments/raw/demand_prefill_ack_retry_20261028_r1/full/server/runtime_audit.jsonl)；
  长度、prefill 分块和批次区间分布来自这两份原始记录的离线统计。

### 拟尝试的机制

1. **在有可推进 decode 时限制单次 prefill 工作。**先比较现有 4096 与
   2048/1024 token 的分块预算。驻留 decode 有工作时减少大块独占区间；
   没有 decode 或算力未充分利用时允许较大 prefill。后续若需要动态预算，
   使用实际新增输入、历史长度和观测执行成本：相同新增量在 5K 与 100K
   历史下成本可能不同。缩块会增加 forward、CPU 选批和提交次数，必须
   检查整轮净效果。
2. **验证原生 mixed chunk。**评估把 running decode 成员并入 extend
   batch，让大 prefill 期间 decode 也有实际输出。先确认 native 的
   return-logprob、input-embedding、beam 等条件允许真实进入 mixed 路径，
   并核对实际成员与 token 增量，不能仅以开关开启作为成功。mixed batch
   也可能拉长 decode 步，需与分块预算分别比较。
3. **改善已就绪短续接的组批。**只针对已经提交、agent 资格允许、
   数据依赖就绪的请求，检查是否存在连续多个小 prefill 被拆开的情况。
   在现有队首、JOIN、驻留、老化和 sibling 选批规则内，考虑实际输入增量
   与恢复成本；已有 sibling 偏好继续沿用。不给尚未 ready 的请求制造
   批次机会，不为凑批等待，也不让一个冷请求挡住可推进的驻留 decode。
4. **扩展现有需求恢复流水线的 chunk 覆盖。**当前
   [stage 入口](../../beliefkv/runtime/demand_prefill_pipeline.py)遇到已有
   `chunked_req` 或 `adder.new_chunked_req` 会回退。后续研究怎样将真实
   chunk 生命周期及 mixed batch 接入需求恢复，使 H2D 等待与可运行 decode
   协同推进。保留 session/attempt 身份、request receipt、Host/Device
   原生锁、ACK、逐层 fence、取消和共享前缀依赖。H2D 与计算重叠、prefill
   与 decode 组批是两个机制，分别证明实际覆盖和消费。
5. **按可持续容量和成本选择 decode 宽度。**维持 running/decode24
   的共同上限，结合实际 ready 需求、上下文长度、缺失 KV、输入和 decode
   增长，避免为加宽 batch 反复换入换出。更细的 CUDA Graph bucket 留作
   独立的通用引擎候选：当前配置 bucket 额外行数约为真实行数的 15.21%，
   这不是已测的浪费时间，更不等于可获得 15.21% 加速。

### 验证顺序

先补齐 mixed 模式的工作量与服务记账，再尝试配置。现有
[decode 诊断脚本](../../scripts/plot_causal_decode_diagnostics.py)
只汇总 `phase=decode` 的输出；mixed extend 中的 decode 输出需要按
实际请求成员单列并与完整输出守恒，否则相位变化会制造吞吐差异。

首轮保持 mixed 关闭，只验证较小 chunk 是否减少长 prefill 打断且不显著
增加控制成本；依据结果选择下一档，不一次展开完整参数网格。随后在选定
预算下单独验证 mixed 的实际执行和净收益。短续接选批、chunk 恢复接线及
自适应预算分别登记后续候选，避免同时改变多项而无法定位原因。
涉及新原生接线时，完成对应生命周期、真实 GPU 字节和模型正确性资格，
再进入完整工作量比较。

正式比较沿用同一完整 V230/V311 的 18 个 workflow、token、依赖、工具
计时、种子、容量、停止方式和全部正常尾部。保留现有 agent 连续性和
已观察 JOIN 规则，工具预取继续关闭，不增加全局 ready-agent 轮转或修改
工作量。原始 paper baseline 及其冻结结果保持原配置；若改变通用引擎
设置，披露差异并做相应的同设置比较，不能把通用分块/mixed/graph 调优
本身当作 agent 调度创新或全部归因于预测传输。

### 验收指标

- 主指标为完整 workflow 平均 JCT 与全程输出吞吐，同时保留逐 workflow
  分布及所有正常长尾；不以某个高压切片或单批加速替代完整结果。
- 同时统计 decode 实际输出 / worker 区间和 decode 实际输出 / 墙钟时间，
  包含 mixed batch 内的 decode 输出；检查 prefill、H2D 期间的实际
  decode 进展和服务间隔尾部。
- 统计最后一个 ready child 的等待、JOIN 解锁到 parent 首次服务，以及
  每 workflow 的 ready-output gap，防止全局吞吐改善却拖慢关键分支。
- 记录 CPU 选批与提交成本、实际 batch/context/chunk 形态、H2D/D2H、
  retraction、已服务前缀重算与容量压力，区分初次冷 prefill。更多传输、
  更宽 batch 或更多 mixed 调用均不能单独作为成功依据。
- 保留负结果；若缩块或 mixed 增加总开销、重算或完整 JCT，则不推广。
  本文记录不改变当前运行配置和 baseline 执行顺序。

## 实验依据

历史分组版的 24.981494 秒关键工具后继服务后增量中，其他 prefill-only
覆盖增加 11.188698 秒。最新完整索引版对准入前卸载的同组增量为
16.683404 秒。worker 区间包含运行时和依赖等待，不能据此把这些时间
全部判为计算、控制 CPU、传输或可消除的 JCT。

完整 prefill 复核位于
`experiments/reports/prefill_pipeline_20261028/prefill_review.py/.json`：

| 完整系统 | prefill 批数 | 实际 extend token | worker 累计/s |
| --- | ---: | ---: | ---: |
| 准入前卸载 | 3774 | 3770457 | 425.946088 |
| 最新索引 FULL | 3804 | 3770495 | 427.057785 |
| V345 工具衰减 ThunderAgent | 3561 | 3800241 | 429.652427 |

最新 FULL 仅多 38 个实际 extend token、1.111696 秒总 prefill worker
区间，却有更高的关键请求 prefill 覆盖。插入时机和恢复依赖值得验证，
不能直接认定 prefill 总工作或新增 CPU 是主因。

67 个共同权重的稀疏匹配样本中，计算 stream span 均值从 108.178140
变为 108.404515ms；selection 线程 CPU 从 69.931465 变为 74.913346ms。
post-physical 匹配均值受一个 285.332523ms 样本显著影响，该样本的
stream span 为 268.682068ms，两者存在重叠。所有样本和异常值保留，
不外推整轮 CPU、GPU 空闲或可扣除 JCT。

## 执行方式

实现入口为 `beliefkv/runtime/demand_prefill_pipeline.py`。原生接口放在
独立 checkout `sglang-demand-prefill-pipeline-20261028`，仅修改
`Scheduler`。原生 commit `d3da19c1` / tree `729aa0caa7a1`，独立累计补丁
为 `patches/sglang-demand-prefill-pipeline-runtime-20261028.patch`。
CPU-tested 文件与该 clean commit 逐字节绑定，补丁在独立 index 从
`fa5cb52a` 重建相同 tree；现已登记精确 engine tree 与补丁，要求独立
完成新的 GPU component、model 和 position 验证。
旧实验及其 engine 不变。

1. 原有 agent 策略决定候选，`PrefillAdder` 执行正常容量、输入和增长
   检查，并物化真正提交请求所需的 Host 前缀，取得原生请求锁。
2. 完成本轮真实选批后，如果有可推进的驻留 decode、完整且未分块的
   native prefill 成员，以及本轮新排入的普通需求 H2D，则启动这批
   已批准的 H2D。保留整个实际成员集合，包括 sibling；不限制为单一
   排队候选，也不新增 agent 资格或改变候选优先级。
3. 请求仍留在原生 waiting queue，不提前分配 request row 或输入
   token。暂时持有各自原生请求锁，并保存传输自己的不可复用 ACK
   finish event。恢复等待期间返回原生 decode 路径。
4. ACK 就绪后，先且仅一次释放旧请求锁，再运行正常选批与容量检查。
   旧 receipt 不能被新的 admission receipt 覆盖。传输时不提前消费
   `on_prefill_candidate_result(admitted=True)`；真正重新准入后才通知。
5. 如果 decode 已无有效工作，或原成员失去正常 agent 资格，释放
   暂存的请求锁，回到原生需求恢复。仍未完成的传输保留真实逐层依赖；
   新批次已有更新的 native consumer 时，不能覆盖它。

维护一个 native 恢复批次及其成员 receipt。没有新增定时器、TTL、
恢复优先权、未来工具信息或逐 token 全 agent 排序。disabled 路径
保留原 callback 顺序；paper/control 环境显式将开关设为零。

## 取消与依赖边界

请求取消、身份/attempt/epoch 变化、终态或移出队列时，释放本候选
拥有的原生请求 receipt。DMA 自己的 Device/Host 锁及 ACK 继续由原生
控制器持有和释放，不同步等待 DMA。

开始暂存传输时，同时向 cache 的原有加载依赖入口发布 consumer。
即使原请求取消，其他命中同一在途前缀的请求仍会获得正确的逐层 fence。
只保留 Python 内的 pending 对象而不发布这个依赖是不安全的。

驻留 decode 可能携带历史 consumer index。仅当现有原生
`beliefkv_prefill_load_consumer` 对它全部已选前缀证明无在途依赖时，
清除无关 index；未知路径或相关 H2D 保留原保护。不能因为新传输的
consumer ring 被复用，就让原本驻留的 decode 等待无关传输。

以下情况直接回退本轮正常原生 prefill：已有 chunk/新 chunk、已有
不明 load queue、非本候选事务、带 command 的 speculative copy、
缺失实际请求/session 绑定、beam、已有 request row 或无有效 decode。
FULL-only、cache-mode、write-back、无 linker/storage/buffer，以及
既有 Full agent 准入是 factory 的必要条件。原生 admission 构造器
还保留单 rank、非 disaggregation、无 speculation 等已有约束。

## 记账与验收

提前恢复后，下一次 match 可以把前缀识别为 Device 命中。保留原生
`cached_tokens_host` 语义，不能人为把它改回 Host 命中来凑对账。
`native_demand_restore_staged` 单独记录实际选批成员、request/session/
attempt、原生 submit 时间、node IDs、FULL token 和实际字节。

`scripts/audit_staged_native_restore.py` 要求每个暂存批次恰好匹配一个
相同 submit 时间、anchor 序列、字节和 pool token 的已完成 untagged
native ACK，拒绝重复认领、tagged copy 或身份不一致。共享批次不把
整批字节归给某个单独请求；这个对账本身不证明首次服务或隐藏了传输。

完整迁移审计后续必须分别保留：原生请求 Host 命中、精确匹配的暂存
需求 H2D、逐层 handoff，以及 tagged JOIN 预取；没有匹配证据的余量
继续判为未解释。旧审计和旧报告保持原样，不能放宽字节守恒门槛。
暂存时间仍计入提交到首次服务及完整 workflow JCT。只有实际驻留
计算覆盖了传输，并改善完整 JCT/全程输出，才算性能收益；仅把等待
从 prefill worker 移到 waiting queue 不算优化。

当前 CPU 测试使用实际 `Req`、`PrefillAdder`、radix tree、allocator、
原生 ACK 提交和修改后的 scheduler 方法；copy/event 与 batch 构造
边界为 CPU stand-in。覆盖单/双成员、callback 时序、锁重取、取消后
共享前缀、无关 decode fence、控制隔离和精确字节对账。初始设备配置、
socket 与 scheduler fixture 失败均保存在本证据目录。

完整 `shared_r3_cpu.xml` 通过 3839 项及六个子测试，保留原有 3718 个
独立测试身份并新增 121 个。`shared_r3_cpu.evidence.json/.sources.json`
核对同一次执行前后的应用/native 源码，并经实际 launcher 环境映射
确认 Full 开启、工具衰减 ThunderAgent 关闭该候选，二者工具预取均关闭。
`native_seal.json` 独立证明提交只改变 native 元数据、内容与 CPU 测试
相同，并绑定可重建的补丁。

第一轮完整回归中旧 free-page checker 正确拒绝了新增 scheduler 接线；
它保持原样。独立 `project_disabled_scheduler()` 只移除精确审核的
默认关闭分支，并证明整个 scheduler AST 等同原生父版本，然后再执行
旧 checker。未知接线、提前 admitted、缺取消/consumer 绑定及其他修改
均有拒绝测试。第二轮全部测试通过但汇总脚本漏做 launcher alias 映射；
修正的只是证据脚本，第三轮完整执行与构造器核对均通过，前两轮 XML
继续保留。

真实 DMA 组件通过后，仍需冻结当前应用，验证
恢复期间 resident decode 的实际推进、消费者首次服务和非强制
模型数值一致性，再完成当前树的位置/session/stop 检查。随后才可在
完整 V230/V311 固定工作量上比较 Full，复用完整 V345 并公开 engine
差异。工作量、停止、全部正常长尾、容量和种子不变，工具预取继续关闭。
其他 baseline/消融继续等待明确、可重复的双指标收益。
实际启动审计要求独立证明 helper 已安装并绑定当前 native cache，不能
只依赖 enable 位。正式 FULL 还需证明恢复/消费被真实执行。

## 完整服务审计与组件入口

`service_gpu_r1_cpu` 对当前来源完整执行 4060 项及六个子测试，保留
此前 3839 个测试身份并新增 221 个；源码在执行前后保持一致。实际
native、Full、工具衰减 ThunderAgent 构造器同时验证安装状态及隔离。
prefill 服务样本补齐 attempt/session/generation，decode 样本不增加
这组逐请求字段；暂存日志记录同一单调时钟域。

`scripts/audit_demand_prefill_pipeline.py` 保留旧 handoff 审计不变，
在其完整工作量、drain 和进程退出检查之后，将 untagged H2D 分成
原生请求 Host 命中、handoff 和精确 ACK 匹配的暂存 demand 三项。
未知余量或重复认领直接拒绝；每个暂存成员另须匹配后续实际 prefill
身份及完成回执，或明确取消。服务证明不等于 KV 复用或隐藏 DMA。

`scripts/qualify_demand_prefill_pipeline.py` 提供四个真实 GPU 组件场景：
resident accessor、双成员批次、无 decode 回退、取消后共享前缀。
它使用实际 Req/PrefillAdder、128 Device/257 Host slots、两层 KV、
原生 copy/ACK 和有限延迟的最后一层。必须证明 resident accessor
在冷恢复 ACK 前完成、消费者 K/V 完全一致、正常回退/取消保留 fence，
且最终容量全部归还。这是有控制延迟的组件检查，不能替代非强制模型
并发推进、输出/logprob、position 或完整性能验证。

冻结应用 `165a331e`、clean native `d3da19c1` 的
`gpu_components_r3.json/.log` 已完成四个场景，原始进程退出零。
每场景的 resident accessor 都在真实 H2D ACK 前完成，所有消费者
K/V 完全一致；双成员共恢复 64 token，其余各 32 token，均精确匹配
untagged ACK。每场景最终恢复全部 128 Device/257 Host slots，
无 decode 回退及取消共享前缀 fence 通过。实际进程结束后 GPU 无
compute process。r1/r2 的导入及包装参数失败继续保留。
该有限延迟两层组件不代表真实模型 forward 或整轮性能已通过。

## 真实模型并发资格入口

`scripts/qualify_demand_prefill_model.py` 使用三个不同 source workflow
的 24576/24576/32768-token 前缀，Device61440/Host8GB、running/decode24。
先经正常容量压力使 B 的旧前缀转为 Host，再让 A 以驻留前缀生成
512 个 greedy token，实际 decode 已开始后提交 B 的需求续接。整个
模型场景不插入人工 copy 延迟、不使用重放 sampler 或预定输出 token。
固定生成长度属于数值 fixture，不改变正式 workload 的停止方式。

验收要求 B 恰好一次暂存至少 24575 token 的原生 Host 恢复，并匹配
精确 ACK、原生 prefill 服务身份；A 和 B 分别与独立 namespace 下的
相同输入 greedy 输出比较，token 完全一致，logprob 最大绝对差不超过
0.05。只有 usage、选批记录或 `waiting_checks` 不能证明并发。

默认关闭的 `BELIEFKV_DEMAND_PREFILL_PIPELINE_PROGRESS_PROBE` 仅用于
这个资格。在实际 batch 结果已处理后，非阻塞查询 pending 恢复自己的
不可复用 ACK，记录 forward_iter、完整请求身份及已产生的 output 数。
资格还核对同一原生 decode worker 的正 token 增量、相同时钟域，并
要求其 batch launch 晚于暂存开始。这证明正常模型结果在 ACK 尚未完成
时得到处理，不提供纯 kernel 起止时间或可扣除的 JCT。
默认关闭时不查询 CUDA 事件，也不生成这些逐 decode 日志。

启动脚本新增 `demand_prefill_pipeline` 补丁选项，仍要求 exact clean
native commit 和对应累计补丁的 reverse-check。模型入口先检查实际
前任退出、相同冻结 application/native 组件来源、GPU 预约及资源。
当前模型入口的 51 项定向 CPU 检查通过；完整 `model_entry_r2_cpu`
单次执行通过 4085 项及六个子测试，保留原有 4060 个身份并新增 25 个。
前后源码及实际构造器均完成绑定。真实模型执行仍待冻结后完成。

第一次模型尝试 `demand_prefill_model_20261028_r1` 使用三个 24576-token
前缀和 Device57344；七个模型请求均完成，但预设近完整冷前缀检查拒绝：
真实 native H2D 为 20487 token/2013954048 bytes，B 仍有 4096 token
驻留。因此保留 failed，不把它重新标为资格通过。记录确实包含恢复期间
的 decode 进展，但尚未完成其余独立验收。

修正只调整数值 fixture：第三个前缀增至 32768、Device 增至 61440，
要求选取输入足够长的三个独立 source workflow。三前缀总量超过池容量，
但 A+B 加 512-token 输出仍有空间；目标是让 B 完整转为 Host，同时
允许实际并发。原有至少 24575 token、精确 ACK、结果及进展门槛保持
不变。正式 workload、容量和历史比较均未改变。

随后独立读取 r1 原始响应发现数值检查同样未通过：A 首个 token 分歧
在第 125 个输出，首个超过 0.05 的 logprob 差在第 50 个输出；B 的
八个输出 token 相同，但最大 logprob 差为 0.1041944623。A 前 48 个
输出的 logprob 相同，变化开始于 B 已恢复并加入服务的阶段。这只能
定位时间，不能证明是 batch 数值差异或 KV 损坏。r1 仍为 failed，
不得跳过数值门槛进入 FULL；下一步需要关闭新 pipeline 的同场景诊断，
同时核对独立参考的前缀构建与 batch 形态。

`--diagnostic-native-demand` 是单独的有界数值诊断入口：恢复第一次
三个 24576-token、Device57344 的固定场景，仅关闭新 pipeline，
执行相同七个请求并保留数值差异。它必须确认 helper 未安装且没有
staged journal，状态只能为 `native_demand_model_diagnostic_collected`，
明确 `model_qualified=false`，不沿用正式模型资格标签。原有正式
`compare_outputs` 的 token 相同和 0.05 门槛保持不变。

数值入口新增两个显式选项。`--deterministic-numerics` 仅在这个有界
fixture 中使用 native 确定性 FA3/math 分支，状态另标
`unforced_demand_prefill_deterministic_model_qualified`，不改正式性能配置。
启动后读取实际 server 参数，验证确定性位、FA3、对应 sampling backend、
session radix、缓存开关、容量及 running24，不能只相信 CLI。
`--reference-build continuation` 则在独立 namespace 中先对原前缀生成
同样八个 token，再续接；两个初始历史也必须通过原有 token/logprob
比较。冷参考保持默认，原有失败记录和严格门槛均保留。

`deterministic_entry_r1_cpu` 单次完整执行通过 4096 项及六个子测试，
保留此前 4086 个身份并新增十个；定向数值入口 34 项通过。准备给
重放的 base-plus-patch native checkout 重建 tree `729aa0caa7a1`，
另有 92 项 native 检查通过。它们尚不建立新的真实模型或性能资格。

冻结 `0c5bee90` 的确定性模型已通过：24583 token/2416607232 bytes
恢复、精确 ACK/后续服务、两个实际 resident decode batch 在 ACK 前
完成，512/8 个模型输出与独立冷参考相同、logprob 差为零。普通后端的
独立续接参考仍使 A 在第155个 token 分歧，B 则相同且最大差0.00063002；
两者的原始结果分别保留，不把确定性通过当作普通后端的通过。

正式配置与实际 progress-probe 状态门槛补齐后，
`formal_entry_r1_cpu` 通过 4100 项及六个子测试，保留全部 4096 个
身份并新增四个。其 source manifest 包含新的正式 JSON 配置。
正式完成检查要求 probe 明确 false，模型资格则要求实际观测到其
开启；配置不改变数学后端或完整 source 工作。当前场景的原生续接
对照、当前冻结模型及 position 仍待执行。
