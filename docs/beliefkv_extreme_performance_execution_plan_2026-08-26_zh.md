# BeliefKV 极致性能执行计划

更新日期：2026-08-26

## 目标与边界

当前主目标是最大化动态 subagent workload 的 workflows/hour、GPU utilization 和
decode throughput。Oracle v2、SGLang 升级和新的功能扩展暂停。后续实验以
`perf-baseline-h200-20260826` 为正确性基线，所有 arm 使用相同模型、runtime profile、
workload 和观测配置。

性能优化不放宽 residency、allocator、ACK、transaction 和 shutdown 的正确性不变量。
Performance Mode 只删除冗余观测和重复验证，不删除物理事务终态。

## 实施阶段

### P0 正确性基线

已完成。基线可完整运行且无 unresolved transfer、lease、reservation 或 transaction。

### P1 JointPlan 输入压缩

已完成首版：

- transfer telemetry 改为有界 journal 和增量 cursor；
- safe-point worker 使用 revisioned page delta；
- Performance Mode 只发布 context-level physical summary；
- HBM/Host resident accounting 与 workflow memory charge 改为 changed-page 增量维护；
- exact Radix closure 只在选中 semantic target 后于 safe point 定向重物化；
- normal semantic path 只构造一次 JointPlan/read-set。

16,384 pages、32 runnable、384 changed pages 的 CPU 结果：

| 路径 | P99 |
|---|---:|
| no-action PolicyInput build | 0.248 ms |
| single-lock delta build | 0.234 ms |
| 384-page worker delta apply | 1.875 ms |
| semantic JointPlan wall | 3.646 ms |
| single-residency snapshot build | 0.307 ms（单样本） |

因此 no-action P99 < 1 ms、physical semantic planning P99 < 5 ms 的 CPU 门槛通过。
context summary 中的 reclaimable bytes 是局部独占上界；它不能直接授权迁移，safe-point
closure rematerialization 和 allocator validation 仍是硬门禁。

### P2 Performance Mode

已完成首版：

- correctness audit 始终保留；
- metrics 使用 allowlist，并在 JSON/queue 前过滤；
- GPU service 只保留固定大小 phase/batch aggregate；
- 禁用逐 request token trace、完整 physical checkpoint extent scan 和 reference snapshot
  持久化；
- 保留吞吐、JCT、batch、HBM/Host、transfer dispatch/ACK、admission 和 shutdown 指标。

### P3 TransferEngineV2

待实现：

- D2H/H2D 独立 logical lane，每个方向最多一笔 inflight；
- bridge 一次 safe point 可提交两个方向的互不相交事务；
- restore H2D 优先于 reactive D2H，reactive D2H 优先于 speculative D2H；
- 使用 SGLang 已有 write/load CUDA stream 和 pinned Host pool；
- D2H 相邻 extent 在 native write queue 合并，按 command 聚合 ACK；
- closure overlap、allocator reservation 和 callback ID 必须守恒。

### P4 Causal Package Planner

待实现。最小动作包绑定：

- ready execution set；
- startup + projected growth HBM demand；
- victim context 与定向 physical closure；
- expected action-unlock boundary；
- transfer/recompute cost；
- beneficiary first-service obligation。

只有 saved beneficiary stall 的保守下界大于迁移与反向 restore 成本时，才允许
COMMIT_CPU。预测器仅发布 action-specific causal slack 和 unlock distribution，不直接
发送物理命令。

## 验证顺序

1. CPU 全量回归和长跑 transaction/liveness gate。
2. 双向 transfer microbenchmark，验证真实 D2H/H2D overlap 和 callback 守恒。
3. 固定 workload 比较 Performance Mode 开关的 instrumentation overhead。
4. 相同冻结动态 subagent workload 比较 baseline P5、优化 P5、完整 P6。
5. 主指标：workflows/hour、GPU utilization、decode tokens/s、useful transfer ratio、
   beneficiary saved stall 和 reverse-transfer rate。

