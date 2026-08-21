# BeliefKV Native Trace P1-P4 Gate B

日期：2026-08-22

## 1. 结论

P1-P4 的小规模 GPU correctness gate 已通过。证据被拆成真实 8-root agent
workload、确定性 running-retraction restore micro 和确定性 Host recompute micro，
避免把低压 workload 中没有自然迁移误判为机制失败。

本轮只关闭正确性与活性门槛，不报告 JCT、GPU utilization 或
workflows/hour 收益，也未生成 KV 时间线。

## 2. Native 8-root workload

运行目录：
`experiments/raw/p5_gate_b_native8/20260821T173114Z`

- 8 个预注册 root 使用 `native_subagent_2to3`，观察到 16 个动态 child，第一轮
  fan-out 均为 2；本轮没有 parent 判断需要第二轮委派。
- 共观察到 644 次 LLM request 和 1,119 次工具调用。
- 4 个 workflow 自然完成并满足 system JCT gate，3 个满足 native-agent JCT
  gate；其余 4 个触发唯一 ActivationDeadline。
- 所有 deadline workflow 均完成 descendant abort 和服务端清理，最大
  server-terminal latency 为 561.17 ms，低于 5 秒门槛。
- shutdown 时 running/waiting 均为 0，无 command、transaction、lease、funding
  或 restore obligation 残留。
- 该 workload 的峰值 native resident pressure 仅 21.32%，因此没有自然
  D2H/H2D；它只用于 P1/deadline 与整体守恒验证。

## 3. P2 beneficiary-bound restore

通过运行：
`experiments/raw/p5_gate_b_restore_micro/20260821T182830Z-slots2-fixed`

专用冻结 profile 仅将 `max_running_requests` 从 32 改为 2，使 victim 和 anchor
占满 slot，replacement 成为真实 waiting beneficiary。模型、850K KV pool、
96 GiB Host 和迁移阈值均未改变。

验证结果：

- `retraction-1` 由一个原子 JointPlan action group 创建；
- D2H 完成 3,222,011,904 bytes；
- beneficiary 获得真实 GPU service；
- H2D 恢复同量 KV，restore obligation `restore-1` 最终为 `satisfied`；
- victim 恢复后记录 340 个 service sample；
- command/ACK、obligation、transaction、lease 和 funding 全部守恒。

验证器产物：`restore_gate_analysis.json`，所有 14 项检查通过。

## 4. P3 Host drop/recompute

最终通过运行：
`experiments/raw/p5_gate_b_host_recompute/20260821T192523Z-final`

该诊断 gate 先让一个 32K parent context 完成 LLM turn，再发布真实
`TOOL_START` 进入 `WAIT_TOOL`。test hook 只负责选择一次生产
`PhysicalBundlePreview`；D2H、Host lifecycle、generation-safe Host drop、native
demand-load 和 recompute service 均走正式数据面。

验证结果：

- 原子 D2H 完成 3,231,154,176 bytes，共 4 个 extent；
- Host 高水位触发 `DROP_HOST_CONTEXT`，安全删除 6,193,152 bytes CPU_ONLY KV；
- context 被标记为 `recompute_required`；
- 同一 context continuation 由 native path 恢复其余 prefix，并对被删除部分执行
  17-token uncached prefill，随后获得 GPU service；
- 最终无 active restore obligation、in-flight command 或 pending transaction；
- `all_online_actions_have_source_joint_plan_id=true`。test hook 明确标记为
  `test_hook`，Host 水位清理明确标记为 `lifecycle`，均不冒充 JointPlan 动作。

验证器产物：`host_recompute_gate_analysis.json`，所有 9 项检查通过。

两次早期 P3 diagnostic 因非原子 context offload 形成 partial D2H，已排除。根因是
leaf-first `write_backup()` 给祖先增加传输锁；最终 gate 改为复用 production
physical bundle 的 parent-to-leaf 原子提交顺序。

## 5. CPU 回归

| 测试组 | 结果 |
|---|---:|
| core control/policy/runtime | 782 passed, 5 skipped, 2 deselected |
| Deep Agents/runtime/collection | 117 passed |
| H200 runtime profile | 15 passed |
| P2 restore/retraction 定向 | 30 passed |
| P3 Host/bundle/retraction 定向 | 54 passed |

两个 deselected 测试需要完整 CUDA toolkit；真实 H200 server 已成功启动并覆盖
对应 SGLang 导入和 allocator 路径。

## 6. 下一步

进入 predictor-off Gate C：使用预注册 64-root `native_subagent_2to3`、全部 root
eager 提交、SGLang max running 32、850K KV pool 和 96 GiB Host。60 分钟检查
starvation、HBM/Host pressure、replacement/rescue 与吞吐；机制正常则允许任务继续
自然完成。显式迁移次数不是通过标准。
