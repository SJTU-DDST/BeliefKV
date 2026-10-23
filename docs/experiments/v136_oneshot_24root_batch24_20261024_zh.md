# V136：恢复一次性 subagent，24 root / batch 24

用户要求暂缓 subagent 复用与选择性 KV 保留，先验证原有的 JOIN、
工具期卸载/预取、Host 增量准备及调度机制。此次将 root 数由 16
增至 24，将服务端 running/decode 上限由 8 增至 24；实际 batch
大小仍取决于就绪请求和原生容量准入。全部 root 同时到达。

使用旧版 `native_in_graph_nested_2to4` 系统提示词和委派中间件。
该版本在初始委派中提示 2–4 个 child，鼓励分阶段多轮委派，并允许
coordinator 再创建叶子 child；模型生成实际工具调用。没有固定重放
DAG，也不加入任何强制复用。完整项目源码只提供给 root，child
通过具体任务描述、选定片段和工具获取其所需信息。

`BELIEFKV_SUBAGENT_RETENTION=off`，不使用稳定可复用 handle API。
predictive 延续安全退休 child 私有物理 KV 的机制；共享前缀和真实
请求/传输锁继续遵守原生所有权检查。native 不额外感知 RETURN
并删除物理 KV，继续采用原生会话引用及缓存淘汰规则。

## 项目和压力依据

新清单是
`configs/workloads/locobench_agent_long_24root_16project_env_20261024.json`，
SHA256 为
`1204acfa790bb85f17e9de026282be5bdbf07ad374296e59750db6fa419dcd6a`。
它按既有 seed 21 顺序，从已固定环境的 16 个项目选取 24 个不同
原始 LoCoBench-Agent 任务，每项目最多两个任务，未根据运行结果选取。
这是 24 个任务、16 个项目，不能称为 24 个独立长前缀。
16 项目的固定镜像、源码 commit、context 与来源证据继续绑定；
启动前检查了 51 个输入绑定。

V135 截止时 Host FULL 高水位为 67.837/160.000 GB，约 42.4%；
Host Mamba 则达到 40.050/40.050 GB，且有组件淘汰。
FULL 的同 session 已服务公共前缀丢失代理为 60894/166104655 tokens，
约 0.0367%，不能等同于全部模型重算率。旧版的 token_usage 和
resident_pressure 会扣除可驱逐缓存，不能用其 48% 活跃期中位数
认定总驻留量只有 48%。V3181 保留原始数字并增加了明确的语义更正。

已检查的数据中，源码语法通过且长度处于 95K–196608 tokens 的项目
只有 16 个；100K 以上为 12 个，50K 以上为 30 个。若要求 32 个
不同项目，在现有 196608 上界内需要降低长度门槛至约 42K tokens。
另有 4 个语法通过、源码约 204K–237K 的项目，尚未完成当前固定
环境与完整输入检查；即使加入它们，也只有 20 个 95K 以上项目。
不能通过加入较短项目后仍把它描述成 32 个独立的 100K 长项目。
理想化的 32 份独立 100K FULL 前缀为 65.536 GB，大于 36.822 GB
FULL HBM 池，小于 160 GB Host FULL 池；这只证明会需要分层存放，
不能单独证明大量重算，也不能保证无重算或 predictive 优势增加。
因此暂不直接扩大到 32，先检查 24/batch24 下真实迁移和重算证据。

## 固定设置与验证

FULL HBM 保持 1797968 tokens，Mamba HBM 保持 513 slots；
Host 保持 200GB、FULL:Mamba 为 80:20，write_back。
上下文 262144 tokens，95% 压缩，root 输出上限 16384，seed 21。
普通 execution-frontier restore 和短工具排队连续性保持关闭。
Full 计划验证 EOS-only JOIN 预取、Host 增量备份、工具时长预测/
压力卸载，以及 workflow/resident-first/stage 调度。
工具预取采用实际 ACK 起算的 1500ms 优先窗口。

在原有每秒一次安全点采样中加入 FULL/Mamba 的 allocator 空闲、
可驱逐、不可驱逐及总分配单位。只读取原生标量计数，不遍历 radix
树，不增加逐 token 扫描或 GPU 同步。无法读取或不一致时报告
不可观测，不将其记为零。该遥测对 native 和 Full 相同。

