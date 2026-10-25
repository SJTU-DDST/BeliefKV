# 论文策略复现与联合方案实施顺序

日期：2026-10-24，America/Los_Angeles。

用户要求先在当前框架复现同类论文，再实施计算、恢复、容量和准入的联合方案。
两段联合设计合并为一个近期执行计划：逐层截止时间决定提前量，容量预算约束
实际可恢复对象，准入机会与同一对象绑定，底层只等待该批真正依赖的事件。
本轮没有开始实现该新调度算法。此前已提交的无关预取等待修复继续独立验收。

## 共同实验条件

采用当前 Qwen3-Coder-30B-A3B-Instruct、18 个不同项目、running/decode
各 24、256K 上下文、Host 200GB、seed 21，复用 v195 自然采集的固定
请求、token、工具和依赖轨迹。继续使用 pretokenized `/generate`，不增加
完整 native 重放，所有正常长尾进入 JCT。改变内部活跃 agent 数属于相应
baseline 的策略，其入口等待仍计入 workflow JCT。

统一底座结果命名为 `method-adapted-sglang`。作者代码、补全论文策略和
额外动态图扩展分别登记。共享原生有效性、容量和 DMA 处理，不共享本系统
的 JOIN 优先、resident-first、短工具续接、PREPARE 或预测器。

机器可读清单位于
`configs/baselines/paper_reproductions_20261024.json`。清单中的 pending
项目仍需完成，不能把已有 CPU 决策函数计为完整系统复现。

## 首批已实施内容

| 方法 | 已实施 | 尚未验证或补齐 |
| --- | --- | --- |
| ThunderAgent | 单后端工作集策略，v209 同工作量完整运行与 JCT 路径审计完成 | 配对重复及动态 JOIN 阻塞的独立扩展，不能以单轮作最终验收 |
| Continuum public | 公开版 TTL、program FCFS、真实 native pin、工具解析、在线间隔和准入已接线；v210 数据面校验及 v211 同工作量运行完成 | 配对重复与独立任务正确性评分 |
| Continuum paper | §4 经验 CDF、全局回退、指数冷启动及 TTL 目标与排序核 | 模型恢复代价、在线队列/记忆性统计、TTL 强制与死锁解除 |
| CONCUR | 论文 AIMD、真实 GPU 命中反馈、agent 暂停/恢复及单列 observed-JOIN 适配已接线，目标 CPU 测试通过 | 完整继承 CPU 资格及同工作量 GPU 比较，证据另行绑定 |
| InferCept | §4 的 chunk-discard 代价、全体中断请求按最小浪费排序、换出预算和剩余 preserve/discard 决策 | 实测 profile、每轮预算求解、原生部分换出和 discard、恢复队列 |

随后继续 TokenCake、Agentix/ATLAS、KVFlow，以及清单中与论文主张相关的
CacheWise、CacheScout、PBKV、Pythia。需要预声明 Step Graph 的方法使用
单独输入组；当前动态轨迹的未来图不能作为在线 metadata。

## 源码与一致性证据

作者源码下载在实验 ops 目录，未安装作者 serving 引擎或改动当前环境：

| 作者仓库 | 固定提交 |
| --- | --- |
| ThunderAgent-org/ThunderAgent | `7ddc8610270e56d3b109eed8796b3a4360fc67c9` |
| Hanchenli/vllm-continuum | `316a58794a6ff86b216e579b74fd56ed0c5a911f` |
| WukLab/InferCept | `3a1a4dac37cb569e767bc4a95ef8ed0995a13b99` |
| pku-lemonade/TokenCake | `1901ebf31fdf63590148e61d5c2f65e9fb7ec010`，保留之前冻结的源码与 PDF |

`scripts/verify_paper_baseline_components.py` 从固定源码提取实际策略类，
在 CPU 上执行作者方法。只去除方法内部的模块导入，保留同一常量和调度表达式，
不导入或启动作者 serving 引擎。首轮验证：

- ThunderAgent：1000 个工作集、暂停及恢复状态组合，决策和容量计算一致。
- Continuum public：12 个已识别/未知工具、均值阈值边界样例一致。
- 这不是端到端论文性能复现，也没有验证所有运行时状态转换。

证据：`/home/longhao/experiment/ops/v3758_paper_author_component_fidelity_20261024.json`。
本轮 26 项决策组件测试另覆盖 TTL 目标、queue 成本、AIMD 双条件、Host 预算、
残余 discard 和请求边界暂停；组件测试通过不替代运行时或 GPU 资格。

## ThunderAgent 适配边界

当前每个顺序 agent invocation 对应一个 program。不同并发 child 不合并到
一个只有单一状态的作者 program 对象，workflow JCT 仍按完整 root 统计。
这一映射需要在结果中明确，不能把单会话路由器说成原生动态多线程调度器。

