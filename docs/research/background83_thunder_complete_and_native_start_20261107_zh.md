# 83 项目：ThunderAgent 完整结果与 SGLang 启动

2026-11-07，America/Los_Angeles。保持用户最后确认的 83 项目配置，
复用已完成的非排序 Full、Oracle 和 learned R5，只接续两组基线。
本文记录完整 ThunderAgent 结果和实际 SGLang 启动，不是五方法
全部完成、正式稳态或重复性验收。

## 相同工作与资源

- 83 个不同原项目的完整原始轨迹，20 个初始 workflow，平均 70 秒
  泊松到达；相同固定 18 条测量成员，完整自然尾部。
- Qwen3-Coder-30B-A3B-Instruct、BF16、FULL-only；Device 805198
  token / 79154282496 字节，Host 4069011 token / 400000057344 字节。
- serving `631911fb89b3`，native tree `434c35dd6621`；FA3、
  running/decode24、chunk2048、精确 graph1..24、mixed off。
- 相同原始 token、依赖和工具时间 overlay。共同吞吐窗口为
  55.6927713505--1638.9548502657 秒，包含所有背景输出。
- 各组固定测量成员全部结束后停止背景。NUMA 放置保留诊断，
  不要求跨节点均匀分布或页面计数相等。
- ThunderAgent 保留 invocation program、5 秒控制和 1 秒
  acting-decay 半衰期，不继承 Full 的预测卸载、JOIN/EOS 预取、
  prepare_host 或剩余工作排序。

此前被用户临时切换要求中断的 ThunderAgent 尝试仍保留为行政中断。
本次是新的完整基线；没有把旧部分计时接回，也没有重跑 Full、
Oracle、learned。

## 完整结果

四组固定测量工作一致：2991 个请求、362157 个输出 token，
输入输出身份/哈希和原生停止路径守恒，保留全部正常测量尾部。

| 方法 | 平均 JCT，秒 | P95 JCT，秒 | 平均 JCT 相对 ThunderAgent 降幅 | 共同窗口输出 token/s |
| --- | ---: | ---: | ---: | ---: |
| ThunderAgent acting-decay | 1125.102412 | 3032.987108 | 0 | 328.158558 |
| 非排序 Full | 1061.121917 | 2775.338936 | 5.686638% | 346.998142 |
| Oracle | 1036.098550 | 2720.397967 | 7.910734% | 346.064627 |
| learned R5 | 1049.209646 | 2704.624676 | 6.745410% | 347.541956 |

R5 相比 ThunderAgent，15 条测量 workflow 改善、3 条变差；平均
减少 75.892766 秒，P95 减少 328.362432 秒，共同窗口输出吞吐
提高 5.906717%。三条变差成员为 dashboard、CMS 和 desktop-media；
其中 CMS 回退 192.712370 秒，仍是后续服务顺序检查的重点。

排序自身的比较使用非排序 Full：Oracle 平均 JCT 改善 2.358199%，
R5 改善 1.122611%。整体对 ThunderAgent 的改进不能全部归因于
剩余工作预测或排序，离线 CPU 候选也没有用于这些 GPU 结果。

ThunderAgent 最后测量完成偏移为 4299.885264 秒，背景计划最后
到达为 4598.502355 秒，未覆盖测量尾部为 0 秒。实际到达 79 个，
自然结束 66 个；17 个非测量成员右截尾，其中 4 个未提交。
未用这些条件完成子集替代固定测量组的平均 JCT。

`baseline_restart_20261107_r1/thunderagent_comparison.json` 验证
完整测量工作、两份新鲜空闲快照及实际 owned-process 退出，三个
固定测量性能检查均通过。报告的 `formal_workload_accepted`、
`performance_accepted` 保留为 false：此审计不自动授予正式稳态、
统计显著性或重复性结论。paper 臂也不冒用 Full 专属的
ready-paused/周期授予诊断。

