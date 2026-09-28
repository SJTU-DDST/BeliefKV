# Child 已交付内容的快速验证 (2026-09-28)

## 问题

此前用最终 `child_reports.json` 文本截取前缀，仅能条件化评价自然
RETURN 的最终报告，无法证明该内容曾在对应时刻交付，也没有继续调用
工具的负例。本次新增 opt-in `--child-stream-content-shadow`，必须同时
启用 `--stream-completion-shadow`。不修改 prompt、不调用额外模型、
不产生 predictive 物理动作；流式模式可能改变模型工具调用和时延，
因此结果不可直接外推至非流式服务。

## 采集与判定

每个 child LLM 请求只在首个正文 chunk、正文每跨越 128 字符、
tool chunk 和 finish chunk 记录观测。`child_stream_content.jsonl` 中
保存请求/child/context epoch/JOIN 身份、回调单调时钟、**截至该时刻**
累计正文字符数以及最多 128 字符的已交付正文尾部；不保存 reasoning
文本和工具参数。LLM 完成记录 tool_call_count/finish_reason，失败记录
错误标识。独立有界队列避免采集写盘阻塞 callback；按 workflow 输出
`child_stream_content_stats.json`，有丢记录或写入异常则整条 workflow
从分析中剔除。原有控制 trace 不写入正文。该文件含原文片段，应仅限
本地诊断，不上传公开训练集。

离线回放按 request_id 和 child 的自然 RETURN 匹配正例；同一 child
本轮回复最终继续调用工具的 LLM 请求是明确负例；取消、异常或非
RETURN 的纯文本轮次暂计删失。只允许用到达时刻已收到的正文快照，
跳过 finish/tool chunk；每个请求只计算**首次触发**。词形二元组与
累计正文长度分别构成内容实验组和长度基线。评分方向仅使用训练项目
拟合，阈值仅用训练项目工具负例的请求级最大分数 95 分位确定；
测试项目只用于最后一次读数。比较自然 RETURN/JOIN-last 覆盖、
继续调用工具的误报、首次触发到 RETURN 的提前量以及 500 ms 门槛。
没有嵌套验证且样本很少，不做可靠泛化或 PCIe 收益结论。

## 执行

`STREAM_CONTENT_SHADOW=1 PILOT_WORKFLOW_COUNT=12 WORKLOAD_POOL_SIZE=32
RUN_ROOT=... bash scripts/run_qwen35_stream_timing_shadow.sh`
选 Astropy 与 Sphinx 各 6 个 root；各做一次反向留出测试。
有任何 dropped、结果身份不符、两类不足或只在 finish 后触发，
本轮结论标记为不充分，不补填“预测成功”。

为释放实验磁盘空间，已从结束的
`qwen35_terminal_join_sealed_20260927_v1` 仅删除 218 个可重建的
`workspace` 克隆；保留 `model.patch`、轨迹、审核、manifest、
评分产物和所有文档。清理前可用空间约 28 GB，清理后约 73 GB。
