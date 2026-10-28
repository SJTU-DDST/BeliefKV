# 冷备份的Host冗余副本预算

完整准入前卸载实验仍只有3.91%的平均JCT及2.20%的全程输出吞吐改善。
9841次idle prepare检查中，7167次因Host预算在扫描前退出；末次Host
自由683tokens、预留5461tokens。计数不能证明历史上每次都有安全
victim，但代码中确实存在新增备份只检查空闲Host、不尝试回收冗余
副本的路径。该候选先解决这一接口限制，不宣称新的工作集调度算法。

## 机制和边界

默认关闭 `IDLE_HOST_MIRROR_RECYCLE` 及其
`BELIEFKV_IDLE_HOST_MIRROR_RECYCLE` 形式，仅Full冷备份模式可启用。
配置为 `configs/migration/predictive_idle_host_mirror_recycle_20261027.json`。
相对上一轮common environment只增加两个开关；完整请求、token、
依赖、工具时间、容量、种子及native引擎均不变。

在已有200ms后台入口内至多每秒检查一次，要求：

1. FULL-only、write-back、cache模式，原生D2H/H2D提交和ACK队列空闲；
2. 当前确有物理HBM压力，Host自由量不足原生idle reserve加一页；
3. 32个dirty节点内找到真实阻塞且尚未备份的完整所有权候选；
4. 32个Host镜像内找到足够的冗余副本，它们的Device页仍存在、由
   原生Device锁保护，且通过原生Host锁及DMA检查。

目标冷状态沿用 `_beliefkv_causal_full_donor`，核对真实session、
generation、引用和阻塞状态。普通ready/排队对象不能成为该目标。
Host victim只释放Host副本，Device值和锁保持不变；Host-only状态、
未完成传输、Host锁以及已有预取服务保护的对象不参与。

优先选择一个足够且最小的镜像，否则在窗口内合并较大的镜像；
不足以满足真实Host预算时不释放部分集合。原生副本仍以完整节点
回收，可能超出本轮所需，需在后续实验核算额外备份与churn。
不把不完整Host副本当作可用镜像。

释放前重新核对冷目标及全部镜像。使用现有原生
`_release_full_host_duplicate`、Host free及observer路径，并更新
duplicate/dirty索引。只在实际Host自由量达到原来的reserve与目标
预算后，调用原生有界read-only冷备份。新Host预算不是HBM容量，
备份入队也不是卸载；后续仍须等真实ACK及原有Device释放权限。

空Host回合会让原生dirty扫描提前返回。新探测只轮转检查过的至多
32个dirty节点，避免同一个拒绝窗口永久遮住后续冷目标；不增加
全队列或逐decode扫描。它不修改agent资格、优先级、逻辑预算、
普通预取权限、分配器或引擎代码，也不是逐层容量交接。

`idle_host_mirror_recycled`记录真实Host释放、目标、预算和零Device
释放。若释放后未能备份，单独计数，不能把丢掉镜像本身当作收益。
所有内部control和paper arm清除开关，实际构造器及最终observer
必须验证有效配置。

## 当前验证

真实native CPU树的反例中，Host已满，阻塞目标有4个未备份槽，
另一个Device锁保护的agent有8个Host镜像槽。旧路径不备份；
候选释放这8个冗余Host槽，完成原生4槽备份入队并留下4槽reserve。
两个agent的Device索引和分配器自由量均不变。D2H ACK前不能称为
物理卸载。4-token批量是明确缩小的CPU fixture，非正式512MiB配置。

保留旧代码的 `initial_red_cpu.xml`（2通过、1预期失败）及初版3项
通过报告。之后155项相关测试通过，包含已有后台备份、排队备份和
准入前工具卸载。最终单次共享回归通过3381项及4个子测试，无失败
或跳过，保留此前3331项身份并增加50项。覆盖锁、DMA、generation、
预测、工具返回、Host预算、扫描边界、退避、baseline隔离和声明
一致性；三个真实构造器验证Full启用、两种ThunderAgent对照关闭，
参考策略五个原有调度/生命周期方法继续直接继承。CPU copy/event
和Host allocator替身不证明GPU字节或性能。

同目录 `measure_cpu.py/.json` 在真实native CPU树/Device allocator上，
每档预热50次、采样500次，测量整个idle callback的thread CPU，
包括真实异步journal入队；fixture构造/销毁在计时外：

| 路径 | 中位数/us | P99/us | 最大/us |
| --- | ---: | ---: | ---: |
| 开关关闭、Host满 | 21.613 | 26.294 | 36.312 |
| 退避、Host满 | 22.965 | 29.127 | 49.614 |
| Host空闲、原生备份 | 230.504 | 337.113 | 346.183 |
| 无冷目标 | 70.037 | 174.361 | 1557.917 |
| 32个stale dirty候选 | 84.653 | 165.194 | 171.804 |
| 唯一镜像被Host锁保护 | 94.183 | 206.397 | 1529.731 |
| 实际镜像回收及冷备份 | 365.117 | 1907.773 | 1970.552 |

成功路径的550次镜像回收和550次备份journal全部保留并完成写入。
测量不包含writer后台线程CPU、GPU干扰、最大32镜像窗口或最坏共享
所有权子树，不能当作完整controller开销或JCT收益。

后续验证真实GPU原生组件，再冻结Full。
继续复用同V311完整V345，不重复native或其他baseline；完整比较须
核算JCT、全程吞吐、Host副本再备份、D2H/H2D、真正提前释放和首次
使用，以及没有复制收益的镜像回收。没有新GPU实验登记，整体目标
仍未达到。

## GPU组件准备失败

冻结fc57f6c0的第一次组件检查保留为 `gpu_components_r1.json`，在
disabled场景建立初始Hot镜像时停止，尚未执行新回收机制。夹具Host
池只有257个1024字节槽，而生产SPAWN包装器保留64MiB，因此合法
拒绝该准备动作。失败后进程退出，NVML确认没有残留GPU进程。

后续夹具使用原生有界read-only备份原语建立8槽Hot镜像，不改变生产
SPAWN保留量、候选实现、共享CPU资格或正式200GB Host配置。
必须使用新的冻结operator及新的输出路径，保留原始失败。

第二次组件检查保留为 `gpu_components_r2.json`，在disabled场景构造
runtime时拒绝：完整Full环境启用了final-stage prefetch，但夹具没有
提供所需event socket。尚未执行回收或验证恢复。进程已退出，NVML
确认没有残留GPU进程。后续夹具为每个场景建立独立临时event socket，
保持原有Full开关和生产构造器校验，不修改候选策略或原生引擎。
