# Root 并发、batch 与项目多样性的容量核算

日期：2026-10-23，北京时间。以下为 CPU 历史轨迹分析，未运行新 GPU 实验。

## 当前候选

独立项目场景优先评估 16 roots 同时到达、每个 root 一个不同项目，
先保留已使用的 running/decode 8，再按实际锁定工作集与 batch
分布判断是否提高到 12。新任务资产已导入，完整首请求和公共 sandbox
preflight 已检查；原始项目的测试/依赖问题仍未解决，尚无 GPU 性能，
不能称为最优参数。详见[候选准备证据](project_disjoint_workload_ready_20261023_zh.md)。

先前 24 roots 同时到达、running request 上限 24 的建议仅适用于
原来的九项目共享场景，decode batch 上限及所需 CUDA graph 覆盖
相应对齐。它是待验证起点，
不能称为收益最大或最优配置。实际 decode batch 由请求就绪程度、
原生准入与 KV 容量决定，不为了凑到 24 而额外等待。
这个建议需要同时说明项目多样性：当前 24 个任务仍来自九个项目，
不能将其当成 24 个独立长项目；若改为项目互不相同，容量与 running
上限需要重新核算，不能继承当前共享条件下的估计。

root 数量、同时执行的 LLM 请求数、graph 可覆盖的 batch 大小分别
核验，不能只增加 graph 大小而仍将 running 限制为 8。
本次只给出参数建议，未改 V134d 已冻结的 36-root/running8 源采集。
正式性能对照必须在相同任务/轨迹、容量、seed 与参数下重新配对。

## 最新完整轨迹的机会数量

使用 V133b 的完整同工作量重放。按既有
`configs/workloads/locobench_agent_long_36root_20261020.json`
的 seed21、项目轮转顺序取前 N 个任务，不按 JCT、是否加速或 JOIN
多少筛选。每个子集均覆盖九个原项目。

| roots | LLM 请求 | spawn | JOIN satisfied | 工具调用 | 实际工具耗时至少 1s |
| --- | ---: | ---: | ---: | ---: | ---: |
| 12 | 2,682 | 54 | 31 | 3,529 | 131 |
| 16 | 3,438 | 75 | 44 | 4,449 | 182 |
| 20 | 4,115 | 85 | 49 | 5,285 | 232 |
| 24 | 4,861 | 95 | 55 | 6,191 | 260 |
| 28 | 5,593 | 109 | 63 | 7,153 | 284 |
| 36 | 6,907 | 130 | 75 | 8,987 | 388 |

这是这些任务在已完成 36-root 运行中的事件归属统计，不是少并发
重跑后的时延、迁移量或动态模型决策预测。24-root 固定子集保留
约 73.3% 的 JOIN 和 68.9% 的工具调用；工具次数和实际长等待也
不等同于存在 Host-only 状态、可安全卸载空间或有效预取机会。

V134c 的可复用 subagent 新源被预约中断，仅有 5 个自然完成 workflow、
10 次 spawn、5 次 JOIN satisfied，无真实 resume。详细工具委派说明
修复后仍需 V134d 的完整源验证，不能把旧轨迹数量当成新 prompt 保证。

## 初始前缀的真实共享

不能用 root 数乘以长上下文大小证明真实物理压力。对 V132 源采集的
36 个 root 首请求，从 `request_tokens.jsonl` 解码并校验 token 哈希，
按 extra_key/cache_salt 分 namespace、排序后累计相邻精确公共前缀，
得到输入 radix 前缀并集。每个 FULL token 按实际 census 的 20,480 bytes
换算，实测 FULL 池为 1,797,968 tokens / 36.82240512GB。

| roots | 不共享时的初始 FULL，GB | 初始输入前缀并集 tokens | 初始并集 FULL，GB |
| --- | ---: | ---: | ---: |
| 12 | 33.821 | 1,225,064 | 25.089 |
| 16 | 44.681 | 1,227,598 | 25.141 |
| 20 | 57.504 | 1,230,445 | 25.200 |
| 24 | 67.913 | 1,233,320 | 25.258 |
| 36 | 104.181 | 1,241,482 | 25.426 |

这些是首请求完整输入前缀的精确并集代理，不是某一时刻的 HBM
分配或 Host 压力，未包含后续私有增长、child 历史、decode reserve、
不同 checkpoint/锁、休眠状态或全部历史分支。24-root 初始并集
比 36-root 仅少约 0.167GB。保留相同九个长项目，可以在减少队列
竞争时保留相近的公共长前缀工作集；是否仍有迁移压力由后续状态决定。

共享来源已沿真实 token 和 prompt 组装核实：

- 清单为九个项目，每项目四个不同场景；同项目使用相同初始源码快照。
- `_build_autonomous_agent` 将完整项目源码追加到 root 的 system
  message；具体 scenario 名称与任务要求在后面的 user message。
- `python_api_gateway_hard_009` 的 bug investigation 与 multi-session
  development 首请求分别为 132,919 / 133,393 tokens，精确公共前缀
  达 132,556 tokens，直到 user message 中的 scenario 名称才分歧。
- 同一 API 项目与 `python_web_cms_hard_074` 的示例仅共享 6,072 tokens，
  在 system message 的 repository identity 位置分歧。
- 两个示例的 extra_key/cache_salt 均为 null。逻辑 workflow/session
  身份并不自动隔离内容完全相同的前缀。

