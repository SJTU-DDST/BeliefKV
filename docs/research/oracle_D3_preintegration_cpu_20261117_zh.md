# Oracle 与 D3 的 CPU 预整合

此候选只准备实验后的线性整合，不注册、替换或重新启动 GPU 实验。
Oracle 已使用 `61a55c95` 完成原 fixed18，D3 四组继续使用冻结
`e117908e` 顺序执行，客户端请求超时继续为 3600 秒。

## 输入与解决的冲突

独立 CPU 目录基于 Oracle `61a55c95`，先通过 `49ce22f7` 修复精确终结
请求的原生服务计时，再使用 squash 合并 D3 `e117908e` 的必要差异。
未在主开发目录执行合并，未改变两个实验开发树或其绑定。
候选提交保持单父关系，原有分支和失败证据继续保留。

实际 Git 预合并发现四处冲突：

- `internal_comparison.py` 同时保留 D3 模式隔离与 0/1800 秒执行兜底校验。
- `ready_service_guard.py` 保留精确完成 ID、当前请求对象和完整身份检查。
- `reference_thunder_admission.py` 同时保留物化许可、事件和完成通知，
  并把本批次真实完成 ID 交给兜底计时；首次物化扣留仍不可被超时绕过。
- `test_ready_service_guard.py` 保留两侧断言及真实终结回调回归。

自动合并部分还包含 D3 原生准入挂接、准备与队列脚本及相关检查。
普通短优先仍按剩余模型和工具关键路径排序、贪心装入，保留 sibling、
活体 JOIN/EOS、工具卸载及原生容量权限；没有新增普通老化或组合效用评分。
本候选包含两个独立可选机制，不代表默认启用 D3 或重新设定任何到达表。

## CPU 证据

`experiments/reports/terminal_service_integration_20261117/combined_cpu_r2.xml`
实际执行 791 项用例，全部通过，无失败或跳过，16 条环境警告保留。
范围包括真实原生回调、精确身份拒绝、物化扣留、容量释放后的实际可选集合、
JOIN/sibling、工具边界抢占、统一控制、四组队列准备及请求超时解析。

新增跨模块用例验证：child 被首次物化扣留时，1800 秒兜底不能赋予许可；
真实容量释放并通过原生选批后，其终结回调必须释放物化记录并清零服务计时。
另将旧的 `tick` 函数对象身份断言改为行为检查：关闭执行兜底时，
该包装必须按同一 `now` 恰好调用一次参考实现并保留返回值。

初次预整合报告 `combined_cpu_r1.xml` 为 779 通过、12 失败，未覆盖或改写：
其中 10 项缺少原始历史配置路径，2 项使用上述过时的函数身份断言。
后续只读复用 D3 开发树原有
`experiments/reports/subagent_materialization_20261116/host200_mean55_prepared_r3/`
产物，没有伪造配置、任务或工具时长。

此前终结回调单独修复的 101 项通过报告和缺原生路径的初次 7 项跳过报告
也继续保留。上述数字分别属于各自实际执行范围，不相加成一次新全套执行。

## 仍需完成

Oracle 与 D3 GPU 实验完成、固定测量工作和 fresh drain 验证、所有相关
服务及协调器实际退出后，才把必要代码合入主目录 `main`。
还需结合主目录现有未提交源码进行整合、检查全部依赖与正式文档，
并运行相应回归。此 CPU 候选不是干净主工作区、完整系统验收或 JCT 收益证明。

## 简化 Oracle 的首次物化顺序

后续源码检查发现：`SubagentMaterialization._ordered()` 原先只识别
非空的 R5 组合选择器。简化全局关键路径 Oracle 的 `_combination=None`，
因此在首次物化预算只够部分 child 时，会按登记顺序放行，提前排除
本应优先的短 workflow。普通后续准入即使排序正确，也无法选择仍被 held
的 child。这是两种设计组合后的实际接口缺口，不能用先前的构造成功证明
排序协作已完成；刚完成的 Oracle 未开启 D3，不受这个缺口影响。

