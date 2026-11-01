# Host 按需回收的有界扫描修复

日期：2026-11-01。本文属于 original56 共同引擎实验的 CPU 修复证据，
不另起主实验版本，不修改正在运行的冻结对照。

## 已确认的实际重复计算

`useful_token_cost_r1/shared_execution_full_previous_prefix_prefill_r1.json`
逐请求匹配 native attempt、session/generation、context 和 prefill 输入区间。
Full 的 9 次正前缀损失 probe 均完整匹配到实际执行的旧输入区间：

- 647,065 个已经服务过的输入 token 重新进入 prefill。
- 涉及 321 个 prefill 批次；没有重复区间计数。
- 测量组 networking hard027 为 141,504 token，
  mobile game096 为 36,852 token，合计 178,356 token。
- 其余 468,709 token 属于背景请求。
- 这些损失均在固定共同吞吐窗口之后发生。

受影响的整批 worker 区间合计 86,841.492683 ms，
包含同批其他工作、传输依赖和主机提交。
它不是这些 token 独占的 GPU 时间，也不是可直接扣除的 workflow JCT。
Host-block 回访归因的 1,019,114 FULL token 单位属于另一种统计，
不能与上述实际输入区间相加。

## 复现到的扫描缺陷

`beliefkv/runtime/host_demand_capacity.py` 的 `ensure()` 在实际工具卸载或
JOIN 恢复缺少 Host 空间时，最多检查 64 个重复备份节点。
旧实现每次都从同一字典开头检查；前 64 个节点持续受保护或不能安全回收时，
后面的安全节点可能一直得不到检查。

完整 Full 记录了 203 次容量检查、9,454 次节点检查、
146 次 `host_demand_insufficient_safe_mirrors`，回收 43,915 token。
146 次失败与反复扫描满 64 个节点相容，但旧日志没有逐次候选身份，
无法据此判定这 146 次都存在未访问的安全节点。
这项源码缺陷也尚未建立与上述 9 次实际重复计算的逐次因果联系。

回归测试使用实际原生 CPU 缓存树、Device allocator 和 Host 释放记账，
将单次检查数设为 1：首节点有 Host 副本但 Device 未锁定，
第二个节点具备完整、稳定且 Device 锁定的备份。
旧实现第二次仍无法访问后者，测试按预期失败。

## 修复与边界

每次先取得最多 64 个字典项的快照，再把实际检查的项移至字典末尾。
因此失败轮次也会推进扫描；没有新增全表扫描或跨轮候选缓存。
字典中保存的节点对象与集合不变，扫描顺序会变化，
后续原生重复备份遍历也会看到该顺序。

安全检查和释放路径继续由既有原生接口执行：

- 只释放完整、稳定、Device 锁定的 Host 重复副本。
- 保留 Host-only 状态、Host 锁、D2H/H2D 和 prefetch-service 保护。
- 释放前重新验证节点身份、创建时间和原生 DMA 条件。
- Device 对象和 allocator 空闲容量保持不变；不创建额外备份。
- 当前扫描窗口的安全容量不足时，不释放部分集合。

最后一条仍有局限：足够容量若分散在多个窗口，每个窗口单独都不足，
轮转不会自动组合它们。跨窗口累计或部分回收需要单独设计与验证。
原生 `drive_host_eviction()` 已经优先回收安全重复副本；
本修复针对 Full 的前置容量管理，不新增原生驱逐优先级。

## CPU 验证

报告均位于
`experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/`：

| 报告 | 结果 | 覆盖 |
| --- | --- | --- |
| `host_demand_scan_rotation_before_r1.xml` | 1 项按预期失败 | 旧代码停在同一窗口 |
| `host_demand_scan_rotation_after_r1.xml` | 136 项通过 | 回收、备份、生命周期、工具策略 |
| `host_demand_scan_rotation_consumers_r1.xml` | 104 项通过 | JOIN 恢复、阻塞交接、预测工具卸载 |

新增测试覆盖扫描推进、容量不足时保留全部备份、重验时新增 DMA、
prefetch-service 保护。全部使用隐藏 CUDA 的 CPU 执行；
原生拷贝和事件采用 stand-in，不构成 GPU 数值或性能资格。

当前 ThunderAgent acting-decay 仍运行冻结提交 `45747eb9`，
本修复未注入该实验。后续若注册修复后的 Full，
需要重新绑定当前源码资格并完成相同 workload 的实际性能比较。
尚不声称降低重算、JCT 或提高吞吐；完整目标仍在进行。
