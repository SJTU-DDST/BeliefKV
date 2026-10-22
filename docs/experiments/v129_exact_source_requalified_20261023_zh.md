# V129 精确源采集与重新审计

V129 已收集全部 36 个 workflow，原监督和驱动进程均已退出。它是一次
预先登记的轨迹采集，启用了诊断 token 快照，不作为新的性能基线。
原始采集监督状态 `capture_failed` 和退出码 1 保留，没有改写为成功。

## 数据与资格

| 指标 | 完整源 |
| --- | ---: |
| 系统测量 workflow | 36 |
| 无 guard 的原生 agent JCT 合格项 | 35 |
| 模型请求，含内部压缩 | 8165 |
| 原生输入 token | 602782233 |
| 原生输出 token | 2352172 |
| JOIN / 嵌套 JOIN | 73 / 1 |
| 动态 subagent | 123 |
| 工具 start/end 对 | 9846 |
| 内部 CALL / compact | 2 / 2 |
| 客户端采集时长 | 4950.705871s |

`python_api_gateway_hard_009_bug_investigation_expert_01` 记录
`guard_intervened_completion`，保留在完整总体中并单列资格限制。
原退出码 1 不能解释为服务端崩溃或 token 录制失败。
无 guard 也不等于独立任务正确性评分，本轮没有独立评分。

## 原监督为何失败

冻结监督器的 token 审计完整通过：所有输入输出身份、原生用量和停止
快照齐全。失败来自其旧版依赖编译器不支持 9 个
`tool_wait_observation`，不是缺少 token，因而不需要重跑采集。

这些事件的实际来源是 `DeepAgentsRuntimeAdapter.observe_tool_wait`，
明确以 `control=False` 发布，属于 trace-only 诊断。若把它们新发送给
回放控制器，会改变原来的信息可见范围。

修复只识别该明确的 `deepagents_tool_wait_shadow` 来源；先按 workflow、
invocation 和实际 tool run/call ID 绑定唯一活跃区间，并核对 elapsed
时间，再与原有生成中诊断一起排除出控制回放。未知来源、错误身份、
工具结束后观测或错误 elapsed 仍拒绝，不通用忽略未知事件。

## 新证据

- 原 `token_evidence.json` 与失败 `causal_evidence.json` 保留。
- V2957：66 项聚焦检查通过，包括原始控制图/释放时刻不变，以及错误
  观测和未知来源的拒绝。
- V2958：同一完整源重新编译通过，所有 8165 个模型请求、73 个 JOIN、
  工具和压缩依赖闭合；token 审计继续通过。保留完整输入前缀及源码哈希。
- V2959：完整严格 token corpus 验证通过，包括原始停止参数和结束原因。
  预先指定前 8 root 的前 3 请求，共 24 次，输入 114454–208158 token，
  全部原生 finish reason 为 stop，同 context 没有 session 切换。
- V2960：143 个模块、4694 项及原有 2 个 subtest 通过，无失败或 skip，
  保留上一轮全部 4686 项。沿用一个真实 Docker progress 集成的明确
  原范围外 deselect。V2961 绑定最终源码。

新冻结运行必须显式引用 V2958/V2959 的离线资格和对应原文件哈希，
不能仅检查旧监督状态，也不能覆盖旧失败结果。
后续仍需真实 GPU token/EOS/session 检查、完整 live control 回放和
同工作量 Full/native/简单调度对照。当前没有新的性能验收结论。