先采集完整自然 native 源轨迹，保留精确输入输出 token、工具依赖、
正常长尾及完成资格。按用户最新要求，将这次自然 native 采集的
完整 JCT、工作量及全程输出吞吐作为 native 参考，不再额外全量
重放 native。源通过完整性及实际 GPU 数据路径校验后，仅重放
Full、JOIN-only 和必要消融，容量、batch、token、停止条件一致。
报表显式标记自然采集参考，并将历史重放校准单列；不宣称当前新源
已重新完成 native 重放校准。不按 Full 收益、固定 JOIN 数或正常
长尾筛选源轨迹。
CPU 资格及注册/运行状态另存报告和 ops 登记；未完成的采集不能
报告为完整 JCT 或收益。

## 实际启动与初期观测

北京时间 2026-10-24 00:34:50 前后，V136 已通过资源检查并启动，
冻结 app commit `79bb1bf24cd574ef52c78b7d9d4d57d22da62f4a`，
引擎 tree `cc9772c6c5c4dcd3678a03b67fa9afc190aec4f8`。
登记文件为
`/home/longhao/experiment/ops/v136_oneshot_24root_running24_source_20261024.registration.json`。
实际 server_info 确认 running=24、context=262144、seed=21，
decode CUDA graph 包含 batch24；实际四个池与参考 census 完全一致。
CPU 最终资格 V3184/V3185 保留原 5355 项，合计 5379 项及 2 项
subtests 全部通过；此前失败的 V3182/V3183 保留，原因是容量测试
fixture 未补全新观测使用的字段。没有把失败资格用于启动。

约 00:39 的封闭事件前缀已有 24 个 workflow_start、48 次 spawn、
24 个 JOIN create、412 个 LLM submit；尚非全程完成统计，
不能将 JOIN create 当成 satisfied 或提前恢复复用。GPU 的一次
利用率采样为 98%，不能视作全程平均。

00:40:05.998 的实际安全点采样：

| 池 | 使用情况 |
| --- | --- |
| FULL HBM | 1795200/1797968 tokens 已分配，99.85%；其中 1390978 可驱逐 |
| Mamba HBM | 511/513 slots 已分配，99.61%；其中 419 可驱逐 |
| Host FULL | 25.273/160.000 GB，15.80% |
| Host Mamba | 22.923/40.050 GB，57.23% |

相邻状态快照记录 FULL H2D ACK 173938 tokens，约 3.562GB，
Host 尚无淘汰。已服务公共前缀丢失代理为 14703/5047636 tokens，
约 0.291%；它不包含所有混合状态重算，不能替代实际重算率。
遥测 failed/dropped 均为零，writer_error 为 null。
这些都是启动阶段证据，不能外推为稳态、全程平均或 32 root 的结果；
它们已经证明当前 24/24 能制造真实 HBM 占用和迁移，不需要为制造
压力立即再增加 root。

## 后续扩容与对照规则

若 24 root 在主要 JOIN/工具等待阶段仍缺少有用 Host-only 目标、
持续迁移或 Host/HBM 容量压力，则直接升级至 32，必要时再到 36。
默认保持 running/decode24、固定容量及 seed21，记录真实项目数，
优先纳入更多独立且通过环境验证的项目。新增项目不足时，不把同
项目的不同任务当作独立长前缀。同步检查有用 Host 状态损失和重算，
避免只增加排队与反复迁移。

改变 root 数后重新采集对应配置的自然轨迹/native 参考，再执行
Full 与必要消融；省去的是额外全量 native 重放，不跨不同工作量
复用旧 JCT。`run_causal_comparison.py` 默认只选择 Full、JOIN-only，
报表带上自然 native 参考和相对变化，精确 token/因果/停止校验保留。

已核对 V133b 的原始重放校准记录：
`experiments/raw/qwen35_locobench_long_36root_running8_reviewed_position_20261023_v133b/native_matched/source_replay_calibration.json`。
native 重放/自然源的平均 JCT 比值为 0.9921719，约低 0.783%；
全程耗时比值为 0.9935081，约低 0.649%，校准 accepted=true。
这支持按用户要求省去重复全量 native 重放，但仍是旧 running8
配置的历史证据，不将其写成本轮 batch24 新测得的误差界。
报告保留未经误差修正的原始相对变化，引用的源 summary 和 native
启动策略均绑定 hash；非 native 策略采集的轨迹不能充当此参考。

后续流程的最终 CPU 资格为 V3188/V3189：保留原 5379 项，合计
5388 项及 2 项 subtests 全部通过，无失败或跳过；新增覆盖不重放
native 的真实 supervisor 流程、源策略校验、原始 JCT 长尾及报表
相对值。V3187 是来源策略加严前的中间报告，不用于最终源码资格。
这些修改已用于下一轮操作流程；正在运行的 V136 仍使用自己的
冻结源码与原登记文件，未中断或更换采集代码。
