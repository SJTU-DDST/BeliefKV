# P6 PREPARE 收益绑定与 PREFETCH 证书刷新修复

日期：2026-09-19  
运行：`experiments/shadow/p6_predictive_wait_shadow_v50/predictive`  
修复提交：`87411cc`

## 结论

v50 是仍在自然运行中的旧代码 gate。它证明 wait-shadow 控制链已经打通，但暴露两个效用问题：

1. `PREPARE_HOST` 能完成 D2H，却没有绑定 beneficiary，无法形成 prepared causal binding；
2. `PREFETCH_GPU` 有 semantic watch，但到达 latest-start 时使用小时级旧证书，被 safe point
   正确拒绝。

因此，v50 不能作为 predictive KV 动作收益结论；它只用于定位问题。

## v50 中期证据

检查时 workload 仍在运行，HBM 约 98.7%，Host pool 接近满载。

### PREPARE_HOST

| 指标 | 数值 |
| --- | ---: |
| semantic command registered | 510 |
| D2H completed | 453 |
| predictive D2H bytes | 10.26 GiB |
| D2H duration P50 / P95 | 137.28 ms / 406.13 ms |
| prepared 后因 return wasted | 336 |
| failed | 57 |
| engine-locked before commit | 56 |
| prepared causal binding registered | 0 |

wasted 样本对应工具调用并不短：P50 约 1.04 秒、P90 约 3.45 秒。核心问题是这些 PREPARE 没有
beneficiary-deficit 证据，ACK 后无法进入 observed planner 的 prepared binding 消费路径。

### PREFETCH_GPU

| 指标 | 数值 |
| --- | ---: |
| semantic intent | 19 |
| unique root context | 4 |
| watch activation events | 7 |
| unique activated intent | 3 |
| activation lag P50 / max | 107.52 ms / 488.96 ms |
| predictive H2D ACK | 0 |

三个到达 latest-start 的 intent 在提交端被拒绝，证书年龄分别为约 3,376 秒、4,793 秒和
6,180 秒。拒绝原因包含 context epoch / invocation revision 变化与 observed residency priority。
telemetry 中的 49 笔 `prefetch_context` H2D 都是 `restore-*` command，且
`predictive_intent_id=null`，属于 reactive/restore 路径。

## 修复

1. 低压直接抑制：gross HBM pressure 低于 observed admission watermark 时不再发布
   wait-shadow PREPARE，避免低价值 shadow copy。
2. beneficiary-bound PREPARE：发布前从前四个 observed seed beneficiary 中选择当前可见、
   存在 immediate/future HBM deficit 的请求；intent 携带 request/context/epoch、startup、
   growth、deficit 和 causal generation。D2H ACK 后可注册 prepared causal binding。
3. prefetch 证书新鲜度：到达 latest-start 的 watch 必须使用满足 refresh 条件且生成时间在
   60 秒内的 intent；否则强制 worker refresh 并每 50 ms 有界重试，不激活旧读集。
4. transient engine lock：D2H 完成后若 Radix node 仅被 scheduler 短暂 lock，command 保持
   pending 并下一轮重试；不再把已完成 DMA 判成失败。extent mutation、clean copy 丢失等
   真实错误仍 fail closed。

## 验证

- 定向回归：7 passed；
- `tests/test_sglang_adapter.py`：200 passed，2 deselected，8 subtests passed；
- 两项 deselected 均依赖当前 shell 缺失的 `CUDA_HOME/deep_gemm` SGLang import；
- `py_compile` 与 `git diff --check` 通过。

## 下一 gate

v50 自然结束后，用 `87411cc` 运行新的短高压 gate：

- low-pressure PREPARE suppression 生效；
- 至少一个 `PREPARE_HOST ACK -> prepared_causal_binding_registered -> real
  ReclaimRequirement 消费`；
- engine-locked terminal failure 为 0，允许记录 transient lock wait；
- 到 latest-start 时 active prefetch intent age <=60 秒；
- 出现 `PREFETCH_GPU -> predictive H2D -> ACK`，telemetry 保留 predictive intent ID；
- 无 worker failure、orphan command/lease/transaction，shutdown masking gate 为 true。
