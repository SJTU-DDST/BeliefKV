# FULL-only 控制索引修复的资格与启动

2026-10-28。本轮实际应用冻结为 `a5164fe30cf8`，native 仍为
`cdd58a4acbb5`。候选包含 FULL-only 冷候选发布索引修复，以及先前
`a3d38d23` 的 Host-only 尾部祖先回收修复；不视为单模块消融。
[设计与 CPU 成本](../design/full_only_pressure_index_20261028_zh.md)
保留反例、局部收益、未知布局的局部回退及完整 3718 项 CPU 资格。

配置复用 `predictive_sibling_service_capacity_20261028.json`，profiling
关闭，工具预取及其优先窗口为零，普通执行前沿恢复绑定关闭。
保留 JOIN/EOS、预测工具卸载、prepare_host、agent 连续性、sibling
共同准入、8/4 layer overlap、75% 生存门槛及 2GiB Host 需求预留。

## 冻结与模型资格

干净应用目录为
`/home/longhao/experiment/.worktrees/beliefkv-full-only-pressure-index-a5164fe3`。
当前 CPU source manifest 与冻结应用逐项一致。复核原生 92 项 CPU
资格及实际 GPU 组件后，仅重绑定 canonical patch 的冻结路径；
补丁字节、native 树及其他状态字段相同，不修改 engine 或既有证明。

新的七请求非强制模型验证实际执行 12296-token 的 8/4 层组交接。
消费者输出与独立 reference 相同，之后从 Host 恢复 donor 的输出也
一致，两者最大 logprob 差均为零。夹具 Device49152/Host8GB、每请求
八个输出 token 与正式工作量分开。模型父进程主动清理后退出码 -9，
不是自然退出；实际父进程、owned 子进程及 GPU 上下文消失均另行核对。
此模型夹具没有覆盖每种 JOIN 祖先形态或完整工作量性能。

独立 `review_qualification.py` 的 CPU/native 与 model 两阶段证明位于
`experiments/reports/full_only_pressure_index_20261028/`。
模型 token 包、阶段、事件及原始 log 按原字节 gzip 归档，
十个归档均与原始本地文件逐字节相同，原文件保留。

随后 `full_only_pressure_index_position_20261028_r1` 完成 24 个精确
token/session/native-stop 检查，24 个均原生停止；running24 的
位置分派增量 wall 均值 0.010198ms。原 position supervisor/server
实际退出后才进入完整 FULL。

## 实际 FULL 启动

注册为 `ops/full_only_pressure_index_20261028_r1.registration.json`，
端口 18685，执行树 `beliefkv-causal-a5164fe30cf8`。
独立 `live_start.py/.json` 验证实际 nice0 watcher816387、
server816848、driver817864 和 sole GPU scheduler816937，
CPU/model/position 来源及未改变的完整工作量/工具时间/容量/停止契约。
首次封存包含 153 个精确完成回执，是前缀而非完整正确性或性能结果。

正式轮仍执行 V230 全部 18 项目、3674 请求、145565966 输入及 549671
输出 token、72 JOIN，使用固定 V311 工具时间、Device805198/Host200GB、
running/decode24、seed21、流式测量和所有正常尾部。完整 JCT 和全程
输出吞吐尚未得到；不能以启动、CPU 加速或前缀速率声明胜出。

复用完整 V345 工具衰减 ThunderAgent，对照引擎 `11b84f7c3198` 不同，
在结果和图中保留此差异。没有额外 native、ThunderAgent、其他 baseline
或消融排队。GPU 冻结代码、注册和输入不再修改。

nice19、CUDA 隐藏的 CPU 绘图观察器817706/start ticks738051857
实际存活，状态为 `waiting_complete_full`，只在完整成功及 owned
进程退出后绘制趋势、分布和 geometry，保留所有正常尾部、实际尾 bin
和终止后不补零。状态为
`ops/full_only_pressure_index_20261028_r1.plots.state.json`；
该观察器没有后续 GPU 启动权限。
