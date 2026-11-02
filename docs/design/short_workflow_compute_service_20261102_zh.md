# 短 workflow 的计算服务机会

用户要求继续尝试短 workflow 调度，即使完整平均 JCT 只改善 1%。
本次仅执行 CPU 审计，保留最新 73 个完整原项目、原 token、工具时间、
12 初始/平均 70 秒到达表及固定 18 条测量组。没有启动 GPU、改变运行时、
重采工具时间或取消 JOIN/EOS 保护。北京时间 2026-11-02 16:16 前的 GPU
限制仍由共享 hold 文件控制。

## 已确认的作用域

现有剩余模型/工具关键路径 oracle 用于 agent 的恢复资格及工具感知压力
选择。首次准入延后、统一恢复、物理卸载联动 paused、工具权重和组合
规划都已有实现或实验，不能把再次增加这些入口描述为新修复。

原生 prefill 的 `ReferenceThunderAdmission.order_ready()` 只接入 sibling
选择。`Scheduler._get_new_batch_prefill_raw()` 先调用
`PrefillAdder.add_chunked_req()`，之后才选择 waiting queue。已有 chunk 用完
输入预算时，runtime 直接返回；此时再排等待队列不能改变计算服务。

这支持检查另一种作用位置：保留 agent 的驻留/资格连续性，在真实计算
预算竞争时使用 workflow 大小。它不自动授权抢占 running 请求、撤销 active
资格、搬出 ready KV、重新开启工具预取或逐层 handoff。

## 完整旧执行的 CPU 清点

证据目录：
`experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/`
`short_workflow_prefill_screen_r1/`。

`report.json` 绑定已独立验证完成的 Full、全部原始事件/请求/服务/准入日志、
原 oracle artifact 和完整关键路径报告。原始完整行前缀在读取后重新检查。
原生 sample 数量、精确请求身份和单调时钟沿用已有验证器核对。

排序同时记录两个预先声明的诊断口径：剩余模型总服务代理，以及模型/工具
关键路径。候选至少比当前 prefill owner 短两倍；不使用测量组身份排序。
只推进实际已观察的事件、native completion 和每 128 output 的进度。

| 检查 | 次数 |
| --- | ---: |
| 全部 prefill 边界 | 11,115 |
| 单 owner、尚未产生输出且不属于 post-JOIN 的边界 | 3,191 |
| 上述边界中有尚未获得首次服务的完整请求 | 2,307 |
| 上述边界中有近期 active/ready 资格证据的候选 | 1,501 |

“近期资格”要求 2.5 秒以内的前置周期快照、精确 request/context/session
身份、application-ready/pending/active-after-pressure，且没有已记录的
后续资格干预。它不是当前物理前缀或可分配容量证明。未完成背景没有完整
请求回执，不能把未进入这份候选表解释为系统没有其他等待工作。

| 大小口径 | 重复边界选择 | 去重请求 | 去重 workflow | 测量关键路径请求 |
| --- | ---: | ---: | ---: | ---: |
| 剩余模型工作 | 848 | 67 | 37 | 22 |
| 模型/工具关键路径 | 749 | 59 | 35 | 17 |

模型口径的 67 个请求中，55 个在后来真实首次服务时的 prefill 输入不超过
512 token。不能据此倒推它们在早期等待期间已经驻留，也不能给全部 55 个
对象授予共同资源。

## 为什么只提前 prefill 不够

假设候选可以免费提前完成 prefill，并免费加入后续原有 decode 批次，
但不改变原 decode 节奏，模型口径的 22 个测量关键路径请求均没有显示
正向的局部提前返回；工具路径口径也相同。全体候选只有一个非测量请求
有约 355 毫秒的局部提前。

这不是全局收益上界。它表明在这份已观察执行中，很多请求即使提前获得
首个 token，后续仍要等待长 prefill 段结束才能获得足够 decode。只修改
waiting queue 排序或统计首次服务改善，不能证明完整 workflow JCT 改善。

## 计入 decode 成本后的局部诊断

`decode_budget_r1.json` 进一步假设：先将短输入共批，再暂缓后续 prefill，
执行现有完整 decode batch，直到该请求返回。模型支付：

