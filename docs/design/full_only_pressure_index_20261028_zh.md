# FULL-only 冷候选发布的索引修复

2026-10-28。此候选只修复控制实现，不更改调度优先级、工作集选择、
工具预测门槛、迁移安全条件或 native 引擎。它与上一提交的 Host-only
尾节点祖先回收修复一起进入下一次 FULL，因此不宣称独立模块收益。

## 已确认的问题

`_publish_parent_pressure_candidates()` 原有快速路径要求 FULL 的
`evictable_device_leaves` 和 Mamba LRU 同时存在。真实 FULL-only native
树的 LRU 只包含 FULL，没有 Mamba 项，所以即使可用 Device 叶节点很少，
仍回退到整个 `_parent_pressure_candidates` 表逐项查询。

真实 native CPU 反例中，一名 owner 的 128 个节点全部被查询；
12 名 owner 的 1536 个节点同样全部被查询。实际输出分别只有一名和
最多八名合法 FULL donor，反例保留为 `initial_red_cpu.xml`。

## 修复边界

仅在 `cache.tree_components == (0,)` 时将 Mamba 候选预算设为零，
允许只根据原生 FULL 叶集合使用已有稀疏索引。发布表仍包含 0 和 2
两个 component key，后者为空；原候选顺序、八项上限、创建身份、
Host 副本、Device 锁、共享引用、在途迁移和 exclude 条件保持不变。
每次仍读取当前原生叶集合，不缓存物理可回收性。

未知布局和声明了 Mamba 却缺少索引的情况保留原有保守遍历。
原生 demote、Host/Device 页分配、ACK、模型运行和逻辑 agent 资格
没有在此修复中改变。它不为普通预取授权驱逐 ready peer。

## CPU 证据

42 项相关检查通过，覆盖真实 FULL-only 树、锁/共享/在途拒绝、排除、
真实 demote 后父节点成为新叶、混合模型原有接口和未知布局回退。
完整 source-bound CPU 回归一次通过 3718 项及六个子测试，保留先前
3705 项并新增 13 项，三个实际构造器与 reference 核心继承隔离通过。

对同一真实 native CPU 树交替调用旧提交 `e45a9c6b` 和新方法，
每侧 200 个样本；构造、查询计数和输出核对均放在计时之外：

| 场景 | 候选节点 | 旧线程 CPU P50/us | 新 P50/us |
| --- | ---: | ---: | ---: |
| FULL-only 1 owner | 128 | 51.9125 | 14.0600 |
| FULL-only 12 owners | 1536 | 642.7140 | 30.7050 |
| FULL-only 24 owners | 3072 | 1251.3690 | 35.3795 |
| 未知布局 | 1536 | 632.9805 | 641.9560 |
| 缺少 Mamba 索引的混合声明 | 1536 | 638.3430 | 636.9590 |

保留未知布局约 1.4% 的本轮局部回退，不把所有场景概括为加速。
所有场景的实际 native 候选前缀和空闲槽位相同；Host 索引、copy/event
仍是 CPU 替身。24 owners 是局部扩展性夹具，不改变正式 18 workflows。
这些时间没有外推成整轮控制成本，也没有换算为 JCT 收益。

证据与算子位于 `experiments/reports/full_only_pressure_index_20261028/`，
包括 `control_cost.py/control_cost_r1.json`、失败反例、targeted CPU、
完整 CPU XML/source manifest/evidence 和 `review_qualification.py`。

## 下一轮比较契约

复用 `predictive_sibling_service_capacity_20261028.json` 的完整策略配置，
保持 profiling 默认关闭。工作量仍为完整 V230、V311 工具时间、
18 个不同项目、3674 请求、145565966 输入及 549671 输出 token、
72 JOIN、Device805198/Host200GB、running/decode24、seed21 和全部正常尾部。
工具预取、优先窗口及普通恢复绑定继续关闭，逐层 8/4 overlap 保留。

先冻结当前通过 CPU 资格的源码，复核不变的 native `cdd58a4acbb5`
组件证明，完成本应用的非强制模型恢复与 24 请求位置/session/stop
资格，再进行 FULL-only 完整重放。只复用完整 V345 工具衰减对照，
明确其引擎不同。完整 JCT 与全程输出吞吐的明确、可重复改善仍是
后续其他 baseline 和消融的前提；不能用本页局部 CPU 加速替代。
