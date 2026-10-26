# 负载执行问题与四个 Baseline 的准备

日期：2026-10-25，America/Los_Angeles。

最新顺序为：先运行 ThunderAgent，推进 KVFlow、TokenCake，再返回
Agentix 适配，之后继续新的一体化调度设计。AgentX 是共同评测负载，
Agentix 是另一条调度 baseline；“用 AgentX 评测 ThunderAgent”不表示
组合两个 baseline。已有
`thunderagent_workflow` 是未启用候选，CPU 正确性不代表已取得性能优势。

## LoCoBench-Agent 的归因

当前不能把问题归到模型一项，也不能把所有测试非零退出都叫环境错误。
至少存在三种独立情况。

1. **本项目环境准备不完整。**真实依赖、Django settings、pytest async 模式、
   fixture scope 和部分测试入口没有预先校验。普通 shell 工具能执行，失败发生
   在命令启动后的 collection/setup。共享 root/child 配置、真实依赖镜像、
   项目 settings 和执行诊断已补齐，环境问题也必须由我们负责修复。
2. **选中数据集项目的测试与实现不一致。**例如 MediVerse 原始归档仅提供
   43 个文件，没有被 importer 遗漏的 settings 或 manage.py；补齐真实
   Django 配置后，原测试仍引用源码不存在的模型接口。一些原始测试在导入
   失败后自行定义替代服务，甚至注册替代模块。这不是修 prompt 或装依赖
   就能还原的真实应用执行。
3. **模型命令与任务执行问题另行判定。**模型可能选错路径、重复命令或修改
   错误代码；应以其命令和可执行的标准入口对照。它不能解释无需任何 LLM、
   在原始源码上就能复现的缺文件/接口。任务本身预期的断言失败也不能被
   修成“环境成功”，更不能为了拉长工具时间增加等待。

V195 的 37 条 root 测试命令中，12 条在 collection/configuration 阶段退出，
1 条出现 fixture 错误，23 条报告了实际 test-call，1 条输出不足。该审计不
覆盖所有 child 的命令，不能把这 37 条当作全部 85 次测试类调用。

进一步的独立探针在 **12 套此前能够执行 test-call 的 Python 测试**中没有
观察到测试阶段的项目实现函数调用。这支持拒绝其“应用正常执行”的资格，
不等于已经证明整个数据集所有项目都不可用，也不以探针耗时充当工具时长。
正反 CPU/Docker 控制覆盖真实应用调用、测试内替代实现、仅 import、仅
fixture 调用及提前创建的后台线程。

证据：

- `experiments/reports/v3853_v195_root_tool_execution_audit_20261025.json`
- `experiments/reports/project_environment_repairs_cpu_20261025.json`
- `experiments/reports/project_source_repairs_cpu_20261025.json`
- `experiments/reports/existing_project_production_calls_cpu_20261025.json`
- `experiments/reports/production_call_docker_controls_cpu_20261025.xml`
- `experiments/reports/production_gate_application_cpu_20261025.xml`

最终继承应用回归 6510 项及 2 个 subtest 通过，无失败、错误或跳过。该结果
验证验收代码，不代表上述项目变成了合格的真实应用负载。

### 2026-10-26 执行问题复核

此次复核区分工具通路、测试入口、实际项目调用和任务断言。V195 原轨迹
包含 545 次 `execute`，另有读文件、搜索与编辑调用，不能表述为整个
LoCoBench 工具接口不能调用。主要失败发生在 shell 启动后的项目入口。

最新 `locobench_switch_project_execution_20261026.json` 复查原 36 项目：
公共依赖 preflight 均成功，9 个入口满足测试执行检查，所有入口的独立
pytest call-phase 探针都没有观测到项目实现函数调用。这个零值限定在
探针覆盖的原测试调用阶段，不表示文件工具、shell 或源码 import 没执行。
入口中有部分测试运行与部分 fixture 失败同时存在，9 不是所有曾进入
test-call 的项目数量，也不是通过全部任务断言的项目数量。

`locobench_real_pythonpath_repairs_20261026.json` 又为 6 个项目显式补入
实际存在的源码包目录，保留原源码和原命令；仍没有项目满足生产调用检查。
因此路径配置可以修复部分导入，但并非全部问题的根因。

