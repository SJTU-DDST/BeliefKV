# P6 v58 predictive H2D 自然 64-root gate

日期：2026-09-20  
运行：`experiments/shadow/p6_predictive_wait_shadow_v58/predictive`  
运行时代码：`38e37d5`；后续 Host 语义化清理：`7ae2be6`

## 结论

v58 自然完成全部 64 个 workflow，并验证 `a772a61` 的 atomic H2D native-eviction
修复。该修复将 predictive H2D 物理成功率从 v57 的 `1/18` 提升到 `24/25`，其中
23 笔在首个 GPU service 后进入 `useful` attribution。

这是一次 mechanism gate，不是 baseline/P6 吞吐 A/B；不能直接报告预测调度收益。

## Workload

| 指标 | 数值 |
| --- | ---: |
| wall time | 10,315.76 s |
| workflow result | 64/64 |
| completed outcome | 63 |
| error outcome | 1 |
| measurement-valid / successful | 29 |
| system-eligible completed | 63 |
| LLM request | 7,229 |
| tool call | 14,544 |
| dynamic subagent | 135 |
| join satisfied / timeout | 70 / 1 |

唯一 error 是 `mwaskom__seaborn-3069` 的 `APITimeoutError`。该 workflow 中一个 child
被取消、JOIN timeout，restore-2 obligation 因 `request_aborted` 进入 cancelled。运行期
`restore-2-command-9` 暂时显示 inflight；shutdown drain 将其显式终态为 `cancelled`，
最终无遗留 command/lease/obligation。

## Predictive transfer

| 方向 | count | completed | rejected | completed bytes |
| --- | ---: | ---: | ---: | ---: |
| predictive D2H | 173 | 137 | 36 | 2,595,422,208 |
| predictive H2D | 25 | 24 | 1 | 22,151,823,360 |

Predictive H2D duration：

| 指标 | 数值 |
| --- | ---: |
| P50 | 510.04 ms |
| P95 | 1,245.37 ms |
| max | 1,521.19 ms |

Attribution：

- service lease registered：24；
- first GPU service 后 useful：23；
  useful bytes：20,972,666,880；
- 1 笔 service window 过期后释放；
- 1 笔 H2D 结束但没有 authoritative GPU copy，显式 rejected。

主要 H2D rejection 不再是 authoritative-free-tokens，说明 `allow_native_eviction`
修复有效。剩余单笔失败属于 native H2D completion / ownership race，需要单独修复。

## Resource pressure

Resource snapshot 共 15,716 个：

| 指标 | 数值 |
| --- | ---: |
| HBM pressure mean / max | 97.40% / 100% |
| Host used mean / max | 169.51 GB / 192.00 GB |
| peak resident tokens | 844,263 |
| GPU utilization mean | 28.72% |
| decode tokens/s mean / P95 | 187.58 / 360.43 |

本运行没有包含 `7ae2be6` 的 Host 语义化清理。Host 达到 192 GB 上限，说明下一轮
需要验证 dead/native-writeback/explicit cleanup 优先级和 forced recompute 变化。

## Correctness

Shutdown summary：

- `final=true`；
- `shutdown_state=acknowledged`；
- inflight/queued command 清空；
- active service lease / restore lease / obligation 清空；
- `no_pending_transactions=true`；
- `shutdown_cleanup_did_not_mask_unresolved_transactions=true`；
- `shutdown_summary_complete=true`；
- scheduler exception / OOM / CUDA error 为 0。

## 限制

1. v58 未包含 `7ae2be6` Host 语义化清理；
2. 未与同配置 observed baseline 配对，不能报告吞吐提升；
3. `measurement-valid=29` 受 agent 语义终态和测量资格影响，不能直接等同任务正确性；
4. 1 笔 H2D completion race 和 1 笔 API timeout 需要后续修复；
5. Host 192 GB 全程成为硬容量边界，native writeback、predictive shadow 与 future reentry
   的竞争仍未解决。

## 下一步

1. 修复 request abort 后 inflight restore command 的即时清理，避免等待 shutdown drain；
2. 修复 H2D ended without authoritative GPU copy 的 ownership race；
3. 使用 `7ae2be6` 运行下一轮 64-root gate，统计 Host cleanup mode/bytes 与 predictive
   H2D 成功率；
4. 与冻结 observed baseline 做同配置 A/B，测量 first-service latency、saved stall 和
   workflows/hour。
