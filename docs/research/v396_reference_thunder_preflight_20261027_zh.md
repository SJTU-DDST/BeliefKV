# 参考 ThunderAgent FULL 的启动准备

运行代码提交为 `aceccac82abe60970ee9b7e73e4f07f289a6d339`。
V394 的 3030 项共享 CPU 检查及 4 个子测试通过，V395 封存源码、
配置和三个实际构造器。候选用途与证据边界见
[组合设计](../design/reference_thunder_migration_composition_20261027_zh.md)。

V396b 随后从 `df0bd5cf4e3c839185defcfd946755cbcffb969e` 启动 FULL，
在首次预测工具卸载时因旧 policy 字段假设停止。V397 封存99条精确
完成回执并验证所有 owned 进程退出。该轮没有完整性能结果；
保留原始失败，修复后需要新注册完整重跑。

## 使用原生引擎与现有对照

继续使用实际树 `11b84f7c31986782be3fbe699bae2574ef092389`。
V396 绑定报告与 V378 原生证明逐字段比较，只允许 canonical patch
路径由历史启动副本变为当前工作区；patch 字节、原生实际树、
tracked/untracked 状态及其余字段均一致。没有修改原生引擎。

后续只运行 FULL，复用完整 V345 工具衰减 ThunderAgent。保持同一
V230 全部请求/token/停止、V311 工具时间、18 个不同项目、
Device805198 token、Host200000077824 bytes、running/decode24、
种子21和流式计时。已有 V246 的 24 请求 GPU token/session/stop
检查只用于未改变的 position 重放路径；它不代表新组合已通过完整
GPU 正确性或性能验收。

## 首次注册拒绝与重试边界

第一次注册错误地把 V378 设置为 `after_registration`。该选项要求
前后实验的完整 `reference_environment` 相同，因此在创建冻结副本后、
写入注册文件和启动 GPU watcher 前拒绝：
`predecessor causal comparison differs: reference_environment`。

保留未使用的 `beliefkv-causal-aceccac82abe` 和对应 engine checkout，
以及 `v396_reference_thunder_pre_gpu_dependency_rejection_20261027.json`。
这是启动参数错误，没有 GPU 实验或性能结果。

已用原有前驱核验逻辑，针对 V378 自身配置重新检查其完整成功、
watcher/server/driver/owned 身份的实际退出。不同配置的独立 V396b
注册将省略这个不适用的同配置依赖，继续保留预约、资源、端口、
GPU 锁和所有工作量/容量/引擎校验。CPU 资格覆盖的运行时代码保持不变。

## 证据

- `experiments/reports/v395_reference_thunder_cpu_evidence_20261027.json`。
- `experiments/reports/v396_reference_thunder_native_source_binding_cpu_20261027.json`。
- `experiments/reports/v396_reference_thunder_pre_gpu_dependency_rejection_20261027.json`。
- `experiments/reports/v397_reference_thunder_offload_integration_failure_20261027.json`。
