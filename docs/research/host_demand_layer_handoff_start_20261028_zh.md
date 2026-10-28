# 工具门槛与逐层交接联合 FULL 已启动

2026-10-28。按最新要求，直接测试 75% 工具卸载门槛、Host 需求优先和逐层
D2H/H2D 容量交接的联合方案，暂不先跑消融或再跑 ThunderAgent。

- 注册：`ops/host_demand_layer_handoff_20261028_r1.registration.json`。
- 冻结应用：`589ff977cebc60da0abdd04581128e77881a0088`。
- 端口：18675。
- 当前状态：`running_full`；原始目录为
  `experiments/raw/locobench_host_demand_layer_handoff_20261028_r1/full`。
- 实际 nice0 进程：watcher 537147、server 537670、GPU scheduler 538005、
  replay driver 539460，已分别核实 PID、启动时间与存活。

当前源与 V345 的 12 项工作量、工具时间、容量、服务参数等共同字段一致，
包括 18 个不同项目、3674 请求、145565966 输入 / 549671 输出 token、
72 次 JOIN、V311 分类合成工具时长、streaming、seed21、running/decode24、
805198 Device token 与 200GB Host。全部正常长尾保留。

原生引擎显式改变为 `0d407f981c5179b20859e82e7088d85589cd08b3`；
V345 仍为 `11b84f7c31986782be3fbe699bae2574ef092389`。
新引擎经过实际组件、当前联合模型及新的 24 请求 position/session/stop 检查。
这是联合系统与已有强对照的测试，不能单独归因于门槛、Host 管理或 overlap。

共享 CPU 3462 项及 4 项子测试、原生相关 CPU 102 项通过。
联合模型实际交接 12296 token，日志中的最低剩余生存概率为 0.75，
消费者与 donor 恢复的 token 均与参考一致，最大 logprob 差为 0。

独立 `experiments/reports/host_demand_layer_handoff_20261028/live_start.py/.json`
绑定当前 CPU 源、模型配置哈希、新引擎 smoke、实际服务进程与最早 69 个精确完成请求。
这些只证明启动配置和前缀正确，不代表完整性能、历史所有归因或显著优势。

nice19、CUDA 隐藏的 CPU 绘图等待进程 538992 已核实存活：
`ops/host_demand_layer_handoff_20261028_r1.plots.state.json`。
它只在完整成功及实际退出后绘图，图题和结果中保留引擎差异，
所有时间段、长尾、实际末尾窗口宽度都保留；不增加 GPU 作业。

实现与验收边界见 `docs/design/host_demand_layer_handoff_20261028_zh.md`；
暂定收益排序见 `docs/research/design_benefit_ranking_20261028_zh.md`。
全局目标仍未完成。