沿用公开代码默认 5 秒控制周期、acting 权重 1、每 program 100 token 预留。
公开代码标记运行中 program 时不会立即释放其容量，当前实现同样只在请求
完成边界暂停。1800 秒请求等待保险规则单独计数。

输入使用当前框架已有的精确 token 数，省去公开 chat proxy 的字符/token
估算及 HTTP 转发；这是明确的统一底座适配。单后端恢复保持原优先分组，
然后采用 BFD 放置顺序。native allocator、HiCache、Host 回收和需求恢复
继续拥有物理控制权，不增加本系统的生命周期感知回收。

未接入的论文名称在预检中直接拒绝。运行日志必须出现真实 paper policy
身份和全套关闭的本系统策略；不能只凭 arm 标签认定 baseline 已启用。

V206 在启动 GPU 前被严格配置预检拦下：校验接续器没有把已声明的
`TOOL_RESTORE_MODE=execution` 传递给原生数据面校验，误用旧默认值。
没有启动 server，也没有 GPU 性能样本；失败登记和冻结代码保留。
新增三个 CPU 复现覆盖普通、frontier 和 cold-frontier 配置，修复后
57 项相关检查通过。修复显式传递原配置，不放宽校验。

V208 的 24 个真实请求均保持原生停止边界，GPU 数据面和 position sampling
检查通过，随后完成 v209。V209 使用 c527d0dc 的冻结代码；新组件没有
加入该运行。原生校验不能代替 paper scheduler 的性能和活性验证。

CPU 新增了一个显式局限样例：1000 token 的逻辑容量中，等待 JOIN 的 parent
占 900 token 和 100 token buffer，pending child 需要 50+100 token。公开
策略仅在负容量时暂停 acting program，因此该 child 不会自然恢复，只能等
1800 秒保险规则。v209 全部完成且没有触发该保险规则，不能据此假设它实际死锁，
也不能把此构造的等待当作论文性能结论。若实测出现该情况，须保留原组，
另行命名最小 observed-JOIN 活性扩展，不注入本系统的 JOIN 优先排序。

## Continuum 物理保留与作者核对

`beliefkv/runtime/continuum_pins.py` 在真实 FULL-only radix tree 上为每个
顺序 invocation 持有独立锁收据，支持共享前缀、原生节点分裂、增量生长、
后继请求已入队时延长保留、session 代际变化释放和容量阻塞解锁。它不会
复制 KV，也不会借用本系统短工具续接的单 pin、1 秒期限或 JOIN 特权。

作者公开队列实际采用 pinned program 优先、program 首次到达 FCFS；
与论文中的 preempted 首级区别保留为两套明确的排序核。公开容量阻塞
分支实际选择截止时间最晚的 pin，虽然源码注释写的是最早，当前跟随实际
表达式。直接执行固定源码的 1000 组队列/pin-victim 核对均一致，连同原有
1000 组 ThunderAgent 和 12 组 TTL 核对，记录在
`/home/longhao/experiment/ops/v3771_continuum_queue_author_fidelity_20261024.json`。

物理组件及论文相关目标回归共 80 项通过：
`experiments/reports/v3770_continuum_native_policy_cpu_20261024.xml`。
此前 v3769 的一个失败来自测试夹具只接受 custom D2H、无法处理测试末尾的
native write-back；保留失败记录，修正该锁测试使用原生 drop eviction。
没有放宽生产锁或容量检查。

需要披露一处保留实现差异：作者 TTL sweep 遍历同时删除同一 list，可能让
相邻到期项延迟到下一步；当前物理组件对 snapshot 遍历，一轮释放所有到期
且没有排队后继的 pin。随后已增加准入接线和原生压力抢占适配：每个 program
继承首次到达优先级，仅在 native decode 容量不足时抢占较晚 program，
不因新请求到达而主动抢占；单 running 请求受 pin 阻塞时先撤销最晚截止 pin，
然后重新执行原生容量检查。随后应用完整 CPU 资格 6263 项和引擎 726 项通过，
完整保留之前 6247 项和 714 项的测试身份，证据为 v3789/v3792。v210 的
24 请求 GPU 数据面校验通过，v211 同工作量比较已完成；这仍是公开策略在
当前模型和引擎上的适配，不是作者完整原栈性能复现。

动态模型不会预先声明哪条生成是最后一步，因此没有导入作者 artifact 中的
事后 `is_last_step` 标签；running 请求都按“尚未知道是最后一步”处理。
公开方法、当前模型格式适配、该信息边界和 snapshot sweep 修复均单列披露。

