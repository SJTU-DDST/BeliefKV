# 原生需求恢复与驻留 decode 流水线

2026-10-28。此候选继续处理完整 FULL 中关键请求被其他 prefill 区间覆盖
的问题。默认关闭 `BELIEFKV_DEMAND_PREFILL_PIPELINE`；CPU 实现、来源绑定
及真实 GPU 组件验证已完成，尚未建立模型并发推进或 JCT 收益。

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
