# Session 模型验证的启动准备

GPU 预约截至北京时间 2026-10-27 11:00。本轮只执行 CPU 检查和独立
源码目录准备，不启动模型、拷贝组件或正式性能实验。

## 组件环境字段修复

`qualify_session_native_model.run` 原来没有将 `reference_environment`
写入注册记录，而执行阶段直接调用的 `prefix_qualification_environment`
必须读取它来定位 CUDA 13 工具链。因此原入口会在组件启动前抛出
KeyError，不能据此推断 GPU 拷贝或模型数值有问题。

现在从冻结目录的 `predictive_free_page_fence_20261026.json` 读取完整
`common_environment` 并写入注册记录。执行前重新检查它与候选声明完全
相同；缺字段、替换 CUDA_HOME、改变可见设备均拒绝。模型的 49152
Device tokens、8GB Host 仍由独立正确性夹具配置覆盖，不变为正式实验
容量。组件环境仅取所需工具链字段，不把整个声明作为进程环境继承。

新增 CPU 测试执行真实 run 注册路径，并让真实组件环境构造函数使用
临时可执行文件/动态库存在性夹具。它不执行这些文件或启动 GPU。
另用真实已安装工具链验证路径存在，CPU 父进程仍为
CUDA_VISIBLE_DEVICES=99999。将来的 GPU 子进程使用明确的设备 0。

修复前保留四项失败的复现；共享回归实际通过 2180 项及四个子测试，
无失败/跳过，完整保留 v243 与 v244 已有的 2176 个 case 身份。
报告为 `v245_session_component_environment_shared_cpu_20261026.xml`；
源码绑定为 `v245_session_component_environment_sources_cpu_20261026.json`。

## 独立补丁绑定

当前 main 的历史 staging patch 不能应用到 free-page 候选的 FA5 基线；
正确 delta 为 `patches/sglang_v0520_free_page_h2d_fence_20261026.patch`。
正式比较和位置重放入口读取 staging patch，因此不能直接把新原生
目录传给旧入口，也不能沿用旧 engine 的 GPU smoke 作为共同新引擎证据。

使用现有 `run_agentx_baseline.freeze` 的通用冻结机制生成独立 application
和 engine 目录。该工具在临时 Git index 中将明确给定的 canonical
patch 绑定到冻结目录内的 staging 路径，不改变 main 或用户 index。
这里复用的是源码冻结工具，并不切换为 AgentX workload 或 baseline。
旧 main 补丁、历史 native/ThunderAgent 结果及旧冻结目录全部保留。

独立目录必须重新核对 application 的全部源码指纹、原生基线 commit、
delta 字节和最终 tree `11b84f7c31986782be3fbe699bae2574ef092389`。
与此前 native CPU 证明相比，只有 canonical patch 路径可重新绑定；
tree、patch 字节、所有实际源文件及既有测试证明均须一致，单独记录
该重绑定，不能把路径变化隐去。

独立目录的 CPU 资格和下一阶段参数单列于
`v245_prepared_free_page_execution_sources_cpu_20261026.json`。只有对应
检查实际完成后，该记录才能标记准备合格；它不代表 GPU 数值或性能
资格。预约解除后仍先执行真实 writer/copy 和 session 模型检查，再在
相同新 engine 上完成位置重放 smoke 与 Full/ThunderAgent 对比。
