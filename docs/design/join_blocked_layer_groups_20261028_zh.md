# JOIN 额度交接与独立传输分组

2026-10-28。此候选将两个实现改动合并声明：
补齐阻塞 agent 的逻辑额度交接，以及按不同层数组合 D2H/H2D。
工具预取和普通执行前沿预取继续关闭，保留 JOIN/EOS 预取、预测卸载、
prepare_host 与 agent 连续性。它是调度与迁移整合，不将额度交接单独包装
为新的工作集调度算法。

配置：`configs/migration/predictive_join_blocked_layer_groups_20261028.json`。
保留 V230 的完整工作量、V311 工具时长、18 个不同项目、running/decode24、
容量、seed21、流式指标和全部正常长尾。现已通过 grouped 组件、非强制模型
与 24 请求 position/session/停止边界检查，正式 FULL 已启动，详见文末；
完整性能结果仍待审计。

## JOIN 的交接链路

此前 reference FULL 的额外准入只能使用已经空出的完整逻辑预算。
新开关 `AGENT_JOIN_BLOCKED_HANDOFF=1` 在两个实际入口尝试交接：
已观察 EOS 的 parent 恢复申请，以及真实 JOIN 后继请求的注册/就绪事件。
没有 EOS 或实际后继请求时，不为了等待中的 parent 提前占用新预算。

| 阶段 | 逻辑资格 | 物理 HBM |
| --- | --- | --- |
| 确认 parent 与实际 JOIN 身份 | 不变 | 不变 |
| 完整预算仍不足，选择真实阻塞 donor | 仅形成计划 | 不变 |
| 再验证后暂停 donor、授予 parent 资格 | 一次性交接完整额度 | 不增加任何空闲页 |
| 原生增量备份、ACK 后安全回收与 H2D | parent 已有资格 | 实际 native 分配及 DMA 检查 |
| parent 原生准入、执行及后续请求 | 延续普通 agent 资格 | 保持原生增长、锁和压力规则 |

新逻辑位于 `beliefkv/runtime/join_blocked_handoff.py`，由
`ReferenceThunderAdmission` 的事件和 `claim_restore()` 路径调用。
原有 JOIN 物理恢复仍走 `_reclaim_wait_prefetch_capacity()`、
原生缓存回收和正常 prefill 准入。逻辑 donor 与最终物理回收对象分别核算，
不会将预算释放当作 HBM 页已经可用。

允许 donor 的必要条件：

- 当前 active、已完成模型调用、没有 pending 请求或暂停标记；
- context/session key 和图状态仍一致，确实等待 JOIN 或工具；
- 工具 donor 仍须通过已有长工具预测；短工具、缺少预测者不提供此交接额度；
- 不涉及其他正在恢复的 JOIN、critical demand 或受预取保护的 owner；
- 不暂停 ready、运行中或等待模型的 agent。

先刷新实际 streaming 长度，按完整上下文与原有 100-token buffer 核算。
单个 fitting donor 优先，否则在有界候选集中累积；只有完整 parent 放得下、
所有 donor 再验证仍有效才一起提交，不做部分释放。
最多检查 64 个 donor，事件上的 ready-parent 窗口为 24；失败后保留 200ms
重试间隔，在真正的请求/阻塞状态变化处重新唤醒检查。没有新后台定时器、
逐 token 全队列扫描、普通恢复 TTL 或 donor-return 关系账本。

### 防止五秒控制撤销交接

reference 五秒控制原先也会考虑恢复仍阻塞的 paused ACTING program。
这可能重新接纳刚暂停的 donor，再因完整预算超限把正在恢复的 parent 暂停。

新开关下，给原 working-set 核心提供可选 `resume_eligible` 条件：
尚未提交新请求且仍被在线图确认阻塞的 paused agent 暂不进入恢复候选。
工具/child/JOIN 真正返回、图状态就绪后，重新参与普通竞争；已经 pending
的真实请求也不被该条件阻挡。active agent 没有因此自动被驱逐。

该条件默认为空，paper ThunderAgent 及旧 FULL 使用原有选择规则。
仍使用相同核心类型和继承的 attach、tick、completed、abort、
streaming-accounting 方法，不伪造 ACTING/pending 状态或折扣完整容量。
这不是无限 parent 优先；真实增长压力与原生容量仍可限制 parent。

### 可审计的后续服务

成功事件 `agent_join_blocked_capacity_admitted` 记录 parent、donor、
完整身份、预算前后值，并显式记录 `released_device_tokens=0` 和
`released_host_tokens=0`。JOIN grant 审计器现在识别该事件，校验：

```text
交接前剩余额度 + donor 完整额度 - parent 完整需求 = 交接后剩余额度
```

