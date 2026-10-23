# 休眠状态规划降本与源采集容量修复

日期：2026-10-23，北京时间。修改位于 main；未取得新 workload 的完整 GPU 结果。

## 规划中的重复汇总

对 512 个休眠 context、每个 33 个 extent 的已训练选择策略做 CPU
profiling，重复汇总私有字节、FULL token 和未备份字节占主要开销。
这些值来自 `inspect_dormant_extents` 已产生的不可变快照。
旧实现每次重新规划都扫描同一快照三次。

当前只在已训练的 selective 评分时，单次扫描生成三个标量，并以
快照 tuple 的对象身份复用。更新驻留、Host 副本、独占性、节点 generation、
token 数或大小会替换快照，下一次评分重新计算。概率、特征、预算、
排序、物理容量去重、原生锁与安全回收检查保持现有逻辑。
物理释放后清除缓存，逻辑 handle 与复用标签仍保留。

不缓存逐节点身份列表。第一版同时缓存身份列表，重复规划虽然更快，
却导致全部新快照的首次规划明显变慢，512 状态冷启动约为原先 2.47 倍。
已移除该实现；负结果及全部样本保留在
`experiments/reports/v3135_dormant_geometry_comparison_cpu_20261023.json`。

## 对照证据

`scripts/benchmark_dormant_geometry_cpu.py` 从提交
`80b48c24fdf1253289a3c22e62faad9de173a965` 载入原始实现。
36/128/512 contexts 分别使用 9/17/33 extents，区分未训练/已训练，
以及全部首次规划、固定快照重复规划、每轮刷新四个状态三种情况。
每组 101 次，交替先后顺序，正常 GC，保留全部 wall/thread CPU 样本。
全部 18 组逐次核验 tier、概率、分数、容量、待处理顺序和计数完全一致。

已训练且快照稳定时的局部规划 P50：

| 休眠 contexts | 原实现 | 当前实现 | 变化 |
| --- | ---: | ---: | ---: |
| 36 | 0.294 ms | 0.239 ms | -18.63% |
| 128 | 1.131 ms | 0.819 ms | -27.59% |
| 512 | 6.407 ms | 4.320 ms | -32.57% |

每轮刷新四个状态时分别下降 15.00%、26.03%、31.84%。
首次规划与未训练分支的变化约在 -1.13% 至 +1.45% 内，微小负结果
同样保留。512 个状态仍有约 4.3ms 的局部规划耗时，不能宣称控制
开销已经消失。报告不包含原生路径观察、DMA 或完整调度周期，
也不能换算成 GPU 吞吐或 JCT 加速比。

最终样本：
`experiments/reports/v3136_dormant_scalar_geometry_comparison_cpu_20261023.json`。
`v3138_dormant_scalar_geometry_qualified_cpu_20261023.xml` 完整保留
5162 项并通过 5212 项和 2 个 subtest。后续合并容量修复的
`v3140_dormant_geometry_native_capacity_qualified_cpu_20261023.xml`
通过 5228 项和 2 个 subtest，无失败或 skip。
新增用例覆盖各快照字段变化、共享节点 generation、实际物理释放、
原生锁及 ACK 依赖，以及采集容量的传入和核验。

## V134b 启动失败与修复

V134b 在北京时间 19:07 左右因 prefill CUDA graph 初始化 OOM 退出，
尚未进入 workload。日志中的池大小是 FULL 2,002,914 token、
Mamba 571 槽，池分配后可用显存约 1.02 GiB。

V128/V133b 成功 native 的容量 census 相同：FULL 1,797,968 token、
Mamba 513 槽，设备 FULL 36,822,405,120 字节、
Mamba 33,096,007,680 字节。V133b 池分配后可用约 8.41 GiB，
prefill graph 成功，graph 本身约占 1.26 GiB。
采集和重放的自动定容得到不同结果；仅配置相同的 0.94 比例不能保证
实际池大小相同。当前证据不能将差异全部归因于某个单独开关。

新采集直接从完成的参考 native census 读取容量，显式传入
`--max-total-tokens` 和 `--max-mamba-cache-size`，记录 census 的摘要与
完整预期容量，并在观察和收尾时核验实际池。缺失、不完整、不合理或
变化的容量不能通过注册或收集。等待资源成功时也更新实际资源快照，
避免保留上一次“GPU 忙”的状态而误报启动时的资源情况。

保留 graph、任务、batch、seed、Host pool 和上下文配置；
这项修复是恢复参考实验的实测容量，并非通过两侧使用不同容量获得收益。
源采集使用 native 策略，尚不属于保留策略的性能组。
旧 V134/V134b 失败产物保留；V134c 使用最新合格源码重新排队。
单次预约 guard 已登记 V134c，时间仍为 20:00 至次日 06:00。
当前目标保持 active，完整源、重放 smoke 和同工作量对照仍待完成。
