# 恢复租约降本与累计 CPU 控制对照

日期：2026-10-20（北京时间）。租约修改基线为 `cb93eec`。
GPU 最早仍于 06:00 启动，没有提前加载模型或执行 GPU 测试。

## 租约修改

`_PrefetchServiceLease` 是不可变对象，改用 slots 保存字段，省去每份对象
的实例字典。期限、epoch、session 等字段与 dataclass replace 更新语义
保留。epoch/session 检查直接逐字段比较，省去每份回执的临时元组。

没有跨调用复用物理状态。每份回执继续独立读取当前期限、workflow、
invocation、context、native session generation、节点代际及各池驻留。
也没有合并 ACK、首用信用、原生锁或不同 context 的生命周期。

V2027 仅修改 slots，96 组中 78 组 CPU P50 较低，18 组回退，包含部分
完整维护路径。其原始结果保留，不能只用其中较好的样本宣传改善。
随后加入直接字段比较，V2028 重新测完整矩阵；两次绝对 CPU 计时的环境
与样本数不同，不能将所有变化精确归因为某一行代码。

## 完整维护对照

V2028 使用 24-workflow 的 CPU runtime fixture，1/2 个 context、
1/8/42 份回执、诊断开关，以及等待、READY、epoch 交接、消费请求、
首次服务、未到期短路和完整常态循环，共 96 组。
每组 201 个样本，20 次预热，新旧交替；fixture 构造、reset、状态比较、
copy/serialization 检查和真实旧 trace 读取在计时外。

96 组的完整状态、日志、回执和锁相同。每组额外核对 copy、deepcopy、
pickle 和 dataclass replace 的字段与类型保留。90 组 CPU P50 更低，
最大下降 12.22%。本轮将原始 wall/thread CPU 样本写入报告，不只存分位。

单 context、42 份回执、关闭诊断及 profiling 的线程 CPU P50：

| 路径 | 基线 us | 候选 us |
| --- | ---: | ---: |
| 等待扫描 | 107.928 | 94.763 |
| READY 扫描 | 111.495 | 102.781 |
| epoch 交接 | 103.318 | 97.799 |
| 后继消费扫描 | 279.029 | 274.243 |
| 首次服务 | 41.422 | 41.567 |
| 未到期短路 | 0.38708 | 0.38930 |
| scheduler/dispatch/completion 循环 | 115.977 | 105.269 |

六项回退全部保留：上述首次服务多 0.145us，以及五组未到期短路多约
0.33–2.47ns。短路函数体未修改，不把这些微小波动宣传成策略代价或收益。
其他 42 回执场景和 84 回执控制均包含在完整原始矩阵中。

当前 fixture 中，成熟的普通对象为 56-byte 实例加 184-byte 实例字典；
slots 实例为 144 bytes。该统计不包括 key、锁及字段引用对象，不是系统
总内存、HBM 占用或完整 allocator 压力，也不能换算吞吐提升。

CPU 双方使用相同原生标量替身，没有真实 allocator、DMA 或 GPU。
文件 writer 仅向列表记录，未测后台序列化、GIL 争用或磁盘 I/O。
首次服务只负责释放/消费现有回执，没有伪造 H2D 或复用信用。

## 此前修改的累计结果

V2025/V2026 在租约修改之前，比较 `cb93eec` 与 `51c0f1f`；
它们的来源哈希绑定各自代码，不能当作包含本次租约修改的最终整体结果。

V2025 包含 24-workflow/8-running、稀疏 session 叶子标记、numpy.float64
创建时间及尾部容量规划的 32 组 JOIN/tool 完整提交。每组 151 样本、
10 次预热，CPU P50 全部降低 1.51%–6.10%；记录、完整 expectation、
native 读取和物理目标相同。64 节点、原生预算、关闭诊断的 JOIN/tool
为 1759.415→1652.032us、1778.650→1674.430us。
depth256 仍最多提交 64 个节点。

V2026 包含 48 组 scheduler/dispatch/completion 循环和两组空 socket
控制，以及 36 项验证。循环覆盖 12/24/156 workflow、8/48 running、
0/16 pending、维护是否强制到期、0/8 工具预测和 profiling 开关。
PREPARE 开启但原期限未到，没有实际备份或模型 worker。
48 组循环的 CPU P50 均降低 4.81%–22.85%，状态和实际动作一致。
空 socket 未改代码，一组多 4.28ns，完整保留。

24-root/8-running、无 pending、关闭 profiling 的例子：

| 场景 | 基线 us | 候选 us |
| --- | ---: | ---: |
| 无预测，维护未到期 | 4.17066 | 3.30936 |
| 8 条工具预测，维护未到期 | 4.86744 | 3.75534 |
| 8 条工具预测，强制到期 | 7.53230 | 6.70210 |

上述为分开的 CPU 对象与受控场景，不是线上频率混合。各次改善比例不可
相加，也不能按旧采样计数直接估计 JCT，或宣称控制开销已经优化完成。

## 检查与启动安排

V2029 的 169 项租约相关检查通过；V2030 使用固定 V88/python 导入和真实
`python -m pytest` 入口执行 57 个文件，2278 项检查全部通过，
无 failure/error/skip。没有为这一对象存储变更新增镜像实现的测试；
完整矩阵保留所有代际、驻留、过期、handoff、首用和锁的原有检查。

提交 main 后更新同一个一次性等待进程的合格源码及冻结 checkout。
最早在北京时间 2026-10-20 06:00 核对 GPU 空闲后运行 predictive，
随后 native；24-root、16+8/900s、running8、256K、Host80:20、V88 引擎
与补丁均保持。新实验仍需检查有用 Host 副本损失、真实恢复/消费等待、
去长尾吞吐和平均 JCT，当前目标继续 active。

- [V2028 最终租约完整对照与原始样本](v2028_compact_lease_live_checks_cpu_20261020.json)
- [V2027 slots 初版](v2027_compact_active_lease_cpu_20261020.json)
- [V2029 租约检查](v2029_compact_lease_live_checks_20261020.xml)
- [V2030 扩大检查](v2030_compact_lease_qualified_checks_20261020.xml)
- [V2025 此前完整提交累计对照](v2025_accumulated_wait_dispatch_cpu_20261020.json)
- [V2026 此前常态循环累计对照](v2026_accumulated_recurring_control_cpu_20261020.json)
