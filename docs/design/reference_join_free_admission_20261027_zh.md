# JOIN恢复使用空闲逻辑预算的集成候选

本候选只修复reference FULL中JOIN预取与agent准入的一个衔接限制，
不改变ThunderAgent paper arm，也不作为独立调度算法贡献。
配置为`configs/migration/predictive_reference_join_free_20261027.json`，
显式开启`BELIEFKV_AGENT_JOIN_FREE_ADMISSION=1`；默认关闭。

## 实测依据

完整R1在相同3674请求、18个项目、V311工具时间与全部token上，
平均JCT比完整V345低3.3184%，全程输出吞吐高1.5529%。
正常长尾保留，最长后续ready输出间隔的均值变差。
完整533个排队至少1秒的工具后继中，94.7951%的累计等待属于
inactive采样持有区间；这不是精确转换日志或可删除JCT。

R1的56个JOIN在完成前有正FULL计划，只有7个开始提前传输并确认复用，
共36.369GB。reference模式原来只允许已经active的owner恢复，
即使存在完整空闲逻辑预算，也不能在真实WAIT_JOIN期间将parent接纳
回来。此限制值得独立检验；这些统计没有证明每次拒绝都可恢复或可获益。

## 授权规则

`ReferenceThunderAdmission.claim_restore()`仍先检查已有活跃资格。
新开关仅在`source=join_ticket`且原检查不通过时尝试一次接纳：

- key必须等于当前`latest`，workflow/context/session/generation/
  epoch/attempt均不变；owner存在且未终止。
- parent必须真实处于WAIT_JOIN，没有并行工具等待或额外blocking child；
  对应JOIN属于同一workflow、尚未满足、仍登记该waiter且没有释放它。
- parent没有已提交模型请求，也没有原生参考策略的pause标记。
- 完整上下文与原有100-token增长buffer必须满足
  `parent.tokens + buffer <= policy.remaining_capacity()`。
  使用未衰减的空闲预算，不能使用ACTING的折扣预算。
- 接纳只将该parent设为active。没有暂停peer、抢占ready请求、
  借出/召回关系、恢复TTL或新的普通请求预取。

工具预取仍要求原来已有active资格；普通请求仍由native demand恢复。
新接纳的parent随后遵循同一参考策略的增长、压力暂停、完成和退休规则。
重复授权不会重复计费，恢复请求也不获得额外永久物理保护。

逻辑接纳不等于物理恢复成功。已有native空闲页、Host备份、session引用、
锁及DMA检查仍然拥有物理决定权；空间不足时拒绝复制。
若接纳后并未复制，parent可能暂占逻辑预算，必须在GPU实验中统计这种
接纳的实际提交、消费和后续压力行为，不能将计数器当作预取收益。
本候选没有实现逐层空间交接，也未增加sibling特殊共同准入。

## 验证与边界

`tests/test_reference_join_free_admission.py`与原reference检查合计55项
通过，包含：

- 真实JOIN空闲预算接纳、重复授权及peer状态保持；
- ACTING折扣容量足够但完整预算不足时拒绝；
- tool/ordinary来源、已释放JOIN、已就绪parent、pending模型请求、
  pause标记及过期身份拒绝；
- Full构造、声明验证及paper/control隔离；
- 对新旧配置逐项比较，确认只有显式开关不同；
- 真实native tree/allocator下，授权本身不分配物理页，空间不足仍拒绝；
  成功路径保留Host副本，等待ACK后解除native锁。

native CPU测试明确使用copy/event/Host-index替身，不是GPU或性能验证。
初轮`targeted_cpu.xml`和第二轮`targeted_cpu_retry.xml`各有一个新夹具
失败：前者的peer没有实际active，后者的首轮parent被原参考排序优先
接纳；夹具最终使用真实的两轮parent continuation后通过。
两个失败报告保留，不修改paper排序或原有算法以适配测试。

`claim_cpu_cost.py/.json`使用33个program的固定CPU夹具，每种路径
采样2000次。已有active路径thread CPU中位数1.695微秒；
新空闲预算授权8.3925微秒、P99为10.391微秒；
完整预算不足拒绝8.5365微秒、P99为10.500微秒。
这个结果只度量授权函数，不是整个控制器、GPU干扰或JCT的证据。

一次共享执行随后通过3143项及4个子测试，无失败或跳过，
保留此前3039项身份并新增104项。三种实际构造器（本候选FULL、
默认ThunderAgent及工具衰减ThunderAgent）均通过配置隔离检查，
参考attach/tick/completed/aborted/streaming-progress仍直接继承。
证据为`experiments/reports/reference_join_free_admission_20261027/`
下`shared_cpu.xml`、`shared_cpu.sources.json`及`shared_cpu.evidence.json`。

共享CPU验证通过后才可冻结新的FULL运行，继续复用完整V345，
保持相同全部请求/token/停止、工具时间、容量、模型、种子及batch。
完整JCT和全程输出吞吐的明确、可重复收益仍是后续baseline门槛。
