# V400b：参考 ThunderAgent 准入与既有 KV 迁移

V400b 已实际进入 FULL 重放。它直接使用工具衰减版 ThunderAgent 的
参考准入核心，接入已有 JOIN/EOS 预取、条件工具预取、增量备份、
预测长工具卸载和执行连续性；逻辑借出、召回及额外恢复资格均关闭。
该组合用于检验迁移机制的完整收益，不作为新的调度算法贡献。

## 冻结与对照

- 注册：`ops/v400b_locobench_reference_thunder_offload_20261027.registration.json`。
- 冻结应用：`a5e31ae7464fa9fe06e2e00f3c9c244e619c28ff`；
  运行时修复在 `7fe941c0dfdfad05a01f24add6cb43e3549d87ea`。
- 实际原生树：`11b84f7c31986782be3fbe699bae2574ef092389`。
- 配置：`configs/migration/predictive_reference_thunder_20261027.json`。
- 端口18659，socket `/tmp/bkv-causal-18659.sock`。
- 运行目录：`experiments/raw/locobench_reference_thunder_offload_20261027_v400b/full/`。
- 已核验 watcher163362、server163860、driver165104 的真实身份、
  父子关系和 nice0，保留登记的 start ticks。

只运行 FULL，复用完整 V345 `thunderagent_acting_decay`。V401 逐项
核验26个共同字段，包括完整工作图/token摘要、V311工具时间表、
原生树、实际容量、种子、running上限与流式计时。共同工作为18个
不同项目、3674请求、145565966输入token、549671输出token、
72次JOIN，保留原生停止及全部正常长尾。

## 验证边界

V399 一次通过3035项共享 CPU 检查和4个子测试，保留先前全部3030个
case身份；新增组合检查使用真实 native FULL-only tree/allocator
配合copy/event替身，验证预测卸载释放物理槽而不改变参考工作集。
三个实际构造器与全部源码摘要均已绑定。

V401 封存198条已完成回执，均与完整 V345 的请求/token/停止条件
一致。所有已读运行时快照均显示健康物理控制、正确参考策略、
关闭逻辑借出/召回、无paper adapter混入。检查的回执、运行时和
status前缀不是同一原子时刻。后续3476个请求、完整GPU正确性、
JCT、全程吞吐和重复性能均保持未验证状态。

V396b 因旧卸载器读取 `.policy.agents` 停止的99条回执及失败现场
保留，不能与本轮拼接。V400 在GPU serving前因旧socket保护停止，
同样保留。启动与修复过程见[前置记录](v396_reference_thunder_preflight_20261027_zh.md)。

## 完成后的吞吐图表

V402 只启动 CPU 后处理，实际进程167057、nice19、隐藏CUDA。
它等待本轮完整成功、原生遥测收尾和实际owned进程退出后，再调用
冻结版本的绘图脚本，与完整V345生成固定60秒分桶的时序、时间加权
分布及batch/context图。保留准备、空闲和正常尾部，不补终止后零。
中断或失败时停止，不产生伪完整图表，不启动新GPU实验。

- 操作器：`scripts/deferred_causal_decode_plots.py`。
- 状态：`ops/v402_reference_thunder_decode_plots_20261027.state.json`。
- 完成后输出目录：`experiments/figures/decode_diagnostics_20261027_v402/`。
- CPU进程与源码绑定：`experiments/reports/v402_reference_thunder_decode_waiter_start_20261027.json`。

目前没有后续GPU实验排队。完整平均JCT及全程输出吞吐须同时得到
明确、可重复改善后，才继续其他baseline；全局目标仍未完成。

## 运行期间发现的后续修复

主工作区发现 reference 模式的工具卸载候选选择缺陷：先选中第一个
需要从Host恢复前缀的请求，随后才检查活跃资格和实际容量缺口。该请求
未获准入，或者当前空间已经足够时，会挡住后续真正需要容量的请求。
修复仅在 reference 模式下提前执行现有资格和需求检查，仍只检查
最多8个候选，每200ms一次。预测门槛、逐节点重新授权、原生锁、
Host 副本、实际释放边界和参考调度核心均保留。

两个反例使用真实 native FULL-only tree/allocator，配合已有
copy/event/Host-index替身；修复前均失败，修复后相关242项CPU检查
全部通过，无跳过。反例确认释放32个Device槽、保留Host副本且不改变
参考program状态。报告归于
`experiments/reports/v400b_followup/`，不新增主实验编号。
早期测试夹具的请求与图身份不一致，未覆盖该缺陷，其报告保留并明确
排除为复现证据。这里没有证明该缺陷在V400b中的发生次数或性能影响。

该修复不在冻结的V400b中，不能归因于本轮结果。当前运行继续使用
`a5e31ae7464f`，没有热更新、重启或安排新GPU实验。逐层卸载A并复用
空间加载B的分层分配器仍未实现，相关状态见
[容量流水线设计](../design/workflow_service_and_layer_recycling_20261026_zh.md)。

## 证据

- `experiments/reports/v399_reference_thunder_offload_cpu_evidence_20261027.json`。
- `experiments/reports/v401_reference_thunder_offload_live_start_20261027.json`。
- [组合设计与完整历史诊断](../design/reference_thunder_migration_composition_20261027_zh.md)。