`ContinuumTools` 复用当前 Qwen3-Coder 的原生完整输出解析器，不解析正在
生成的输出，也不读取重放的未来 action。shell 沿用公开版的命令首词身份；
其他工具使用已生成的函数名。单次输出包含多个并行工具时保守视为未识别，
这与公开版只接受单个 bash action 的输入假设分开披露。

`ContinuumHistory` 仅在同一顺序 invocation 的后继到达时学习
“上次输出完成到本次请求到达”的间隔。不同工具独立维护 sum/count，
使用常数时间更新公开版均值，不接入本系统旧工具预测模型。
未知工具、普通返回或已退休程序不会产生伪造的零间隔样本。

在 v195 全部 2054 个真实完整输出上离线核对，识别出 1990 个工具输出，
另有 64 个未识别或普通回答；未将这 64 个全部认定为正常最终回答。
解析 thread CPU 总计 253.31ms，P50 0.050ms、P99 1.374ms。
这是完整输出解析的 CPU 样本，不能当作 serving 调度、pin 生命周期或
整个 baseline 的控制开销。证据：
`/home/longhao/experiment/ops/v3777_continuum_real_output_parser_cpu_20261024.json`。
新增在线工具与已有物理组件的目标回归 66 项通过，见 v3776 XML；
完整继承 CPU 资格另行绑定，不覆盖原来的 v3772–v3774。

## 最新内部对照

V205 scheduler-only 已完整完成，2054 请求、116355888 输入 token、
387242 输出 token，与 v204 和 v195 工作量完全一致，原生停止条件保持。
两次新鲜 idle telemetry 观察后清理了所有自有进程。

| 条件 | 平均 workflow JCT（秒） | 全程输出 token/s |
| --- | ---: | ---: |
| v195 自然 native | 1509.740834 | 201.434306 |
| v204 Full | 1415.542276 | 214.032350 |
| v205 scheduler-only | 1458.076082 | 208.423131 |
| v209 ThunderAgent 单后端公开策略适配 | 1091.940321 | 230.646022 |
| v211 Continuum 单后端公开策略适配 | 1341.755403 | 220.921987 |

V209 的 18 个 workflow、2054 请求、116355888 输入及 387242 输出 token
完整一致。全部正常长尾保留，无质量排除，337 次控制 tick、0 次 1800 秒
强制恢复。结束后观察到两份新鲜 idle telemetry，owned 进程清理为空且已
通过真实 PID 检查。报告仍标记 `performance_accepted=false`，独立任务
正确性尚未评分，也没有重复实验。

完整实测依赖路径审计见
`/home/longhao/experiment/ops/v3781_thunderagent_matched_jct_paths_20261024.json`。
平均路径上的 submit 到首次 launch 从 v204 的 561.38 秒降为 463.19 秒，
首次 launch 到原生完成从 794.11 秒降为 569.15 秒。各组最后阻塞路径可能
不同，这些分解不能视为因果消融或纯 GPU 时间。

V209 与 v204 的同 session 旧前缀重算代理均为 0；H2D 与容量周转明显不同。
该结果说明目前不能只以 native 或 scheduler-only 验收联合方案，应先完成
其余强 baseline，并研究限制跨请求活跃工作集所减少的反复恢复。

这是同一开发轨迹的单轮结果。Full 相对 scheduler-only 的增量仍较小，
尚未经过配对重复或独立任务正确性评分。旧输入重算、需求 H2D 和额外
控制成本需要分别审计，不把这些数字当作已经达到论文验收目标。

V211 保持相同的 18 workflow、2054 请求、116355888 输入及 387242 输出
token，全部正常长尾计入，没有质量排除。结束后取得两份新鲜 idle telemetry，
owned 进程清理为空，真实 PID 已复查。平均 JCT 比自然 native 低 11.13%，
全程输出吞吐高 9.67%，仍慢于 v209。该结果与此前冻结 Full 的比较同时包含
引擎版本差异，不能当作仅切换 TTL 或 program 优先级的因果消融。

## 原始材料

- ThunderAgent：`https://github.com/ThunderAgent-org/ThunderAgent`；
  `https://arxiv.org/abs/2602.13692`。
- Continuum：`https://github.com/Hanchenli/vllm-continuum`；
  `https://arxiv.org/abs/2511.02230v7`，本次核对 §4–5。
- InferCept：`https://github.com/WukLab/InferCept`；
  `https://proceedings.mlr.press/v235/abhyankar24a.html`，本次核对 §4。
- CONCUR：`https://arxiv.org/abs/2601.22705`，本次核对 §4.3 的公式和 §5 的参数。
- TokenCake：保留 ops 下 `tokencake_prefetch_20261020` 原始源码、PDF 和审计。
