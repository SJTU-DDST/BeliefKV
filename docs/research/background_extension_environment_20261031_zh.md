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
