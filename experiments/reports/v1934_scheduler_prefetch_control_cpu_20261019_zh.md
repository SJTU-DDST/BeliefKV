# 调度与预取控制 CPU 降本

日期：2026-10-19。开发分支 `main`，基线 `e5c660f`。
GPU 实验保持用户要求的暂停状态。本轮只运行 CPU，没有启动服务或 DMA。

## 实现

1. `dispatch_join_prepare` 先取现有 WAIT_JOIN 集合。没有等待 parent 且没有
   旧压力候选时直接返回，不读取 FULL/Mamba 空闲容量；存在旧候选时仍走
   原清理、发布路径。`dispatch_tool_prepare` 没有工具预测且无关键 parent
   需求时同样跳过容量读取。已有检查周期和最终物理检查保留。
2. 无关键 parent 需求时，回收入口不再读取无用途的时钟。
3. JOIN、工具、handoff 和准入共用按命令核对 ACK 的方法，从最新历史向前
   查询，找到本批全部命令后停止。不增加第二套历史或定时失效缓存，
   保留原 128 条历史上限和各调用方原有的方向、pending、缺失回执处理。
4. 物理事务登记对常见单发布节点使用标量身份和冲突检查，避免临时集合；
   字节在原验证循环中累计一次。多发布节点、合并命令、节点上限、重复、
   在途冲突、正数、对齐与 ACK 归因仍按原合同校验。

原生分配器的最终恢复条件没有被微基准替代。`prefetch_expectation_from_native_op`
仍从实际 native 操作核对各池索引、数量和字节，再登记事务。

## 受控 CPU 结果

以下均为线程 CPU 的 P50，单位 us。同一场景交替测量基线与候选，不使用
全程 GPU/JCT 推算。V1931 使用 151 个样本、20 个预热样本。

| 场景 | 基线 | 候选 |
| --- | ---: | ---: |
| 空 PREPARE 来源，24 workflow / 8 running，到期 post-forward | 23.78 | 3.21 |
| JOIN ACK，1 条刚完成命令 / 128 条历史 | 8.12 | 1.35 |
| JOIN ACK，4 条刚完成命令 / 128 条历史 | 8.54 | 1.90 |
| JOIN ACK，16 条刚完成命令 / 128 条历史 | 9.71 | 4.21 |
| 工具 ACK，1 条刚完成命令 / 128 条历史 | 8.37 | 1.33 |
| 登记 16 条单节点恢复命令 | 71.74 | 59.04 |
| 登记 64 条单节点恢复命令 | 302.57 | 247.45 |
| 登记 16 条命令，每条 4 个 child receipt | 161.53 | 125.35 |

空来源场景容量读取从每次 2 次降为 0。这里刻意将既有 PREPARE deadline
设为到期以测量对应分支，不意味着每次 decode 都会读取两次容量。

ACK 查询的改善有边界：缺失一个 ACK 时仍要遍历有界历史。最终版本中，
JOIN 的 4/16 命令缺失场景分别为 8.70→9.51us、9.87→11.39us，工具侧
为 8.53→9.32us、9.65→11.02us。正常刚完成批次更快，异常路径仍多约
0.8–1.5us；未隐去这些回退，也没有把“未完成”改判为完成。
V1930 保留首版测量，V1931 是优化查询循环后的本轮最终结果。

V1932 测量真实 Python 观察、规划、重验和登记路径，native enqueue 使用
CPU stand-in，不运行原生 allocator 或传输。24 组 JOIN/tool、4/16/64
节点、原生/回退容量预算、诊断开/关场景均保持恢复目标和 pending 记账
一致。完整路径 P50 降幅为 0.3%–2.8%，小幅改善接近噪声时不作普遍结论。

| 64 节点、诊断关闭、原生容量预算 | 基线 us | 候选 us | P50 降幅 |
| --- | ---: | ---: | ---: |
| JOIN 完整 CPU 选取与登记 | 2618.02 | 2558.72 | 2.27% |
| 工具完整 CPU 选取与登记 | 2603.01 | 2537.58 | 2.51% |

另有 9 组 prefill 恢复依赖检查，覆盖相关/无关 context、状态未就绪及
legacy 路径，准入判定、原生依赖检查和 pending 状态均一致。

V1933 使用完整固定基线 runtime、observer、physical 模块，避免只替换
部分方法而混入候选 helper。8 组 24/156/512 workflow 场景覆盖无 Host
备份、已有备份、无 JOIN 与图状态变化；每组 80 个测量轮次、5 个预热
轮次，PREPARE 动作、工具刷新顺序和压力候选发布一致。7 组非空来源的
session snapshot 次数均为每侧 640，优化没有通过少检查机会换取耗时。
非空路径只有小幅波动，512-workflow unbacked 的 P50 4.262→4.289ms，
不宣称其改善；无 JOIN 的 P50 14.75→2.03us。

## 验证与边界

运行以下两组检查，合计 1161 项通过；查询循环调整后，另复查其中
37 项直接相关检查通过，不将重复执行算作新增覆盖。

- `test_scheduler_prefetch_control_cpu.py`、`test_prefetch_control_cost.py`、
  `test_sglang_v0520_physical.py`、`test_native_wait_candidates.py`：277 项。
- runtime、工具预测迁移、execution handoff、confirmed/parallel JOIN、
  wait prefetch batch、重验和部分恢复：884 项。

新增检查覆盖空来源但 GPU 仍有服务需求、无 waiter 的旧候选清理、
读取最近 ACK 后停止、错误方向/未知/超出历史的命令、pending 不被旧记录
误记为完成，以及单/多节点非法身份、重复冲突和取消后的字节归还。

最新完整 V85d 的已有记录含 `physical_planning_after_forward=1811270`、
`native_physical_completed=558`、`join_prefetch_acked=172`。它们说明 ACK
核对并非每轮都发生；不能把本地加速百分比乘全部调度调用数。无来源
到期频率也没有由这组微基准测出。本轮没有测得吞吐、JCT 或预测准确率，
不将这些 CPU 结果记为 GPU 端到端收益。

仍值得处理的成本包括闭包观察、native 操作数量/字节核对以及真实 native
执行路径。本轮完整 CPU 路径只有几个百分点改善，调度控制成本尚未消除。
24-root 动态委派配置仍为候选，未因 CPU 验证自动恢复 GPU。

原始结果：
[控制路径](v1931_scheduler_prefetch_control_cpu_20261019.json)、
[完整预取与依赖检查](v1932_wait_prefetch_dispatch_cpu_20261019.json)、
[等待 PREPARE](v1933_wait_prepare_cpu_20261019.json)。