1. 候选后来真实首个完整 prefill 的整批 worker 时间，作为新增输入成本代理。
2. 所需全部 decode 步，按紧接该边界的真实完整 decode batch worker 成本收费。
3. 将 decode 单步成本分别乘以 1、1.5、2，检查改变 batch 成本的不确定性。

每个请求只保留最早符合条件的选择，不挑局部收益最大的未来边界。测量组
及其关键路径仅在选择完成后附加分析标签。

| 模型工作排序，完整观察跨度 | 原 decode 成本 | 两倍 decode 成本 |
| --- | ---: | ---: |
| 有完整短输入标签的去重请求 | 51 | 51 |
| 其中测量关键路径请求 | 18 | 18 |
| 有正向局部提前的测量关键路径请求 | 17 | 11 |
| 上述局部提前累计 / 秒 | 182.205 | 150.064 |
| 提前量超过两倍额外成本的测量关键路径请求 | 8 | 5 |

这些请求均不是 workflow 的最后一次模型调用。更早返回能否转成 workflow
提前完成，还取决于后续工具、JOIN 及再次 prefill 能否继续获得服务。
完整模型的同一请求重排、额外增长、graph 形状、物理恢复和所有被延后
workflow 的 JCT 都未模拟。因此不能用 182.205/18 声称约 1% JCT 改善，
也不能把收费系数当成实测的新 batch 成本或严格上下界。

## 下一候选的实现边界

有意义的候选应同时处理输入预算和后续计算，先以显式 oracle 诊断：

1. 在 chunk 分配前，从当前 ready、active 且满足原保护条件的 agent 中有界
   比较 workflow 剩余工作。无跨 workflow 竞争时直接走原路径。
2. 使用即时原生前缀检查，要求短续接确实在 HBM、无待完成 H2D，并由实际
   `PrefillAdder` 检查请求行、完整输入及 decode 增长容量。
3. 只从长 chunk 中划分实际需要的短输入，保留长 chunk 的最小进度。
   入批失败时不保留空预算；不通过取消旧 chunk owner 规避原生生命周期。
4. 短续接进入 decode 后，允许现有完整 decode batch 获得有界服务。
   不只推进短请求，也不让 oracle 输出长度直接决定无界连续 decode。
   时间/步数上限需要组件成本依据，本次局部“完成整次调用”只用于诊断。
5. 短续接返回工具或 JOIN 后释放计算偏好；驻留资格、真实工具状态和迁移仍
   由原工作集管理。后继真正 ready 才再次参与，不按预计返回时间制造请求。

现有 `inspect_short_continuations()` 已能在私人预算副本上检查部分共批
条件，但本地冻结引擎没有把这些检查落实为上述实际在线共批和 decode
策略。本次不把 CPU 机会审计称为已实现的服务策略，也未登记 GPU 后继。

后续先补足即时物理可执行性和成本证据，再决定是否进入同负载 GPU 诊断。
对照必须有一致的 NUMA 页面、执行 profile、到达和固定测量组；不能把旧
preferred 对照与新严格本地结果的差异归因于排序。1% 是用户本轮接受的
继续信号，单次配对不能证明稳定的 1% 改善，需要换运行顺序复测。

## 验证

两份分析器及相关 oracle、因果路径、准入和已有短续接检查共 139 项通过，
其中新增 26 项；无失败或跳过。测试覆盖输入和 output 进度守恒、工具等待
与模型服务不同排序、资格快照失效及 session 变更、完整 decode 成本计入，
以及使用最早选择而非最有利未来边界。

这是 CPU 分析资格，不替代共享服务源码绑定、实际 CUDA/模型验证或完整
workflow 性能结果。已有 6,975 项 CPU 服务资格对应的冻结操作目录保持原样。

## 默认关闭的实现

后续已实现 `beliefkv/runtime/workflow_compute_service.py`，通过原生插件
`AROUND` hook 接入真实选批、chunk 准入和 prefill/decode 选择，不修改冻结
native 引擎的原文件。只有显式配置路径和对应 SHA 同时存在时注册 hook。
普通 Full、paper、native、工具预取和逐层 handoff 的历史配置不启用它。
比较环境会清空所有非 Full 臂的四个配置字段，避免从 common 环境泄漏。