四方法图位于本 campaign 的
`baseline_restart_20261107_r1/four_method_completed_figures/`：
`complete_JCT_and_decode.png`、PDF、CSV 和来源清单均已生成，
PNG 已实际查看。JCT 使用完整固定测量组；decode 趋势与 CDF
使用相同墙钟窗口，CDF 按真实 bin 时长加权，沿用 Okabe-Ito 配色，
没有从单轮构造置信区间。

## 完整服务指标

复用 `audit_measured_request_interactivity.py`；四组均为相同
2991 个完整请求和 18/18 workflow 流式首输出覆盖。
请求 E2E normalized interactivity 为完整请求秒数/token 的 P90
取倒数，包含首次服务等待；workflow TTFT 是从原始到达到任一
descendant 的首个实际流式输出。

| 指标 | ThunderAgent | 非排序 Full | Oracle | learned R5 |
| --- | ---: | ---: | ---: | ---: |
| pooled 请求 E2E normalized interactivity，token/s | 9.833988 | 14.413483 | 14.185364 | 14.899629 |
| 每 workflow 独立计算后等权平均，token/s | 10.906149 | 15.633241 | 15.692796 | 16.157328 |
| workflow 平均 TTFT，秒 | 87.344796 | 74.609520 | 58.784412 | 73.484559 |
| workflow TTFT P90，秒 | 162.206561 | 158.398874 | 91.039642 | 169.310529 |

R5 的平均 TTFT 和请求交互性优于 ThunderAgent，TTFT P90 则更差。
完整平均 JCT 仍是主验收指标；各服务指标独立报告，不能从一种
改善推导所有尾部或所有 workflow 都改善。没有新增 50 token/s SLO。

## 实际 SGLang 回放

ThunderAgent supervisor/server/scheduler/driver/固定测量观察器
均已实际退出后，原 coordinator 自动启动相同 workload 的
`workflow_original83_host400_mean70_native_20261107_r1`，端口 19023。
没有重复资格试验或另启工作负载。

04:17 的 `baseline_restart_20261107_r1/live_native_verified.json`
独立核对：

- 实际唯一 GPU scheduler 为 392898，server 392813、driver
  393784、supervisor 392354；GPU owner 均为 nice0。
- 固定测量观察器 393795 为 nice19；coordinator 370129 存活。
- 实际模型、FA3、MoE 配置文件及 graph 与共享执行声明一致，
  Device/Host 容量精确匹配上述四组。
- `enable_beliefkv_admission=false`，实际无自定义 runtime；
  当前臂使用 native 调度和 KV 路径。
- 首批 15 个已完成请求、636 个输出 token 的源包哈希、身份、
  position sampler 和 native sampler 保留标记匹配。

现场读取器为
`ops/verify_shared_native_execution_live_20261107.py`；它按 native
合同验证实际引擎和原始回放，不要求 Full 的准入快照。证明绑定
源码、注册、独立封闭前缀和实际进程，不是完整 native JCT 或
未经修改的上游 SGLang 证明。

04:20 状态为实际回放、23 个已到达、0/18 测量成员完成，error
为空。CPU 服务指标接续 386508 已完成 ThunderAgent 审计，继续
等待 native 完整对照，随后自动汇总五方法服务指标；原 coordinator
继续生成完整 native 对照及五方法 JCT/decode 图。
SGLang 完整结果和约 1% 排序收益的重复性验证仍未完成，目标保持 active。

## R5 与 Oracle 收益差距

本节复核已有完整运行及封闭日志，不新增 GPU 实验。两者均相对同配置
非排序 Full 计算平均 JCT 改善：Oracle 为 2.358199%，R5 为
1.122611%，差 1.235588 个百分点，等价于 R5 平均 JCT 多
13.111096 秒。不能直接将这个差值全部归为预测误差。

### 选择目标仍有差异

Oracle 直接按含模型和工具的剩余关键路径秒数排序；R5 按剩余模型
总工作量的条件分布生成权重，再选择 JOIN 覆盖收益之和较大的集合。
当前实际执行使用 capacity-v7 的 `benefit` 目标，没有将收益除以
请求成本。其在线权重仍为三档概率乘以 `1, 0.5, 0.25` 后求和；
连续工作量读出器尚未用于本轮 serving。

