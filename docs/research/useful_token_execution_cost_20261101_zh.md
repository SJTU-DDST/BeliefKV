# 每个有效输出 token 的执行成本

2026-11-01。本轮改查模型执行与原生数据路径，保留原 workload、到达表、
测量成员和 retained Full。工作量 Oracle、工具预取、逐层 handoff 均未启用。

## 已完成轨迹的成本核算

输入是 original56 / initial12 / mean70 的已完成 Full：

- 注册：`ops/workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json`。
- 证据：`experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/full_accounting_r1.json`。
- 审计：`scripts/audit_useful_token_cost.py`。
- 所有 157,449 次 native forward 都保留，逐 request 校验输出 ordinal。
- 9,311 份已完成回执对应 1,221,808 个输出 token，全部核对一致。
- 固定 18 个测量 workflow 的 2,819 个请求、362,072 个输出完整保留。
- 另有一个被行政截断的背景请求已经观察到 64 个输出；保留这些输出，
  不将它作为完成请求，也不估计它的 JCT。

共同墙钟窗口为到达原点后的 `[55.692771, 1638.954850)` 秒，包含背景工作：

| 成本 | Worker 区间合计 | 占比 | 实际执行量 |
| --- | ---: | ---: | ---: |
| Decode | 1,129.310390 秒 | 71.4139% | 474,160 个输出 token |
| Prefill | 452.049107 秒 | 28.5861% | 4,404,031 个 extend 输入 token |
| 全部 | 1,581.359497 秒 | 100% | 477,974 个输出 token |

全部 worker 成本除以全部有效输出为 **3.308463 ms/token**。
Decode 单独除以 decode 输出为 **2.381707 ms/token**；
prefill 除以实际执行输入为 **0.102644 ms/input-token**。
输入 extend 可能包括必要新增输入和重算，当前统计没有把二者混同。

Decode 的 477,723 个 request step 中，3,563 个没有新输出，占 **0.74583%**。
这些终止/重叠尾部仍计入成本，不能直接归为可省 GPU 工作。
配置图档位共对应 560,957 行，较真实行多 **17.4231%**；
这只是图几何，不能当作 17.4% 可节省时间。

共同窗口按 worker 区间精确裁剪后的覆盖为 1,581.290645 秒，
其余约 1.971434 秒。Worker 包含 CPU 提交、GPU 工作、传输依赖与结果处理，
所以这个覆盖率不能替代真实 GPU busy，也不能用它断言主机开销很小。

## 先核验的底层候选

当前 GPU 为 H200 NVL，模型为 Qwen3-Coder-30B-A3B-Instruct，
48 层、BF16 权重和 KV、FA3、TP=1、running 上限 24。
旧配置 decode 捕获到 256，实际常见档位是
`1,2,4,8,12,16,24`；例如 5 行填至 8，9 行填至 12。

先用实际 batch 的 token 前缀验证精确图档位能否降低每个有效输出的成本。
输入来自共同窗口中已观察到的 3、5、7、8、9、13、17 行组合，
含各成员不同的真实上下文长度。正式源任务、原 trace 和停止条件没有重写。

原服务日志还记录了缺少
`E=128,N=768,device_name=NVIDIA_H200_NVL` 的 MoE 调优配置，
目前使用通用 Triton 配置。缺文件本身不是性能归因；
需要以 GPU profiler 中对应 kernel 的成本决定是否值得调优。

## 组件验证的失败与替代入口

`benchmark_decode_graph_cost.py` 原先尝试使用原生 standalone benchmark helper
构建分块前缀，再在同一模型实例内交替切换图档位。

- `component_r1.log`：模型与图捕获完成后，首个自定义前缀构建出现 CUDA 非法访存。
- `component_r2.log`：增加 chunk 间同步后，明确定位至首个 2,048-token chunk；
  同样没有进入 decode 对比，不能作为性能结果。
- 两次进程均已退出；原生引擎和 retained Full 未修改。
- CPU 准备曾因 JSON 中 identity 为 list 而未转 tuple 被严格源校验拒绝；
  已修复读取器，没有放宽捕获身份检查。
- 初次测试命令引用了不存在的旧文件名，未运行测试；
  更正后已有 36 项针对性检查通过。

