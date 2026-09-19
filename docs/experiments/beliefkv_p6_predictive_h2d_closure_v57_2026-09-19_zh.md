# P6 v57 predictive H2D 物理闭环

日期：2026-09-19  
运行：`experiments/shadow/p6_predictive_wait_shadow_v57/predictive`  
运行时代码：`312ab69`；后续 atomic H2D 修复：`a772a61`

## 结论

v57 首次在自然 64-root 高压 workload 中完成 predictive H2D 到 useful attribution 的完整
闭环。检查时 workload 仍在运行，本报告是机制 gate，不是吞吐结论。

成功链路为：

```text
request visible pending
  -> observed_service_prefetch semantic intent
  -> same-safe-point physical commit
  -> PREFETCH_CONTEXT command queue
  -> H2D dispatch / ACK
  -> admission rescue + service lease
  -> request_started
  -> first GPU service
  -> predictive_action_outcome(state=useful)
  -> service lease release
```

## 成功样本

目标 context：`deepagents-context:0ebb8015fb97ff66`  
request：`beliefkv:01a0b9c3-e661-7ad2-ad5a-e640b4513462`  
intent：`predictive-service-prefetch:beliefkv:01a0b9c3-e661-7ad2-ad5a-e640b4513462:c10:r454089`  
command：`predictive-residency-110-command`

| 指标 | 数值 |
| --- | ---: |
| H2D bytes | 325,189,632 |
| extent count | 11 |
| H2D duration | 89.72 ms |
| request visible -> intent | 30.26 s |
| intent -> H2D submit | 70.97 ms |
| H2D ACK -> service lease | 460.9 ms |
| service lease -> request_started | 2.94 s |
| request_started -> useful attribution | 914.3 ms |

telemetry 中 `direction=h2d`、`command_kind=prefetch_context`、`status=completed`，且
`predictive_intent_id` 非空，因此这不是 reactive `native_demand_load`。

## 中期统计

检查时 predictive 传输为：

| 方向 | count | completed | rejected | completed bytes |
| --- | ---: | ---: | ---: | ---: |
| D2H | 148 | 121 | 27 | 2,067,038,208 |
| H2D | 18 | 1 | 17 | 325,189,632 |

17 笔 H2D 拒绝原因均为旧的 atomic H2D authoritative-free-tokens 检查。成功一笔说明事务和
归因链正确；失败 17 笔说明容量路径仍过窄。`a772a61` 已为携带
`allow_native_eviction` 的 service H2D 放开该检查，并继续交给 native HiCache 安全
eviction。该修复尚未进入 v57 运行时。

## 健康状态

检查时：

- server/workload tmux 均存活；
- 无 scheduler exception、SIGQUIT、OOM 或 CUDA error；
- inflight/queued command 为空；
- active service lease 与 restore obligation 为空；
- `no_pending_transactions=true`；
- 所有已观测在线动作均有 source JointPlan ID。

## 验证

当前 worktree 回归：`201 passed, 2 deselected, 8 subtests passed`。两项 deselected 仍仅因
当前 shell 缺失 `CUDA_HOME/deep_gemm` 导入。

## 下一门槛

用 `a772a61` 运行下一轮短高压 gate：

- predictive H2D completed 明显高于 1/18；
- rejected 原因中不再出现可通过 native eviction 解决的 authoritative-free-tokens；
- H2D 后 first-service latency 和 saved stall 可配对测量；
- 无 orphan command/lease/transaction；
- shutdown correctness 全部通过。