之后仍须用原生 session/epoch、实际拷贝和首次服务证据验证恢复与执行。
资格授予本身不计为已执行、已释放显存或 JCT 收益。

## 不同的 D2H 与 H2D 粒度

新 native 分支 `beliefkv-layer-groups-20261028` 已提交
`1f5fac9e9f7ad1d97f89244cdda4d9f311d365c4`，树为
`2ce5e5389979a718c19f80abb9cc9b381b8cff0e`。
旧 `0d407f981c51` 引擎与所有历史结果保留。

候选参数：

| 方向 | 参数 | 初始候选 |
| --- | --- | ---: |
| D2H | `BELIEFKV_LAYER_HANDOFF_D2H_LAYERS` | 8 层 |
| H2D | `BELIEFKV_LAYER_HANDOFF_H2D_LAYERS` | 4 层 |

两方向独立配置，允许不整除，例如 5/3、3/5。复用现有 HiCache
`transfer_hicache_all_layer` JIT，通过层指针表的切片一次复制整个层组；
没有用 Python 循环调用单层接口来假装合并。
`layer_first` 与 `page_first` 使用各自正确的 token stride。

每个 H2D 组等待覆盖其目标层范围的最后一个 D2H 组事件；D2H 同流有序，
因此跨越多个 donor 组时也不会提前覆盖。前面组就绪后，计算仍按每层
消费就绪事件，不需要等待最后一组完成。默认 1/1 保留旧粒度。
原有 donor/consumer token 数不同的交接仍保留，消费者使用 donor
预留 extent 的前缀，多余尾部只能在真实 D2H ACK 后释放。

在当前 48 层模型上，8/4 将 D2H/H2D 接口调用从 48+48 减到 6+12，
层完成事件也从 96 个减到 18 个，二者均减少 81.25%。
这不是整段提交时间或 JCT 减少 81.25%；索引、锁、发布和其他 CUDA 事件仍有成本。
较大的 D2H 组也会增加首组提前量，8/4 尚不是经过性能选择的最优值。

原生补丁：
`patches/sglang-layer-handoff-groups-runtime-20261028.patch`，
SHA256 `8b8a8d35404d9f1c035a57645684c6758f15c3e48ee6ef83551b3e5815754612`。
应用包装器、启动器及 replay 引擎白名单绑定该精确树，旧引擎不能冒充
支持分组。新的模型 qualifier 还要求 GPU component 的两方向分组与配置一致，
不能用旧 1/1 的 component 证据验证 8/4。

## CPU 验证

一次最终共享执行通过 3519 项及 4 个子测试，无失败/跳过，保留前次
3481 项全部身份，新增 38 项。原生相关 CPU 检查通过 53 项。
实际构造器确认 FULL 的新交接与 8/4 生效，工具预取关闭；
默认和工具衰减 ThunderAgent 均未继承新开关。

测试覆盖额度不足不部分释放、ready/pending/受保护 donor 拒绝、
交接期间 donor 返回、重复检查退避、五秒控制不撤销交接，以及真实 native
树/分配器上的预算交接、恢复拒绝、Host-backed 回收、H2D、ACK 与
parent 后续候选选择。原生测试覆盖不同方向组、非整除组、正确 stride
与指针范围。CPU copy/event 替身不证明实际 CUDA 安全或模型数值等价。

局部控制成本使用真实 runtime 回调、33 个合成 program、31 条 pending
记录和异步日志，每条路径预热 50 次、测量 500 次：

| 路径 | thread CPU 中位数/微秒 | P99/微秒 |
| --- | ---: | ---: |
| 关闭新交接 | 29.243 | 32.526 |
| 成功额度交接及日志 | 121.328 | 141.781 |
| 本轮没有可用阻塞 donor | 73.528 | 78.802 |
| 200ms 重试退避 | 34.116 | 38.468 |

它不含 writer 线程 CPU、最大 64-donor 窗口、整控制器或 GPU 开销；
不能据此声称控制开销已低于所有场景的迁移收益。

命名证据目录为 `experiments/reports/join_blocked_layer_groups_20261028/`。
最终共享来源绑定为 `qualified_shared_cpu.*`，原生为 `native_final_cpu.xml`，
回调成本为 `control_cost_cpu.py/.json`，总来源检查为 `review_cpu.py/.json`。
保留首次两个测试夹具错误、一次 nested-harness 时间戳乱序、
精确复查、完整重跑以及最初 CPU benchmark 统计字段错误。
没有为通过测试改写 workload、排序实际源事件或丢弃失败证据。

