# Interactivity 官方定义复核

2026-11-04，Asia/Shanghai。用户要求重新核对最新 AgentX 文档；
若官方 AgentX Pareto 使用 E2E，则保持既有实验和报告口径。

## 核对结论

继续使用 **E2E Normalized Interactivity P90**，不改为 decode-only。
既有图表、HTML、实验记录和分析数据保留原样。尚未投入使用的
decode-only 报告规则已撤回，没有改变 GPU 实验或运行中的服务。

官方 `SemiAnalysisAI/agentx-harness` 的 `docs/metrics-reference.md`
明确说明，`e2e_normalized_interactivity_p90` 和 `p75` 复现
InferenceX AgentX Pareto 曲线的横轴，包含 TTFT，支持流式与非流式请求。

每个请求先计算：

```text
ratio = request_latency_seconds / output_sequence_length
E2E_normalized_interactivity_P90 = 1 / percentile(ratio, 90)
```

先取秒/token 的慢尾分位数，再取倒数；不是对 token/s 取 P90。
这是请求级指标，不能将整个 workflow JCT 或请求间的工具/JOIN 等待
直接放进公式。原报告的固定测量组与共同窗口提交请求仍是不同人口，
比较时继续显式标注。

## 文档与实现交叉核对

- 官方文档：
  `https://github.com/SemiAnalysisAI/agentx-harness/blob/master/docs/metrics-reference.md`
  的 `E2E Normalized Interactivity` 节直接声明 AgentX Pareto 横轴身份。
- 实际计算：
  `src/aiperf/metrics/e2e_normalized_interactivity_analyzer.py`
  使用 `RequestLatencyMetric` 和 `OutputSequenceLengthMetric`；
  实际表达式为 `(latency_ns / NANOS_PER_SECOND) / osl`，
  随后 `numpy.percentile` 和 `1.0 / ratio_p`。
- 同一文档另有 `Output Token Throughput Per User`，
  定义为 `1 / ITL`，明确排除 TTFT。这是另一个可用指标，
  不能据此将 AgentX Pareto 的 E2E 指标解释为 decode-only。
- 复核时文档最近一次提交为
  `60a094f256b42f55fde1efb3d45672e1d9a44566`，
  GitHub 记录时间为 `2026-11-03T23:38:44Z`，
  即北京时间 2026-11-04 07:38:44。固定版本可用该提交替换 URL 中的 master。

## KV 与结果解释

指标定义没有要求 KV 全命中。若某个部署中的排队、KV 恢复或重算
延迟了请求完成，实际请求时延会反映这些等待。
某次配置的缓存命中情况是实验条件，不是指标公式的前提。

因此，原 E2E interactivity 改善可能来自更低的首次服务等待、
恢复等待、prefill 或 decode 时长；不能直接表述为同等比例的
纯 decode 加速。继续单列请求 TTFT、完整 workflow JCT 和系统吞吐，
保持相同任务/token/资源、完整尾部及单次运行的证据边界。
