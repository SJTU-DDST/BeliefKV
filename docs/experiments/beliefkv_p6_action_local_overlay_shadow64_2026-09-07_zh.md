# P6 Action-local Physical Overlay 64-root High-pressure Shadow

日期：2026-09-07  
代码：`1dd0fef` (`fix(predictor): add action-local physical overlays`)  
运行目录：`experiments/shadow/p6_action_local_overlay_shadow64/20260907T051443Z`

## 目标与配置

本轮只验证 predictor-only 的 action-local physical overlay 和 beneficiary-bound
risk 路径，不开放预测式物理动作。配置保持为 Qwen3-Coder-30B BF16、850K KV
tokens、96 GiB Host pool、`max_running_requests=32`、64 个 root 同时提交。

停止条件为以下任一项：3 个 fresh-positive package；32 个高压下的
closure-complete candidate；高压持续 5 分钟仍无 positive。本轮达到第二项后停止，
不等待 workflow 完成，因此不进入 clean JCT，也不生成 KV 时间线。

## 运行结果

- HBM 峰值达到 95.0%，高压起点约为 89.1%。
- 高压起点后完成 35 个 risk evaluation；全程共评估 279 个 candidate。
- Predictive worker `749/749` 完成，`0 failed`、`0 dropped`、`0 pending`。
- `fresh_positive_before_latest_start=0`，未满足 PREPARE_HOST canary 门槛。
- shutdown ACK 正常返回；无遗留 command、transaction、lease、reservation 或
  restore obligation，shutdown cleanup 未掩盖未决事务。
- decode batch 主要为 31/32，说明服务端存在稳定 GPU-ready backlog；本轮零收益不能
  归因于客户端并发不足。

Beneficiary opportunity 分类如下：

| 分类 | 数量 |
|---|---:|
| capacity available | 314 |
| slot only | 270 |
| near HBM risk | 4 |
| HBM blocked | 2 |
| slot with near HBM risk | 5 |
| slot then HBM blocked | 2 |

这说明大多数 deferred request 受 running slot 或尚未发生的容量条件限制；整体 HBM
pressure 不能自动归因为某个 beneficiary 的 admission deficit。

## Overlay 与物理形态

仅 4 个保存的 snapshot 携带 live action-local overlay。早期 overlay 为约
261--789 MB、2 extents；接近耗尽时，两个逻辑 victim 都扩展为同一祖先 closure：

- copy bytes 约 9.04--9.09 GB；
- 122--124 extents；
- exclusive reclaimable bytes 约 1.38--1.90 GB；
- cross-context bytes 约 7.19--7.66 GB。

因此两个逻辑 context 并不是两笔独立物理动作，且存在明显 closure amplification。
后续候选必须按 physical generation/closure 去重，并在估值前检查
exclusive/copy/cross-context envelope。

## 控制面开销

| 路径 | P50 | P95 | P99 |
|---|---:|---:|---:|
| safe-point capture | 0.263 ms | 18.975 ms | 29.954 ms |
| overlay capture | 0.061 ms | 0.133 ms | 222.970 ms |
| predictive submit | - | 0.039 ms | 0.055 ms |
| eligibility | - | 2.341 ms | 4.113 ms |
| risk total | 1.227 ms | 359.560 ms | 879.760 ms |
| trigger-to-validation | 731 ms | 2517 ms | 11308 ms |

Overlay 分布是双峰的：cheap probe 很轻，但真正调用
`previews_for_context(SHADOW_CONTEXT)` 时需要 200--395 ms。该路径位于 safe point，
不能进入 canary。高压下的 graph/delta apply、belief compose 和 admission 编译也重新
产生明显 GIL 干扰。

## 离线重放

使用同一轮保存的 15 个 snapshot 和当前 shape-aware transfer artifact 重放：

- 15 个 PREPARE_HOST candidate，0 positive、0 eligible；
- 8 个 candidate 存在预测 pressure，最大 overflow 约 5.44 GB；
- 13/15 获得 shape support；因此零收益不能主要归因于 shape OOD；
- scenario failure 中，`projected_beneficiary_hbm_block_unavailable=50`，
  `shadow_completes_after_pressure=2`；
- latest feasible start 已错过约 450 ms。

在线运行把 273 个 candidate 全部标为 `shape_unsupported`，而离线同一 transfer model
只拒绝 2/15，说明 action-local overlay 没有进入在线 targeted transfer estimate，
在线路径错误退化到静态 fallback。

## 裁决与下一步

本轮通过了 workload pressure、worker reliability、PageIndex 和 shutdown correctness，
但没有通过策略价值、动作新鲜度或控制面性能门槛。PREPARE_HOST canary 继续关闭。

下一步只做三项定向修复：

1. overlay 成为 PREPARE 候选的唯一物理证据，禁止回退到 stale 全局 mirror；
2. safe point 只发布紧凑 closure envelope，并在 worker 侧用 bytes/extent 条件查询
   shape-aware transfer model，消除同步完整 bundle 物化；
3. cheap probe 从当前容量判断升级为 bounded-seed beneficiary 的 projected block
   what-if，区分 slot release、running growth 和真实 HBM deficit。

修复后先离线重放已有 snapshot 和 CPU 契约测试；只在出现 fresh-positive 且验证早于
latest-start 时，再运行一轮短高压 shadow。
