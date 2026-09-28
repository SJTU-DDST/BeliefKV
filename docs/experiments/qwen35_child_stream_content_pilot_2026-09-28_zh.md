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

离线回放按 request_id、child/context epoch 和自然 RETURN 匹配正例；
同一 child 本轮输出 tool_calls，或本轮纯文本回复后又实际启动工具的
请求，是明确负例；取消、异常或非 RETURN 且无后续工具证据的纯文本
轮次计删失。只允许用到达时刻已收到的正文快照，
跳过 finish/tool chunk；每个请求只计算**首次触发**。词形二元组与
累计正文长度分别构成内容实验组和长度基线；另报告纯内容消融。
评分方向仅使用训练项目
拟合；RETURN-vs-tool 的阈值取训练项目工具负例的请求级
最大分数 95 分位，临近 2 秒目标的阈值还纳入最终报告
过早部分的请求级最大分数；
测试项目只用于最后一次读数。比较自然 RETURN/JOIN-last 覆盖、
继续调用工具的误报、首次触发到 RETURN 的提前量以及 500 ms 门槛。
另拟合“距 RETURN 不超过 2 秒”的目标；同一最终报告中更早
已交付的正文也作为过早触发负例，避免只把工具轮次当成负例。
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
评分产物和所有文档。随后也只清理结束的旧工具窗口、
cold-tool、EOS、hidden-child 与 early-tool 诊断批次的 workspace；
正式导出的训练/校准表未删。清理前可用空间约 28 GB，最终回升至
约 97 GB（同时本次 pilot 已写入约 11 GB）。

首次服务启动因共享 SGLang checkout 已有另一补丁而拒绝启动，
未改动该 checkout；在相同固定 commit 的独立 worktree 应用 staging
补丁后重启。本次有效采集目录为
`experiments/raw/qwen35_child_stream_content_pilot_12root_20260928_v2/`。

## 快速验证结果

12 个 root 均有终态：10 completed、2 incomplete；不能当成
12/12 自然完成的端到端样本。12 个内容文件的异步写入完整且
`dropped=0`。child 轮次包括 31 次可配对的自然 RETURN 和
1392 次继续使用工具的轮次（其中 10 次为先输出文本、随后
继续启动工具）；1318/1392 个工具轮次没有足够的前 128 字符
正文进入该内容分类器。因此下表中的误报同时报告全部工具轮次
分母；仅有可观测正文的工具轮次合计 74 个，模型并没有在其余
1318 次做有内容的判断。只有 3 个自然 JOIN-last child。

| 留出项目 / 目标 / 特征 | 首次触发落在 RETURN 前 0.5–2 s | 工具轮次误触发 |
| --- | ---: | ---: |
| Astropy / RETURN-vs-tool / 长度 | 3/17 | 1/620 |
| Astropy / RETURN-vs-tool / 纯内容 | 0/17 | 1/620 |
| Astropy / 临近 2 s / 长度 | 3/17 | 1/620 |
| Astropy / 临近 2 s / 纯内容 | 1/17 | 1/620 |
| Astropy / 临近 2 s / 内容+长度 | 2/17 | 1/620 |
| Sphinx / RETURN-vs-tool / 长度 | 0/14 | 6/772 |
| Sphinx / RETURN-vs-tool / 纯内容 | 0/14 | 4/772 |
| Sphinx / 临近 2 s / 长度 | 0/14 | 0/772 |
| Sphinx / 临近 2 s / 纯内容 | 1/14 | 0/772 |
| Sphinx / 临近 2 s / 内容+长度 | 0/14 | 0/772 |

即使纯内容提前触发，很多发生在 RETURN 之前数秒至十余秒：
RETURN-vs-tool 的纯内容首次触发中位数在两方向分别提前
约 9.76 秒、8.58 秒；临近 2 秒目标也只分别有 1/17、
1/14 个首次触发落入 0.5–2 秒窗。它没有证明 token 内容
已经提供足够精确的在线终态信号或物理 H2D 收益。
这仍是两个项目、低压力、流式模式下的**快速筛查**，
不是模型路线不可能奏效的证据。候选下一步应在训练项目中
检验更明确的终态语义转折及不同终态长度，并扩大未见项目
与 JOIN-last 样本；上线前还需按真实带宽/容量/首次复用门禁
重新验证。

后处理在自动启动器中途退出但 stderr 当时未持久化，无法
追认确切异常原因；本次使用同一原始数据、修订后的评分脚本
手动重算了 `content_holdout_astropy.json` 和
`content_holdout_sphinx-doc.json`，已增加标准错误持久化。