独立 CPU 用例用两个真实 SPAWN child、同样的初始输入和只能容纳一个
初始状态的容量，复现旧实现错误放行长 workflow。修复只在明确开启
全局关键路径排序、且没有组合选择器时复用既有 `_order_work_keys`；
关闭排序的 Full 和原 R5 分支保持原顺序规则。候选上限、共享容量扣减、
输入后缀预留、原生实际准入和进展出口都保留。

`materialization_oracle_order_before_r2.xml` 为修复前 1 失败、2 通过。
其中工具等待从 0 增至 100 秒时，即使模型输出少，整体剩余路径也应后排；
关闭排序则保留原登记顺序。修复后的
`materialization_oracle_order_related_r1.xml` 实际执行 273 项，全部通过，
无错误或跳过，16 条环境警告保留。新增用例继续走生产 runtime 和原生
候选选择接口，核对实际可选请求及逻辑 active 集合，没有模拟 GPU 时间。
该数字不与此前 791 项或其他报告相加。

初版 fixture 将普通窗口 deadline 设为 0，而 0 在生产协议中表示尚未
开启窗口，造成另外两项无服务失败；原
`materialization_oracle_order_before_r1.xml` 保留，修正为已到期的正 deadline
后再得到上述缺口复现。另一次 CPU 命令缺少 Python 执行名，在 env 解析
时退出，未运行测试，不计为验证。

当前四组 D3 使用原冻结源码及原配置，不热改或重启。
本修复留待实验完成后的线性整合与最终源资格检查，不注册额外 GPU 消融，
也不据此声称已有 JCT 改善。

## D3 普通许可不得重启完整控制时钟

2026-11-17 的 Host200/initial20/mean55 同窗只读审计进一步发现，
物化许可的 `_publish()` 原先每次都令 `admission.next_tick=0`，
包括无物化压力时的普通放行。实际关闭 D3 的 R5 对照在公共窗口有
598 次完整控制，全部相邻间隔超过 2 秒；D3 开启组为 704 次，
其中 111 个相邻间隔短于 1 秒。现有事件和运行轨迹也会影响时序，
这些数字不单独证明每次提前检查的原因或 JCT 代价，但确认本轮实际
控制节奏已发生变化。

该窗口中实际 Host 丢失前缀再次执行为 645148/1335329 token，
prefill 输入为 5668004/6110237 token，均为对照/D3 顺序。
这是按完整固定公共窗口匹配实际 prefill 输入区间的结果，不是尚未
完成的 D3 测量组最终重算或 JCT。此前约 1.64 秒的 D3 自身线程 CPU
计数也不包含它触发的其他完整控制，不能视为全部附加 CPU 开销。

修复只调整已有控制器之间的通知：

- 有 deferred 统一准入控制器时，新增物化许可保留既有完整控制 deadline，
  通过原 pending 索引和 `defer()` 等待普通窗口，不创建新时钟或额外授权。
- 撤销许可仍可立即清零完整控制 deadline，并同步撤销尚未物化的 active
  资格。没有 deferred 控制器的参考路径保留原先的即时协调行为。
- 400 ms 容量检查、真实容量唤醒、输入后缀预留、JOIN/sibling、
  1800 秒独立执行兜底及原生分配/锁/ACK 检查保持原规则。

新增 CPU 用例同时使用生产 Oracle 和 R5 runtime，验证窗口前不执行
额外完整控制、窗口到期后精确请求进入实际可选集合，以及许可撤销后
active 被暂停且原生候选不能物化。修复前
`materialization_cadence_before_r1.xml` 为 2 失败、2 通过，失败均来自
普通许可将原 deadline 改为 0；修复后
`materialization_cadence_related_r1.xml` 实际执行 201 项，全部通过，
无错误、失败或跳过，16 条环境警告保留。它不是先前 1279 项或 9385
身份绑定的新一次全套执行，也不能与其他报告相加。

该改动只存在于未服务的 CPU 候选。当前 D3 四组继续运行原冻结源码，
原负面结果和同窗证据保留；先前 e9b53ec6 的资格绑定只证明对应旧源码。
此改动须纳入实验结束后的线性整合，并重新完成最终源码的必要资格检查，
不能复用旧绑定后宣称已验证新 GPU 性能。

