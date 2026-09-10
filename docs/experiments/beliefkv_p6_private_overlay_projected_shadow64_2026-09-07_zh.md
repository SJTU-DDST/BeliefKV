# P6 Action-Local Overlay 高压 Shadow64 验证

日期：2026-09-07

## 结论

本轮验证了 64-root workload 能形成持续 HBM 压力，且 action-local beneficiary probe、
Predictive worker 和受控关闭路径均保持活性。但没有出现可归因于 HBM 的 projected
beneficiary，也没有 fresh-positive package，因此继续关闭 `PREPARE_HOST` canary。

实验还暴露了一个独立的软件缺陷：同一 material risk signature 的 seed-generation
refresh 会在 delta coalescing 时清除先前发布的 authoritative overlay，worker 随后回退到
旧 `worker_page_mirror` 并产生无效候选。该问题已在实验后修复并加入回归测试；本轮
172 个在线候选及其 stale certificate 不能作为策略收益证据。

## 冻结配置

- 有效运行：`experiments/shadow/p6_private_overlay_projected_shadow64/20260907T085549Z`
- 排除的配置错误运行：同级 `20260907T_current_invalid_api_base`，其 API base 缺少
  `/v1`，64 个调用均为 HTTP 404，不进入任何实验统计
- 模型：Qwen3-Coder-30B-A3B-Instruct BF16，NVIDIA H200 NVL
- KV pool：850,000 tokens；Host pool：96 GiB
- `max_running_requests=32`，CUDA Graph 最大 batch 32
- 64 个预注册 root 同时提交，`native_subagent_2to3`
- observed P5 在线；predictor/risk 只读；predictive physical action 关闭
- 旧版 beneficiary projection horizon：2,000 ms；该值仅描述本轮历史探针，后续实现不再将其作为硬门禁
- 达到高压并持续观测后受控停止，不评价 workflow JCT 或吞吐

## 高压与活性

- HBM 峰值：97.77%
- HBM >=80% 的观测持续时间：625.43 秒
- 峰值 migratable KV：61.85 GB；engine-locked KV：17.77 GB
- 运行期间维持约 30-32 running，并存在约 94-96 waiting backlog
- Host KV：0；策略性 D2H/H2D：0
- Predictive worker：806 submitted / 806 completed / 0 failed / 0 dropped / 0 pending
- 受控关闭后无 pending transaction、command、lease、reservation 或 obligation
- `shutdown_cleanup_did_not_mask_unresolved_transactions=true`

高 HBM 占用不是 predictive KV opportunity 的充分条件。旧 probe 对 680 次 material
hint 的分类为：`capacity_available=435`、`slot_only=245`，没有
`beneficiary_hbm_blocked` 或 `beneficiary_slot_then_hbm_blocked`。这只说明 bounded seed
首个 deferred request 的一个 admission quantum 在旧版 2 秒近似下没有形成 projected
deficit，不能排除长 prefill 的后续增长、排名第 2--4 的 beneficiary 或更晚 pressure。

实验后对这 680 次 probe 做了 2 秒 look-ahead audit：44 次随后获得 service，532 次
处于 slot 饱和且没有 service，104 次缺少直接证据；没有观察到显式 native HBM rejection
意义下的严格 false negative。由于旧日志未保存未选中的 deferred candidates，该审计
不能估计完整机会探针的 false-negative rate。

离线重放 20 个冻结 snapshot 后：20 个旧定义候选均有 shape support，但 pressure、
positive 和 eligible 数量均为 0；80 个 scenario 全部为
`projected_beneficiary_hbm_block_unavailable`。该结果只适用于旧版首候选/固定 horizon
定义，不应外推为 workload 没有预测式 KV 机会。

在线路径中出现的 172 个候选来自错误的 `worker_page_mirror` fallback，而非
action-local overlay。它们的 expected benefit 全为负，recourse credit 全为 0，且
certificate 172/172 stale。该数据只用于定位 overlay 生命周期缺陷。

## 控制面

| 指标 | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| action-local overlay capture | 0.072 ms | 0.110 ms | 0.121 ms |
| predictive submit | 0.024 ms | 0.038 ms | 0.045 ms |
| eligibility | 0.774 ms | 1.646 ms | 2.412 ms |
| safe-point delta capture | 0.263 ms | 22.150 ms | 30.045 ms |
| risk evaluation | 0.955 ms | 336.755 ms | 386.566 ms |

`safe-point` 高分位仍受高压 Python/GIL 路径影响。172 次完整 risk evaluation 的 planning
P50/P95 为 270.51/383.57 ms，但它们由 overlay 被误清除后的旧 mirror fallback 触发，
不能代表修复后候选局部路径的正常成本。cheap no-candidate 路径仍占 620/806。

## 实验后修复

`_maybe_publish_observed_seed_hint_delta()` 现在只有在以下情况才设置
`action_local_overlay_replaced=true`：

- material risk signature 改变；
- hint 被清空。

仅 seed generation 刷新时保留已有 overlay/probe，避免 capacity-one latest-wins
coalescing 清除 authoritative physical evidence。配置生成器同时显式写入 projection
horizon，避免实验依赖手工 JSON 修改。

验证结果：`196 passed, 2 deselected, 1 warning, 8 subtests passed`。两个 deselected
测试需要当前环境未设置的 `CUDA_HOME`，与本次逻辑无关；`git diff --check` 通过。

## 裁决

- 通过：持续高压、cheap beneficiary 分类、worker 活性、overlay capture 开销、shutdown
  守恒。
- 未通过：beneficiary-bound positive package、及时 fresh certificate、canary 门槛。
- 不应通过延长固定 horizon、降低收益阈值或制造 HBM credit 来产生正例。
- 下一次 GPU 运行应使用修复后的代码，并以 future-growth deficit 且 validation 早于
  latest feasible start 为先决条件；不等待当前 beneficiary 已经真实 HBM-blocked，也不
  再进入旧 mirror scenario evaluation。
