# 长上下文交互负载候选

更新：2026-10-19。当前仅完成 CPU 输入与语法检查，未启动该候选的 GPU 实验。

## 选择与边界

LoCoBench-Agent 提供交互式软件工程任务，官方实现支持模型根据当前状态选择
文件操作、搜索、编译及调试等工具，并使用多阶段会话。官方最多 50 轮是上限，
不能当作实际工具调用次数。任务和阶段的定义固定，执行时的工具及参数可动态变化。
论文报告不同模型平均约 10–22 轮对话；这不是逐任务工具次数，也不能外推为
我们适配后的交互密度。
任务本身不保证我们的多轮、嵌套 spawn/JOIN，也不保证每个 agent 长时间使用
100K 以上上下文。

官方当前代码默认 `initial_context_mode=minimal`，仅放入部分入口和说明文件；
因此“项目包含 100K 源码”不代表普通 agent 输入已经达到 100K。本候选明确
使用完整项目输入：root、child、嵌套 child 的 system context 都携带冻结源码。
这是完整材料输入方式，不能描述为工具历史自然增长到 100K。材料可见后可能
减少读取工具，所以实际交互密度仍是待验证项。

这些项目来自生成数据，不能描述为真实 GitHub issue 修复任务。原 SWE-bench
负载保留作普通长度对照。LongBench v2 的长文多选题虽方便可靠评分，但没有
自带的工具执行工作流，本次没有将其替换为工具密集主场景。

## 已准备输入

官方 archive SHA-256：
`53299531d0e6fe51166b5d2e12d9d9aad462ce4eb46ae00a3808bfa0b95e63a1`。
源码版本固定 `2ab9218e6d46e50ebdbb139442503e5573bc4a5d`。

在 100 个 Python 项目中，用本地 Qwen tokenizer 统计完整输入，筛选
110000–170000 token，并检查实际 Python 3.9 沙箱支持的语法。最终 6 个项目
提供 12 个不同原始任务，每项目最多两个，种子 21；按项目顺序轮转选择，
不根据运行成绩筛选。类别为 bug 调查 3、功能实现 3、跨文件重构 2、
集成测试 2、多阶段开发 2。

完整材料为 110395–149832 token，按任务等权平均 130780.67。只删除生成
文件的一层外部 Markdown 围栏、合并重复路径；不补文本、不截断源码、不修复
项目语义。每个文件的原始/落盘 digest 均保存。参考答案、预期解法及评分标准
单独保存在冻结数据目录，不写入 prompt 或工作区。

V1887 的初次检查发现一个项目使用 Python 3.10 `match`，不能在现有 3.9
镜像解析；原失败和 V1888 原清单保留。导入器按沙箱版本筛选后，V1890
确认六个项目都可在真实 CPU Docker 沙箱中解析，输入和 Git 对象完整。
这不等于项目功能、依赖或测试可执行性已通过：基础镜像仍缺部分项目使用的
FastAPI、Pydantic、SQLAlchemy、PyYAML、Celery、NumPy、aiohttp 等库。
补齐项目环境前不运行正式性能实验；工具交互密度、任务正确性仍未知。

V1889：295 项 CPU 检查通过，1 项现有真实工具进度测试因未显式指定镜像
而跳过。另行执行的六项目沙箱解析检查不代替该测试或任务评测。

## 并发候选

入口：`scripts/run_qwen35_long_repository_ab.sh`；
清单：`configs/workloads/locobench_agent_long_12root_20261019.json`；
配置：`configs/migration/qwen35_locobench_long_12root_running8_2026-10-19.json`。

预设 12 个 root，8+4 两批，间隔 900 秒；已准入请求上限 8。当前引擎没有
独立 decode 分批控制，因此 decode 上界同时为 8，这不是两个参数已各自调优。
上下文 256K、输出 16384、工具 180s、workflow 14400s、Host 200GB/80:20，
EOS JOIN、工具计时与 FULL PREPARE 保留；需求 handoff 关闭。
两侧按相同配置重新比较，不能把旧任务集 native 当配对结果。

以既有 FULL 每 token 20480 byte 和约 36.822GB 池估算，单份独立源码 FULL
约 2.261–3.069GB，8 份最大源码约 24.548GB，尚未计入历史增长和共享。
等待 parent 与其他 child 仍可形成超过 HBM 的恢复需求；不能从此估算直接
认定实际发生迁移或关键路径阻塞。Host-only 丢失和重算必须同时观测。
去长尾阈值预设为 4 个活跃 workflow、预热 600s，已接入冻结、resume 和比较，
不沿用小负载无法满足的旧 48 阈值。

## 验收

验收分别检查实际普通 agent 输入长度、工具次数和耗时、模型轮次、多轮及
嵌套 JOIN、正 Host-only FULL 需求、实际迁移、请求恢复等待、有用副本损失、
共同去长尾 JCT 与吞吐。失败、未完成和尾部保留。输入够长但工具交互很少时，
该候选只作为长上下文压力对照，不称为已验证的工具密集主负载。任何筛选标准
都不变成拒绝模型自然结束的运行时门禁。

一手资料：官方 `README.md`、`locobench/core/agent_session.py`、
`locobench/generation/scenario_converter.py`，位于
`https://github.com/SalesforceAIResearch/LoCoBench-Agent`；
论文 `https://arxiv.org/html/2511.13998v1`；
LongBench v2 原始数据说明
`https://huggingface.co/datasets/zai-org/LongBench-v2`。
