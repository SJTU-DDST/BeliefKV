# 输入 checkpoint 恢复与滚动剩余工作对照

## v4 证据与边界

五次预测 H2D 的目标与 FULL session leaf 相同：
pylint-4551 为 node 131，pylint-4661 为 135，xarray-7393 为 108，
xarray-7233 为 91，xarray-6721 为 137。其 548/192/128/805/192 个
FULL token 是叶节点的尾段，不是整条前缀长度。因此撤回
“选中了浅层公共节点”的初始猜测。

可核验的四次 parent epoch+1 服务只命中 0/0/4096/0 个 device
token，均未复用目标节点。v4 没有保存 FULL 原始匹配长度、
Mamba 分支点或 ACK 前缀与下一输入的交集，不能仅凭旧 trace
断言五次失配均由同一种原因触发。

原生源码与 CPU 复现确认了一种明确机制：即使 FULL 有匹配，
若相同 token 边界上没有可用的 Mamba checkpoint，Hybrid 的
可复用前缀仍会被截短，随后通过 prefill 重建分支状态。
Qwen3.5 chat template 会重序列化历史 reasoning/tool call，
生成后的尾叶不能直接作为下一请求的稳定恢复目标。

## 代码修复

运行时现在独立于旧多头 worker 记录原生请求的输入长度。
PREFETCH_GPU 在已知输入长度时，选择该长度内最深的已有
Mamba checkpoint，仅恢复到它为止的必要 FULL 祖先；较浅的
Mamba 副本与超出该边界的输出尾段不再成为恢复目标。
PREPARE_HOST 和原生物理容量、session/generation、ACK 校验不放宽。

Tagged native session 的 Mamba 引用也优先绑定已有输入 checkpoint，
而不是生成后的输出尾叶。不存在可用输入状态时保留原生回退，
不生成新 checkpoint，不固定额外 GPU 槽位。该规则同时作用于
reactive 与 predictive；比较两侧必须使用同一新版 staging patch。
保留更可复用的状态可能改变驱逐顺序，实际压力与收益需实测。

新增遥测：

- `prefetch_native_issued` 记录命令、目标/叶节点与输入恢复上界。
- 原生请求记录 `native_full_kv_hit_length`、Mamba branching seqlen。
- 首次服务保存 ACK 时冻结的目标前缀与新输入的公共长度、
  完整前缀匹配和 cache namespace 是否一致，不落盘原始 token。
- `no_reusable_input_restore_step` 区分无输入恢复步骤与原有尾叶机会。

这些字段用于区分前缀变化、Hybrid 边界损失和节点失效；
不是 ACK 即收益，也不将 Mamba 复用未知计为浪费。

## 预测覆盖修正

旧导出仅取正文 0/128/512/1024 字符的快照，在线却持续滚动
预测。新增 `rolling_250ms` 导出，跳过正文不足 32 字符的观察，
按已到达的时间戳选取快照，语义输入截为最后 1024 字符，与
在线口径一致。追加未来快照不改变已经选出的历史快照。
输入仍不含未来 RETURN、最终长度或未来无服务等待。

冻结原有训练项目与 Astropy 校准、Sphinx 评估划分，复用仅在
训练项目适配过的 encoder。训练增加 v4 predictive 的完整自然
轨迹；含连接错误的 v4 reactive 不加入。本轮收集 9006 个快照，
只重拟合头和校准，不重新适配 encoder。

产物：
`experiments/models/child_semantic_work_stage2_rolling_20261001_v1/`。
同快照对照是 `same_snapshot_comparison.json`，逐快照值见同名 CSV。

| Sphinx 同快照指标 | 原模型 | 滚动重拟合 |
| --- | ---: | ---: |
| 自然 RETURN 请求数 | 35 | 35 |
| 最后快照剩余 token 中位绝对误差 | 158.23 | 115.35 |
| 最后快照中位有符号误差 | +158.23 | +115.35 |
| P90 绝对误差 | 249.25 | 239.34 |
| 首次越线自然终态 | 34 | 34 |
| 首次越线工具/继续轮误报 | 8 | 14 |
| 首次越线 precision | 80.95% | 70.83% |

末段中位绝对误差下降约 27%，但高估仍明显，且阶段误报变多。
不将这个候选直接替换线上模型，不声称达到亚秒 RETURN 精度。
这是重复使用开发项目的探索性对照，不是新密封测试。
后续应在保持阶段头稳定的条件下，单独比较剩余工作建模形式，
同时检验提前量与误报，不以末段 MAE 单独决定上线。

