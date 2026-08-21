# BeliefKV Oracle v2 CPU 反事实估计：有限候选修正版

日期：2026-08-20

状态：历史 diagnostic。CPU Counterfactual Oracle 已退出正式收益门禁；该报告只保留模拟器重建与契约证据，后续 GPU 实验不再等待本报告的 opportunity/gain gate。当前路线见 ../beliefkv_gpu_native_subagent_oracle_plan_2026-08-20_zh.md。


## 1. 裁决

本轮是 **CPU Counterfactual Finite-Candidate Oracle Lower Bound**，不是最终 GPU O0--O3，也不是全局最优 Oracle。

审阅后完成三项修复：

1. C1 将 C0 和四种固定 execution policy 分别完整模拟，按真实 whole-run makespan 取最优；C3 同时比较 C0、C2、全部 C1 和四种 execution+C2 组合。因此严格满足 `C1 >= C0`、`C3 >= max(C1,C2)` 的吞吐支配性。
2. Joint-opportunity gate 只读取同一条 `C0 + NOMINAL + measured-fastpath` row，不再跨 service envelope 拼接最大值。
3. Opportunity 计费将 victim-beneficiary pair、去重 victim byte-time 和去重 beneficiary blocked-work 分开，并区分 eviction、stall-free round-trip 和 net-positive 三类窗口。

旧 32-root trace 复跑后，C1/C3 相对 C0 的收益区间为 `[0, 0.523%]`，C2 为 0，C3 相对 `max(C1,C2)` 的 synergy 为 0。该结果只证明有限 execution 候选不会劣于 no-op；它仍未证明 execution-KV joint synergy。

## 2. 四臂准确语义

| Arm | 当前 CPU 语义 |
|---|---|
| C0 | observed-order work-conserving execution + reactive LRU-style KV |
| C1 | C0 加四种固定 execution policy，分别完整运行后选择 makespan 最小者 |
| C2 | observed execution + causal-next-use KV、proactive D2H shadow、latest-feasible H2D |
| C3 | C0、C2、全部 C1 及四种 execution+C2 whole-run 候选中的最优者 |

四种 execution policy 为 observed package、minimum remaining demand、action unlock 和 maximum batch fill。这里的“最优”仅针对有限候选集合，不代表全排列或全局最优调度。

## 3. Opportunity 契约

机会统计不再只检查 D2H：

```text
eviction_opportunity:
  parked migratable victim
  AND HBM-blocked ready beneficiary
  AND slack > D2H + commit_guard
  AND future reentry
  AND Host feasible

stall_free_round_trip:
  eviction_opportunity
  AND slack > D2H + H2D + 2 * commit_guard

net_positive_opportunity:
  eviction_opportunity
  AND beneficiary unlock gain > restore stall
```

统计量分别为：

- `opportunity_pair_count`：唯一 victim-beneficiary pair 数；
- `*_window_count`：同一 pair 离开后再次进入机会窗口可形成新 window；
- `*_unique_victim_byte_ms`：每个时间区间按 victim 去重，同一 victim 阻塞多个 beneficiary 只计算一次 KV；
- `*_blocked_beneficiary_work_ms`：每个时间区间按 beneficiary 去重。

正式资格行固定为：

```text
C0 + nominal_p50_graph16 + measured_fastpath
```

三个 gate 字段必须在这同一 row 内同时满足，输出 `source_row_id`；只有通过时才设置 `qualifying_row_id`。

## 4. 冻结输入与 C0 校验

输入仍是两条 H200 BF16 `parallel_analysis_2to3` trace，共 32 个 root：

| Trace | Workflow | LLM call | Tool | Invocation | SPAWN | JOIN |
|---|---:|---:|---:|---:|---:|---:|
| Train-01 | 16 | 1,241 | 1,824 | 48 | 32 | 16 |
| Train-02 | 16 | 1,249 | 1,960 | 48 | 32 | 16 |

固定 HBM 850,000 tokens、Host 96 GiB、max running 32、prefill chunk 16,384 tokens。

| Trace | Makespan error | Decode batch error | Pressure error | Zero migration |
|---|---:|---:|---:|---|
| Train-01 | 2.98% | 0.59% | 3.67 pp | pass |
| Train-02 | 2.70% | 0.12% | 0.66 pp | pass |

该校验只覆盖无迁移区间的执行重建；没有验证真实高压下的 D2H/H2D、Host 竞争和 restore stall。

## 5. 32-root Whole-Run 结果

### 5.1 Measured-fastpath

| Service envelope | C0 wf/h | C1 wf/h | C2 wf/h | C3 wf/h | C1 winner | C3 winner |
|---|---:|---:|---:|---:|---|---|
| SLOW | 3.628 | 3.628 | 3.628 | 3.628 | C0 no-op | C0 no-op |
| NOMINAL | 14.804 | 14.865 | 14.804 | 14.865 | max-batch-fill | C1 max-batch-fill |
| GRAPH32 sensitivity | 21.103 | 21.103 | 21.103 | 21.103 | C0 no-op | C0 no-op |

