# Child 首次 KV 物化背压：实现与 CPU 验证

用户要求保持 subagent 原任务不变，在有效 KV 接近容量上限时，
通过系统准入延后新增 child 的状态。现已实现独立、默认关闭的候选；
尚未启动本候选的 GPU 实验，未声明 JCT 或重算改善。

## 实现入口

- 候选工作树：
  `/home/longhao/experiment/.worktrees/beliefkv-subagent-materialization-pressure-20261116`。
- 实现提交：`469be6eaefa20433b00fc54c4f09efa55e8da06a`，
  `perf: defer first child KV materialization under memory pressure`。
- 最终提交：`440a69171727f0e5f6566313c12176b67b4310c0`，
  `fix: clear stale pause marks before child materialization waits`。
- 基础：已通过 CPU 验证的严格剩余工作量优先候选 `b07de6a5`。
- 设计文档：
  [完整设计](../../../.worktrees/beliefkv-subagent-materialization-pressure-20261116/docs/design/subagent_materialization_pressure_20261116_zh.md)。
- 实现：候选中的 `beliefkv/runtime/subagent_materialization.py`。
- 配置：`BELIEFKV_SUBAGENT_MATERIALIZATION=pressure_v1`；默认 `off`。

SPAWN、JOIN、客户端请求登记和 workflow 原到达时间保持原样。
仅在新 child 首次 native prefill 前等待，延迟计入完整 JCT。
已经开始 prefill 的请求保留连续性，已有状态的续接和恢复不重新限流。
后到的真实 SPAWN/CALL 会补登记尚未开始的首请求。

容量使用真实空闲 Host、可回收 Host cache、真实空闲 HBM，
扣除已有需求预留以及初始 prefill 尚未分配的后缀。
有引用的 Host-only 状态、DMA/锁定状态、JOIN 服务保护和未归入树的 Host 占用
不提供可回收额度。共享树节点只计一次，实际回收和准入保留原生权限。
候选顺序复用 R5/Oracle 的剩余 workflow 工作量，不另加老化或请求成本密度。

限制覆盖登记、周期恢复、免费补位、sibling、工具替换候选及超时强制恢复。
没有已有可执行工作时提供单分支进展例外，允许沿实际嵌套 JOIN/CALL 推进，
避免 parent 等 child、child 等内存的循环。例外和容量缺口单独计数。

## CPU 证据

证据根位于候选工作树下：
`experiments/reports/subagent_materialization_20261116/`。

- `bound_cpu_r2/evidence.json`：实际通过 9,267 个唯一用例及 23 个 subtest，
  保留全部 9,219 个前驱身份，新增 48 个；失败、错误、跳过均为零。
- 最终完整执行 233.20 秒；21 条警告保留在 `bound_cpu_r2/pytest.log`。
- 2,783 份源码、365 项继承外部输入及本轮 5 项配置/模型/到达/测量绑定检查通过，
  前后源码、原生引擎和输入保持一致。两组输入绑定存在重叠，不能相加当唯一数量。
- `control` 和 `pressure` 两臂都通过生产环境 preflight、实际 runtime 构造和 cache attach。
  挂载构造使用容量替身，不声称执行了 GPU 或完整原生 allocator。
- 新用例另外执行真实原生 CPU 树上的拆分、备份 ACK、卸载和计量，
  并覆盖实际 native 候选过滤、后缀预留、重试、取消、窗口退避和嵌套 ALL-JOIN。
- 保留开发用例 `focused_cpu_r2.xml` 的两次失败：
  容量准备 fixture 缺少已有 FULL-only/NUMA 声明；补齐 fixture 后
  `focused_cpu_r3.xml` 的 47 个用例通过，随后新增 JOIN 用例纳入上述完整验证。
- 初版 `bound_cpu_r1` 也完整通过，但最终复核补充了迟到 SPAWN 遗留待暂停标记的断言。
  `pause_mark_before.xml` 使用原 `469be6ea` 登记方法实际复现失败；
  `440a6917` 清除未分配 KV 请求的旧标记，最终以 `bound_cpu_r2` 为准。

`census_cpu_r3.json` 的标量节点替身微基准中，空闲容量快速检查中位约 3.2--3.4 us，
1,024/8,192 节点完整普查分别约 0.396/3.078 ms。
该微基准绑定 `469be6ea`；原始 Git blob 哈希验证通过，
最终暂停标记修复没有改变容量观察类或普查函数的 AST，未为无关改动重复测量。
这不是整个控制器、真实 allocator、传输或 GPU JCT 的成本。
持续高压下完整普查采用现有 2 秒控制周期兜底，
普通标量检查为 400 ms，真实完成/ACK 提前唤醒；不在每个 decode tick 普查。
超出 8,192 节点或不可观测时显式回退原调度，不把未知占用记成零。

## 匹配实验准备

`host200_prepared_r3/{control,pressure}.json` 保留：

- 133 个原项目、20 初始 workflow、平均 60 秒到达、同一固定 18 条测量成员。
- 相同模型、HBM、运行/decode 上限、工具、输入输出、依赖和剩余工作量预测器。
- 两臂均使用 200 GB Host，沿用现有 NUMA 放置诊断，不增加平均分布要求。
- 两臂仅切换首次物化策略。不同 Host 容量的历史结果不作为匹配控制。

初始 20 个 root 的已知输入合计 1,609,748 token，按当前 FULL KV 单位约 158.245 GB。
200 GB Host 与现有 HBM 共 2,839,704 个槽位。
这只是初始输入的容量参照，不是同时驻留、无重算或吞吐预测。
依据为原始 `workload_assessment.json`，SHA256
`39df3e5dabc56ed5914f6938295c6fa155ab79aa0febdd8f04df6be3aba91fd4`，
及不变到达表 SHA256
`faed8d3737785a32641d0293ecb90eb199d5bcc3ee4480ba60c89b5e1f5f1695`。

GPU 验收需同时看完整 JCT、共同墙钟吞吐、有用前缀丢失、实际额外 prefill、
首次物化等待、JOIN 推进、进展例外及累计控制 CPU。
新 root 和已开始 agent 的后续增长仍可能施加压力，本候选不保证任意负载零重算。
只有减少重算的收益超过新增排队和控制成本，才值得启用。
本轮没有修改当前排队服务的冻结源码或 GPU 占用策略。
