# Workflow 首次提交前准备与 JCT 口径

## 完整工作流集合测量

`scripts/audit_workflow_startup.py` 对完整的 18 个 workflow 分别测量
`workflow_start → 首次 llm_submit`，不去除长尾或只选择完成较早的任务。
证据见 `experiments/reports/v300_workflow_startup_audit_cpu_20261027.json`，
其中保留逐文件 SHA256、逐 workflow 数值和两组身份一致性检查。

| 计时来源 | 平均，秒 | 最小，秒 | 最大，秒 |
| --- | ---: | ---: | ---: |
| V230 自然源 | 36.283566 | 31.431470 | 38.998404 |
| V286 ThunderAgent replay | 36.297232 | 31.466212 | 39.001754 |

逐 workflow 的重放额外初始延迟均值约 13.67ms，最大绝对差约 34.74ms。
因此三十多秒主要是源中已有的客户端准备时间；当前 replay 并没有再次
执行原应用的 Deep Agents 图构建，而是将源准备时间保留为因果依赖定时器。
两组工具计时不同，上表仅用于检查初始准备保真，不能当作自然计时与
合成计时的等价性能对照。

## 图构建

当前 `_run_autonomous` 在调用模型前执行 `_build_autonomous_agent`；
`_autonomous_subagents` 递归构建角色及其嵌套 task middleware。
安装的 Deep Agents `_build_task_tool` 当场编译全部 raw spec。

`scripts/profile_autonomous_graph_startup.py` 使用当前同一
`native_in_graph_short_workstreams` profile、fake model 和不启动的
sandbox，实际计数出每个 root 构建 25 个 subagent 图与 1 个 root 图，
共 26 次 `create_agent`。18 个 root 对应 450 个预编译 subagent 图；
即使运行中没有嵌套委派，这些 grandchild 模板也已经构建。
角色/层级模板并不是已经运行的 450 个 child。

可复核的单 root CPU profile 保存在
`experiments/reports/v302_agent_graph_startup_profile_cpu_20261027.json`：
图构建总 wall time 1.225s，其中 subagent 构建累计约 0.986s。
该 fixture 不包含真实长 prompt 准备、18 root 并发竞争或历史 profiler，
因此不能据此认定历史 36.28s 全部来自编译，或者将 18 倍单次测量
当作已证明的实际等待。

提前构建尚未使用的图是可以避免的客户端工作。后续合适的改法是按
模型实际选择的角色延迟构建，并保留角色声明与独立的 child middleware
状态；实际发生的构建仍要计入对应 spawn 过程。不能跨 workflow 共享
带 backend、summary cursor、预算或 callback 状态的可变 middleware。
本次没有改变图执行语义，也没有通过调整时钟来宣称优化。

## JCT 边界

若定义用户到达至 workflow 正常完成的端到端 JCT，任务到达后实际发生的
图构建、prompt 准备与客户端控制就应计入。因此本轮及历史冻结 replay
继续使用 `workflow_start → workflow_end` 为主要 JCT。

系统初始化期间、所有任务到达之前真正完成的共享编译或预热可以排除，
前提是所有方法采用同一实际执行方式并披露边界。不能把任务专属准备
事后移出计时，或仅从 Full 结果扣掉这段时间。

为观察服务系统本身，同时保留 `首次 llm_submit → workflow_end`
作为诊断性持续时间。它不包括用户尚未提交模型前的等待，不等于端到端
JCT，也不是“实际删除定时器后重新执行”的性能结果。

## 对性能比较的影响

V286 ThunderAgent 的完整平均 JCT 为 1876.334933s，初始准备均值
36.297232s，约占 1.9345%；首次提交后的诊断均值为 1840.037701s。
共同的初始延迟会稀释百分比收益。若逐 workflow 准备时间相同，扣除
它不会改变两种方法的配对绝对 JCT 差值，但会改变归一化分母。
目前两组实际 replay 都应先核对同一初始定时器与少量执行 drift，
不能只假定任意历史实验的准备开销均相同。

首次服务指标受这项开销影响可能更明显，应拆分到达至首次提交、
首次提交至原生服务、服务至首 token，而不把三十多秒全部解释成
调度不公平。全程吞吐的时间分母则按共同的 workflow event makespan
计算；并发 workflow 的准备区间重叠，不能把 18 份准备时间相加后
从 makespan 扣除。

真实消除准备时间可能提前改变请求到达和竞争关系，需独立新 capture
或明确声明的新 replay timing profile。旧定时器和完整正常尾部保留，
不同 profile 之间不混合给出同工作量、同计时的性能排名。

V286 Full 随后完成。V308 对其同一组 18 个 workflow 的独立初始计时
复核得到均值 36.297035s，与 ThunderAgent 均值相差约 0.198ms。
Full 平均 JCT 为 1669.294213s，首次提交后的诊断均值为 1632.997179s。
该轮完整 JCT 比默认 ThunderAgent 低 11.0343%，全程输出吞吐为
189.339669 对 175.160173 token/s，高 8.0952%。两组请求数和 token 数
相同，未去除正常尾部；本轮结果不能归因于未参与执行的新工具主动
卸载模块。这里没有将单轮结果提升为可重复性或所有 baseline 验收。
