# 16 独立项目 workload 的 CPU 准备

日期：2026-10-23，北京时间。尚未运行新 GPU 实验，性能目标仍 active。
后续的固定环境清单、依赖修复和 V135 候选替换计划见
[最新 CPU 记录](../reports/2026-10/closure_and_project_environment_cpu_20261023_zh.md)；
本文保留初次准备时的 V3156--V3165 证据和原始失败。

## 已落地的输入

清单为 `configs/workloads/locobench_agent_long_16project_20261023.json`，
SHA256 为
`bd11ed764e0781f42cb6ad129585de7eb2d44b6aa6d1c65fe7749ebe3bd84e2d`。
全部取自原 pinned LoCoBench-Agent archive，16 个不同项目，每项目
一个原始任务；seed21 决定项目顺序和任务轮转，不按运行表现筛选。
保留源码、场景和 provenance 哈希，不传入参考答案，不作语义修复。
新导入的源码、context pack 和原始参考文件与旧资产分开保存。

独立对照配置为
`configs/migration/reusable_subagent_retention_16project_20261023.json`。
16 roots 同时到达，running/decode 8，seed21，256K 上下文、
95% 压缩；采用自然 reusable profile，不强制委派轮数、嵌套或复用。
保留原 Host 200GB/80:20 和参考 FULL/Mamba 实际池容量。
LRU/selective 的休眠 Host/HBM 预算仍为 32GiB/2GiB。
工具时长统计和压力卸载保留，工具 H2D 默认关闭；若启用 ACK 后
1.5 秒有限优先期，须对所有自定义保留策略共同启用。

## 完整首请求的证据

V3156 通过实际 `_build_autonomous_agent`、middleware 和工具 schema
采集首个模型输入，模型替换为只在本地返回的 CPU probe，不执行工具
或真实生成。使用本地 serving tokenizer 渲染完整 chat template，
直接完整编码与现有块缓存计数逐请求一致。

| 指标 | 数值 |
| --- | ---: |
| roots / 不同项目 | 16 / 16 |
| 首请求最小输入 | 103,260 tokens |
| 首请求最大输入 | 201,727 tokens |
| 首请求超过 100K | 16 / 16 |
| 首请求精确前缀并集 | 2,045,128 tokens |
| 并集对应 FULL 状态 | 41.884GB |
| 相邻排序请求的公共前缀 | 4,764--4,769 tokens |

所有输入加 16,384 completion budget 均小于 256K，也未在首请求触及
95% 压缩阈值。前缀并集超过参考 FULL 池的 36.822GB，但只是初始
内容工作集，不是某个时刻的物理 HBM 占用或迁移量。实际 pressure、
可释放状态、JOIN 时的 Host-only 字节和恢复收益仍需采集。

第一版审计曾错误地把 tokenizer 的结构化返回长度当成 token 数，
因此拒绝产生报告；另两次启动分别暴露了错误的常量 import 和不完整
的 reusable 配置。现已修复，V3156 为最终成功报告，审计器 SHA256
与记录匹配。未修改运行时计数或原始 trace 来通过核验。

## 工具环境的证据和限制

V3157 在相同 pinned Python3.10 镜像内，对 16 个项目执行已有公共
依赖 preflight。16 项均通过，实际 image ID 完全一致。
环境检查器对新项目使用原样 pytest collection，不再因只认识旧的
六个项目而直接 KeyError；旧项目的特定 probe 保持原样。

原始项目测试结果为：3 项返回 0，1 项返回 1，6 项返回 2，
5 项返回 3，1 项返回 5。失败包括旧项目已有问题，以及缺少 PySide6、
pandas、SQLAlchemy API 版本不匹配、项目模块缺失、配置缺失和
dataclass 定义错误。原始失败和输出全部保存，未用 stub 修改源码，
不把公共 preflight 通过宣称为测试、全部依赖或任务正确性通过。
依赖与任务质量的不足须在使用前继续处理或明确限定实验口径。

源码自身的 requirements 也不能直接合并：TimeClip 和 CampusConnect
要求 Pydantic2，CampusConnect course service 要求 SQLAlchemy2，
旧镜像为原项目保留 Pydantic1/SQLAlchemy1.4。TradeLedger desktop
清单甚至同时声明 Pydantic1 和要求 Pydantic2 的 settings 包。
下一步应按项目/实际任务范围检查依赖兼容，复用现有每 workload
的 pinned image 支持，不全局升级旧镜像，也不改任务源码来凑通过。

## 采集和对照的适配

源采集增加显式 `--source-workload`。缺省仍严格保留原 36-root
参考配置。新清单必须包含完整、非重复的任务和项目声明，冻结副本
必须与原清单相同，并绑定 context pack、provenance 和 Git commit。
新任务仅允许自然 reusable profile；不借用旧的固定 JOIN 门槛。

监控器按实际 root 数读取 `client_N/summary.json`，校验完整群体；
reusable 审计按同一数量验证所有 workflow 和实际 started resume。
重放对照也从源 summary 核验全体 workflow 的身份和完成数量，
不再以常量 36 阻止新场景，也不允许只取加速的一部分 workflow。
保留策略矩阵另核对声明 root 数及原始清单 SHA256，防止混用数量
相同的另一份轨迹。真实 16 项配置已走过全部五组环境构造，采集端
确认 33 个外部输入；selective 的实际运行时构造在 CUDA 被屏蔽时通过。
这些检查位于注册和结束边界，未增加逐 token 的控制工作。

V3159 的 227 项相关回归通过。V3160 完整保留 V3153 的 5284 项，
5312 项和 2 个 subtest 通过，无失败或 skip；新增 28 项覆盖新项目
预检、真实首请求组装、16/36-root 完整性、资产变化、不同 client
目录和旧接口兼容。
V3162 的后续完整回归保留为失败证据：旧测试明确拒绝 16 roots，
与本次新增场景冲突。已将该断言更新为“16 只改变 root 数、其他策略
设置不变”，非法数量继续拒绝；V3163 的 169 项相关回归通过。
最终全量资格以之后的 V3164/V3165 为准，不复用修改前的绿色报告。
V3164 已通过：5328 项及 2 个 subtest，无失败或 skip，完整保留
V3160 的 5312 个用例身份；相对本轮开始的 V3153 增加 44 项。
V3165 由实际 `qualify_sources` 生成并通过源码检查。旧固定 root 数
断言的语义更新已明确记录，不宣称其原有“拒绝 16”要求仍被保留。

## 后续验收

先完成新项目环境限制的处理，之后在预约结束和资源空闲时采集真实
源轨迹，保留全部模型自主决策；检查真正的多轮/嵌套 JOIN、旧 handle
复用、独占状态与有用状态淘汰。随后完成该源的 GPU replay smoke，
才对同轨迹运行 native、all、drop、LRU 和 selective。
另以相同源轨迹运行 JOIN-only 等简单基线，避免只与原生缓存或
保留策略的两个极端比较；不能把不同源轨迹的基线混入主对照。

相同实际池、batch、seed、token、请求、工具时长、停止条件和工作
完成口径下比较完整平均 JCT、正常长尾与吞吐。更高迁移量、较少
root 或更长输入本身不作为收益。当前仍未证明优于 native/最强简单
基线，未证明选择性 KV 管理的独立增量收益。

本轮不替换已冻结的 V134d 36-root 源采集队列。北京时间
2026-10-24 06:00 前继续禁止 GPU 实验；已有 guard 和 CPU watcher
仍须按真实进程核验。