后续补充容量释放后的恢复检查：Oracle 的 held 候选被普通 dispatch
移出 pending 索引后，放行必须重新索引；R5 可以保留原索引。两者均须
保留普通窗口和完整控制 deadline，并在原窗口到期后进入实际可选集合。
`materialization_cadence_release_r1.xml` 为 5 通过、1 失败，初版错误要求
R5 也必须删除 held 索引；修正这项前提后，
`materialization_cadence_release_r2.xml` 的 6 项检查全部通过，原生选批、
预算、物理 ledger 和未物化断言保留。服务源码仍与 d070d314 完全相同，
该 6 项执行不与前述 201 项相加，也不是新的全套源码资格绑定。

## D3 物化容量的实际承诺与开始前复核

用户指出 D3 以保护可复用 KV 为目的，而匹配公共窗口的重算反而上升，
因此本轮继续修复准入实现，不能把此前的控制时钟修复当作完整解决。
现有 Host200/initial20/mean55 对照仍是 Full+R5 与 Full+R5+D3；
冻结服务源码不热改，原失败和负面结果保留。

生产路径中确认并用 CPU 用例复现了以下缺口：

- `outstanding` 原先只统计本控制器登记的已启动 child，遗漏正在分块
  prefill 的 root、普通后继和本批已选但尚未分配槽位的请求。
  例如剩余可用 750 token，已有输入还承诺 600 token、child 还需 200 token，
  旧 gate 仍会放行 child。现在同一份预算覆盖所有实际在途或已选请求，
  按精确身份去重，并扣除已分配槽位与已选请求持有的前缀。
- 许可在等待普通 active 准入时可能变旧；原生真正开始 child 前没有
  再核对物化容量。现在在已有 `defer_prefill_for_prefetch()` 原生回调中，
  于前缀匹配、H2D 和 native 分配前复核，包含本批刚选中的其他请求。
  仅对尚未开始、已经获得许可的 child 复核；既有请求和 decode 不增加普查。
  真实容量不足时撤销许可，不能沿用 400 ms 或 2 秒前的额度。
- 原普查将无引用 Host-only 祖先直接计入可回收量，未检查其后代是否
  被锁定、DMA 或预取保护。现在按原生叶子到祖先的释放约束计量。
  另外，重复 Host 副本只有仍在原生回收索引中的同一节点才提供额度。

这不是“native 没有回收重复备份”的修复。原生 write-back 已有两轮
完整重复副本回收；此前只读检查已经确认，本轮不添加重复的物理回收器，
也不修改原生锁、ACK、淘汰策略、工具时间、到达表或实际任务。

新增检查覆盖 in-flight/running/chunk/selected 的 root 和普通后继、
多个 native 列表间去重、许可等待期间的容量消耗、同批新承诺、
已有请求不重复检查。真实 CPU radix tree/allocator 检查还验证：
锁定后代存在时原生不能释放祖先，解除锁后普查额度等于原生实际释放量；
在途 root 留有未分配输入时 child 被 hold，真实终止释放容量后可以进入
生产 runtime/native selector，同时待恢复 parent 的 Host-only 副本保持原对象。
传输仍使用已有 CPU 队列替身，不能称为 GPU 或完整 workflow 性能验证。

原 `materialization_capacity_before_r1.xml` 为 8 失败、1 通过；
其中一个失败是新增诊断字段尚不存在，其余为行为断言。
首轮修复后的定向报告 `materialization_capacity_after_r1.xml` 为 68 通过。
真实 native 扩展初次报告 `materialization_capacity_native_r1.xml` 为
9 通过、2 失败：fixture 错将 `release_radix_session()` 的固定返回值 0
当成失败，以及没有等待普通逻辑准入协调。只修正这些前提后，
`materialization_capacity_related_r2.xml` 为 230 通过；
其中原生回收结果未排空的 fixture 警告也保留，后续用原生 `evict_host()`
完整排空返回值。r3 扩展为 274 通过、7 失败，七项均缺少工作树下的
`third_party/sglang-v0.5.20` 源码入口，非 D3 行为失败。
后续只将该 CPU 入口链接到已冻结的同一 `434c35dd` 原生 checkout，
不编辑或复制其源码，不改运行实验的绑定。

