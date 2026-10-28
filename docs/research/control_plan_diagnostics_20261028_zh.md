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
