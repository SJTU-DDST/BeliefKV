# FULL 容量扫描在混合压力下保留进度

日期：2026-10-23，北京时间。应用基线为 main 的 `facb37b`。
V128 仍使用冻结应用 `04191b0` 和原冻结引擎，没有注入这次修复。

## 已复现的问题

`UnifiedRadixCache.reclaim_beliefkv_handoff_capacity` 对 FULL 叶节点使用
有界扫描，将未扫描部分保存在 `_beliefkv_handoff_full_scan`。
原实现遇到只缺 Mamba 容量的调用时，会清空这份 FULL 队列。FULL 与
Mamba 容量请求交错时，下一次 FULL 回收重新从叶表开头开始，位于扫描
预算之外的安全 victim 可能反复得不到检查。

V2886 在真实 CPU radix、allocator 和请求池夹具上先验证旧实现失败：
13 个有序叶节点，仅最后一个已有 Host 副本；每轮最多扫描 4 个，FULL
尝试之间插入 Mamba-only 请求。第四次 FULL 尝试仍未释放所需容量，
失败点正是缺少预期的 allocator 释放，没有将该失败记录当作通过资格。

修复只删除 Mamba-only 分支清空持久队列的赋值，局部 `scan` 仍为
`None`，该次调用不进行 FULL 扫描。下次真实 FULL 短缺继续原队列。
不增加扫描或事务预算，也不增加索引、线程、计时器、遥测或 D2H。

## 正确性与进度

V2887 的 6 项真实 CPU 回归通过：

- 四次 FULL 扫描分别检查 `[4, 4, 4, 1]` 个节点，仅迭代一次叶表，
  最后释放 2 个 FULL token 单位并保留其 Host 副本。
- 其他未备份叶节点的 FULL device 值保持相同对象，未提交备份。
- 等待期间 Host 副本丢失、device lock、新增活跃 session、在途 D2H
  或在途 H2D 五种变化都会阻止释放。恢复扫描仍使用当前原生安全证明。

V2888 对独立候选引擎全部 33 个 `test_beliefkv_*` 模块检查：
657 项测试及 48 个 subtest 通过，无失败、错误或跳过。
V2889 应用完整资格为 133 个模块、4390 项及 2 个 subtest 全部通过，
保留 V2884 全部 4368 项，新增本轮 6 项及等待 FULL 尾部的 16 项。
沿用原资格集唯一明确 deselect 的可选真实 Docker progress 测试，没有
把它计作已执行。V2891 绑定完整应用源码，并检查旧用例身份全部保留。

规范补丁机械重建后仅改变该分支和文件 blob 标识，没有其他引擎差异。
V2890 检查补丁构建树与实际候选树完全相同：
`cc9772c6c5c4dcd3678a03b67fa9afc190aec4f8`。
候选位于独立引擎工作区
`../.worktrees/sglang-full-scan-continuity-facb37b-20261023`。

## 历史证据的边界

最新完成的 V128/no_tool_duration_model 有容量检查反复扫描前 64 个
unbacked 节点、每次重新开始的现象。这促成了上述复现，但自然扫描
耗尽和其他调用也可能重启队列；尚未证明历史每次重启都由这个分支引起。
不将 Host-hit 逻辑前缀跨度当作 DMA，也不把 CPU 进度修复直接换算为
可隐藏 H2D、JCT 或吞吐收益。

本次不改变备份选择、Host 容量、EOS 触发或工作负载。端到端目标仍需
同配置和接近工作量的 Full/native/简单调度基线验证，保持 active。
