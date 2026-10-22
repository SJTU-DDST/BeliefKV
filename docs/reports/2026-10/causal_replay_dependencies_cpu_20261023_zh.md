# 因果回放的依赖编译与 CPU 执行验证

日期：2026-10-23，北京时间。基线为 main 的 `8e6ebda`。本轮补齐严格
比较所需的依赖编译和执行器，尚未完成真实 GPU 回放。V128 的冻结
Full/native、prompt、任务集、到达设置和实验队列没有修改。

## 为什么推进这个步骤

最新 Full 与消融组的逐 workflow 工作量差异仍大。继续仅按总输出接近
或原始 JCT 排名，无法隔离 KV 管理的增量。辅助回放必须固定同一条真实
轨迹的模型工作量，并让依赖按本轮完成情况解锁；原始墙钟提交时间与
GPU 排队、推理时间都不能作为后继的固定等待。

动态模型自主委派的端到端实验继续保留。本步骤不把 CPU 模拟或固定
轨迹当作独立任务正确性，也不取代端到端平均 JCT、吞吐和质量验收。

## 依赖编译

`beliefkv/experiments/causal_replay.py` 校验客户端与原生 submit/result
的完整请求总体、workflow/invocation/context/epoch 身份和用量。
每次原生模型执行是独立的未定时操作，其 delay 为 0；CPU 检查或未来
backend 提供本轮完成时间，编译器不搬入原始模型服务时长。

核心依赖包括：

- 工具从产生它的模型结果启动。每个工具按自己的 start/end 配对，
  保留持续时间和并行关系；下一条模型请求等待相关工具全部结束。
- spawn 绑定到对应 parent 的模型结果，child 首请求等待真实 launch；
  JOIN_WAIT 可以先于 child 返回，JOIN_SATISFIED 等待所有本轮成员 RETURN。
- 同一输出可以同时触发 spawn 与普通工具。parent 已进入 JOIN_WAIT 后，
  同轮普通工具仍可启动，不能把 JOIN 完成误加为其前置依赖。
- 嵌套 JOIN、同 context 的串行请求和内部 CALL/压缩独立保留。
  压缩后的 parent 请求等待 summary invocation 返回和 context compact。
- 关联 request 的完成提示绑定原生请求完成。JOIN 后的阶段提示绑定已经
  完成的依赖，避免把原 child 的 GPU 时间重新变成固定延迟。

原生 `complete_monotonic_ms` 和 clock-domain 标记用于核对原生完成是否
落在客户端 submit/result 区间，并计算完成到结果送达的剩余延迟。
这验证的是记录中的边界一致性，不是新增 CUDA kernel 计时。
没有 request/token 边界的生成中语义提示保持未知；不补造边界。

重复、缺失、跨身份、未闭合工具/JOIN、请求阻塞期间重入、不同原生
时钟域、错误工具时长和循环依赖都会拒绝。当前不支持的通信语义或
非 ALL JOIN 也拒绝，不能静默变成顺序请求。

## 执行器

异步执行器只向 backend 提交已就绪的单个请求及其本轮 submit 事件。
控制事件以本轮单调时钟重新发出，不向事件接收端提供整个未来 DAG、
真实未来工具结束时间或原始 stream/callback 时间戳。

backend 必须返回匹配的请求 ID、当前时钟域、原生完成时间和完整
prompt/output 用量。任一不匹配均在发送模型结果、解锁后继前失败，
并取消该执行器仍在等待的并行协程。
这些取消测试没有声称验证了原生服务端 abort。

送达延迟从 backend 回执的原生完成时间计起。若 HTTP 传输已花掉一部分
送达窗口，只等待剩余部分，避免在 HTTP 返回后重复增加整个窗口。
工具的结束事件报告本轮实际计时，工具开始时不暴露其未来结束时间。

该 backend 接口目前完成 CPU 夹具验证。执行器只核对原生回执用量；
精确 token codec 是独立能力，真实强制输出、原生 EOS、session API 和
GPU 执行器尚未连接，因此返回值明确保留 `gpu_replay_qualified=false`。

## 停止证据录制

原有可选 `BELIEFKV_REPLAY_TOKEN_CAPTURE=1` 现在同时快照原始
max/min new tokens、ignore_eos、stop 字符串/正则/token ID，以及
完成时的原生 finish reason。mutable 参数和 finish reason 在 CPU
边界取得独立快照，编码与写盘仍使用既有后台线程。

同请求两端的停止参数必须一致；旧日志缺少这些字段仍只能验证旧的 token
身份，不据此获得 EOS 回放资格。只有 finish reason 或长度记录也不能
替代实际 GPU 路径验证。默认关闭时不读取这些新增快照字段，没有新增
普通调度周期、后台线程或周期扫描。

## 最新真实输入验证

V2926 使用最新完整 V128 Full，保留各输入的完整行前缀与 SHA-256：

| 指标 | 完整观测 |
| --- | ---: |
| Workflow | 36 |
| 模型请求，含内部压缩 | 6352 |
| 输入 token | 497585282 |
| 输出 token | 1609498 |
| JOIN | 66 |
| Spawn | 116 |
| 工具 start/end 对 | 8468 |
| 内部 CALL / compact | 2 / 2 |
| 图节点 / 依赖边 | 36928 / 50769 |
| 排除的原诊断进度事件 | 6121 |

全部请求与原生身份、用量对齐，依赖图无环，零模型服务夹具仍能完成所有
workflow 的依赖传播。该夹具不消耗 GPU 或容量，既不是 JCT oracle，
也不是可加速量上界。原 6121 条诊断进度不变成固定 decode 定时器。

Full 没有精确 token 录制，因此该输入的 `token_evidence_complete=false`，
更不能获得 GPU 回放资格。没有根据长度、哈希或 root 的最终历史填充
child 输入。V2921 保留首轮拒绝混合工具/JOIN 的编译器负结果；V2922、
V2923 为中间验证，V2926 对应最终源码。

随后 native 也已完成。V2928 复核完整对照，Full 原始平均 JCT 低
4.91%，全程输出吞吐低 5.57%，且工作量与 guard 资格仍不同；没有通过
性能验收。不能因为编译通过或原始平均 JCT 较低就跳过严格比较。
V2929 对最终 native 的 6419 个请求、78 个 JOIN、8023 个工具以及
3 个内部压缩完成相同核查，包含其 10 次嵌套 JOIN；依赖编译通过，
精确 token 与 GPU 回放资格仍为未通过。

## 资格验证与剩余工作

V2924：136 个模块、4540 项、2 个 subtest 通过，无失败或 skip。
完整保留 V2918 的 4490 项，新增 43 项依赖/异步执行检查和 7 项录制
快照/停止参数检查。仍明确 deselect 一个原范围外的真实 Docker progress
测试，没有声称运行该项。V2925 绑定最终应用、测试、配置和补丁源哈希。
测试隐藏 GPU，并使用已资格验证的 FULL 扫描连续性候选引擎。

还需要在独立录制运行中取得完整真实 token 与停止证据，连接并资格验证
真实 GPU backend、EOS 和 session 生命周期，再以同一工作量运行
Full/native/简单调度基线。当前没有获得端到端性能验收，目标保持 active。