## GPU 验证

下一轮仍为完整 36-root 对照，不扩大 workload，不用短 gate/canary。
冻结原预测头，保持两侧任务、seed、running=48、同 NUMA 200 GB Host、
HBM Mamba/FULL=0.9、通知与收尾优先级一致，PREPARE_HOST 均关闭。
本轮首先验证输入 checkpoint 规则、原生传输、真实首次复用，
以及新增诊断能否解释剩余失配；不提前填写吞吐收益。

## v5 完整结果

目录为 `experiments/raw/qwen35_input_checkpoint_h2d_ab_36root_20261001_v1/`，
两侧冻结提交均为 `6a330e9`。两侧 36/36 completed，连接错误、
guard 干预、遥测丢失和账本失效均为零。未做独立任务正确性 grading。

| 指标 | Reactive | Predictive |
| --- | ---: | ---: |
| 整轮时间，秒 | 1963.08 | 2028.74 |
| Completed workflow/hour | 66.02 | 63.88 |
| 36 个配对任务平均 JCT，秒 | 755.07 | 857.55 |
| 模型调用 | 5211 | 6466 |
| 工具调用 | 5062 | 6360 |
| 输出 token/s | 498.95 | 627.08 |
| GPU 利用率采样均值，% | 65.73 | 91.07 |
| 综合缓存命中，% | 95.44 | 95.40 |
| 预测 H2D ACK | 0 | 0 |

Predictive completed throughput 低 3.24%，没有预测传输，不能据此
宣称 H2D 收益。两侧生成轨迹与服务工作量明显不同，较高 GPU
利用率也不是预测成功的证据。

v4 的 pylint-4551/4661、xarray-7393 首次 parent 服务原来命中
0/0/4096 token；v5 predictive 命中 8576/8512/9088 token。
仍有剩余损失：xarray-6721 的原始 FULL 匹配为 11327，但 Hybrid
device hit 只有 4096，Mamba branching point 为 11264。
36 个首次 parent continuation 中 reactive/predictive 各有 4/1 次
零 device hit，中位 device hit 为 8156/8155。缓存复用已有改善，
但旧版缺少原始 FULL 匹配字段，不能把所有差异都归因于同一机制。

原生 reactive H2D 两侧各为 17/20 个有 Host 命中的请求，Host FULL
hit 仅 10083/26654 token；Host Mamba hit 为 17/20 个 checkpoint。
Native D2H 仍发生，不是关闭了传输。但多数被保留的 parent 输入
checkpoint 已在 GPU，或没有可恢复的输入副本，H2D 机会较少。
这一轮不能作为有足够预取空间的正式工作负载资格证明。

Reactive 的 Host FULL/Mamba 高水位为 76.09/71.73 GB，均无驱逐；
predictive 为 103.63/94.65 GB，发生 2.18/16.48 GB 驱逐。差异伴随
明显不同的模型/工具工作量，不能单独解释为预测开关改变了压力。
两侧均已清理 36 个正常完成的 workspace，保留所有 trace 和 patch，
服务器停止，GPU 已释放。

## 实验后输入末端修正

本地 tokenizer 复现发现原生成提示最后一个 token 为单换行
（198），历史 assistant 重序列化后对应位置为双换行（271）；
示例初始输入 24 token，公共前缀只有 23。v5 中多条 parent 的
原始 FULL match 也等于旧输入长度减一。

因此在 v5 完成后，将 H2D 输入上界与 Mamba session 引用上界都改为
`max(0, input_tokens - 1)`，不再引用恰好覆盖整个输入末端的状态。
此补丁未进入 v5，两侧实验身份不回填。若输入末端正好与 checkpoint
网格对齐，而较早状态已不存在，保守跳过仍可能需要重算；当前不
额外生成 checkpoint，后续需验证原生 prefill 的安全边界追踪。

下一阶段不重复扩大相同配置：先补齐安全输入 checkpoint 的存在性
和 Host 副本机会，再在保持阶段识别稳定的前提下改善剩余工作头。
滚动候选只降低约 27% 的末段 token MAE、却增加误报，因此保留为
离线对照，线上冻结模型尚未替换，亚秒级 RETURN 预测仍未达标。
