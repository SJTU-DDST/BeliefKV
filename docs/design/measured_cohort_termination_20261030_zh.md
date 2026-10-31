# 固定测量组完成后结束实验

用户明确要求不再等待所有背景任务结束。当前56原项目Full和后续匹配
比较改用同一规则：固定18个测量workflow全部自然完成后结束观测，取消
剩余非测量任务及尚未到达的背景提交，保留全部测量JCT。

## 到达与统计

原56项目、tokens、依赖、工具时间、8初始/平均90秒到达表和12/18/26
固定分组保持不变。旧cohort文件和运行登记保留，新建schema2评价契约，
明确非测量任务可以右删失，18个测量成员及其请求必须全部完成。
不能重新选择最先完成的18项，也不把背景取消记作正常完成或零JCT。

JCT继续从预定到达到自然结束。各策略可以在各自最后一个测量成员
完成时停止，因为之后的背景执行不会改变已经完成的测量JCT。
不同策略的整个观察期输出量除以各自结束时间不再作为共同吞吐指标。

共同吞吐窗口统一为首个测量成员到达至最后测量成员到达：
530.928631--2368.047777秒，约30.62分钟。窗口只由原到达表决定，
在所有测量成员完成之前必然已完整覆盖；包含预热和背景的实际输出，
继续按native批次完成时刻和output_token_delta统计。全部测量JCT包含
这个窗口之后的正常尾部。

## 当前冻结实验的关闭

独立nice19、CUDA隐藏的观察器增量读取已观察到的workflow边界，不把
测量组身份传给在线调度器。每2秒检查一次，在18项全部结束前无GPU
控制动作；只在完成数变化或30秒心跳时写状态。

触发后短暂暂停监督进程，向重放客户端发送SIGINT。现有asyncio取消
路径停止未来提交、取消非测量工具定时器及模型请求，通过原abort_request
和session排空路径清理并刷新日志。GPU服务器继续运行，以便获得两份
新鲜空闲遥测快照；核对全部测量请求的源token数量、哈希、身份及停止
原因后，再恢复并关闭监督进程，验证其owned进程实际退出。

暂停的是监督进程，模型服务器在取消及排空期间继续运行。任何异常路径
都恢复监督进程，避免将其留在暂停状态。进程信号必须验证PID和start_ticks，
不能向已复用的PID或其他实验发送信号。

旧冻结监督器仍可能将SIGTERM记为stopped_for_investigation；保留其原始
记录。新的intent、receipts、telemetry_drain和completion证书明确证明这是
用户指定的测量组结束，状态为measured_cohort_complete_background_censored，
不伪装成完成全部56项。完整人口守恒为false时继续如实记录。

## 分析与后续

新cohort审查要求测量请求集合与完整源trace完全相等，测量输出逐项与
native服务计数一致；非测量已生成但未获得最终回执的输出不冒充完整
请求。完整人口审查的历史默认保持不变。

完成后运行cohort、持续供给、周期决策及定时有用KV并集审查，使用显式
2.9异构门槛生成新协议的负载判断。持续ready供给、背景覆盖、压力及前缀
丢失要求没有放宽。未通过时不自动启动oracle或baseline。

旧的“等待全部56项完成”的CPU审查须明确取代。后续oracle登记也需要
读取新的完成证书并复用同一schema2评价契约，不能使用旧的全人口完成
校验来声称本次已完成56项。历史完整控制和原始证据保持原样。

实现入口：scripts/stop_after_measured_cohort.py。

## 本轮登记

实现与分析提交为`811e55ad`，223项相关CPU测试通过，包括测量组完整性、
删失统计、增量JSONL、token/停止回执、PID身份、异常时恢复监督进程，
以及原生客户端取消和session排空回归。冻结观察目录为
`beliefkv-cohort-stop-20261030-811e55ad`，当前GPU仍为`be9f062c`。

实际登记：
`ops/workflow_original56_measured_completion_20261030_r1.registration.json`。
观察器PID2838460，实际nice19、CUDA99999及OMP/OpenBLAS各1线程，
输入哈希及存活均已核对。启动前缀为9/18测量成员完成、24项已到达，
状态waiting_fixed_measured_cohort；没有发送GPU停止信号。

原全人口审查PID2829978及2829996已明确取代并验证退出，保留原登记和
状态。证据在`measured_cohort_stop_r1/{contract,observer_launch,live_observer_r1,
superseded_full_population_reviewers}.json`。
结束与完成审查结果将写入原GPU运行目录的`cohort_termination/`及
`measured_cohort_complete_original56_r1/`。目前只是确认观察器已启用，
不代表已经完成18项或验证了实际GPU关闭。
