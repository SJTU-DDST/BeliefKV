# 准入前工具卸载的完整结果

本轮完成全部工作和独立审计，但仍未达到完整平均JCT、全程输出吞吐
均明确且可重复优于强ThunderAgent的目标。新增入口确实释放了KV，
不能据此归因为大的性能收益，也没有实现逐层容量交接。

## 完整工作与对照

Full登记为
`ops/tool_offload_before_admission_20261027_r1.registration.json`，
执行源码为716ef9a3e4ad，native树为11b84f7c3198。
复用完整V345工具衰减版ThunderAgent，没有重复运行native或对照。
两臂均为V230的18个不同项目、3674个请求、145565966输入token、
549671输出token、92个child及72次JOIN。52次为单child，20次为
双child，没有grandchild；所有root都使用过最终返回前的委派提醒，
这些负载形状与干预继续保留。

工具时间共同采用V311的可见类别合成合同：931个execute计时器被
held-out TraceLab WebFetch/WebSearch样本替换，其余2543个工具计时器
保留原值。它不是自然LoCoBench工具时间。在线策略只看已公布的
类别和历史，不能看未来采样时长。完整输入、依赖、停止条件、容量、
seed21、流式协议与引擎一致。

实际完成时间为北京时间2026-10-28上午；目录名沿用登记时的
20261027实验身份。原watcher/server/scheduler/driver和CPU绘图等待器
均已实际退出，GPU上已无本实验进程。两次新鲜空闲telemetry和原始
停止检查通过，没有删除普通长尾或未改善的workflow。

## 主要结果

| 指标 | Full | V345工具衰减ThunderAgent |
| --- | ---: | ---: |
| 完整平均JCT，秒 | 1631.194643 | 1697.583645 |
| Workflow event makespan，秒 | 2304.709796 | 2355.344551 |
| 全程输出token/s | 238.499008 | 233.371801 |
| Decode worker区间输出token/s | 346.325953 | 340.158312 |
| Worker时间加权decode batch | 8.274797 | 8.108286 |
| 平均workflow首输出，秒 | 134.728729 | 139.028403 |
| 每workflow最大后续ready输出间隔的均值，秒 | 90.642600 | 73.449107 |
| 实际H2D，十进制GB | 2714.687472 | 2809.042010 |
| 实际D2H，十进制GB | 1132.365251 | 2255.862596 |

平均JCT改善3.91079%，全程输出吞吐改善2.19701%。17个workflow更快；
`python_web_social_hard_001`为1670.303490秒，对照1591.623565秒，
慢78.679925秒。后续ready服务间隔也更差，不能只引用首服务或平均JCT。

相对上一轮free-continuation Full，平均JCT增加1.138795秒，即
0.06986%；全程输出吞吐增加1.04168%，decode worker区间输出率却从
351.421309降至346.325953token/s。这里是不同配置的一次完整执行，
不是独立重复，也不能把makespan改善称为decode kernel提速。
三张完整图均已检查：两臂在主要计算区间交错领先，没有持续的大幅
worker吞吐分离，普通尾部仍全部保留。

## 提前卸载是否真正执行

新增入口累计1209次检查，1194次首次请求重新唤醒。实际有41次
`before_native_admission`节点释放，累计3995 FULL token、
392724480字节，关联41个消费者。41个消费者均已在释放前提交，
并可绑定到随后完成的原生request/context/epoch/session/clock服务。
这与上一轮独立预测提前释放零字节不同。

释放日志没有完整的消费者释放时attempt/session身份，也没有释放
物理索引到消费者分配的映射；这两项仍为未知。累计释放量不是同时
空闲容量，run-batch入口不是kernel启动，不能推算隐藏的传输时间。

独立核对的原生分配压力路径仍另行计数：

- JOIN等待页：548次节点释放，累计108.957303GB；
- 工具等待页：191次节点释放，累计6.181945GB；
- 额外预测提前释放：41次，累计0.392724GB，均发生于原生准入前。

Host备份ACK不算物理卸载，原生untagged驱逐及终态child清理也不混入
上述总量。新增预测释放量相对整轮迁移仍很小，不能把整体结果直接
归因于这0.393GB。

