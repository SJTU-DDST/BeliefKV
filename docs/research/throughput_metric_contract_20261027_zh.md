# 固定 trace 下的吞吐评测口径

用户指出，固定 trace 的总输出 token 已经固定，全程输出率取决于最后
一个 workflow 完成的时间。因此将其称为 decode 吞吐，或把它与
makespan 改善作为两条独立证据，均不准确。历史数值保留，纠正名称
与解释，不通过改分母或删正常尾部制造性能收益。

## 应用完成指标

所有 workflow 均从原始 workflow_start 计至 workflow_end：

- 完整平均 JCT：每个 workflow 等权，保留正常尾部与原始提交前开销。
- Makespan：最后一个 workflow_end 减第一个 workflow_start。
- 端到端输出吞吐：完整输出 token 数除以 makespan。

对于相同固定输出量，端到端输出吞吐与 makespan 互为倒数关系；
它描述整批任务完成速度，包含工具等待、串行依赖、准入、迁移、
prefill、decode 及收尾。两者不能计作独立的 decode 效率证据。
平均 JCT 与 makespan 仍描述不同目标，均需保留。

## Decode 执行诊断

使用完整原生 forward 记录中的 decode 阶段：

    decode_worker_output_rate =
        sum(observed_decode_output_token_deltas) /
        sum(decode_worker_intervals_seconds)

这是 decode worker 区间内的观测输出率，分子也只包含 decode 阶段
输出。该区间可能含运行时、KV 依赖等待和调度影响，不称为纯 GPU
kernel 吞吐。累加 worker 区间也不等价于完整运行时间。

同时报告完整平均 batch、worker 区间加权 batch、上下文长度和 CUDA
graph padding。解释策略差别时使用共同的 batch/context 分桶检查，
不能只取高并发窗口或不同上下文集合的峰值作为主结果。需要严格
kernel 吞吐时，应另做公共、对称的 CUDA 时间线验证。

## 存在模型需求时的服务吞吐

已有 `scripts/audit_outstanding_request_throughput.py` 还报告：

    demand_outstanding_output_rate =
        complete_output_tokens /
        union(api_received_to_native_complete_intervals_seconds)

该分母保留排队、资格、恢复、prefill 和 decode，只排除没有任何在途
原生模型请求的时段。它用于区分外部等待与服务效率，不等同于 GPU
利用率或纯 decode 吞吐，也不证明全部被排除时间都是工具时间。
客户端尚未提交的就绪工作仍由完整 JCT 计入，不允许仅依据该诊断
隐藏控制开销或工作流尾部。

## V330 Full 的已验证示例

依据 V341 完整工作验证和 V340 完整 decode/原生事件审计：

| 指标 | 数值 | 解释 |
| --- | ---: | --- |
| 完整平均 JCT | 1646.613s | 18 个 workflow 等权 |
| Makespan | 2311.112s | 全部正常尾部保留 |
| 端到端输出吞吐 | 237.838 token/s | 549671 / 2311.112390 |
| API 模型需求在途服务吞吐 | 277.466 token/s | 排除零在途模型请求时段，保留排队和恢复 |
| Decode worker 区间输出率 | 354.557 token/s | 545997 / 1539.940081，仅 decode 观测输出 |
| 完整平均 decode batch | 6.250711 | 按 batch 次数平均 |
| Worker 区间加权 decode batch | 9.023021 | 不是普通 batch 均值或 GPU 利用率 |

固定输出工作下，不能将 237.838 token/s 单独称作 decode 执行效率。
不同分母的三个输出率不能互相作加速比。V330 的匹配 ThunderAgent
因用户预约而中断，尚无本轮同时间表的完整性能对照。

后续只使用 ThunderAgent 的 `thunderagent_acting_decay` 配置。
匹配 baseline 可在只改变 predictive 策略时复用；改变工具时长表、
请求/token/依赖、容量、公共执行环境或其他影响对照的共同条件时，
必须重新检查可比性。V295 用 V269，V330 用重新抽样的 V311，
不能将二者当作已经满足这些条件的同时间表比较。
