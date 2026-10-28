# 需求恢复流水线的真实模型与数值诊断

2026-10-28。正常原生 H2D 期间的实际 resident model decode 推进已观察到，
但真实模型资格尚未通过，不能进入正式 FULL 或宣布 JCT 收益。独立
pipeline-off 诊断也违反原有数值门槛，因此当前分歧不能直接归因为新
流水线损坏 KV；这也不能排除它另有缺陷。

## 正常传输中的模型推进

冻结应用 `55e54416`、clean native `d3da19c1` / tree `729aa0caa7a1`。
模型为 Qwen3-Coder-30B-A3B-Instruct，running/decode24，Device57344、
Host8GB，三个不同原始 workflow 的 24576-token 前缀。实际执行七个
greedy 请求，A 和独立参考各输出 512 token，其余各八个；无预定输出
token、重放 sampler 或人工 copy 延迟。这是数值 fixture，不是新的
自然 workload，也不改变正式比较中的停止条件。

B 的实际需求恢复为 20487 token、2013954048 bytes，恰好对应一个
untagged native H2D ACK。恢复期间有 11 个在暂存之后才启动的实际
decode batch 处理了 A 的正输出增量，且在处理完成时查询的原生不可
复用 ACK 尚未完成。它证明正常传输下存在模型推进，不能直接外推隐藏
了多少整轮等待或可减少多少 JCT。

`review_model_progress.py` 与 `model_progress_review.json` 已独立对账唯一原生 ACK、B 后续实际
prefill 身份与时钟，以及上述 11 个不同 forward_iter 的实际 decode
worker 正 token 增量。恢复前已经提交的另一个结果不计入这 11 个。
它保持原模型 failed，不修改近完整冷前缀或数值门槛。

第一次运行保持 failed：B 另有 4096 token 仍驻留，未达到预设至少
24575 token 的近完整冷前缀门槛。另行读取全部原始响应也发现数值
门槛未通过，不将前一个场景检查失败当作可以跳过后续检查的理由。

## 关闭 pipeline 的同场景诊断

诊断应用 `d8837321`，native 状态与首次完全相同，使用原先的三个
24576-token、Device57344 场景，关闭新 pipeline。它仍完成七个请求；
实际 helper 未安装，未产生暂存日志，B 的原生请求记账为
4096 Device + 20487 Host 命中。它只能记为
`native_demand_model_diagnostic_collected`，明确 `model_qualified=false`。

| 与各自独立参考比较 | pipeline 开启 | pipeline 关闭 |
| --- | ---: | ---: |
| A 首个输出 token 分歧的序号 | 125 | 81 |
| B 八个输出 token 是否完全一致 | 是 | 是 |
| B 相同 token 的最大 logprob 绝对差 | 0.1041944623 | 0.1041944623 |
| 原有 token/0.05 数值门槛 | 未通过 | 未通过 |

`review_native_diagnostic.py` 与 `native_diagnostic_review.json` 已实际执行，逐项核对两侧七个请求的
原始输入 token、采样配置、metadata、salt、请求 ID 及其他正文相同，
只允许不同服务器拥有各自真实 session 命名空间。三个 source request
及 native commit/tree 相同；应用 git diff 也确认 `beliefkv/` 和
`patches/` 没有变化。每个响应经原有 `response_record` 验证真实 usage、
有限 logprob 和停止方式，原始 JSON 在读取和发布前后重新计算 SHA256。
两个 owned server 均完成清理，没有残留 GPU compute process。
server 的受控清理退出码为 -9，不描述为自然正常退出。

在首次运行中，A 前 48 个输出 logprob 完全相同；变化开始于实际 batch
从 1 变为 2、B 加入 decode 的阶段。与独立参考相比，其首个超过 0.05
的差发生在第 50 个输出。这个时间定位和 pipeline-off 反例支持继续
检查 batch 数值差异及参考前缀构建。独立参考目前对整个续接输入执行
chunked prefill，而被比较对象保留了此前逐 token 生成的 KV；两者
不是相同的浮点运算路径。上述证据尚不能独立证明哪一项是根因。

A 分歧后的 sampled-token logprob 对应不同 token，不能将其最大差
当作相同 token 的 logit 数值误差。两条件各只有一次执行，也没有
相同实际 batch 的反事实，不用于性能或因果收益结论。

## 后续验证边界

原有严格模型门槛继续保留。下一步先核对独立参考的 prefix 构建过程，
并以有界、明确声明的确定性数值模式检查 batch 变化的影响；不能把
开启确定性后的数值检查当作原正式设置的吞吐结果。现有 native 源码
在 FA3 backend 和 MoE kernel 配置中已有确定性分支，尚未在这个场景
实际执行或完成资格。

另一个只改变 fixture 的候选已经 CPU 绑定：前缀
24576/24576/32768、Device61440，目标是让 B 完整换出同时让 A+B
及 512-token 增长能够放下。至少 24575-token、ACK、服务和数值门槛
保持不变。该场景尚无真实模型通过证据，正式 V230/V311 工作、容量、
工具时间、完整尾部及既有 V345 对照均未改变。

## 来源与状态

证据目录为 `experiments/reports/prefill_pipeline_20261028/`：

- `model_entry_r2_cpu`：4085 项和六个子测试，继承 4060 个身份。
- `model_fixture_r1_cpu`：4085 项和六个子测试，对修正 fixture 重新绑定。
- `native_diagnostic_r1_cpu`：4086 项和六个子测试，继承全部 4085 个身份。
- `gpu_components_r4/r5.json/.log`：对应冻结应用的四个真实 DMA 场景通过。
- `gpu_model_r1.log`、`gpu_model_r1_raw.tar.gz`：失败模型及全部原始资料。
- `gpu_native_diagnostic_r1.log`、`gpu_native_diagnostic_r1_raw.tar.gz`：
  独立原生需求恢复诊断及全部原始资料。
- `review_native_diagnostic.py`、`native_diagnostic_review.json`：
  已完成的逐请求、数值、源码和退出核对。
- `review_model_progress.py`、`model_progress_review.json`：精确 ACK、
  后续实际 prefill 与 11 个实际 resident decode batch 的独立物理核对。

归档与本地原始目录逐文件比较通过。实际 GPU 任务已结束，本轮没有
启动新的完整 FULL、其他 baseline 或消融。工具预取保持关闭，整个
相对所有 baseline 的明确、可重复 JCT/吞吐目标仍未完成。
