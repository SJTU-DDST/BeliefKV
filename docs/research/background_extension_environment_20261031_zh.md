# 补足共同背景到达流的原项目环境准备

## 目标与边界

固定 18 个测量成员保留完整 JCT，背景持续到达以维持后半程竞争。
已有 56 项目的到达流在测量长尾前结束，仍不满足稳态背景覆盖检查。
本准备只补充同一 LoCoBench 数据集内的不同原项目，不修改任务、
源码、测试、已有 token trace、工具时长、测量成员或运行中的到达表。
不因更容易得到策略收益而改写任务；短上下文也不预先等同于短工作量。

当前原有 12 初始、平均 70 秒的比较仍冻结运行。补充候选在环境、
真实 root/child 工具及自然 GPU 轨迹均获得证据前，不进入新比较。
不登记与当前比较重叠的 GPU 采集或自动后继。

## 原题与独立项目核验

复用 `background_extension_original26_inventory_r1.json` 的原清点：
其中实际通过原语法筛选的候选是 25 个，而不是目录名里的 26。
它们与当前 56 个项目的编号家族均不重复；原有到达前缀保持不动，
同一 seed35 的 25 个暂定后续槽位可将最后到达从 3259.859 秒延至
4999.367 秒。槽位尚未绑定新 trace，不能据此声称已经覆盖测量尾部。

`background_extension_original25_population_r1.json` 将这 25 个项目重新
绑定到原始全量清点；逐字段核对项目、原场景、难度及公开任务 hash，
并以当前 56 家族更新排重检查。没有使用各策略的输赢选取候选。

原准备入口默认只接受 easy/medium。新增显式
`--allow-original-difficulty` 可重复参数，允许此次声明 easy、medium、
hard、expert 四档，保留旧默认规则及原题难度，不重新标注。
变更后的实际准备声明为 5 easy、9 medium、7 hard、4 expert。
原有源码/任务不可变、不同家族、上下文范围和语法筛选检查继续生效。

新增 `--source-dir` 允许把新导入放到独立目录，
避免复用旧项目导入时依赖另一份 preparation 的 context/provenance 文件。
本轮使用 `workloads/sources/locobench_original_background_20261031`，
未覆盖旧工作负载源码。

实际输出在 `background_extension_original25_preparation_r1`：

- 25 份完整原题及私有 reference；reference 不作为模型任务输入；
- 462 份原项目文件的 bytes 与完整 context 一致性检查全部通过；
- 采用当前 Qwen3-Coder tokenizer 核对完整上下文；
- 保留固定 Node 工具链镜像
  `sha256:ae7747bcc4719f63672af1947af26126982180a8e02ac154e091edfd318d8593`；
- `capture_ready=false`，环境、自然工作量和 JOIN 结构仍需验证。

新增 13 项测试覆盖显式 hard/expert、原标签与输入不变、非法策略拒绝，
以及扩展难度不能绕过其余选择约束。连同旧原题准备和真实 npm 生命周期
fixture，共 37 项通过，无跳过。有效修复前的未知参数失败保留在
`background_extension_preparation_cpu_r1/before.xml`，通过记录为 `after.xml`。
这不是当前新增采集的完整源码绑定或 GPU 资格。

## 依赖准备

运行既有 `provision_original_node_projects.py --dependency-only`，
每次最多一个安装容器、一核、4 GB，CPU operator 为 nice19、
CUDA 隐藏、OMP/OpenBLAS 单线程。安装仅作用于私有 workspace，
保留原 package.json/源码/测试；执行真实依赖安装钩子，项目自身钩子
仍留给 agent。使用已有 npm cache，不修改原版本范围或填入替代模块。

输出为 `background_extension_original25_dependencies_r1`。
当前已观察到 gateway081 的原始 `swagger-jsdoc@^7.2.0` 无可匹配版本，
graphql079 没有原始依赖清单，rest042 成功安装。
这些结果只说明对应准备路径是否成功，不代表任务本身正确与否。
全部失败原始日志保留，后续不能仅因工具链镜像可用就把失败项目算作合格。

成功安装后仍需固定依赖镜像、重新验证实际 root/child 工具，
再准备干净源码与自然 GPU 采集。不得提前把 25 个候选写成 25 条已完成
轨迹或把暂定到达槽位写成可执行背景负载。

## 并行 Oracle 状态

当前 resource-combination 比较使用 `db82e94d` / native `434c35dd`，
端口 18942；新的 24 请求检查均为原生 stop，batch1/8/24 采样通过。
`calibrated_combination_initial12_mean70_prepare_r7/live_oracle_r1.json`
已独立验证实际容量与旧控制一致、6 份精确 token 收据、
26 条显式新选择器记录及唯一 nice0 GPU scheduler 3265190。
CPU 固定测量组观察器 3265978、结果观察器 3265979 实际存活，
nice19、CUDA 隐藏、单线程；原有完整尾部收尾条件不变。

