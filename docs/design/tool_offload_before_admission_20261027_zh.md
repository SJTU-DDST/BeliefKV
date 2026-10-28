# 原生分配前的预测工具卸载

本候选修复已有预测卸载入口的时序，不提出新的工作集切换算法，也
不是逐层容量交接。继续以完整平均 JCT 和全程输出吞吐共同验收。

## 已有证据与缺口

完整 continuation R1 只有3.97788%的平均 JCT 改善及1.14342%的全程
输出吞吐改善。独立预测提前卸载零字节，分配压力下的工具等待页
释放2.006188GB，二者不是同一路径。3800次没有合格等待消费者的
记录不能证明历史上从未出现可利用的请求；当时主要检查位于 forward
提交后，当前批次的请求已经从 waiting queue 中移走。

当前 native scheduler 的顺序为：

1. runtime 维护及实际 agent 资格；
2. 建立 `PrefillAdder`，生成并验证请求计划；
3. 请求匹配、KV 分配/恢复和当前批次构造；
4. forward 提交；
5. 检查剩余 waiting queue 中的预测卸载消费者。

因此，已经获得资格、即将恢复的请求可能在第3步进入需求恢复，而
第5步的独立预测卸载器只看到剩余队列。这个入口缺口是代码与 CPU
真实 cache 反例证明的可能性，尚不是完整历史根因或性能归因。

反例使用实际 native FULL-only radix tree 和64-slot allocator，
Host索引、copy/event使用CPU替身。阻塞 donor 有32个已备份页，
待恢复消费者缺16页，只有4页空闲。先前维护未见消费者并设置退避；
消费者随后得到原始 agent 资格。旧规划入口未释放 donor；
候选在当前请求分配之前释放已备份的32页，真实空闲页增至36。
随后 native load 可申请16页，不改动 ready peer 的索引。

## 实现

默认关闭 `TOOL_OFFLOAD_BEFORE_ADMISSION` 及
`BELIEFKV_TOOL_OFFLOAD_BEFORE_ADMISSION`，仅用于 Full 的 reference
agent 核心和已启用的 `demand_cost` 预测卸载。
独立声明为
`configs/migration/predictive_tool_offload_before_admission_20261027.json`。
它与上一轮的 common environment 仅相差这两个开关；请求、依赖、
输出、停止条件、V311时间映射、种子、容量及引擎均不变。

`plan_native_prefill` 先复用原有规划，再用已有选择器得到当前请求
顺序，随后在 native 分配前尝试原有 offloader。保留未标记请求的
位置和 JOIN 优先关系。优先检查实际计划前部，沿用8个消费者、
8个 donor、每个 donor 至多16个节点的边界；不为增加覆盖率轮换
ready agent，不授予任何逻辑资格。

已耗尽 chunk/input 预算、没有当前准入槽位及空计划均跳过这个入口。
原生 `PrefillAdder` 的 `rem_total_tokens` 和 `cur_rem_tokens` 动态读取
真实 allocator 与 evictable 计数，释放后的页可由原生再次检查，
不需要虚构即将释放的容量，也不修改 allocator 或请求空间预算。

每个当前 request/context/epoch/attempt/session identity 可以在首次
出现时重新唤醒一次检查，避免刚结束的空维护退避掩盖新请求。
已见身份记录最多512个；同一持续等待请求保持原200ms退避，没有
新增周期计时器或空decode回调扫描。重新唤醒不代表允许卸载；
实际缺失Host前缀、当前物理短缺和正常agent资格仍须通过。

预测置信度、剩余时长动态成本、Host ACK、独占页、native锁、
session generation、真实工具阻塞及恢复保护均保留。缺失备份只能
异步准备，不能当作已经腾出的空间。普通 ready/排队 agent 不能
成为 donor。原维护入口仍保留，且其队列、running batch、轮转位置
及阶段标记在本次局部检查后恢复。