标量普查微基准 `materialization_capacity_cpu_r1.json` 每种场景执行
100 次：1024 个节点完整普查线程 CPU 中位数 438.564 us，
8192 个节点 3494.592 us；空闲量足够的标量快路径约 3.0--3.3 us。
这是有节点字段替身的离线 Python 成本，不是实际 native 全控制成本、
额外 GPU 时间或 JCT 收益。开始前复核包含在 D3 原 CPU 计数中，
另记录 `subagent_materialization_start_checks`；日志保留初始 child
与全部在途输入两个独立统计，避免继续用旧字段表示完整预算。

上述修复只消除已复现的容量高估与陈旧许可，并不预知未来输出或所有
尚未获选的新输入；有限容量下也不能保证无重算。是否减少本轮同工作量的
实际旧前缀重算、并保持 decode 吞吐和完整 JCT，仍需修复版本的匹配 GPU
比较。当前四组按原计划顺序执行，不为每个小修复插入独立 GPU 消融。

最终扩展报告 `materialization_capacity_related_r4.xml` 实际执行 776 项：
770 通过、6 失败，无错误或跳过，16 条原环境警告保留。
新增的 13 项容量/原生检查全部通过；未再出现原生回收返回值未排空警告。
五项旧 Mamba 替身检查与当前冻结 native 的接口不一致：两项仍假定
track index 已转为 Python int，三项未给 AST namespace 提供
`BASE_COMPONENT_TYPE`。另一项关闭 D3 的旧 handoff 检查仍期望工具事件
改变已有前台 child 的优先级。没有修改这些无关测试或生产行为来换取通过。

`materialization_baseline_existing_failures_r1.xml/.json` 在独立 CPU 进程中，
从 Git `f3be1500` 恢复唯一修改的 runtime 方法，逐项重现同样的六个失败。
AST 检查确认移除本轮新 D3 回调后该方法与旧版完全相同，其他生产源码
和同一冻结 native 仍保持原样；这六项未开启 D3。
该对照只证明它们不是本次容量修复造成的回归，不将其计为通过，也不将
770 项称为全套绿色资格。首次 r1 扩展命令还因测试文件名写错在收集前
退出、未执行测试，不计作验证。

### 原生拒绝后的复核退避

提交 `2cd79a6e` 后继续检查开始前复核的成本边界：如果 native 在复核后
仍返回 `NO_TOKEN`，不能在每个 decode 调度步都重做完整普查。
仅对这种实际原生拒绝复用 400 ms 退避，真实完成/容量释放/ACK 会立即
清除退避；期满可以自动重试。普通批次预算原因的 `OTHER` 不触发此退避，
也不重新关闭普通准入窗口。native 当前 chunk 预算已为零时直接等待下批，
不进行无意义的开始前普查。新增三项检查覆盖自动期满、释放提前唤醒、
`OTHER` 区分和 chunk 预算恢复后的继续执行。

中间的 `materialization_capacity_related_r5.xml` 为 772 通过、同六项既有
失败，15 项新容量用例全部通过。加上 chunk 预算检查后的最终定向报告
`materialization_capacity_related_r6.xml/.json` 实际执行 **605 项，全部通过**，
无错误、失败或跳过，保留 16 条环境警告。其中新容量和重试用例共 16 项，
其余覆盖物化、普通/Oracle/R5 准入、统一控制、sibling、执行兜底、
原生 FULL 生命周期和共享 runtime。该次执行记录生产文件和测试输入
摘要，并核对执行期间源码未变；没有将保留的六项旧失败改记为通过。
先前标量普查函数随后未修改，其微基准仍仅代表相应函数的离线替身成本。

`materialization_capacity_fix_r1.json` 已核对运行服务 checkout 干净，
并验证原生完整源码状态与已登记 `native_sources.json` 一致，
包括 `fa5cb52a` 基础加 canonical patch 后的 `434c35dd` tree。
原生工作树的补丁属于冻结资格本身，不应将其 dirty 状态误判成本轮新改动。
这份 r1 绑定对应首次容量修复的源码；最终重试保护使用 r6 的新摘要。
二者均不是 9,385 身份的全套重跑，修复版本的匹配 GPU 收益仍待验证。

