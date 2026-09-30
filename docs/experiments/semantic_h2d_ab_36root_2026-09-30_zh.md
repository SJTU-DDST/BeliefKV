# 36-root 语义 JOIN H2D 完整对照

本轮是开发工作负载上的完整收益验证，不是短 gate/canary，也不是
正式论文测试集。任务、到达、模型、采样 seed、runtime prompt 与
完成通知保持一致；两侧冷启动服务和 KV 缓存，依次运行 reactive
和 predictive H2D。代码在启动前提交冻结。

## 实现

新语义头在独立 CPU process 中加载，单批最多四个观察、待处理请求
至多 64 个，按身份合并替换；模型结果 fd 接入现有 idle poller。
scheduler 只收取结果和验证 live request/context/epoch。
正文最多 1024 字符，每请求至多每 250 ms 发送一次；工具片段、
真实工具继续、epoch/终态变化使旧预测失效，不改变 agent 执行。

模型输入使用已送达正文之前 100 ms 界内的真实 decode 计数，和
离线训练口径一致。剩余工作在接收后按已发生的 decode 进度滚动
扣除，不把后续排队当作输入。Native EOS 是已完成生成的事实，
但不等于 child 已 RETURN：在因果图仍等待最后一个 child 时可利用
这段协议窗口；JOIN 满足后不再追加此模型的预测 H2D。

旧收尾优先级从 prefetch 开关中解耦，两侧均保留相同的 native
完成通知优先级；纯模型推断的阶段不获得额外执行优先级。
Reactive 显式关闭所有本轮预测 H2D；predictive 只允许新模型
指导的 JOIN H2D。两侧 PREPARE_HOST 均关闭，native D2H 不关闭。

## 配置

- Qwen3.5-35B-A3B，当前 patched SGLang 0.5.20 staging 源码。
- `beliefkv-next`，36 root，server running=48。
- BF16 FULL/Mamba，HBM mamba/full ratio=0.9，mem_fraction_static=0.94。
- NUMA node 1，Host 总预算 200 GB，比例匹配实际 Device 两池字节。
- `native_in_graph_1to4`，允许动态多轮；graph limit=2048。
- 完成通知、native-reactive guard profile 与其他 runtime 设置一致。
- 新模型使用固定的校准档位，仅作为候选证据；不是 child 门禁。

启动脚本：`scripts/run_qwen35_semantic_h2d_ab.sh`。
有效对照目录：`experiments/raw/qwen35_semantic_h2d_ab_36root_20260930_v3/`。
初始化会保存 code commit、workload/模型 SHA256 与共用配置。
每侧结束后只清理 patch 已归档且正常结束的 workspace，保留原始
trace、patch、模型、失败现场和报告。

## 报告与边界

`scripts/summarize_semantic_h2d_ab.py` 输出完成 workflow 吞吐、
完成集合的 JCT 与配对完成集合 JCT、请求 token 吞吐、GPU 利用率、
Host high-water/eviction、缓存命中与 uncached input、模型推理/排队、
H2D ACK 和首次服务证据。独立 SWE-bench grading 未运行时，不把
完成 throughput 命名为正确任务 throughput。

FULL 复用必须通过 native prefix/物理节点证明。Mamba 未验证的
复用单列，不将未知计作浪费。ACK 到首次服务的 byte-seconds 是
潜在提前驻留上界，不是始终实际占据 HBM 的时间；同步 H2D 暴露
等待暂未被这份聚合报告单独识别。ACK 数和缓存命中不是吞吐收益。

运行时状态日志用于核验 shared priority、PREPARE disabled、
reactive 无预测动作，以及模型 worker 和物理账本状态。真正的
worker/ownership/telemetry 故障使该侧无效，应修复后重跑；
零有效预测迁移则照实分析 Host 副本、容量、模型时效和窗口原因。

## 状态

异步接入、共享策略解耦、对照启动和报告脚本已实现并通过聚焦回归。
完整 GPU 对照将在该提交冻结后启动；本文件不提前填入收益结论。

首份 reactive 开发运行在 child 已自然 RETURN、JOIN 已满足后出现
`TimeoutError`：native session close 的同步 HTTP 默认只有两秒，
清理异常中断了 parent，而非 workflow deadline 到期或控制链路失效。
该份运行已停止并保留作诊断，不作为对照或训练数据。现将 session
close 的超时改为 30 秒，不吞掉错误、不修改 guard，修复后两侧均
重新运行完整 workload，使用新的结果目录版本。

v2 的 reactive 完整结束，36/36 completed，约 3304.84 秒，
39.22 completed workflow/hour，预测 ACK 为零。v2 predictive
运行暴露 overlap barrier 仍只接受 confirmed JOIN ticket，导致
provisional 模型候选无法进入安全 H2D 发射路径。已停止该侧、
保留 v2 reactive 作为开发参考；不将其拼成同版本正式对照。
现先在只读阶段生成候选，再允许有效 provisional ticket 请求
一次有界 overlap drain，物理发射仍在排空后重检。
v3 将顺序改为完整 predictive、完整 reactive，使用同一修复提交。
