# 需求恢复完成后的原生准入重试

2026-10-28。当前 main 修复需求恢复路径的一处原生满批状态遗留。
该改动通过4102项CPU及六个子测试，保留原有4100个测试身份并增加两个。
独立冻结5cf3bb8b现已完成GPU组件和确定性真实模型I/O验证，尚无本修复
的完整性能结果；已完成R1/R2仍冻结为01510ed5。

## 残余 decode 回退的范围

`prefill_pipeline_20261028/decode_copy_envelopes_vs_preadmission.json`
对完整R1和准入前卸载保留全部decode与正常尾部，按传输提交至ACK的
保守窗口分类。所有正字节传输均有可用窗口，没有把未知边界当作零：

| 对称形态匹配 | R1 worker均值/ms | 准入前卸载/ms | 比值 |
| --- | ---: | ---: | ---: |
| 全部decode共同档 | 15.751342 | 15.570641 | 1.011605 |
| 前后均为同成员decode的共同档 | 14.879445 | 14.471020 | 1.028224 |
| 上述稳定decode且两侧均在传输窗口之外 | 14.051457 | 14.076510 | 0.998220 |

这里按batch、sum/max上下文档位和graph状态匹配，最低支持10批；
与之前2048-token平均上下文档、最低32批的0.84930%采用不同口径。
另有131个请求的13333个完全相同单请求输出位置，在两侧都处于窗口
之外且上下文、graph、token增量相同时，均值5.583327/5.583465ms。
这些条件化描述不支持所有decode被控制逻辑普遍拖慢。

窗口相交只能表示可能存在copy重叠，不能证明实际DMA占用、拷贝干扰
或可消除GPU等待。它也不能排除CPU、布局、其他device-to-device拷贝
或未记录因素。源码默认JIT quota为2个block，并且有原生fallback；
没有实测本轮实际JIT路径或SM占用，不能直接断言拷贝核占满GPU。

## 已复现的状态缺口

原生PrefillAdder可以成功选中一批请求，再因下一个候选超过请求槽或
内存预算而设置running batch的`batch_is_full`。正常路径会运行已选
prefill；需求恢复流水线则暂存这批成员，让已有decode继续运行。

旧代码在ACK完成时释放原生请求receipt并重新选批，但保留满批标志。
`_get_new_batch_prefill_raw`随即在早期guard返回，已经恢复的成员也不能
重新进入PrefillAdder，直到其他正常事件重置这个标志。

新的CPU用例执行当前native scheduler真实方法、Req、PrefillAdder、
radix tree及allocator，选中两名成员后在第三个候选处触发请求槽停止。
旧代码ACK完成后无法到达native批次构造，红例明确复现；修复后重新
通过原生选批，仍一起选择原来两名成员，且没有第二次H2D或提前分配row。

修复只在ACK完成且原生满批标志为真时清除旧标志，请求一次正常容量
重试；原生请求槽、输入、增长预算、资格和锁仍有最终决定权。
另一个用例在ACK前将可用请求槽变为零，确认仍拒绝准入、恢复满批标志；
正常容量事件后才重新选批。没有新增agent资格、优先级、TTL或容量信用。
新的计数与ready日志字段每个完成恢复批次至多更新一次，没有逐token
排序或额外CUDA查询。

历史R1没有记录这个满批标志，不能证明实际发生频率或给出JCT贡献。
一次离线检查发现747个有ready日志的成员关联中，仅3个ready至首次
launch超过100ms，最大约1.258秒；该粗核对不提供状态缺口的机会分母，
也不用于宣称修复会带来明显性能收益。

## 验证与失败记录

原生/纯pipeline/选批定向检查42项通过。完整共享CPU检查4102项及六个
子测试通过，保留4100个原有身份、增加两个；实际构造器和来源manifest
保持绑定，native仍为d3da19c1/tree729aa0caa7a1，没有修改原生引擎。

首次红例在断言原生默认空receipt时失败，它不是状态缺口的证明。
修正为检查原生`DecLockRefParams.node_id`后，第二个红例准确失败于
ACK后未达到批次构造。两份XML均保留，未将首个fixture错误冒称为成功复现：
原始文件留在本地，失败报告以原字节gzip归档入库。

- `admission_retry_red_cpu.xml`
- `admission_retry_native_red_cpu.xml`
- `admission_retry_targeted_cpu.xml`
- `admission_retry_shared_r1_cpu.xml/.evidence.json/.sources.json`

## 完整重复试验的边界

上一轮4.94374% JCT、2.64506%全程吞吐收益仍只有一次完整运行。
准备的R2重复固定原R1的01510ed5、native729aa0caa7a1、配置、工作量、
工具时间、容量、种子和原始停止；本次main准入重试修复不包含在R2。
新修复的CPU证明与旧冻结方案的重复性能证明分别保留。

`repeat_registration.py`通过原冻结supervisor启动，只改变输出目录、
端口、登记时间和前置/来源证明等注册元数据，不重新冻结生产源码或
重新采集native/ThunderAgent。原资格文件必须全部保留，源码、工作量、
环境或引擎变化都被拒绝。九项CPU元数据边界检查通过，实际R1完整结果、
两次idle、owned退出、来源及配置核对也已通过。真正启动前和排队期间
仍由原supervisor检查最新hold、pause、GPU和端口。

其他baseline与消融仍等待明确、可重复的双指标优势。R2只检验原冻结
方案的重复性，不授予当前main修复的GPU资格或最终目标完成。

## 独立冻结目录

ACK重试修复另冻结为
`/home/longhao/experiment/.worktrees/beliefkv-ack-readmission-5cf3bb8b-20261028`，
与正在运行的R2目录分开。`readmission_source_check.py`核对该目录的
完整1815个CPU-manifest文件、4102项通过报告与原生源码身份；它复用
已有完整CPU执行的相同字节，不重新运行同一套测试，也不提供GPU证明。
后续仍须等R2实际退出，并完成新源码的component、model和position
验证后，才可登记修复后的正式FULL。

## 新源码GPU与模型I/O

R2完整退出且GPU无compute进程后，5cf3bb8b的四项实际GPU组件全部通过：
驻留访问、多成员选择、无decode回退和取消后共享前缀；恢复K/V逐项相同，
Device/Host最终空闲量分别回到128/257。证据为
`gpu_components_ack_retry_r1.json`，不是全模型性能。

同源码确定性模型随后通过七个未强制输出的请求：24583token、
2416607232bytes正常H2D，精确原生ACK与后续prefill均验证；两个暂存后
提交的resident decode批次在ACK尚未完成时实际完成。A的512个token、
B的八个token及logprob与独立冷参考相同，最大差均为零。

`readmission_gpu_review.py/.json`独立重读实际请求/响应、原生传输、
服务与清理，核对4102项CPU来源、四项组件及当前冻结源码。
模型server及组件进程已实际退出，核对时GPU无compute进程。
实际使用FA3 deterministic/pytorch，限于I/O数值fixture；旧普通
FA3/flashinfer长greedy等价性失败仍保留，正式性能数学未改变。

本fixture的`retry_native_capacity_check=false`：它验证新源码的正常
KV恢复和模型进展，没有进入旧满批标志重试分支。该分支继续由真实native
CPU调度用例证明，不冒称为GPU中实际触发或已测JCT收益。
新的position与完整修复方案性能仍待验证。
原始模型证据另外保存为`gpu_model_ack_retry_r1_raw.tar.gz`；已逐字节
核对归档中的20个source-bound模型输入，未归档environment文件。
