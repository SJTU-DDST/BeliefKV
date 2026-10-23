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
正常长尾及完成资格。该采集不是性能对照组。源通过完整性及实际
GPU 重放校验后，再用相同工作量、容量和 batch 对比 native、Full
及 JOIN-only。不按 Full 收益、固定 JOIN 数或正常长尾筛选源轨迹。
CPU 资格及注册/运行状态另存报告和 ops 登记；未完成的采集不能
报告为完整 JCT 或收益。
