# 36-root 语义 JOIN H2D 完整对照

本轮是开发工作负载上的完整收益验证，不是短 gate/canary，也不是
正式论文测试集。任务、到达、模型、采样 seed、runtime prompt 与
完成通知保持一致；两侧冷启动服务和 KV 缓存。v4 依次运行
predictive H2D 和 reactive，代码在启动前提交冻结。

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
当前对照目录：`experiments/raw/qwen35_semantic_h2d_ab_36root_20260930_v4/`。
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

异步接入、共享策略解耦、对照启动和报告脚本已实现并通过回归。
v4 完整 GPU 对照已结束，结果与限制见下文，不提前填入收益结论。

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

v3 实际发射了两次 H2D，首次 ACK 被验证，但首次服务未复用 FULL；
第二次 ACK 跨过 parent continuation 的 epoch 前进，严格相等检查
错误地停用了预测路径，故该侧已中止且不作为 benchmark。修复允许
PREFETCH_GPU 在相同、原生仍存活的 session/generation 下前进一步
接力 ACK，不放宽字节、pool、节点和 generation 校验。v4 会重新
运行完整 pair；首批 ACK/非复用记录保留作为诊断，不提前计为收益。

## v4 完整开发对照

两侧均使用 `dc23b990929ff97dc173140741c92b181ac956f4`，容量校验一致，
物理账本与模型 worker 未失效，逐请求遥测没有丢失或写入错误。
完整产物是 `qwen35_semantic_h2d_ab_36root_20260930_v4/comparison.json`。
`complete` 表示两侧均收齐终态，不表示所有任务成功或收益得到证明。

| 指标 | Reactive | Predictive H2D |
| --- | ---: | ---: |
| Workflow completed / error | 35 / 1 | 36 / 0 |
| 整轮时间，秒 | 2028.99 | 1868.12 |
| Completed workflow/hour | 62.10 | 69.37 |
| 35 个共同完成任务的平均 JCT，秒 | 799.47 | 809.39 |
| 模型调用 | 5779 | 5017 |
| 工具调用 | 5644 | 4937 |
| 已完成请求输出 token/s | 573.29 | 565.61 |
| GPU 利用率采样均值，% | 89.63 | 80.19 |
| 预测 H2D ACK | 0 | 5 |
| 可验证 FULL 首次复用字节 | 0 | 0 |

Completed throughput 的表面涨幅为 11.71%，但配对平均 JCT 略差，
输出 token/s 略低，执行轨迹与调用数量也不同；再加上 reactive
的一次连接错误，本轮不能作为预测调度提升吞吐的因果证据。
未运行独立 SWE-bench grading，不把 completed 命名为正确任务。

### 传输与工作负载

五次预测 H2D 共 360,140,800 字节。四次取得首次服务记录，均未
复用对应 FULL，合计 34,263,040 字节；另一次因 epoch 再次前进
没有可验证的首次服务记录。Mamba 的首次复用仍未知，不把所有
传输字节都计为浪费，也不将 ACK 计为收益。

Predictive 有 38 个真实 subagent，均自然 RETURN；38 个 ALL JOIN
中只有 xarray-7393 和 requests-1766 两个 workflow 有第二轮。
上下文 summarizer 是内部调用，不能算作动态 subagent。Guard
干预计数为零，74 次自然语言终态均被接受，没有格式修复或强制
终态。多轮调用较少是当前 workload 的实际行为，不补造轮次。

Host FULL 高水位分别为 84.94 / 73.45 GB，Mamba 两侧均到 94.65 GB
满池；FULL eviction 为 5.73 / 3.60 GB，Mamba eviction 为
110.62 / 69.41 GB。块级证据分别识别到 4505 / 1534 个 FULL token
在驱逐后被重算，其余未重访块不能判为有用丢弃。Uncached input
包含新输入，Mamba 的块级恢复位置仍未知，不能将两者直接当成
完整的 eviction-induced 重算量。

### 在线预测偏差

`scripts/audit_semantic_h2d_online.py` 使用冻结阈值
0.5816927983520741，按请求记录首次越线与最后一份已接收预测；
输出在本轮的 `online_forecast_audit.json`，不是新的校准或训练。

152 个有预测的请求中，44 个首次越线：37 个是真实自然终态，
7 个随后继续执行，终态覆盖 37/38。首次越线的剩余 token
中位绝对误差为 142.91，中位有符号误差为 -130.94。最后一份
预测的真实剩余 token 中位数为 14.5，预测中位数为 217.88，
中位有符号误差为 +196.28。临近结束仍明显高估，是 latest-start
偏晚的直接线索；不能只用终态分类覆盖率代替时间精度。

五个 latest-start 的 `remaining_ms` 均为零，且均不早于相同 child
请求的 native result。因此本轮只验证了生成结束至 runtime RETURN
之间的协议窗口，未验证 decode 尚未结束时的有效预测预取。
平均模型推理为 14.74 ms，观测年龄中位数为 401.58 ms，两者不同。
以上是重复使用开发项目的诊断，不是独立泛化精度。

### 连接错误与后续修复

Reactive 的 xarray-6461 child 在 epoch 34 发生
`APIConnectionError`，底层为 `ReadError` / errno 104。
请求未出现 native 服务记录，控制事件正常，服务端没有崩溃。
现有 trace 缺少 HTTP 阶段，不能确认是否由陈旧连接复用引起。

此前仅 EOS/HTTP 诊断路径使用禁用 keep-alive 的同步、异步客户端，
普通实验仍走默认连接池。完成 v4 后统一覆盖普通路径，并为所有
请求补 `http_transport_failures.jsonl`，记录连接/发送/响应失败阶段。
流式消费计时仍按原开关启用，不新增重试、不修改 guard 或终态。
该修改消除复用已过期连接这一可能原因，尚未通过新 GPU 批次证明
所有断连已解决，不能回填修改本轮实验身份。

本轮自动删除 71 个已归档且正常完成的 workspace，保留一个失败
现场及所有 trace、patch、报告；服务器停止后 GPU 已释放。

### 下一步

优先查清恢复锚点为何未被 parent 下一请求复用，区分 FULL 前缀
连续性、Mamba checkpoint 位置与 native match 的截断行为。然后
补临近结束的剩余工作训练样本，保持项目隔离与按请求评估。
不因本轮表面 throughput 涨幅扩大实验，也不将动作收益交给
预测模型学习；模型负责阶段和剩余工作，runtime 负责容量与传输。
