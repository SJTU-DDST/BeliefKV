# Sessionless Full 的原生前缀迁移

2026-10-25，开发候选；真实 GPU 组件、完整模型和性能验收均未完成。
既有冻结实验及其参数没有改动。

## 原因与边界

AgentX 请求不使用 native session。Full 的旧备份/恢复入口依赖 session
anchor，不能把这些入口未执行的运行叫作完整 KV 管理。新候选用实际
请求中已经观察到的 token prefix、workflow/context/epoch、namespace 和
native 节点代际建立所有权，不创建假 session，也不读未来的最后一次调用。

复用 `NativePrefixTransfers` 的原生分配、锁、事件、节点分裂和独立
receipt ledger。Full 自己的 adapter 接收登记、真实准入、完成、重排、
取消及已过滤的运行时事件。它不引用 KVFlow 的距离策略或 TokenCake
的调度策略。独立开关默认为关闭，内部控制组强制关闭，论文 baseline
不继承这条路径。

## 资格与传输

后台 SPAWN 只把 parent 的已发布前缀加入增量备份队列，parent 可以继续
执行。每轮最多检查四个 delegator，一次最多新增 4 GiB Host 副本。
预算小于单个 native extent 时，按已观察 key 在原生树上拆分边界，再
提交受预算限制的备份。已有 Host 副本不重复传输，不主动回收 Host。

普通维护每 200ms 轮转检查最多十六个 context，冷备份批次上限为
128 MiB。真实 JOIN/同步 child 阻塞，以及已等待至少 1s 或已有有效
长工具预测的工具等待，可以备份并释放已确认的副本。D2H 提交并不释放
HBM，必须等 native ACK；运行锁、在途传输和原生可淘汰叶条件保持。
另有增量所有权索引跟随 native 节点分裂，某页仍属于就绪/排队 owner
时禁止主动卸载，避免只检查等待 parent 就释放共享 child 的前缀。

缺失 JOIN 通知不能被补成 `WAIT_JOIN`。但请求完成后没有下一次请求，
是一个实际可观察的状态。只有没有合格计算可推进、确有未获资格的请求
排队、原 owner 已无请求至少 1s 且没有恢复保护时，才允许它出借逻辑
资格。记录为 `observed_idle`，不改变 RCCG 的 READY 状态，也不授予
JOIN 优先级。新请求到来沿用普通 agent arrive 流程。该规则属于新的
Full 候选，不改变 ThunderAgent paper arm；有其他合格计算时保持连续性。
资格借用先在同一低频轮次检查现有资格集合，借出 owner 最多另检查十六个；
这与十六个冷页 owner 检查分别计数，不把两者合称为总共十六次。

真实 JOIN 确认，或真实 native no-tool EOS 对应已观察 ALL-JOIN 的唯一
剩余成员时，才为 parent 建立有界恢复候选。恢复先检查完整缺失前缀与
已准入但未分配的输入、当前 decode 增长预留，再向同一 agent 申请
恢复资格。没有容量时不激活额外资格，不为了该请求驱逐就绪 peer。
恢复 ACK 后由该 agent 的实际 native admission 消费原有资格预留。
普通请求没有新恢复 ticket，继续使用原生 HiCache 需求恢复。

## AgentX 的能力限制

推断 JOIN 被隔离后，当前 AgentX 无法提供真实 parent 等待边界；固定
输出长度也不提供原始 child EOS。因而上述 JOIN/EOS 恢复正向路径仅能
由独立受控事件验证，不能在 AgentX 中伪造触发。该 workload 可验证的
收益主要来自 agent 资格、观测空闲借用、SPAWN 增量备份、外部等待与
原生需求恢复的配合。不能用零 JOIN 预取量冒充该功能已经取得收益。

目前工具恢复模式为 execution，未知外部间隔不能直接送入旧语义预测器。
工具结束前的预测式 H2D 不属于这份声明已经验证的能力。

## 验证要求

CPU 检查使用真实原生树、分配器、策略与 receipt 对账，GPU copy/event
采用 stand-in。64-slot 小池显式把 100-token serving buffer 缩为 1，
只用于组件场景，不能成为正式 benchmark 的默认值。

后续须在空闲 GPU 上验证全部层的 K/V 字节、释放页写坏后的恢复、
增量拆分、真实 ACK、忙碌 producer、共享 owner、Host 压力及真实请求
准入。组件通过后仍需完整模型验证和相同完整工作量的 JCT/吞吐重复
对照。保持当前四 baseline 加 Full 的用户顺序，完成该周期之后再降低
同时准入的 workflow 数，计入其准入等待。

最新共用 CPU 报告：
`experiments/reports/full_sessionless_prefix_all_baselines_cpu_20261025.xml`，
961 项通过，无跳过，含本地 serving tokenizer 检查。新增组件覆盖 24 项：
增量备份与批次拆分、ACK、共享 owner、真实 JOIN/EOS 与多 child、推断
JOIN 拒绝、无普通恢复 ticket、原生输入预留、重排、namespace 变化、
物理回执失败和低频维护。首次失败来自小池保留了 100-token serving
buffer，初始报告保留；只缩放 CPU fixture 的 buffer，没有修改正式策略。
`full_sessionless_prefix_lifecycle_retry_cpu_20261025.xml` 和
`full_sessionless_prefix_lifecycle_edge_cpu_20261025.xml` 保留中间验证。