## 完成结果及基线接续

原冻结版本的两臂现在已完成：固定 18 条平均 JCT 为
1851.699339/2236.150711 秒，D3 回退 20.7621%；共同窗口输出吞吐
288.674771/250.090910 token/s，下降 13.3659%。
测量组实际 Host 丢失前缀再次 prefill 为 2478348/3447194 token。
两臂都自然完成全部 133 条，完整工作、排空和实际退出证明均通过。
详细范围和逐条差值见
[完整 D3 负结果](subagent_materialization_completed_20261117_zh.md)。

完成后原协调器在绘图阶段因未登记四组颜色标签而退出，失败保留；
CPU 候选已以单父 `19bf613c` 纳入最小绘图修复，
与 D3 工作树的 `28b32285` 两文件内容一致。修复前真实渲染用例失败，
D3 工作树修复后 28 项相关检查通过；没有更改原服务实现。
已恢复的协调器复用 control/pressure 两份完整证明，实际进入
ThunderAgent acting-decay，后继仍为 native。不能把原接续 r2 已停止的
状态当作当前队列，当前入口是 D3 工作树的
`gpu_campaign_guarded_3600/plot_recovery_r1/continued_campaign_r3/plan.json`。

只读整合预检 `dirty_main_integration_preview_r3/evidence.json` 已更新为
main `59949b38` 与容量候选 `29e0ae09`，补齐原 `9efca1d2` 四文件增量和
readout 两文件；最终对象树 `f7bc1021423b05fcc15c24c374a7f8ee7ec0d36b`。
18 个原未跟踪文件、3 处 planning-advice 挂接和完整物化修复保留，
125 个变更 Python 文件语法通过，真实 main、索引和运行源均未改。
该预览早于后续绘图修复和完整结果文档，最终整合要继续纳入这部分增量，
并等待 ThunderAgent/native 实际完成退出后再操作 main。

后续无额外工具边界抢占的 Full/Oracle 配对已更新至
`matched_boundary_off_pair_r2`：实际生产 CPU 构造重新通过，保持同一
133 条轨迹、固定 18 条、Host400/initial20/mean60；仅切换两个排序布尔值。
Full 的配置说明已改为类别/上下文顺序，Oracle 才声明剩余模型和工具
关键路径顺序，1800 秒兜底说明与两臂实际 guard 一致。
这些是未注册的准备，不能与当前 Host200/mean55 的 D3 配对混用，
也不能代替最终主目录源资格或 Oracle 调度收益验证。

## 完整预整合兼容性检查

`dirty_main_integration_preview_r4` 在 r3 私有索引结果上补入绘图修复和完整
D3 结果文档，得到预览树 `69aca3854c113f123f8641e631c076106944f39c`。
18 个原未跟踪文件逐字节保留，127 个变更 Python 文件语法通过。
移除主目录既有的三个 planning-advice 回调及其初始化后，
整合 runtime 的完整 AST 与修复候选相同，不只是检查回调仍然存在。
真实 main 的 HEAD、文件和索引摘要均保持原样。

临时检出上实际执行 874 项 CPU 用例，包含原 605 项、
主目录既有 planning/delegation/harness，以及容量审计、协调器、
运行结果读取和真实绘图。首次收集因缺少机器辅助模块搜索路径而出现
14 个错误，未执行用例；补齐与原环境相同的路径后，873 项通过，
唯一失败为临时检出少复制了实验 `preparation.json`。
补入同一原清单后只重跑该项并通过，没有为此修改生产代码。
原报告均保留，`retry_r2/bound_cpu.xml` 汇合 874 个实际通过的身份，
逐项记录其执行报告来源，不称为一次重新全绿执行。
一个明确需要 Docker 的 shadow 用例未选入本次 CPU 范围。
原先六项既有扩展失败没有重标为通过，这也不是 9,326 身份的全套重跑。

最终源和已登记 canonical native 状态未变；临时检出已删除，
预览 Git 对象保留，供本轮 GPU 全部退出后的正式线性整合核对。
真正合入 main、最终源绑定以及修复版 GPU 性能仍待完成。

## 补齐相同配置的两组基线比较

