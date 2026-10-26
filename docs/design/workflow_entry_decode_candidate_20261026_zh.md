# 新 Workflow 入口与 Decode 批次候选

v235 在完整同工作量上提高吞吐，但平均 JCT 未超过 ThunderAgent。
本候选检验是否可以先推进已展开的 workflow，减少执行资格向新
workflow 分散，同时保持真实可执行的 decode 宽度。它是待验证假设，
不把 v235 的交错执行跨度当成已经证明的性能根因。

## 边界

保留 v235 的 agent 粒度连续性、已备份阻塞预算出借、JOIN 归还、
JOIN/EOS 预取、长工具卸载和冷增量备份。引擎 running/decode 上限仍为
24，模型、所有请求和 token、依赖、工具时长、种子及物理容量保持
v230/v232 的共同设置。不重采来源，不再执行完整 native。

唯一新增参数为 `AGENT_WORKFLOW_ENTRY_MIN_READY=8`，默认 0 关闭，
只允许 Full 的 retained agent 策略启用。

## 选择规则

尚未获得过 native 服务的新 workflow，在已有至少八个其他 active、
pending 且非已知阻塞 agent 时暂缓首次资格。计数使用当时可观测状态，
包括正在运行的请求，不读取未来执行长度或 trace 的未发生依赖。
该条件不直接保证 batch8，也不是活跃 workflow 数的硬上限。

同 workflow 已有 active agent，或者已发生过一次 native 准入，
它的后续 root/child、JOIN 后继和工具续接仍遵循原 agent 策略。
不为门槛抢占 running，不轮转 ready owner，不新增普通请求恢复。
普通五秒恢复点最多纳入一个新 workflow；空闲推进与有证明的阻塞
预算出借也必须先检查目标入口条件，再触及 donor。
保留原有 1800 秒饥饿兜底与原生物理容量/锁/DMA 检查。

所有 workflow 的 JCT 从原始提交时间计算，包含入口延迟。
若等待变长、有效宽度变窄或吞吐回退，应如实保留，不能改用
“从准入开始”计时来宣称改善。

## 验收

先完成共享 CPU 回归和真实 runtime 配置检查，测量新增控制路径。
再用不变的 v230 来源独立执行 Full，核对全部 3674 请求及 token，
保留全部普通长尾并排空遥测。
完整比较平均 JCT、全程输出吞吐、实际与 worker 加权 batch、
相同 batch/上下文的 worker 区间、首次服务延迟、JOIN 等待、
实际 H2D/D2H、重复传输和旧输入重算。
不因 batch 变大或某个阶段更快单独验收，不提前启动其他 baseline。

## CPU 证据

共享回归通过 1527 项，无跳过，保留 v235 全部 1465 项。
实际 runtime 构造、入口身份传递、已有 workflow 子代理和 JOIN、
容量不足、running 保护、出借前过滤、原饥饿兜底、终止清理和
其他比较 arm 的隔离均已检查。原生树/allocator 与 Docker 工具
fixture 纳入同轮回归；CPU 拷贝/事件替身不是 GPU 数值证明。
早期 targeted 报告中的断言错误与未设置 CUDA_HOME 所致跳过保留。

报告为 `experiments/reports/v236_workflow_entry_shared_cpu_20261026.xml`。
`scripts/benchmark_workflow_entry.py` 在 18/64/128 个逻辑 agent、
0/4/8 个 ready active 的相同状态上分别测量单次门槛检查和恢复选择，
每场景预热五次、测量两百次。36 个场景的 thread CPU 均值最高为
0.4361ms；fixture 构造、原生 KV 操作和 GPU 拷贝不在测量范围内。
报告为 `experiments/reports/v236_workflow_entry_control_cpu_20261026.json`。

v236 原登记在使用 GPU 之前报停：原启动器遇到其他用户正在使用 GPU
会直接失败，并非持续排队。原状态和登记保留。v236b 只修正监督路径：
初始准备和每个 cold arm 前分别等待连续两次资源就绪观察，间隔十秒；
期间继续处理新的 hold/stop，不启动 GPU 子进程或终止外部作业。
重新共享回归通过 1534 项，无跳过，包含前轮全部 1527 项及
初始繁忙、组间繁忙、连续观察重置和等待中取消的复现。
报告为 `experiments/reports/v236b_workflow_entry_shared_cpu_20261026.xml`。
此修复不改变已声明的模型工作量和测量中的调度策略。