为使 KV 分配、分块、stream 与对象生命周期由实际 scheduler 完成，
改用 `profile_native_token_cost.py`，通过 native `/generate` 和原生 profiler 验证。
两个模式都保持 Device KV token 容量、精度、attention backend 和请求输入一致；
组件不创建 Host KV，也不运行 Full 准入控制。

每组先真实计算完整输入前缀，再做三次 96 输出 token 的模型请求。
96 是本次数值/成本短测长度，不是正式 workload 的停止规则。
同一宽度的重复结果与 profiling 请求分开，profiling 开销不纳入普通时延对比。
宽度 8 在两个模式下使用相同图档位，可用于观察两次模型启动间的环境波动。
这组短测不是完整 workflow JCT，也不能归入 Predictive 独有的调度收益。

证据目录：`useful_token_cost_r1/native_component_r1/`。

## 精确图档位的原生 serving 结果

`graph_comparison_r1.json` 保存两臂完整审计与逐请求数值核对。
每臂 186 个普通测量请求、17,856 个输出 token，所有输出序列完全一致。
下表按各组实际输出数除 worker 区间，再取三次普通 burst 的中位数；
profiler 请求不参与时延统计：

| 实际宽度 | 稀疏图 ms/output-token | 精确图 ms/output-token | 成本降低 |
| --- | ---: | ---: | ---: |
| 3 | 3.582194 | 3.235128 | 9.6886% |
| 5 | 2.876205 | 2.584423 | 10.1447% |
| 7 | 2.147761 | 2.056355 | 4.2558% |
| 8，同档位对照 | 2.237144 | 2.241706 | -0.2039% |
| 9 | 2.477289 | 2.431910 | 1.8318% |
| 13 | 1.804835 | 1.768949 | 1.9883% |
| 17 | 1.805833 | 1.765433 | 2.2372% |

这些宽度覆盖旧共同窗口总 worker 区间的 35.5001%。
仅将已测形态的相对变化代回旧宽度统计、其余形态保持不变，
得到约 19.468937 秒、**1.23115% 总 worker 区间缩短**的筛查值。
这不是 JCT 预测或因果上界，也未覆盖所有上下文分布。
不据此启动完整 workload 的重复对比或更改 retained Full 默认值。

可复用的显式参数保存在
`configs/execution/qwen3_coder_h200_exact_decode_20261101.json`。
这是所有 baseline 都可使用的通用 engine 执行优化，
不能包装成 Predictive 独有的调度创新。

## Kernel 级归因

宽度 9 的 decode 捕获了 24 个步骤。稀疏图的 kernel duration 累计
575.262366 ms，而实际 kernel 区间并集为 525.337539 ms。
不能把相互重叠的 kernel duration 当作可相加的独占成本。

| 组 | 累计 duration | 累计 duration 占比 | 无其他 kernel 组重叠的区间 |
| --- | ---: | ---: | ---: |
| Attention，包括 combine | 303.007092 ms | 52.6729% | 302.619115 ms |
| MoE GEMM | 162.242872 ms | 28.2033% | 113.521426 ms |
| Activation | 43.074574 ms | 7.4878% | 1.295583 ms |

Activation 与 MoE 的重叠约 41.724 ms；激活函数累计看似较贵，
其独占窗口很小，当前不作为优先优化对象。
精确图下 attention 累计 302.987499 ms，基本未变；
MoE GEMM 累计降至 153.968056 ms，支持填充改善主要影响这部分的解释，
但这个单组 profiled 结果不替代上面的普通 burst 时延。

下一项有界验证因此选择 MoE launch 参数：
相同 BF16、128 experts、top-8、hidden 2048、intermediate 768，
仅筛选 8 个现成 Triton tile/warp/stage 组合。
先覆盖均匀和偏斜的合成 routing，并核验数值；超过 24 的输入规模
保持原生 default 配置，随后必须用上述真实模型 serving 输入验证。
合成 routing 的微测本身不能证明真实请求或 JCT 改善。

初次 MoE 微测在执行前因单 rank TP group 未初始化而退出；
已按原生 API 补齐初始化与清理，保留
`moe_tuning_r1.log`。它不是一次完成的 kernel 性能测量。
