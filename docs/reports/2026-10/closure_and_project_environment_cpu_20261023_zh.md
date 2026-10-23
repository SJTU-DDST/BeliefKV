# 节点快照控制成本与项目环境修复

日期：2026-10-23，北京时间。以下均为 CPU 工作，未运行新的 GPU 实验。

## PREPARE 控制成本

完整 PREPARE 路径的 CPU profile 中，主要时间仍在节点路径遍历及
快照构建。将 `observe_unified_node_closure` 每个节点的 FULL 长度和
key 长度边界检查内联，减少重复辅助函数调用；保持节点身份、池归属、
容量边界、锁、session、pending 和实时读取。使用 shape 元数据，
不读取 tensor 内容，不跨动作缓存快照。

V3168 对照提交 `65f1a9a`，101 次交替测量，24 个合成 workflow、
running8，覆盖路径深度 16/64/256、已有备份、控制事件和原生拒绝。
12 组的选择、发布、原生读取次数、计数器和 pending 状态完全一致。
完整路径线程 CPU P50 下降约 1.15%--4.00%。

| 路径 | 原 P50，微秒 | 当前 P50，微秒 | 降幅 |
| --- | ---: | ---: | ---: |
| 深度 16，待备份 | 1389.829 | 1373.862 | 1.15% |
| 深度 64，待备份 | 3243.583 | 3152.685 | 2.80% |
| 深度 256，待备份 | 10399.382 | 10115.918 | 2.73% |
| 深度 256，原生拒绝 | 19047.786 | 18458.586 | 3.09% |
| 深度 256，已有备份 | 9464.970 | 9085.971 | 4.00% |

这些是 CPU stand-in 路径，未包含真实 DMA、分配器争用、writer I/O、
GPU 执行或 JCT 收益。未将百分比换算为端到端加速。
新增测试覆盖 shape-only 读取、零长度、布尔/负数/浮点及超容量长度。

## 原始项目的环境

原始依赖声明与源码 API 不总一致。几个项目虽然在 requirements 中
声明 Pydantic2，源码仍直接导入 Pydantic1 的 `BaseSettings`。
新增镜像保留 Pydantic1，补充 Pandas、scikit-learn、PyArrow、
PySide6、Prefect、MLflow 和 Plotly 等实际缺失依赖。Web social
项目另外使用 SQLAlchemy2/sqlmodel 兼容镜像，其余 15 项保持
SQLAlchemy1.4。原始源码、参考任务和既有基础镜像均保留。

V3166 首次构建在 `pip check` 通过后，因过旧 Pendulum 的 API 不兼容
而在真实 Prefect 导入时失败。固定 Pendulum/Griffe/Typer/Click 后，
V3167 又在 Qt 实际导入时发现缺少系统库。通过 ldd 确认并补齐
fontconfig/freetype；使用离屏、软件 OpenGL 检查 QApplication。
失败日志保留，最终镜像经过 `pip check` 和实际 API 导入，而非 stub。

最终固定的镜像为：

- 扩展环境：`sha256:d622a4f6cc52a890e18ec07bc2d5bf90dde43c3a379a534c843d8a318be81bcd`
- SQLAlchemy2 环境：`sha256:bf8c1a179ea6a97b26c5eff0328a3250e3130e0c95d096a2bf7b5c8363e9469d`

环境检查入口先解析实际 image ID，再运行容器；显式 override 和
可选 `--pin-manifest` 产生派生清单，不覆盖原任务。派生清单绑定
完整预检报告、原清单、Git commit 和镜像 ID。源采集注册前检查
报告/原清单哈希及镜像是否存在，这些均在实验边界执行。

## 实际核验与限制

V3177 在最终镜像上完成 16 个项目的公共依赖预检，全部通过。
原始测试探针有 5 项成功/收集成功，3 项没有测试，另 8 项仍有
断言、缺失项目模块、Django 设置、测试模块命名冲突或不正确的 skip
等原始问题。测试收集成功不证明生产代码或任务正确性。未用修改
源码、跳过失败模块或改变任务来获得绿色结果；任务质量保持 ungraded。

新清单为
`configs/workloads/locobench_agent_long_16project_env_20261023.json`，
SHA256 为
`44d685f8e91740ed38aa88c550f0cc36f078e2be9f2c7050e5c26d30862ba06c`。
除固定镜像及环境证据外，16 个任务与源码字段逐项相同。
V3178 使用真实 agent 构建路径的 CPU fake model 再次核验：
所有首请求 token 哈希与 V3156 相同，仍为 103260--201727 tokens，
前缀并集对应 41.884GB FULL。源注册的 35 个外部输入验证通过。

V3179 保留 V3164 的全部 5328 个用例身份，5355 项及 2 个 subtest
通过，没有失败或 skip。新增 27 项覆盖标量长度检查、镜像 override、
实际 image ID 执行、派生清单和环境证据变更拒绝。
所有 CPU 测试均以 `CUDA_VISIBLE_DEVICES=99999` 运行。
V3180 由实际 `qualify_sources` 生成并通过 `qualify_report` 核验。
引擎复用原已验证工作区的 V2888/V2890：657 个用例、canonical tree
`cc9772c6c5c4dcd3678a03b67fa9afc190aec4f8` 匹配；未修改引擎源码。

## 下一轮验证

完成准备后的源采集为 V135，使用 16 个独立项目、同时到达、
running/decode8、seed21、256K/95% 压缩和相同实际 FULL/Mamba/Host 池。
模型自然决定委派、轮次、嵌套和旧 handle 复用；先用 native、retention
off 采集，后做该源的 GPU replay smoke 和同工作量对照。
正常长尾保留，独立任务正确性尚未验证。

新的冻结队列确认存活后，旧 V134d CPU watcher 已按身份核验后停止。
用户随后明确授权立即使用 GPU；北京时间 2026-10-23 23:20:30 解除
本次旧预约，23:20:37 启动 V135。GPU 源采集及后续证据另记于
[V135 运行记录](../../experiments/v135_project_disjoint_gpu_pilot_20261023_zh.md)，
不计入本文的 CPU 证据。性能目标继续 active，CPU 成本、
环境修复和更大 KV 工作集均不构成已经击败 native/简单基线的证据。
V135 随后于 23:58:30 中止：15/16 自然完成、无真实复用、剩余 workflow
连续无效编辑。保留截止证据，不进入后续保留矩阵或报告完整 JCT。