`configs/scheduling/short_workflow_compute_{reference,oracle}_20261102.json`
声明两个独立模式：

- `reference`：在相同真实物理候选中采用原队列次序，保护现有 ready JOIN。
- `oracle_model_work`：从已有 oracle 的不可变模型剩余工作缓存读取分数，
  在跨 workflow、相对当前 chunk owner 至少短两倍时选择更短者。
  不在选批中运行模型或扫描整个未来 DAG。工具状态仍由原阻塞、卸载与
  retention 管理；该分数明确不包含未来工具时间，不称为工具综合最优排序。

两种模式都沿用正常 agent 资格和即时 FULL 前缀检查，仅使用实际空闲页，
不获得额外 eviction credit。先让短输入经过原生 `add_one_req()`，
成功后再调用原生 `add_chunked_req()`。这避免先保留预算但短输入失败时
留下空白；长 owner 至少保留原 chunk 的七分之八。实际提交的短请求立即
移出本轮 native waiting queue，并只发出一次原准入回调。

prefill 完成后仍使用完整 running decode batch，不裁掉长 workflow。
每个间隙最多八次 prefill deferral 或 200 ms；实际 prefill 推进后重新
arm 这一小段预算。一次 focus 总计最多 64 次 deferral、最多两秒，终态、
身份变化、资格撤销、无对应 running 成员或空 decode 时不强制推进。
这些是尚待模型实验验证的有界诊断参数，不是已确定的最优配置。
deferral 是调度决策，实际 CUDA 服务需关联既有 native batch 回执。

runtime 状态单列 `workflow_compute_service` 的配置、模式、focus 和计数。
oracle adapter 分开报告恢复是否使用未来信息，以及整个系统是否还在计算
服务入口使用未来信息；不能在恢复排序关闭时把本候选标成无未来信息。

### 实际 native CPU 验证

`workflow_compute_service_cpu_r1/native_related_r4.xml` 通过 531 项检查。
其中新模块 46 项，涵盖真实 FULL tree/allocator/`PrefillAdder` 的跨 workflow
共批、真实 hook 注册、固定 native `get_next_batch_to_run()` 方法中的整批
decode 选择与回到 prefill、预算与终态退出、JOIN 保护和 baseline 隔离。
copy/forward 仍为 CPU stand-in；不是 GPU、graph 形状或性能资格。

保留首轮、补充轮和修复后的所有报告。补充测试曾发现真实接入问题：
私有预算副本调用 `add_chunked_req()` 时也被新 hook 截获，导致候选全部
拒绝；现在只有当前真实 cache/owner 的调用进入 cofill，预算副本保留原生
只读试算。另两项测试 fixture 缺失 stash stand-in/重复 workflow 参数，以及
新测试额外插入前缀耗尽真实小池的问题也保留在原失败 XML；没有降低生产
容量检查。后者通过原生退役替换不用的 fixture 成员，恢复相同池容量。

完整共享回归使用 `workflow_compute_service_shared_bound_cpu_r1/`，
已通过 7,047 项及 23 个子项，保留上次全部 6,975 个 literal 身份、
source 路径和 381 个 external 输入；当前共 2,462 个 source 输入、387 个
external 输入，源码/外部输入/原生树在执行期间均未变化，无失败或跳过。
不能把 23 个子项重复算成另外 23 个独立 testcase。
此模块尚未登记 GPU 后继。需再冻结干净代码、绑定显式配置并完成真实
原生模型、token/session/stop 与采样验证，才能进入完整 JCT 对比。

实验归因还需保留原 Full 对照：oracle 相对原 Full 的变化包含共批与服务
节奏；相对 `reference` 才能进一步检验 workflow 大小驱动选择的边际作用。
两者均不能由本次 CPU 局部提前量替代。

原先两份只读分析使用的 oracle 源码已保存在干净的
`beliefkv-short-prefill-analysis-99838db9` 操作目录。
`short_workflow_prefill_screen_r1/original_analysis_source_archive.json`
逐字节验证原报告、全部原始完整行前缀和输入；其中一份源码路径显式重定位
到原提交，不改写旧报告或将新实现误记为当时分析源码。