| 示例 | 已确认的具体原因 | 对应处理 |
| --- | --- | --- |
| MediVerse | 原环境缺 Django settings；独立修复环境和启动导入后仍有测试/实现接口不匹配 | 先传递真实 settings，再独立审计数据中的接口，不把配置成功称为完整修复 |
| EduBridge | pytest 参数名 `request` 与框架保留名冲突，另有普通函数中的 `async with` 语法错误 | 属于原测试缺陷；若修复须保存独立补丁并保留原始结果 |
| Physage Academy | 加入 `src` 后能定位包，但测试需要的 `core.command` 不在提供的源码中 | 核对原始归档与任务要求，确认是任务目标还是不配套测试，再决定修复或换项目 |
| PulseLink | 加入项目根路径后仍无法导入源码未定义的 `ConfigValidationError` | 属于符号接口不匹配，不能靠继续增加 PYTHONPATH 解决 |
| CampusConnect | 测试文件自己定义精简版 `CourseService` | 测试执行不等于项目实现执行，需使用实际项目入口 |
| PulseStream | 导入失败时注册内存 stub，并测试 stub；提供的源码路径是 `src/processing` 等 | 核对真实实现与测试目标；不以 stub 测试证明实际应用已运行 |
| ShowTime Stash | 合约测试缺 `brownie` 及对应执行依赖 | 建立真实工具链后复查；不能通过只安装 pytest 宣称就绪 |

前期 manifest 的 `python_ast_check` 明确只是 syntax-only，preflight 只
导入公共库。曾以此将一批项目用于采样，是工作负载准备检查不足。
后续应在调用 LLM/GPU 前，以同一个真实镜像、cwd、源码路径和环境变量
直接执行任务相关入口。纯配置问题归入环境修复；原测试/源码不配套归入
带版本的任务修复或候选替换；任务本身合法的断言失败保留，不预修目标。
新源必须以实际使用的源码提交和 root/child 共用环境重新采集。

官方工具列表和原版主要评测包括文件操作、搜索和代码分析，但官方仓库
也提供 `CompilerTool.compile_code/run_program/run_tests`。Python
`run_tests` 实际执行 pytest，不能将本地 pytest 失败全部解释为我们
增加了官方不支持的要求。本项目的多 agent、Docker 环境和完整长输入
仍是单独的 runtime 适配。真实执行后仍很短的工具应如实记录。

### 官方流程与本地使用的差异

本次直接核对固定作者提交
`2ab9218e6d46e50ebdbb139442503e5573bc4a5d`，不将读取数据集等同于
已经复现完整官方评测命令。

- 官方 `CompilerTool` 的 Python 配置 `compile_command=None`；
  `compile_code` 对这种配置直接返回成功，不执行语法、依赖或测试检查。
  这条编译返回不能证明 Python 项目可运行。
- 官方 Python `run_tests` 配置为 `python -m pytest {test_dir} -v`，
  命令非零会返回测试失败；官方工具本身不会自动补齐 Django settings、
  包路径或项目缺失的模块。不能声称它只是假装执行所有语言的测试。
- 默认启用的 `BiasFreEvaluator` 把最终 `execution_success_rate` 作为
  tool-diversity 指标计算：工具种类数除以调用次数对数加一，再限幅；
  该函数不读取工具退出码或 pytest 通过数。评测分数不是项目测试通过率。
- 官方默认 minimal 初始化，让 agent 按需读取源码。本项目把完整源码
  放在 root 上下文，并增加动态 child 和独立 Docker 工具环境；
  这是 LoCoBench-derived 多 agent 工作负载，不是原版 harness 的原样运行。

论文第 2.1 节确实写了 compilation/execution validation 和 test passage，
所以发布样本中的语法错误、缺模块与测试内替代实现也是需要记录的上游
复现问题，不能全部归责于本项目要求不同。现有证据不确定作者是否使用了
其他环境、数据修订或未公开校验；也没有测得官方完整 pipeline 在这
36 项目上的同版本失败率，不把本地报告冒充官方复现结果。

下一步区分两项：先以相同项目和固定源码做官方工具与本地工具的输入、
工作目录和退出码核对，再对真正存在的项目执行缺口修复环境或更换候选。
无需先调用 LLM 才能比较同一个 pytest 入口；上游样本缺陷应保留原始反例。

固定作者源码：

- `https://raw.githubusercontent.com/SalesforceAIResearch/LoCoBench-Agent/2ab9218e6d46e50ebdbb139442503e5573bc4a5d/locobench/tools/compiler_tool.py`
- `https://raw.githubusercontent.com/SalesforceAIResearch/LoCoBench-Agent/2ab9218e6d46e50ebdbb139442503e5573bc4a5d/locobench/evaluation/bias_free_evaluator.py`
- `https://raw.githubusercontent.com/SalesforceAIResearch/LoCoBench-Agent/2ab9218e6d46e50ebdbb139442503e5573bc4a5d/locobench/evaluation/agent_evaluator.py`

