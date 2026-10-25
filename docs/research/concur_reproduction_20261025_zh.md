# CONCUR 在当前框架中的复现

状态：论文策略和动态 JOIN 适配已经接线，完整 CPU 资格与 GPU 结果待验。
它不是新的联合方案，也没有借用 BeliefKV 的预取、备份、驻留或 JOIN 优先。

## 论文依据与明确补全

依据 arXiv `2601.22705` 第 4.2–4.3 节及第 5 节参数。本次保存的原始材料为
`/home/longhao/experiment/ops/paper_baselines_20261024/papers/concur.pdf`、
同目录 `concur.txt`；原始页面为 `https://arxiv.org/abs/2601.22705`。

论文明确给出了 agent 级 admit/pause/resume、跨工具调用连续性、非抢断
边界和 AIMD 控制式。沿用 alpha=2、beta=0.5、Ulow=0.2、Uhigh=0.5、
Hthresh=0.2。窗口控制 agent 数，running/decode 仍分别为 24。

材料没有明确控制周期、初始窗口、缩窗 victim 同序或动态 JOIN 规则。
这些部分按以下公开声明补全，不能称为作者代码的逐行复现：

- 每 1000ms 最多更新一次。初始窗口为启动时已知的 root 数，本轨迹为 18。
  最大窗口为 512，来自统一准入候选上限，不读取未来会出现的 agent 数。
- FIFO 恢复等待中的 agent。缩窗先暂停未运行的较晚 agent；仍运行的
  请求只标记为在调用完成后暂停，释放前不虚报可用 permit。
- 工具等待保留已准入 agent 的逻辑地位；没有增加物理 KV pin。KV 分配、
  LRU、D2H/H2D 与原生容量抢占仍由同一 HiCache 数据面处理。
- 窗口低于在途 agent 数时允许暂时超出，直到真实边界到来。不会把缩窗
  实现成降低 decode batch，也不会中途截断正在生成的请求。

## 反馈的实际口径

当前 FULL-only 模型中，使用与原生 `sglang:token_usage` 相同的口径：

```
U = (GPU_capacity - allocator_free - native_evictable) / GPU_capacity
```

它是不可立即回收的 GPU 使用率。另记
`(GPU_capacity - allocator_free) / GPU_capacity`，明确区分物理已分配量。
共享 radix 页只计一次，不按 agent 历史长度重复加总。

H 为本控制区间新测到请求的 `cached_tokens_device / prompt_tokens`，
按 token 加权。使用真实首个 prefill 已计算的 cache breakdown；Host、
存储命中和后续 chunk 的已计算 token 不计入 GPU 命中。只在 prefill
选择回调观察新请求，decode 不重复扫描前缀。

没有新命中样本时记为未知，不填成 0，也不拿旧的低命中样本反复乘减。
Ulow 分支仍可独立增加窗口；高使用率分支需要实际低 H 才会缩窗。
原生计数不一致时保留样本并延后反馈，不把异常值当作拥塞。

## 动态 JOIN 适配单列

原论文主要描述顺序 ReAct agent。直接按 invocation 分配 permit 时，
W=1 的 parent 若持有 permit 等待 child，会阻止 child 执行。

因此比较名为 `concur_observed_join`，实际策略身份为
`concur_paper_observed_join`。只在实际 CALL/JOIN 等待已被观测后，释放
该阻塞 parent 的逻辑 permit；parent 提交真实后继时再通过同一 FIFO
竞争。若旧请求仍在运行，则等其完成后才释放。

这项扩展不预读未来 DAG、不优先最后 child、不增加 parent 优先级，不执行
KV 卸载或预取。纯论文 AIMD 与通用 agent permit 状态机分别保存在
`paper_baselines.py` 和 `concur_admission.py`，可以独立验证。

## 当前验证边界

v3794 的一个失败来自复用测试夹具时 child 已 RETURN，parent 已 READY，
不能用于复现阻塞 JOIN；保留失败记录。修复测试为经过真实 graph/runtime
回调的 parent 等待、child 准入、RETURN 和 parent 重入过程。

v3795 的 82 项目标回归通过。此前 ThunderAgent/Continuum 作者组件又核对
1000/1012 组，保持一致，见 ops 下 v3796。CONCUR 没有作者实现一致性的
证据，因此不把这份报告当作 CONCUR fidelity。

当前 v211 Continuum 实验继续使用冻结的 dd6ceba7，不受本次修改影响。
后续比较继续使用 v195 完整轨迹、相同容量和种子，保留所有正常长尾，
不增加完整 native 重放，也不以未完成工作或更少 token 认定收益。