实际 grouped GPU 拷贝已通过下述组件验证，非强制模型输出一致性及完整性能仍需分别验证。
后续 FULL 继续复用 V345，显式披露引擎差异，评价完整 JCT、全程输出、
decode 随时间曲线、迁移量和计算干扰；当前没有新的性能优势结论。

首次真实 GPU component 从冻结应用 `0decf93f`、原生 `1f5fac9e` 启动，
在交接前发现共用夹具仍硬编码原有 2 层/1024 字节，而新的分组夹具使用
17 层，因此主动拒绝。`gpu_components_r1.json` 保留失败，进程 613553
已退出、NVML 已为空。只修复夹具按声明层数检查每层 512 字节，仍保留
128 Device / 257 Host slots 校验；71 项定向 CPU 检查通过。迁移与调度
代码和原生引擎不变，下一次实际 GPU 验证使用新的冻结应用。

## 实际 GPU 组件完成

R2 使用冻结应用 `4675f4ad` 和同一 clean 原生 `1f5fac9e`，
`gpu_components_r2.json` 的正常/延迟尾组两个场景均通过：

- 实际 D2H 8 层、H2D 4 层，17 层原生 K/V 池；
- donor 48 token、consumer 32 token，预留尾部 16 token；
- donor 原有 GPU reader 在覆盖前完成，受保护 resident 的全部 K/V 不变；
- Host donor 与 HBM consumer 的全部层 K/V 与参考逐项一致；
- 延迟最后一组 D2H 时，consumer 第一层读取在最终 donor ACK 前完成；
- 发起后原生 free count 仍为 4，尾部只在真实 ACK 后释放，最后为 20；
- donor 早返回不能读取未完成的 Host checkpoint。

这是带有限延迟的正确性夹具，正常小拷贝场景没有观察到第一层提前于最终
donor ACK；不因此推算真实工作量中的隐藏时间，也没有将该延迟注入 workload。
进程 616670 已实际退出、NVML context 消失，未启动 FULL 或 LLM 模型服务。

夹具修复后的最新单次共享检查为 3524 项及 4 个子测试全部通过，
保留前次 3519 项身份并新增 5 个 geometry 案例；报告为
`fixture_qualified_shared_cpu.*`。`gpu_review.py/.json` 绑定实际冻结应用、
原生引擎、组件源码与报告、最新 CPU 来源以及进程退出。原 R1 失败保持不变。

## 非强制模型验证

`model_r1/report.json` 使用同一冻结应用 `4675f4ad` 与原生 `1f5fac9e`，
实际发生 D2H 8 层、H2D 4 层的完整模型交接。consumer 恢复与 donor 再次
恢复的自然 greedy 输出均与独立参考完全一致，最大 logprob 差都为 0。
每个请求生成 8 个 token，seed21；没有强制输出录制 token。

该验证使用 49,152-token Device / 8GB Host 的正确性夹具、16K/16K/24K
三个源前缀和受控工具等待，不能替代完整 workload 或证明 JOIN 逻辑额度
交接已经带来 GPU/JCT 收益。server 623498 已退出、GPU 无 compute context，
清理记录无剩余 owned process。`model_review.py/.json` 复核实际报告、
分组组件、冻结来源、当前 CPU 绑定及退出，生成用于后续重放注册的
`native_sources.json`。正式 FULL 前仍需新 grouped 引擎的 24 请求
position/session/native-stop 检查及 running24 的 sampling 开销检查。

## 正式完整对比

`join_blocked_layer_position_20261028_r1` 已通过 24 个请求的精确 token、
session 及 native 停止边界检查；24 个请求均观察到原生 stop。
running24 的 position 采样额外派发开销均值约 0.01036ms。
检查用进程退出后启动 `join_blocked_layer_groups_20261028_r1`，port18677，
冻结应用 `913e3238`、grouped native tree `2ce5e5389979`。

`live_start.py/.json` 独立确认实际 nice0 watcher/server/driver、唯一 owned
GPU scheduler、实际新开关和 8/4 粒度生效、工具预取及优先窗口为零。
复用 V345 工具衰减对照，保留明确的 grouped/common 引擎差异。
两边工具时长 artifact 的存放路径不同，但文件 SHA256、entries、
transformed plan、种子、公开来源、分类及离线 prior 均相同并逐项核对；
不修改已冻结的注册记录。

完整评价前仍须全部 3674 请求、18 个正常长尾完成，原停止条件一致，
两条新鲜 idle telemetry 与实际退出通过。`complete_review.py` 同时核对
精确工作量，生成 decode 时间曲线、分布和相同 batch/context 几何对比，
并保留相对上一轮 joint FULL 和 online-refresh FULL 的退化或改善。
该运行尚无完整性能结果，不能据此宣称逐层分组的独立收益。