offloader 可以在安全重检时接收工具返回或取消事件；语义版本变化后
只重新生成一次当前计划，不再次卸载或解释旧计划为准入许可。
实际动作日志新增 `dispatch_stage=before_native_admission`，维护阶段
保持 `maintenance`。CPU预检查和最终实际 runtime 配置核对均验证
新开关；所有内部 control 和外部 paper arm 清零，历史对照缺字段
只允许解释为默认关闭。

## 验证边界

`initial_red_cpu.xml` 保留旧代码的一项通过/一项预期失败。
初版扩展测试另有一次构造器夹具漏传事件 socket，保留
`expanded_cpu.xml`；修正的是测试入口，未改变事件协议。
后续 `targeted_cpu.xml` 的379项相关测试通过。完整共享回归、
源绑定、三种真实构造器及 CPU 代价结果另行记录，不能由这个定向
结果推导GPU数值或性能资格。

测试覆盖工具返回/消费者取消、语义重规划、缺备份、锁、短预测、
无物理压力、失效身份、inactive消费者、pending/ready donor、
无准入余量、相同请求退避、异常后的作用域恢复，以及真实 native
adder和分配器读取释放后的页。仅使用CPU copy/event替身，未完成
真实GPU或完整模型的候选比较。

## 完整比较

只运行本候选 Full，继续复用同 V311 时间、同 native 引擎及相同
完整工作的 V345 工具衰减版 ThunderAgent；同时与上一轮 Full 对比。
先检查实际动作阶段、当前消费者与后续真实原生服务的绑定，再检查
完整 JCT、吞吐、D2H/H2D、旧输入缺失、重复迁移和控制开销。

不能把额外释放字节本身作为收益，也不能把原生压力下的释放改名为
预测提前卸载。若本入口仍没有实际动作，保留零结果并继续检查机制
机会，而不是仅降低置信门槛或制造 ready-agent 迁移。只有清晰、
可重复地改善两项主要指标后，再执行其他适用 baseline。

逐层A卸载、B接管空间并开始计算仍未实现；本候选不提供这项证据。

## 完整 CPU 资格

`qualified_shared_cpu.xml` 在一次执行中通过3331项及4个子测试，
无失败或跳过，保留前一轮3291项身份并增加40项。
`qualified_shared_cpu.sources.json` 与 `.evidence.json` 绑定配置、源代码、
测试、模型tokenizer文件及三个真实构造器。Full新开关为真，原始
ThunderAgent和工具衰减版均为假；参考核心的attach、tick、completed、
aborted及流式增长方法仍直接继承。

首轮 `shared_cpu.xml` 中3328项及4个子测试通过，一项新增对照观测器
测试漏了source请求数/完成计数字段；随后补齐夹具并保留原失败。
`empty_source_red_cpu.xml` 另保留两个无预测源/物理控制关闭时仍调用
新检查的反例；新增快速返回后，无工具预测或空计划不扫描候选。

使用真实64-slot native CPU cache的单消费者夹具，每档预热100次、
采样500次，测量整次plan回调的thread CPU：

| 路径 | 中位数/us | P99/us | 最大/us |
| --- | ---: | ---: | ---: |
| 新开关关闭 | 7.037 | 9.449 | 10.468 |
| 无工具预测源 | 7.149 | 9.123 | 9.573 |
| 同一请求，维护仍在退避 | 20.051 | 22.832 | 26.345 |
| 到期安全检查，donor被锁拒绝 | 226.538 | 237.580 | 3835.282 |
| 新请求重新唤醒检查 | 227.266 | 235.090 | 260.093 |
| 空计划 | 4.236 | 5.474 | 6.743 |

完整结果在同目录 `measure_cpu.py/.json`。它未测量成功释放、异步日志、
最大八消费者/八donor窗口、GPU干扰或JCT；为重复测量，在计时外重设
维护时间或已见身份，并冻结policy monotonic时间。初版测量和原始
代码单独保留为 `initial_measure_cpu.py/.json`，不能当作最终源码测量。

这项资格仍是CPU原生树/allocator加copy/event替身。下一步冻结当前
源码，只运行新的完整Full并复用V345；必须观察真实GPU上是否发生
准入前动作、是否被对应消费者使用，以及完整性能是否改善，不能
以CPU通过宣称全局目标完成。
