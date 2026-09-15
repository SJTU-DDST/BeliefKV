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

CPU 和数据面实现已完成，GPU 双向门禁尚未开放：

- command queue 和 controller 已拆分 D2H/H2D logical lane，每个方向最多一笔
  inflight，并拒绝 closure 相交的双向事务；
- bridge 可在一个 safe point 提交互不相交的两笔事务，优先级固定为 restore H2D、
  reactive D2H、speculative D2H；
- SGLang 继续使用独立 write/load CUDA stream 和预分配 pinned Host pool；
- 同一个 physical bundle 的 D2H extents 改为一次 Host 分配批次、一次 native queue
  operation 和 grouped node ACK，消除逐 extent synchronize/launch；
- transfer_engine_v2_enabled 默认关闭。现有硬件 artifact 不支持 concurrent PCIe
  transfer，因此 backend capability 仍冻结为单 inflight，不能用于正式实验。

### P4 Causal Package Planner

observed-state 首版已完成。最小动作包绑定：

- HBM-blocked beneficiary 和 startup/growth deficit；
- victim context、定向 physical closure 与实际 exclusive reclaim；
- expected beneficiary first-service boundary；
- D2H 与未来 H2D restore 成本；
- Host capacity 和 beneficiary saved-stall 下界；
- package ID、transfer cost、net benefit 与 first-service latency 归因。

Residency planner 不再因单独的 emergency pressure 选择 victim。只有明确 beneficiary
存在、safe point 重物化后的实际 closure 能满足 deficit，且 saved stall 大于按实际
bytes/extent 重新估计的 transfer/restore cost，才允许 COMMIT_CPU 或 DROP。预测器后续
只能更新 causal slack 和 action-unlock 分布，不能直接发送物理命令。

## 验证顺序

1. CPU 全量回归和长跑 transaction/liveness gate。
2. 双向 transfer microbenchmark，验证真实 D2H/H2D overlap 和 callback 守恒。
3. 固定 workload 比较 Performance Mode 开关的 instrumentation overhead。
4. 相同冻结动态 subagent workload 比较 baseline P5、优化 P5、完整 P6。
5. 主指标：workflows/hour、GPU utilization、decode tokens/s、useful transfer ratio、
   beneficiary saved stall 和 reverse-transfer rate。

