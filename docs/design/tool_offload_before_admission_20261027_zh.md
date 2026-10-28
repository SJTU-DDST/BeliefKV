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

## 实际启动

源码已提交为 `716ef9a3e4adc45da086b55a14f096490ca5086b`，
本轮登记为
`ops/tool_offload_before_admission_20261027_r1.registration.json`。
native树仍为11b84f7c3198，端口18665，socket为
`/tmp/bkv-causal-18665.sock`。前一轮和V345已确认实际退出，最新
预约、空闲资源、端口、726项native源码绑定及3331项共享CPU证据
检查通过后，只启动本轮Full；没有重复native或ThunderAgent。

同目录 `live_start.py/.json` 核对冻结源码及13项共同工作/工具时间/
容量/引擎/服务字段与V345相同。实际nice0进程为：

- watcher363193/start732042783；
- server363623/start732045344；
- GPU scheduler363990/start732046759，与NVML唯一GPU进程相同；
- driver365023/start732060155。

nice19、CUDA隐藏的CPU绘图等待器为364601/start732051797，
显式使用专门的plotting Python，只在完整成功和实际退出后绘图；
不会启动GPU。其状态为
`ops/tool_offload_before_admission_20261027_r1.plots.state.json`。

启动观察封存69个精确完成请求，新开关实际为真、独立demand-cost
offloader启用、物理控制健康。该独立片段中，一次准入前检查及一次
新请求重新唤醒已经执行，但额外提前释放仍为零。这不是完整工作、
收益或可利用机会为零的证明，跨日志封存也不是原子快照。
保留冻结源码和全部原停止/尾部；没有后续GPU实验排队。

## 准入前释放与后续服务

独立的 `collecting_early_service.py/.json` 封存1731个精确完成请求。
该片段中有21次 `before_native_admission` 原生节点释放，累计
231309312字节，关联21个不同消费者。21个消费者均能以原生
request/context/epoch、session及clock domain绑定到释放后的实际服务
和完成receipt。对应请求在释放前已经提交，没有观察到首次服务早于
关联释放的矛盾。这证明新入口确实执行，而非仅开关启用。

该日志没有记录释放时消费者的完整attempt/session身份，也没有把
释放的物理索引绑定到消费者的分配，因此这两项证据仍为未知。
累计释放字节不是同时可用容量；run-batch入口不是CUDA kernel启动，
不能由释放到服务的间隔推算隐藏传输或可消除JCT。各文件独立封存，
该片段不是原子快照、完整工作量或性能验收；保留全部后续普通长尾。

## 完整审计准备

同目录 `complete_review.py` 要求本轮和V345实际退出、原始停止与完整
请求/token一致、两次新鲜空闲telemetry、共同配置的完整图表，以及
迁移/JCT路径/采样层等待/工具重入/准入等待/容量/资格消费/EOS反例
和触发生命周期的独立审计。原始source graph只在其登记路径单独授权；
运行arm的完整封存仍不能引用其他arm。

新增提前释放审计保留完整消费者状态和未知身份字段；
`complete_offload_paths.py` 再将准入前、维护阶段的预测释放，与原生
分配压力释放及Host备份分开核对。两个operator尚未获得本轮完整输入，
准备好不代表已通过完整审计。

`review_precheck.py/.json` 已实际通过14项CPU边界检查，覆盖真实已完成
零释放arm、正常消费、缺服务/epoch/clock/session证据、服务早于释放、
提交晚于释放、错误字节/单位/上下文、重复submit和截断/跨arm封存。
这些检查只约束离线审计，不是新的运行时、GPU或性能资格。

## 完整运行结论

本轮已完成3674个精确请求、145565966输入token、549671输出token
和18个正常workflow尾部。原始停止、两次新鲜空闲telemetry、共同
配置/工作量、实际进程退出、全部独立完整审计及三张图表检查通过。
平均JCT1631.194643秒，对照V3451697.583645秒，改善3.91079%；
全程输出238.499008token/s，对照233.371801，改善2.19701%。
17个workflow更快，web_social慢78.679925秒；后续ready输出间隔
均值也更差。尚未建立明确、可重复的双指标优势。

实际准入前释放41次、392724480字节，关联41个已完成原生消费者；
释放时完整attempt及物理索引分配仍未知。独立压力路径JOIN释放
108.957303GB、工具等待释放6.181945GB，不能混算为预测提前卸载。
455个JOIN拷贝83.901383GB全部复用，其中5个JOIN提前复用
22.711566GB；工具预取拷贝仍为零。277个授予后程序服务全部验证，
四个工具EOS反例与55个触发生命周期通过。

本轮相对上一轮Full的平均JCT略差0.06986%、全程吞吐好1.04168%，
不是重复试验或模块因果收益证明。完整数据和后续边界见
[完整结果](../research/tool_offload_before_admission_complete_20261027_zh.md)。
继续复用V345，保留负结果，不启动其他baseline或消融。
逐层容量交接仍未实现，没有新GPU任务排队。