早期 `live_combination_choices_r1.json` 封存 326 次入口：
12 次可重放搜索，其中 10 次没有满足现有资源的可行组合，
2 次确实选择了可行替代集合，但均未加入原参考外的成员。
其余为 276 次空参考快路径和 38 次无竞争。
这证明新增选择路径已触发，不证明更多有效并行、JCT 或吞吐收益。
A→B 和压力暂停仍不在组合审计范围内。

首次只读分析命令在冻结 checkout 使用了错误的相对 cohort 路径，
在写报告前失败。改用绝对路径后产生上述有效报告；没有重启或改变
GPU 运行。该命令失败不能归为服务端或测量失败。

## 完成的环境结果与任务边界

依赖安装本轮已结束，25 个原项目的结果为：

| 原样安装路径 | 项目数 |
| --- | ---: |
| 完整成功 | 3 |
| 实际 npm ETARGET，原版本范围未匹配到包 | 9 |
| 原项目没有依赖清单 | 5 |
| 原生 ML/GUI 栈超出本批准备范围 | 7 |
| 需要独立本地 workspace 依赖绑定 | 1 |

完整依赖成功的是 rest042、inference016、social037；失败及未尝试项目
保留原始状态，不通过修改版本或补模块使其变成成功。
依赖镜像固定为 `sha256:50fbe7c65f5a…`，完整 digest 在
`background_extension_original25_dependencies_r1/image_id.txt`。
三个项目在该镜像上的真实 root/child 工具、同一环境、共享工作区、
非零返回码和清理均通过。`background_extension_source3_r1` 保留这个
较强环境子集的采集声明，仍没有自然 GPU 轨迹。

源码绑定 `background_extension_source3_bound_cpu_r1/evidence.json`
通过 6373 项及 23 子项，保留全部 6360 项旧 literal identity 和旧输入路径，
共 2262 个源码输入；执行中源码、外部输入与原生树均不变，无失败、
错误或跳过。该证据绑定三项目声明，不能冒充后述 25 项目的人口资格。

### 工具可用性与应用依赖成功分别核验

这 25 道原题都是完整的 `code_comprehension` 场景。
完整安装应用并不是读取源码、使用工具完成代码理解的必要条件，
不能因为 npm 清单缺陷或没有清单，就断言 runtime 不能正常执行工具。
同样，基础工具可用不能当作应用测试或任务正确性已经通过。

因此使用实际工具后端另做 25 项逐项目检查：

- root、child 都实际执行既有工具链、环境与共享工作区检查；
- 两者都额外用真实 Node `fs` 读取一份原始 JS/TS 源文件，
  校验其 SHA256 与宿主原文件完全一致；
- 该读取只在私有资格 workspace 中执行，不改变采集或实测工具命令；
- 三个完整依赖项目复用已验证链接，其余保留明确的未准备应用依赖状态；
- 项目源码、测试、任务要求与难度不修改，不执行或修复应用测试。

实际 25/25 通过，没有拒绝项目。输出在
`background_extension_original25_tools_r1`，包含逐项目报告、
`toolchain_live_root_child.json` 和 `qualified_manifest.json`。
报告仍使用 `live_root_child_tools` 的原有资格范围，并显式补充
原源码读取证据及每个项目的应用依赖状态，不宣称 25 个应用可运行。

这使全部候选都可以进入代码理解自然轨迹的下一步审查，
不必仅限于三个完整安装项目。新 trace 中仍须检查模型有没有实际推进
原任务、是否大量停留在配置/collection 错误、真实多成员 JOIN、
完整自然结束与工具时间；不以资格探测代替正常工作场景。

### 25 项目后续声明

`background_extension_source25_toolchain_r1` 已生成独立声明、配置与原始输入
绑定，保留 4/8 child、8/16 root 软预算、原生工具时间及完整自然尾部，
没有硬调用上限或预设 DAG。额外披露 3 个已安装与 22 个未完整准备的
应用依赖状态，并要求审查真实工具失败及任务推进后才能使用轨迹。

当前 `population_specific_CPU_binding_pending=true`、
`capture_GPU_qualification_pending=true`、`capture_ready=false`，
新自然轨迹仍为零，没有 GPU 采集注册。三项目的 6373 项证据只保留为
共享代码与该子集证据，不能自动移用于 25 项目采集准入。
25 项目将来加入后须形成新的匹配负载与对照；不能用旧 56 项目性能数字
冒充扩展人口已经通过的稳态比较。

## 新 Oracle 路径的后续服务证据

`live_combination_choices_r2.json` 封存 872 次入口：
21 次搜索、5 次集合变化，其中 2 次加入参考外成员。
这两次成员均保留了实际周期资格、匹配同一请求 attempt 的后续服务，
并在下一控制或干预前完成了有效计算。
14 次资源回退仍无可行子集，3 次选择可行替代集合；
元数据与选择 CPU 只是控制开销的一部分。

相邻实际 runtime 计数观察到 19 次深前缀刷新均生成聚合摘要，
说明深路径分支实际触发。不能将这些触发、服务或局部代理分数等同于
完整 workflow JCT 的因果改善；固定 18 个测量成员仍按原协议自然收尾。