## 原生 GPU 组件检查

新增 `scripts/qualify_full_prefix_migration.py`，使用两层真实 K/V、
128 个 Device slots、257 个 Host slots、原生 Req、PrefillAdder 和缓存
释放路径。检查十种场景：受预算限制的增量备份、未启动的共享 owner、
D2H 中提前返回、真实 JOIN 确认恢复、受控 native stop、length stop
拒绝语义 EOS、producer 忙碌、Host 空间耗尽后重试、已准入但尚未分配
的 chunk 预留，以及推断 JOIN 不触发物理操作。

恢复前将全部空闲 Device slots 写坏，再逐层比较恢复的 K/V。实际准入时
另记恢复资格的消费，不能用 workflow 结束时清空状态代替该证据。
最终核对所有请求 row、Device slots、native 锁和 receipt。受控 stop
是组件输入，不是从 AgentX 恢复出的原始 EOS；本入口不执行模型 forward。

同一套场景函数先经 CPU stand-in 运行，且损坏字节、缺少 ACK、残留容量、
改变源码/前驱身份和扩大报告范围均有反向检查。共用回归 1011 项通过，
无跳过，报告为
`experiments/reports/full_prefix_gpu_supervisor_all_baselines_cpu_20261025.xml`。
首轮 CPU 失败来自清理路径调用 write-back 参数时 stand-in 未转发该参数；
原报告保留，修复仅涉及 CPU controller 适配。

V227 已冻结登记：
`experiments/raw/full_native_prefix_v227_20261025/registration.json`。
监督进程 PID 3071982、start ticks `715994041`；应用 commit
`e53af6c6f493b66dcdfc7381c0dea9c8be7aa746`。它排在 V226b 真实退出和
完整清理之后，随后才检查 GPU 资源并启动子进程。等待中没有 GPU 子进程。
通过前不增加“真实 GPU 已验证”标记，通过后仍不自动升级为模型或性能结果。

## 真实模型输出验证候选

新增 `scripts/qualify_full_native_model.py` 和独立声明
`configs/migration/full_native_model_qualification_20261025.json`。
该入口在自己的冻结源码上重新执行上述十个 GPU 组件场景，再启动真实
Qwen3-Coder 模型。保持共同 805198 Device / 2034506 Host token slots、
running/decode24 和 seed21；不缩小正式容量来人为触发迁移。

从同一发布 token 包中按固定规则选择最短的 64K--128K 输入，不使用其
最终输出长度。当前选择 65600-token 前缀，对应约 6 GiB FULL KV。
验证中的六个请求分别为 parent 冷启动、驻留重复、工具 child、
parent 需求恢复、JOIN child、parent JOIN 恢复。child 使用该输入的
128-token 前段及独立 namespace。

验证请求每次真实生成八个 token，使用 greedy 与 seed21；关闭 replay
plugin/bridge，不提交 custom processor、custom parameters 或预定输出。
比较 parent 四次实际 token ID，并检查输出 logprob 的最大绝对差不超过
事前声明的 0.05。若不满足则保留失败，不在结果出现后放宽条件。
该检查证明的是这些有界输入的模型行为，不是所有输入的数值等价证明。

物理证据同时要求：

- 工具等待后确实产生 D2H ACK 和 Device demotion；需求恢复请求实际命中
  几乎完整的 Host 前缀，不能以重算后输出一致代替迁移。
- 受控 JOIN 返回后产生 H2D，并在 parent 提交前完成实际 native receipts；
  parent 随后实际命中 Device 前缀。
- 恢复预留由同一个 agent 的真实 PrefillAdder 准入消费。新增
  `native_restore_consumers` 在有效预留被真实准入消费时计数；
  workflow 收尾清空记录不产生这个证据。
- 最终全部六个请求、context 和 native receipts 收尾，采集两次新的
  空闲遥测，再清理自有进程。监督进程持续保存子进程身份以处理异常退出。

工具与 JOIN 是显式组件控制输入，不是从 AgentX 推断出的真实工具、
等待边界或语义 EOS。原始发布工作量没有变化；这六个请求单独标为
qualification，不计入任何论文方法的 JCT 或吞吐。

CPU 首次报告 `full_native_model_targeted_cpu_20261025.xml` 保留了 147 项
通过和一项失败：不完整登记的失败收尾再次索引缺失字段，掩盖原始错误。
修复后 `full_native_model_targeted_retry_cpu_20261025.xml` 的 154 项检查
全部通过。通信顺序使用真实 RCCG 和 HTTP/event stand-in，实际 GPU
模型输出仍需队列执行，不能把这些 CPU 响应当作模型数值证据。

共用报告 `full_native_model_all_baselines_cpu_20261025.xml` 的 1040 项
检查全部通过，无跳过。V228 已独立冻结登记至
`experiments/raw/full_native_model_v228_20261025/registration.json`，
应用 commit `37318e5a843303ea9c175528f169d80e0a712132`，监督进程
PID 3084149、start ticks `716232632`，正常优先级 NI=0，端口 18641。
它在 V227 实际退出和完整清理后才检查 GPU 并重新验证组件及模型。
冻结 checkout 中的源码清单、人口、前驱身份和无强制输出环境均已复核。
当前监督进程存活、等待前驱、`owned=[]`，尚无 GPU 模型结果。