## 预取、正确性和等待

全部455个tagged JOIN拷贝均确认复用，共83.901383GB；其中5个JOIN
在完成前启动并确认复用22.711566GB。两者范围不同，不能把完成后
恢复都算为EOS提前收益。工具预取实际拷贝仍为零，不为增加覆盖率
驱逐ready/排队agent。

273次资格授予覆盖277个程序服务关联，全部验证。其中191个为普通
continuation，80个为sibling，2个为parent。授予后关联的已验证复用
3.558998GB包含后续拷贝，不能与提前复用量混同。

四个旧工具结束符反例全部通过；55个实际EOS触发均有真实final
RETURN，未发现被触发的中间工具请求。两臂同会话旧输入探针均为
3564个请求、140952652个共同旧输入token，缺失计数为零。这不是
对每个kernel重算的完整测量。

386个至少排队一秒的工具后继，采样累计等待2796.307863秒：
active244.762087秒，inactive且完整原始逻辑预算可容纳272.098666秒，
inactive且逻辑预算不足2271.404721秒，未知8.042389秒。
122个请求出现过可容纳区间。区间可以互相重叠，采样不提供精确
状态转换时间；逻辑预算也不等于可回收独占物理页，不能从JCT扣除。

27个Full恢复采样批次累计等待4272.785ms，对照18个批次2662.835ms。
这不是匹配批次或全量等待，不能扩张为整轮总损失。

## 传输流区间和下一步边界

全量传输记录均带有效CUDA流事件区间，并与独立字节计数一致：

| 方法/方向 | 完成操作数 | 实际GB | 传输流区间合计，秒 |
| --- | ---: | ---: | ---: |
| Full D2H | 12870 | 1132.365251 | 27.797246 |
| Full H2D | 444 | 2714.687472 | 85.968206 |
| ThunderAgent D2H | 27591 | 2255.862596 | 51.947387 |
| ThunderAgent H2D | 281 | 2809.042010 | 88.825712 |

这些是操作事件区间求和，不是GPU暴露等待或workflow关键路径。
两个方向可能重叠；日志也没有CUDA事件的wall-clock锚点，不能相加
作为端到端损失。等待逻辑准入或空间的时间可能在传输区间之外。

prepare_host的9841次检查中，7167次因Host空闲预算在扫描前退出。
末次Host自由683tokens，保留5461tokens；另外实际发行80个节点、
17516tokens。这证明记录到的Host预算门槛经常生效，不证明存在可
安全牺牲的Host副本，也不支持盲目放宽门槛。

本轮补齐并验证了一个真实入口缺口，但没有建立大的隐藏传输收益。
下一步应检查实际逻辑准入、冷页与Host备份容量如何共同限制恢复，
优先证明有足够的真实暴露等待和安全donor。不能把更多借出/召回
变体包装为创新，也不能为了增加预取量牺牲ready peer。
逐层A卸载、B接管空间并提前计算仍未实现；本轮没有验证该机制，
也不能由这些区间断言它会改善完整JCT。

## 证据

- [完整比较](../../experiments/reports/tool_offload_before_admission_20261027/complete_review.json)
- [实际卸载路径及传输流区间](../../experiments/reports/tool_offload_before_admission_20261027/complete_offload_paths.json)
- [工具备份和分配压力卸载](../../experiments/reports/tool_offload_before_admission_20261027/complete_wait_offload.json)
- [完整decode趋势](../../experiments/figures/tool_offload_before_admission_20261027_r1/decode_timeline.png)
- [完整decode分布](../../experiments/figures/tool_offload_before_admission_20261027_r1/decode_distributions.png)
- [相同几何档位](../../experiments/figures/tool_offload_before_admission_20261027_r1/decode_geometry.png)

完整operator已实际执行成功，14项新增审计边界CPU检查通过。
运行时资格仍使用原3331项共享CPU及原生726项源码绑定；没有新引擎
改动。原有失败、负结果和用户未提交的研究文档均保留。其他baseline
及消融仍未满足启动门槛，没有后续GPU任务排队，整体目标保持未完成。