## AgentX 发布轨迹评测

**2026-10-25 后续用户指令覆盖以下早期候选描述：AgentX 是当前唯一主负载，
不继续准备备用负载。直接以发布轨迹运行 baseline，不采集新的 native 源轨迹。
发布 workflow 的完整 JCT 可以评测；原始应用正确性不可核验不构成拒绝
JCT 的理由。**运行时空闲规则、固定人口完成指标和已实现的直接入口见
`docs/research/agentx_jct_and_workflow_interactivity_20261025_zh.md`。

用户指定的 AgentX 按 SemiAnalysis AgentX 处理。固定数据：

- Dataset：`semianalysisai/cc-traces-weka-062126-256k`
- Revision：`8fecd2fc56694469f758f0afbbb6335ad3043740`
- 文件：568864747 bytes，SHA256
  `e39cd2ff3eba21d4a3664be51da743ac3d2149a1933898cafc7bfeac8147eeef`
- 作者 harness：`SemiAnalysisAI/agentx-harness`
- Commit：`e4bb2f835ffedbbe6b76252ea5c93caec68c5faf`

这是实际 Claude Code 会话衍生的脱敏结构数据。公开输入主要是请求长度、
输出长度、64-token block hash、时间、显式 subagent marker 和子代理内部
请求。当前固定文件的 393 个会话中有 175 个带显式子代理。

原始顶层请求含辅助调用、并行链及压缩前后续接，不能全串成单一 root：
原始逐项检查有 359 个会话出现顶层 API 区间重叠。简单 marker 分组得出的
115 个并行 JOIN 会话只是初查；使用作者固定提交的分链函数后，按实际主链
和作者 tiered JOIN 规则得到 **89 个带并行 JOIN 的候选会话**。
这两个数字的差异保留，不以初查数字作为最终人口。

目前审计直接执行未改动的作者 schema/分链函数，仅读取相关 AST：
用普通 Pydantic BaseModel 替代 harness 的路由基类，不启动作者 serving
框架。主链、推断 worker、辅助调用、压缩 seam 和所有原始请求均保留。
该组件复用不等于已经完成整个作者 harness 的重放一致性验收。

初次 seed21 哈希顺序选择的 18 个候选完整保留各自公开行中的请求：

| 观测 | 数量 |
| --- | ---: |
| 全部模型请求 | 5327 |
| 显式子代理 | 161 |
| 至少两个成功且有模型调用成员的 JOIN 组 | 30 |
| 输入 token 总数 | 359121600 |
| 输出 token 总数 | 5069634 |

这个初选人口 **尚未成为 GPU 实验配置**。它包含明显长会话和很长的请求
间隔。需先界定人类空闲、外部间隔和推断依赖，选择有资源上限的完整会话，
不得截取结果有利的片段，或者在重放结果中裁掉正常长尾。
不要把全部原始时间差再加在新模型完成之后，重复计算旧模型执行或 JOIN 等待。

已有 `agentx_trace.residual_ready_gap_seconds` 明确从后继到达时刻减去
已完成前驱 API 与必要依赖中最晚的结束时刻。尚未实现 GPU 执行适配，
该函数也不能自动判别一个无标签间隔究竟是工具还是用户空闲。

### 必须披露的边界

- **结构与应用正确性不同。**数据不包含原始工具命令、代码内容和工具输出，
  不能拿它证明测试执行成功或修复任务正确。
- **公开行与原始完整会话不同。**256K 版本按请求删去超出上限的调用，
  部分 child group 仅保留一部分内部请求。完整执行公开行不能被称为
  完整原始代码任务的 JCT。该上游过滤必须作为 benchmark 定义披露。
  不能改用官方随机中段启动、预热和固定一小时窗口，随后又把各组不同的
  完成工作量当作完整同工作量 JCT 对照；本项目需要独立命名的固定人口
  run-to-completion 适配。
- **会话与项目不同。**脱敏信息不足以证明 18 个会话来自 18 个不同原始项目。
  后续结构重放应为每个完整树使用独立 cache namespace，树内沿用原 block
  hash 共享；这能避免跨 workflow KV 共享，但不能冒充项目来源证明。
- **JOIN 是推断边界。**显式 child marker 是原始记录，阻塞 JOIN 的具体后继
  按作者的 recorded-end/timestamp 规则推断。推断 worker 和嵌套关系不能
  直接称为原模型输出中已验证的 task 调用。
- **单模型适配要单列。**源数据有多个 Claude 模型；映射到同一个 Qwen3-Coder
  比较的是同一请求结构在共同底座上的性能，不是原模型/工具生态复现。