已有 `review_materialization_campaign.py --baselines` 只输出 D3 开启组
相对 ThunderAgent/native 的结果，遗漏 Full 对照组相对这两条基线的比较。
现在两组都输出，原 D3 比较文件名保持兼容，新增
`thunderagent_acting_decay_control_comparison.json` 和
`native_matched_control_comparison.json`。
每份报告明确记录 reference/candidate arm 并绑定输入摘要；先核对
全部测量请求、实际容量及真实退出，再比较完整 JCT。
本轮 `Full control` 的实际策略是 Full + R5，`Full + materialization`
是 Full + R5 + D3，不与 Host400/mean60 的 Oracle 混算。

新增八个用例中，初版七个 fixture 错将只允许新建文件的 `write()` 用于
修改测试输入；改用测试输入覆盖后，修复前为 1 失败、7 通过，
失败准确复现缺少 Full 对照比较文件。补齐实现后的相关 30 项与
11 个子用例通过，含四组比较、负结果保留、实际工作/容量/退出不匹配拒绝，
以及原生四标签 PNG/PDF 渲染。
该变更只改未服务候选的离线结果读取及测试，不热改当前协调器。
本轮完成后用新读取器补齐现有结果，无需重新执行 GPU。

## 需求恢复暂存期间的输入预算遗漏

继续检查 D3 容量承诺时发现，生产 `DemandPrefillPipeline.stage()` 已在
native adder 中选中请求并启动 H2D，随后将请求保存在 `pending.members`，
清空 `adder.can_run_list`。这些请求仍可能没有 native 请求行，也没有私有
输入槽位。因此，只查看 running、当前 adder、已分配槽位和已启动 child
仍会漏掉其未分配输入，不能将它们视为没有容量承诺的普通候选。

新增用例调用生产 `stage()`，保留真实可见身份和逻辑准入：
parent 有 600 token 输入、100 token buffer，已经持有 100 token 前缀，
尚需预留 600 token。HBM 剩余 750 token、Host 无可回收容量时，
另一个需要 200 token 的 child 应继续 held。修复前却记录
`outstanding_input_tokens=0` 并允许 child 使用这部分容量。
`materialization_staged_capacity_before_r2.xml` 实际执行四项，
1 失败、3 通过；失败精确落在上述 0/600 预算断言。

修复只扩展既有 `_outstanding_inputs()`：一次读取当前流水线成员，
要求同一请求对象及当前 visible/latest key，再与其他 native 列表合并记账。
已持有前缀扣除，同一请求同时出现在流水线和 adder 时只计算一次。
取消流水线后承诺立即消失，旧 epoch 或被替换的请求对象不额外占用预算。
没有添加新的控制器、定时器、租约或扫描无关候选。

新增四项覆盖有效成员、旧 epoch、不同请求对象、与当前选批去重，
并在有效成员用例中验证取消后的实际 child 放行。
首轮 before_r1 的四项均因 fixture 缺少 session 身份而在 `stage()` 前提
检查失败；只补齐测试请求登记前的合法 session/generation，未放宽生产验证。
扩大检查的 r7 报告为 584 通过、54 跳过，原因是命令遗漏
`SGLANG_SOURCE_CHECKOUT`，该报告不作为原生检查通过的证据。

补齐同一冻结 native 入口后，`materialization_capacity_related_r8.xml/.json`
实际执行 **638 项，全部通过，无错误、失败或跳过**，保留 16 条环境警告。
它覆盖此前 605 项、四项新增容量检查和 29 项需求恢复流水线检查；
执行期间绑定的生产和测试文件摘要未改变。它不是全系统资格或 GPU 性能证明，
也不与先前 874 项合成为一次新全绿执行。

旧 D3 完整负结果继续保留。当前 ThunderAgent/native 顺序执行不变；
未来修复版 Host200/initial20/mean55 配对需绑定本轮新源码及 CPU 证据，
不能继续用旧 605 项对应的源码摘要声明已验证此修复。

## 最新整合预览的实际 CPU 执行