因此这里共享的是通用系统指令、工具说明与同项目完整源码，不只是
少量通用系统指令。独立容器和不同任务不会改变相同历史 token 的
前缀复用条件。旧清单按 Python 3.9 筛选得到九个合格长项目；
若研究独立项目场景，
需要增加真实项目多样性。同项目多任务场景可以单独保留并说明共享。

24-root 子集的请求输入 P50 为 46,140 tokens，均值约 70,846；
其中约 25.9% 请求达到 100K。完整 36-root 请求输入 P50 为 46,450，
均值约 74,327；170 个 agent context 的最大输入 P50 约 48,842。
不能把所有 child 当成 100K agent。若 24 个完全独立的执行前缀同时
驻留，FULL 池平均仅能容纳每个约 74,915 tokens，且尚未扣除增长预留。
实际 batch 内的共享可能降低需求，需实测可执行 batch 和锁定量。

## 不同项目的 CPU 重筛

检查 pinned Docker image
`sha256:3c5ba4d6b17bb7c5eed115435d274c0aa30c7b03bbf544c2aa2a554270274395`
发现当前镜像实际为 Python 3.10.16，CPU 容器也确认可解析 `match`
语句。旧清单的 `sandbox_python_feature_version` 仍是 3.9，因此
排除了三个仅因模式匹配语法被拒绝的长项目。此前的 Python 3.9
预检属于旧基础环境，不能代替当前 pinned 镜像的版本。

V3155 对相同 pinned 数据集的全部 100 个项目重新渲染、tokenize、
按 Python 3.10 解析，源码不作语义修改，不按运行结果选任务。
仅做 profile，未修改旧清单或已冻结的 V134d 采集。候选保持原上限
196,608 source tokens，将源码下限由 100,000 调为 95,000：

| 条件 | 独立项目数 | 源码 tokens 合计 | 源码 FULL 体积合计，GB |
| --- | ---: | ---: | ---: |
| 原 3.9 筛选，源码至少 100K | 9 | 1,190,841 | 24.388 |
| 3.10 重筛，源码至少 100K | 12 | 1,600,496 | 32.778 |
| 3.10 重筛，源码至少 95K | 16 | 1,993,714 | 40.831 |

最后一组为 16 个不同项目、每项目一个不同原始任务。12 个源码本身
超过 100K，其余四个为 95,792--99,370 tokens。随后 V3156 已通过真实
agent 构建路径核验完整 root 首请求，16 项均超过 100K，范围为
103,260--201,727 tokens，精确前缀并集对应 41.884GB FULL 状态。
这些仍不是瞬时 HBM 占用或迁移量。重筛通过仅表示源码语法、长度
与原始场景可用，不代表依赖、
原始测试或任务正确性已通过。

这里 40.831GB 是各项目完整源码的 FULL 体积合计，不是实时 HBM
占用，也不是精确首请求 radix 并集。它尚未计入完整 prompt、历史
增长和 child 状态；与旧 36 roots 的 25.426GB 精确首请求并集口径
不同，不能直接当作实测容量增幅。独立项目消除了同项目 100K 级
源码的重复，仍保留真实通用系统前缀的自然共享。

16 项中最大八份源码合计约 24.008GB，最大十二份约 32.778GB；
实际在跑状态还包括指令、历史、输出预留、child 和其他池约束。
因此用 16 roots/running8/decode8 开始比直接沿用 running24 更有
容量余地；这是容量上的候选判断，不是预言更高吞吐或迁移收益。
如果坚持源码本身全部至少 100K，则先采用 12 项，而不是凑出
16 个名义上不同、实际重复项目的 workflow。

结果保存在
`experiments/reports/v3155_project_disjoint_py310_profile_20261023.json`，
SHA256 为
`6da7df50d8c67676d51ffe500391ba4f4b12b412f965c08a1ac170b4eedcb96d`。
后续继续处理 sandbox 依赖和原始项目限制，再完成冻结资产检查并选择
参数相同的 native/custom 比较；旧共享轨迹作为单独场景保留。

## 共享场景的验证与调整

对原九项目共享场景，24 roots 优先于直接减到 16 的原因是保留更多
child 私有状态、JOIN
和工具等待机会；不能据此预先认定系统会加速。batch24 若提高计算
效率，迁移可能更显著；更多在跑请求也会锁住更多状态，可能减少
parent 恢复可用的空间。两种效应均需观测。

重点记录实际 batch 分布、忙时吞吐、完整 workflow JCT、原生回撤、
有用状态驱逐/恢复、parent 容量受限比例和暴露的恢复等待。
若 batch24 引起持续回撤或难以为 parent 恢复腾出空间，先保持
24 roots、单独将 running/decode 降至 16。若有用迁移几乎消失，
先检查共享后的活跃/休眠独占工作集，再判断是否回到 28 roots。
低 JCT、更多 H2D 字节或更多 JOIN 单独均不足以证明 KV 管理收益。

原始证据位于：

- `experiments/raw/qwen35_locobench_long_36root_running8_reviewed_position_20261023_v133b/native_matched/replay/runtime_events.replay.jsonl`
- 同组 `server/runtime_events.sglang.jsonl` 与 `server/native_capacity_census.json`
- V132 `native_capture/server/request_tokens.jsonl`，原位置由
  `experiments/reports/v3143_v133b_causal_jct_paths_20261023.json`
  的 `source_evidence.source_arm` 标明
- `experiments/reports/v3144_v134c_reservation_cutoff_partial_20261023.json`

北京时间 2026-10-24 06:00 前继续仅做 CPU 工作。GPU 性能目标仍未验收。