在相同 Oracle 历史状态中，仅将含工具路径改为真实总模型工作量，
会改变 19 个集合，其中 5 个涉及测量成员。这个 CPU 对照证明目标
差异确实可能改变选择，不能据此估计其反事实 JCT。

### 主要实测损失表现为服务等待

`completed_service_analysis/complete_cohort_jct_paths.json` 复核三组
相同的 2991 请求、362157 输出和全部 18 条完整 JCT。实际最后完成
依赖链的平均差值为：

| R5 减 Oracle | 平均秒数 |
| --- | ---: |
| 请求提交至首次 native launch | +17.249654 |
| 首次 launch 至 native complete | -4.080137 |
| 其他事件及 dispatch | -0.058420 |
| 完整 JCT | +13.111096 |

launch 至 complete 包含交错和暂停，不是纯 GPU 时间。这里是实际
路径分解，不是控制开销或某项模块的因果归因。

CMS 的完整 JCT 多 182.568793 秒，首次 native launch 晚
178.716244 秒；desktop-media 分别多 73.992911 和 79.852145 秒。
其他成员有正有负，不能从这两条直接推导可消除的系统平均 JCT。

`completed_service_analysis/cms_combination_scores.json` 保留了
7 个包含 CMS 的完整搜索容量竞争边界。其中 6 次仍在等待首个
请求，CMS 的逻辑需求为 211505 token，真实剩余模型工作为
8664.99。示例边界的逻辑余量为 216267 token：

| 候选集合 | 真实剩余模型工作 | 当前收益 |
| --- | ---: | ---: |
| CMS 单成员 | 8664.99 | 0.507176 |
| 两个其他 workflow | 32352.86、28421.00 | 0.306349 + 0.346011 = 0.652359 |

CMS 单独具有规划可行性，但两个较长 workflow 的累计收益更大。
这 7 个边界替换为连续模型工作真值后，所选集合均不变。因此这个
具体问题不只来自预测误差或档内分辨率，也来自集合目标。规划可行
仍不等于立即获得原生服务；这些边界没有覆盖 CMS 的全部准入路径。

### 预测与缓存延迟的证据边界

同候选、同资源、同组合目标的完整搜索 CPU 对照为：

| 权重来源 | 124 个竞争边界的成对排序正确率 | 502 次完整搜索改变集合数 |
| --- | ---: | ---: |
| 实际缓存预测 | 83.440860% | 0 |
| 同模型的即时预测代理 | 84.865591% | 4 |
| 真实连续模型工作量 | 100% | 15 |

即时代理使用已发布观测重建输入，不等于精确 runtime 接收状态，
也没有模拟 worker 队列。连续真值 100% 由排序标签定义，不是模型
准确率。候选人口包含开发项目，不能称为项目隔离测试。
当前证据不支持为消除缓存滞后新增同步推理；连续数值和集合选择
仍值得改进，但 CPU 集合变化不是 JCT 收益。

已提交的 `workflow_shortest_frontier.py` 仅是独立 CPU 候选：
先保留一个预测最短且规划可行的 workflow，保留必选 JOIN，
同组 ready sibling 整体放得下时共同保留，再用原组合搜索补满。
同 502 个状态中，124 个竞争边界包含真实最短 workflow 的次数
从 96 增至 106，改变 16 个集合，8 个涉及测量成员；其中 7 个
减少 agent 数量，存在并行度损失风险。候选没有接入 serving，
没有 GPU 或完整 JCT 结果，后续不能将其效果混入本轮五方法比较。

证据入口为同 campaign 的
`completed_service_analysis/learned_prediction_weights.json`、
`oracle_rank_objectives.json`、`shortest_frontier_candidate_cpu.json`。
详细原始分析见 `background83_gpu_start_20261106_zh.md` 的对应章节。
当前先完成已运行的 SGLang 对照，复用全部已完成方法，不自动扩展
GPU 队列或重新运行排序三组。