`dirty_main_integration_preview_r5` 已纳入 staged 输入预算修复和两组基线
比较读取器，得到树 `d3360f778e868917346e43616eaaea6828afd5d2`。
预览继续保留 main 原有 18 个未跟踪文件和 planning-advice 挂接，
128 个变更 Python 文件语法通过。主目录 HEAD、工作文件和真实索引未改，
当前 GPU 服务和协调器的冻结源码未改。

在该树的临时检出上，`cpu_validation_r1/executed_cpu.xml` 一次实际执行
**915 项，全部通过，无失败、错误或跳过**；16 条原环境警告保留。
它逐项包含先前 874 个整合身份和当前 638 个定向身份，并加入新的比较读取检查，
不是将两份报告数字相加。只排除明确需要 Docker 的单个 shadow 用例。
命令包含原测试辅助模块路径，并复制原四组配置及 `preparation.json`，
没有修改生产代码来满足 fixture。

执行前后 registered canonical native 状态相同，临时检出干净且已删除，
主目录内容继续逐字节保持原样。本次结果补齐当前整合树的相关兼容性证据，
不称为 9,326 身份的全套重跑，也不替代实际 main 合入、最终源资格或 GPU 验收。

## 修复版只排匹配双组

完成当前四组后，修复版仍需重新比较同一源码下的 Full+R5 和 Full+R5+D3。
原协调器固定四组，直接复用它会再次排入 ThunderAgent 和 native，
并在双组配置下尝试读取不存在的基线产物。候选现在提供显式 `--pair-only`，
保留四组默认行为，校验完整阶段列表和恢复前缀，双组只生成配对比较。
它不改变服务策略、请求、容量、到达表或原生执行。

先以单父提交纳入此前已审阅的 `9efca1d2` 四文件增量，
与已有整合预览的对应内容相同；没有修改正在运行的 D3 工作树。
新增检查覆盖真实准备函数生成的两个 Full 项、默认四项、
错误阶段顺序及隐藏项拒绝、恢复前缀，以及完成后实际启动的 CPU 比较命令。
候选上的 `materialization_pair_campaign_cpu_r1.xml` 实际执行 49 项，全部通过，
无失败、错误或跳过。对应整合树的源码绑定还需随此启动链路改动更新；
此前 1,983 身份的整合绑定继续保留为原树证据，不能直接改写为新源码。

## 已选首次 Child 的预算只能扣除一次

继续核对实际 native 批处理顺序，发现新的组合缺口：
需求恢复流水线可能延后整个 `adder.can_run_list` 的准入结果回调。
此时第一个 child 已被原生选中、输入尚未分配，却仍是 `started=false`。
检查第二个 child 时，前者已计入 `outstanding_input_tokens`，
又进入待物化候选排序，被第二次扣费，甚至被撤销已有许可。

CPU 用例实际复现：两名 child 的初始预算分别为 400 和 200 token，
HBM 有 603 token 空闲。第一个已经选中后，增长预留为 2 token，
其 400 token 承诺应只计一次，剩下 201 token 足以容纳第二个。
旧代码仍以完整 400 token 再比较第一个，撤销它的许可。
`materialization_selected_commitment_before_r1.xml` 保留精确断言失败。
这说明容量承诺修复不能只覆盖 parent，还必须保持已选 child 的连续性；
它不是该轮旧 GPU 负结果的独立因果证明。

修复复用同一份原生已选/暂存请求映射，按当前请求对象与完整可见身份核对。
已计入在途预算的首次 child 保留本次许可，不重新排序或占用候选窗口；
取消选择后回到普通未开始候选，不提前标记为已物化。
不同对象、旧 epoch 和取消成员不能凭同名 request ID 获得保留资格。
没有新增定时器、抢占、预测或永久 KV 保护。

`materialization_selected_commitment_related_r1.xml` 实际执行 113 项，
全部通过，无失败、错误或跳过，保留 16 条原环境警告。
范围包含四项新增检查、独立复现用例、已有实际原生容量/生命周期检查、
需求恢复流水线、普通准入节奏和 Oracle 次序。
原有 1,995 身份绑定与已准备的自动接续已停止在 GPU 注册之前；
当前 native 基线继续使用原冻结实现。后续必须生成新的整合树、源码资格
和匹配双组接续，不能修改旧准备后继续引用旧摘要。