zero-overhead 的最大 C1/C3 gain 为 0.523%。跨六种 service/overhead 假设：

- C1：`[0, 0.523%]`，nonnegative；
- C2：`[0, 0]`；
- C3：`[0, 0.523%]`，nonnegative；
- C3 synergy：`[0, 0]`。

### 5.2 Joint opportunity

资格 row 为：

```text
c0_current:nominal_p50_graph16:measured_fastpath:c0_observed
```

该 row 中：

- eviction window：0；
- stall-free round-trip window：0；
- net-positive window：0；
- unique victim byte-ms：0；
- blocked beneficiary work-ms：0。

因此 gate 未通过，`qualifying_row_id=null`。这说明旧 32-root trace 没有 KV 容量竞争机会，不能据此否定 C2，也不能投入 GPU O0--O3。

结果工件：

`experiments/oracle/cpu_counterfactual_v2_whole_run/estimate32_loadscale/cpu_counterfactual_oracle_estimate.json`

旧 `cpu_counterfactual_v2_corrected/` 结果保留为 pre-dominance 历史工件，不再用于 C1/C3 收益结论。

## 6. 自然机会采集池

`configs/p6/oracle_v2_workloads_v5/natural_opportunity_collection_plan.json` 的证据角色是 **natural opportunity prevalence pool**，不是 Representative benchmark：

- 64 个 train-only 预注册实例，calibration/test 保持封存；
- 所有预注册轨迹都进入自然 workload opportunity prevalence 的分母；
- guard/timeout 轨迹中，censor 前完整的 parked/reentry 局部区间仍可贡献 opportunity 统计；
- 只有 clean trajectory 可进入 whole-run Oracle/JCT truth；
- 不因 clean 与否或 Oracle 结果替换任务；
- 后两波以 Django 为主，必须按 project 报告，不能声称均衡代表目标分布。

## 7. 可执行 KV-Pressure Workload

冻结入口：

`configs/p6/oracle_v2_workloads_v5/kv_pressure_execution_plan.json`

执行契约：

- 32 个固定 train instance，固定顺序，不做 outcome-based replacement；
- `parallel_analysis_2to3`，每个 root 产生 2--3 个真实 child；
- parent prompt 目标循环为 64K/96K/128K/160K；
- context pack 从对应 `base_commit` 的 Git tracked text 按固定 seed 构造；
- Qwen tokenizer 离线冻结实际 token 数，运行时不依赖 tokenizer；
- 32 个 pack 的目标误差最大 2 tokens，总压缩大小约 3.28 MiB；
- 两批 root 各 16 个，第二批在 30 秒后提交；
- wait 来源是真实 child LLM 与 Docker tool execution，不注入 synthetic sleep；
- `actual_parent_prompt + output_reserve + runtime_overhead_reserve < 262144`；
- 850K KV pool、96 GiB Host、32 running，不缩小 KV pool。

launcher 现已从冻结 batch 读取 arrival schedule。CLI 参数仅能断言相同值，不能覆盖清单；`saturated_root_backlog` 与分批到达互斥。

停止规则：先运行 C0。若同一 NOMINAL+measured row 未通过 stall-free exact joint gate，则报告 insufficient opportunity，不运行 C1--C3，也不替换任务。

## 8. 尚未完成

1. 新 natural opportunity workflow 尚未采集。
2. KV-pressure C0 尚未产生 truth/physical sidecar，也未经过 CPU exact gate。
3. C1/C3 仍是有限候选 lower bound，不是全局 Oracle。
4. CPU 模型尚未在真实高压迁移周期上完成 C0 校验。
5. exact incremental action boundary 仍不可用，不支持 early dispatch/run-to-action。

## 9. 下一步

1. 按冻结顺序采集 natural pool，所有任务统计 prevalence，clean 子集导出 whole-run truth。
2. 运行冻结 KV-pressure 的 C0 observed arm，先生成 truth/physical sidecar。
3. 只重放 C0 并执行同一 row 的 stall-free exact gate。
4. gate 通过后计算 CPU C0--C3；要求 C3 相对 C0 约 10%，且明显优于 C1/C2，才运行真实 GPU O0--O3。
5. stress 中仍无明显收益则降低核心论点；只有 stress 有收益则明确限定适用场景。

## 10. 验证

- Oracle contract/provider/estimator/runner：35 passed；
- Deep Agents runtime + collection：90 passed；
- 冻结压力清单：32/32 context pack 可加载，实际 token target 最大误差 2；
- 32-root finite-candidate matrix：60 个完整 rollout，完成且 C1/C3 dominance 断言通过；
- `git diff --check`：通过；
- GPU/SGLang：本轮未启动。
