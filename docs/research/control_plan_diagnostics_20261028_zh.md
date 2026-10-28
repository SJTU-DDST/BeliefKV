# 控制成本与物理计划诊断 FULL 声明

本轮的目的是获得缺失的控制线程 CPU 样本及 JOIN 物理计划退出原因，
用于选择下一项有瓶颈证据的修复。它不是新调度策略、独立模块消融或
性能改善的重复验证。

配置：`configs/migration/predictive_control_plan_diagnostics_20261028.json`。
相对 sibling-service/capacity 配置，环境只增加
`PROFILE_SHARED_PATH=1`。保持完整 V230 请求/token/依赖、V311 工具时间、
18 个不同项目、Device805198/Host200GB、running/decode24、seed21、
流式指标、原始停止和全部正常长尾。工具预取及普通执行前沿恢复均关闭。
原生引擎仍为 `cdd58a4acbb5038207d3a5020c54d7cbcef983b9`。

## 预先声明的诊断问题

1. JOIN 物理尝试的结果是否逐项解释：已无缺失、真实空闲容量已足够、
   消费者失效、donor/Host/锁条件拒绝、备份等待 ACK 或实际物理交接。
   对同一快照核对结果计数之和与尝试计数；未到达的后续状态保持未知。
2. 新计时是否真实启用，是否同时提供 wall 与 `thread_time_ns`，样本
   是否保留完整嵌套、按各顶层阶段每 64 次采样；只分析已测样本，
   不倍乘估算全部控制成本。
3. 哪些阶段 CPU 较贵、哪些主要消耗 wall 等待；原生 worker/forward
   边界仍须单独用于分析提交推迟，计时器本身不证明暴露 GPU 空闲。
4. 完整请求、停止、回执、telemetry drain 和 owned cleanup 是否继续
   通过；保留采样与迁移分布及所有正常尾部。

完整 ThunderAgent 对照复用 V345，保留其共同引擎 `11b84f7c3198`
与本轮 `cdd58a4acbb5` 的差异，且 V345 未开启新 profiling。与旧 FULL
比较也明确存在诊断成本和运行波动，不能将单次差异归为单模块收益。
不额外采集 native 或运行其他 baseline。

## 启动配置修复

启动前发现 `arm_environment()` 强制将 `PROFILE_SHARED_PATH` 置零。
新配置单独写入 1 仍无法生效。最初 CPU 失败记录保留在
`experiments/reports/control_plan_diagnostics_20261028/initial_profile_configuration_red.xml.gz`，
其中保留原始 XML 字节与失败输出空白。

修复保留默认 0，接受经过 0/1 验证的显式开关，并同步其服务端别名。
CPU preflight 使用私有临时 opportunity writer，直接核对实际 runtime
计时状态及采样间隔；writer 的构建与关闭均在 CPU 预检范围内，
不进入 workflow JCT。

## 验证与执行边界

重新进行绑定本配置和当前源码的共享 CPU 检查后，才冻结干净执行树。
保留用户的四份未提交研究文档，不将其纳入本次代码提交。
原生引擎没有变化；组件证明须核对真实树、分组及算子源码，当前配置
还需完成非强制模型恢复检查和位置采样/session/stop GPU smoke。

每次实际 GPU 启动前重新核对共享 reservation、pause、真实 GPU 进程、
端口、Host 内存及前序 owned 退出。失败尝试保留，不自动重启部分轨迹。
GPU 资格、小模型夹具或开始执行都不表示完整性能验收通过。

当前配置的一轮共享 CPU 资格为 3683 个唯一 testcase 及六个子测试
通过，保留先前全部 3668 个用例并增加 15 个。FULL 及两个 ThunderAgent
构造器均直接验证实际计时开关和 64 次采样间隔；没有改变 reference
核心调度方法。源文件 manifest 位于本轮报告目录的
`shared_cpu_r2.sources.json`，配置、实验适配器和预检均有源码绑定。

保留 `shared_cpu_r1.xml` 和定向失败轮次。新增 CPU 预检测试最初继承
了只用于路径检查的 `{}` 假 service-seed 文件，真实构造器正确拒绝；
该测试随后显式关闭这三个无关种子，与既有真实构造器测试一致。
这不是 serving 运行中的模型或迁移故障。

## GPU 资格与实际启动

冻结应用为 `fe0ce6b27f40cffd7c44e8a97b3ea02f05217699`，1798 个
资格源文件逐项匹配干净执行树。原生组件沿用相同 clean engine、
同一算子 SHA 和 8/4 分组的既有真实 GPU 证明；当前应用重新完成七次
非强制模型调用，实际交接 12296 token。消费者 handoff 和 donor
恢复均与独立参考输出相同，最大 logprob 差为零。

真实模型 runtime 已观察到 schema 2、`thread_time_ns`、每 64 次顶层
采样及实际 phase 数据。小夹具为 Device49152/Host8GB，不是正式
工作量容量。模型父进程在主动清理后已不存在，GPU 无残余计算进程；
其记录的最终退出码为 -9，不将主动清理表述为自然退出。

位置注册第一次在 GPU 启动前因 canonical patch 路径不同而拒绝，
没有创建注册文件。`rebind_native.py` 复现旧拒绝，确认仅
`canonical_patch.path` 改变，补丁 SHA、实际/预期树和其余字段全部
一致后写入独立新证明；旧证明保留。对应
`native_binding_review.json` 不表示新增 GPU 资格。

随后 `control_plan_diagnostics_position_20261028_r1` 完成 24 个精确
token/session/stop 检查，24 个均原生停止；running24 的位置分派
增量 wall 均值为 0.009996 ms。实际位置 supervisor/server 已退出。

诊断 FULL 注册为
`ops/control_plan_diagnostics_20261028_r1.registration.json`，端口18681，
只执行 FULL，复用完整 V345。`live_start.py/.json` 独立验证实际
nice0 watcher774837/server775366/driver776815/GPU scheduler775715，
完整工作量/工具时间/容量/停止契约、实际 profiling 和原生状态。
首次封存前缀核对 168 个精确完成回执；不是完整工作或性能结果，
不以该前缀吞吐比较完整对照。

本轮报告目录保存模型、CPU、路径拒绝/重新绑定与启动证据；运行中
冻结应用、原生树、配置和原始轨迹均不再更改。CPU 绘图观察器只在
完整成功及实际 owned 退出后生成趋势/分布/geometry 图，图中保留
引擎和 profiling 差异，不授权下一轮 GPU 工作。
模型请求 token 包和阶段记录以 `.json.gz` 原字节归档，原始本地
JSON 保留，不把长 token 数组展开为 Git 文本差异。

CPU 绘图观察器首轮只查服务端别名，但注册文件的 reference environment
保存的是短名 `PROFILE_SHARED_PATH`，因此在绘图前拒绝并退出。保留
`plots_r1_failed.state.json/.log`，且其算子 SHA 与提交 `fe330e64` 的
原始文件一致。后续观察器接受相同配置的两种字段表示，改用独立
`plots_r2.state.json`，GPU FULL 的冻结源码和进程保持原状。
