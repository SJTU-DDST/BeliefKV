# 预取提交归因的批内祖先读取复用

日期：2026-10-21，北京时间。

## 证据与修改

V2619 的完整正传输 CPU 校准中，64 节点提交会调用 64 次
`_node_key_path`，同一链上的公共祖先被重复读取。
最新完成 Full 仍为 V109；其 `wait_prefetch_plan` 的 submitted/submitted_partial
记录共有 74 条 JOIN、53 条工具记录，其中 29/36 条分别只有一个计划节点。
这些是提交计划记录，不是独立 JOIN/tool episode，也不是 ACK 字节数。
因此不能只优化最大批次而增加常见小批次的控制成本。

修改只在原生批量 enqueue 已完成之后的同步 issue-record 循环内共享
前缀长度与深度索引。该循环不 drain 事件、不修改原生树；退出或异常时
释放临时索引。没有跨调度迭代保留祖先缓存，没有复制 token 数组。
保留原生节点/creation time/分配身份、4096 层边界、ACK 和首次服务证明。
开启 token 级诊断时仍采集原始 key 路径与独立快照。

以实际原生返回的成功提交数量决定是否建索引；不按计划大小判断。
实际数量不超过 16 时保留原路径。这个 CPU 快路径阈值来自本次摊销对照，
不是预取覆盖或容量上限，也不能声称适合所有硬件和路径形状。

## CPU 对照

V2632 对比 main 的 `fa68711` 与候选：36 个合成 workflow、running 8，
1/4/16/32/64 节点、JOIN/tool、记录开关与两种容量模式，共 40 个 case。
每个 case 预热 10 轮，再交替计时 101 轮，保留正常 GC 和全部样本。
比较了所有物理目标、完整 ledger expectation、pending credit 及归因记录。
下表选用开启运行时记录、native_next_prefill 容量模式，单位为 thread CPU us。

| 提交 | 节点数 | 原版本 P50 | 候选 P50 |
|---|---:|---:|---:|
| JOIN | 1 | 232.438 | 234.413 |
| JOIN | 4 | 365.424 | 371.497 |
| JOIN | 16 | 777.511 | 781.033 |
| JOIN | 32 | 1307.389 | 1277.394 |
| JOIN | 64 | 2520.686 | 2303.606 |
| tool | 1 | 321.991 | 325.574 |
| tool | 4 | 463.612 | 465.519 |
| tool | 16 | 893.238 | 894.392 |
| tool | 32 | 1419.985 | 1394.921 |
| tool | 64 | 2651.920 | 2464.532 |

该表中 32 节点约改善 1.8%-2.3%，64 节点约改善 7.1%-8.6%。
小批次仍有约 0.1%-1.7% 的 P50 退化，不能称为完全零成本。
全 40 个 case 的物理目标、pending credit、记录与完整 expectation 均一致。
第一次无小批后备的 V2629 对照和中间版 V2630 均保留，不能用它们冒充
最终候选源码的计时。

CPU 测量使用真实规划/注册代码与标量原生 enqueue 替身，不含真实 allocator、
DMA、ACK、writer 竞争或 GPU 工作；不能外推吞吐或 JCT。
保留这项有界局部优化，不把它宣称为 Full 落后简单 JOIN baseline 的完整解法。

## 验证与实验边界

新增回归覆盖首次服务在 ACK 前后、原生分配/代际变化、不完整前缀、
分支与逆序、跨批 reparent/key 变化、深度上限、循环、异常清理和部分提交。
V2633 保留此前全部 3550 项并新增 84 项，3634 项通过、无跳过，另有
2 个 subtest；明确 deselect 1 个原资格范围外的 Docker 沙箱测试。
完整 CPU 资格以 V2633 XML 与 V2634 源码映射为准。
V2631 是缺少显式引擎/tokenizer 绑定的诊断运行，保留其跳过项；
Docker 沙箱用例不属于本轮 CPU 资格，不能把跳过改成通过。

引擎仍为 `53335e6`。本次未重跑其独立 405 项检查，不改变调度政策、
workload、容量、batch、seed、保护开关或 GPU 预约。
2026-10-22 06:00 前不启动 GPU 实验；最新冻结登记由 V115 接替。

证据：

- `experiments/reports/v2629_scoped_prefetch_dispatch_cpu_20261021.json`
- `experiments/reports/v2630_scoped_prefetch_dispatch_guarded_cpu_20261021.json`
- `experiments/reports/v2632_scoped_prefetch_final_cpu_20261021.json`
- `experiments/reports/v2633_scoped_prefetch_final_qualified_cpu_20261021.xml`
- `experiments/reports/v2634_scoped_prefetch_cpu_sources_20261021.json`