- **缺工具语义。**当前 393 行均无可用的 input/output type 或 stop 注释。
  旧语义工具预测器不能凭事后间隔恢复成在线工具类型。
- **未来数据隔离。**驱动可持有完整重放 DAG；在线控制器只能收到已经发生的
  spawn、等待、请求完成和工具/外部阶段事件。不能预先知道 child 最终
  token、结束时刻或未来复用。
- **新源类型独立。**现有 natural-source 注册器保持项目与实际应用执行门槛；
  不向其伪造 LoCoBench 类型、原始 EOS 或任务评分来导入 AgentX。

原始数据和负结果保留。AgentX 用于结构、前缀复用、迁移和完整发布会话的
JCT 比较。此前准备的 SWE-rebench 不再推进，也不作为替代评测负载。

审计：

- `scripts/audit_agentx_workload.py`
- `experiments/reports/agentx_workload_inventory_20261025.json`
- `experiments/reports/agentx_workload_inventory2_20261025.json`：
  增加全语料请求/token 数完整性核对、分析源码哈希和上游过滤边界
- `experiments/reports/agentx_trace_focus_cpu_20261025.xml`：10 项通过

## 四个 Baseline 的准确范围

**Agentix 的 program 公平和 JCT 目标是已有设计。**PLAS 继承程序累计服务；
多线程 DAG 使用 ATLAS 的已观察最长服务路径，配合离散 MLFQ、call quantum、
等待/服务比老化、multi-step 执行与聚合 swap。原文 §4.2.2 已明确讨论
频繁切换造成的 KV 搬运，因此不能将“Agentix 会频繁迁移”当作未经实验
就已成立的结论。仅把 identity 从 request 换成 agent 不是本项目的创新。

并发子代理工作流的 Agentix program 对应完整 root workflow；多个 agent
共享原生程序服务账户。不能把每次新 child 都注册成新的零服务 program。
现有 `program_service.py` 只有服务继承参考核，缺少真实执行器计时、量子、
运行中抢占与换出，仍不算完整 Agentix。

| 方法 | 当前状态 | 下一步必需工作 |
| --- | --- | --- |
| Agentix | MLFQ、executor 记账已验证；暂缓且未启用 | 补原生 LLM hook、抢占与完整 Host 路径所有权，再进行整体验收 |
| KVFlow | Step Graph、共享前缀优先级及原生驱逐已通过 CPU 验证 | 接通已观察事件、固定前缀边界、物理预取和状态准入循环，明确图信息契约 |
| ThunderAgent | 已修复流式占用与原生重排身份；V219c 正在执行 | 完整同工作量结果和重复；V219b 中断保留，旧 V209 属于修复前历史证据 |
| TokenCake | 6 类公开策略各对照 5000 状态一致；原生迁移与 Host 租约组件已验证 | 接通预测上传、关键/共享容量保护及原生准入，分开登记公开版与论文版 |

最新实现范围、动态记账修复及未完成项见
`docs/research/paper_baseline_ports_20261025_zh.md`。当前新增路径回归 239 项、
共用调度路径回归 178 项通过。新增物理组件的拷贝使用 CPU stand-in，
这些结果不能替代真实 GPU 搬运和完整性能比较。

KVFlow、TokenCake 使用预声明图的版本放在独立的同信息输入组。若动态
AgentX 图只能逐步观测，就明确标为 observed-graph adaptation；不能只让
某一组读取未来完整图，也不能将论文所需输入缺失默认为组件已经复现。
匿名外部阶段回退与具备工具身份的原论文策略也应分别披露。

每一条物理适配均需证明实际 D2H/H2D、保护/释放和准入生效，保持本项目
JOIN 优先、resident-first、PREPARE、短工具续接等关闭；通过组件测试
后才允许成为 runtime arm。完整对照按相同 token、依赖、容量、种子、
完整正常尾部进行，工具模拟间隔与真实工具时间单列。

统一登记：`configs/baselines/paper_reproductions_20261024.json`。
尚未执行新的 GPU 对照，也未宣称任一候选在全部 baseline 上取得优势。

## 原始资料

- https://inferencex.semianalysis.com/agentx/methodology
- https://github.com/SemiAnalysisAI/agentx-harness
- https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126-256k
- https://www.usenix.org/system/files/nsdi26-luo.pdf ，§4.2
- https://github.com/PanZaifeng/KVFlow
- https://github.com/ThunderAgent-org/ThunderAgent
- https://github.com/pku-lemonade/TokenCake
