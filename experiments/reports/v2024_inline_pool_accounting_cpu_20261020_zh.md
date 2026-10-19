# H2D 池名处理与完整预取提交的 CPU 降本

日期：2026-10-20（北京时间）。基线 `e9d0c18`。
GPU 最早仍于北京时间 06:00 启动，本轮只执行 CPU 工作。

## 修改与边界

H2D expectation 构造在逐池循环中直接规范化、验证池名，省去每个池一次
短函数调用。每次操作仍复制一份池名到 entry 的映射，然后调用原生
`_num_tokens_by_pool`、`_transfer_num_bytes`；没有别名原映射，也没有跨调用
缓存几何。entry 的 unit size 仍在核算回调之后读取。

保留任意非空规范字符串的 sidecar 池名、重复规范名拒绝、全部索引、计数、
字节、身份与 generation 检查，以及原生两个核算方法。D2H、引擎、原生
allocator、ACK 和首次消费证明均未修改。

12 项新增边界用例覆盖合法别名与派生池、非法/重复别名在核算前被拒绝、
核算回调替换映射或 entry，以及原地改变 unit size。当前操作使用捕获的
entry，下一操作重新捕获映射，entry 内部尺寸改变仍被实时读取并触发
字节不匹配拒绝。

## 标量核算

V2021 使用已固定版本原生控制器的两个真实 AST 方法、CPU tuple 索引，
6 组 H2D 加 6 组未修改 D2H 控制，每组 151 个样本、每样本 100 次调用，
20 次预热。所有 expectation 字段一致。

| H2D 输入 | 基线 P50 us | 候选 P50 us |
| --- | ---: | ---: |
| FULL2 | 5.861 | 5.558 |
| FULL4096 | 6.066 | 5.581 |
| FULL4096 + Mamba1 | 11.821 | 11.631 |
| Mamba2 | 11.848 | 11.624 |
| hybrid + FULL-derived sidecar | 14.011 | 14.110 |
| Mamba2 + derived sidecar | 14.231 | 14.116 |

sidecar 的 0.099us 回退保留；D2H 代码未改，其变化作为控制记录，
不能归因于此次修改。所有池计数和输入形状为受控参数，不代表在线频次。

## 完整提交

V2022 使用 24-workflow/8-running CPU fixture，包含稀疏 session 叶子标记、
numpy.float64 创建时间及可用尾部回收。JOIN/tool、4/16/64/256 深度、
诊断开关和原生/回退预算共 32 组，每组 151 样本、10 次预热，新旧交替
执行，比较完整记录、pending expectation、物理目标及原生读取次数。

31 组 CPU P50 降低 0.13%–3.38%；另一组 JOIN、depth4、诊断开启、
回退预算为 413.588→414.397us，增加 0.809us。全部记录、预留和原生
读取次数一致，未制造 DMA、ACK 或首次复用。

原生预算、关闭诊断的线程 CPU P50：

| 来源 | 路径节点 | 实际提交节点 | 基线 us | 候选 us |
| --- | ---: | ---: | ---: | ---: |
| JOIN | 64 | 64 | 1684.783 | 1655.434 |
| tool | 64 | 64 | 1687.816 | 1656.818 |
| JOIN | 256 | 64 | 3086.129 | 3070.759 |
| tool | 256 | 64 | 3119.668 | 3078.388 |

生产命令 ID 生成未改；仅 CPU fixture 对齐命令命名空间与记录时钟。
fixture 构造、reset、核对和 cProfile 均在计时外。256 深度仍至多提交
64 节点，不能按观察深度报告传输量或迁移次数。

## 检查与后续

V2019 的新增测试初次有一个失败：期待内层 `host geometry changed`，
而原有 `PhysicalReceiptError` 继承 `ValueError`，会被外层包装为
`native prefetch accounting unavailable`。V2020 仅修正新断言，同时
核对原始 cause，247 项 physical 检查通过；生产错误处理未改。
失败和通过记录均保留。

V2023 在进程启动前固定 V88/python 的 PYTHONPATH，通过真实
`python -m pytest` 入口执行 57 个文件，2278 项检查全部通过，
无 failure/error/skip。包含传输、所有权、容量、生命周期、预取、
PREPARE、准入、原生回执与首次服务路径，没有使用 GPU。

本轮没有真实 CUDA、allocator、DMA 或并发 writer/GIL 测量；局部及完整
CPU 提交结果均不证明吞吐/JCT 收益。提交 main 后发布新资格和冻结源码，
同一等待进程最早于北京时间 06:00 核对资源后启动 predictive，随后 native。
24-root 配置、V88 引擎及补丁保持。

- [V2021 标量核算](v2021_inline_native_accounting_cpu_20261020.json)
- [V2022 完整提交](v2022_inline_pool_wait_dispatch_cpu_20261020.json)
- [V2023 扩大检查](v2023_inline_pool_qualified_checks_20261020.xml)
- [V2019 初次新增断言失败](v2019_inline_pool_accounting_checks_20261020.xml)
- [V2020 修正断言后检查](v2020_inline_pool_accounting_checks_20261020.xml)
