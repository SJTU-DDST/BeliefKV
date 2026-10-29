# 平均两分钟到达的配对实验

2026-10-29，按最新用户要求，将后续 workflow 的泊松平均到达间隔从
60 秒改为 120 秒。保留 18 个不同项目和原有工作量：首批 10 个在
时刻零到达，后续 8 个使用独立 arrival seed21。最后到达约为
863.431 秒，最长相邻间隔约 197.940 秒。本次不是从零开始的纯泊松流，
也不声明这 18 条轨迹建立了稳态性能。

ThunderAgent 工具衰减版本和 FULL 共用一份到达表，按该顺序串行执行。
两侧分别保留上一轮的原生引擎和政策；FULL 使用已通过实际组件、
独立历史模型和位置重放验证的 free-extent 引擎，工具预取与普通队列
预取继续关闭。模型、running/decode24、4096 prefill、mixed off、
805198 Device token、200GB Host、全部种子、931 个分类合成工具计时、
3674 个请求和全部正常尾部不变，不重新运行 native。

JCT 从计划到达时刻算到完整 workflow 结束，保留驱动投递延迟、
准入等待和 KV 恢复等待。120 秒间隔可能减少排队，也可能减少有效
迁移压力；两者均待实测。旧 60 秒结果单独完成和保留，不代替本次
匹配的 ThunderAgent 控制。完整结果应同时检查平均及尾部 JCT、
workflow 首服务和 ready 服务间隔、decode 吞吐时间曲线、有效 KV 压力、
H2D/D2H 和旧输入重算。

新 CPU 等待进程须验证当前 60 秒 FULL 的全部工作、原始成功退出、
两份新鲜 idle 遥测和实际所属进程退出，才能登记下一项 GPU 任务。
每个 GPU 比较仍使用原生排队器的独占锁与资源检查，新 hold、pause、
源码变化或失败都会停止后续队列，不重启部分轨迹。

为了让同一个已验证提交支持这两个方法的独立登记，比较启动器的
冻结路径增加 registration 绝对路径摘要；引擎、运行目录和端口仍各自
独立，已有冻结路径不修改。这只是启动路径命名变化，不进入测量期
调度或传输控制。

配置、固定到达表和 CPU/排队记录保存在
`experiments/reports/arrival_mean120_20261029/`。

## 登记状态

代码与配置已提交为 `622c5feb5280e6031cf72633aa424835aa509bdf`。
在该提交的独立干净工作树上，单次 CPU 回归通过 4,734 个用例和
6 个子测试，保留之前全部 4,713 个用例；1,925 份源输入在执行前后
保持一致。证据为该目录的 `cpu_r1/evidence.json`。

2026-10-29 21:11:16 Asia/Shanghai，CPU 等待进程
PID 1341226 / start ticks 744960744 已独立核验为真实存活、
命令行与登记一致、nice0，状态为 `waiting_predecessor`。
队列登记位于 `queue_r1/registration.json`。新 ThunderAgent 与 FULL
分别使用 18871、18872；当时两侧尚未登记 GPU 比较进程，唯一 GPU
使用者仍为原 60 秒 FULL 的 scheduler PID 1326025。

上述状态只证明后继任务已排队，不是新 120 秒实验已经启动或获得性能
收益。实际执行状态由队列与两侧各自的状态文件继续记录。

原 60 秒 FULL 在 21:17:44 完成收集并实际退出后，120 秒 ThunderAgent
已自动接入。独立 `thunder_live_start.json` 于 21:35:17 核验
1283/3674 个完整回执、17 个已投递 workflow 的原定到达时刻、
nice0 watcher1345254/server1345626/driver1346717 和唯一 GPU
scheduler1345920。paper acting decay 配置生效，未继承 FULL 的
工具预取、普通预取、需求流水线或逐层交接。该前缀不是完整 JCT 结果。

当前冻结排队器在所有 GPU 比较结束后使用服务 Python 执行绘图，而
该 Python 缺少 matplotlib。为保留已运行源码，另用
`review_after_pair.py` 登记只做 CPU 后处理的独立进程：两侧 GPU
完成和真实退出后，通过已有 `ops/cpu_plotting_20261027` 环境生成
匹配报告。它不登记 GPU 实验、不替换比较进程，也不把原排队器后处理
失败误报为 GPU 实验失败。main 的后续队列已改用该绘图解释器；
当前冻结队列保留原始执行证据。

该 CPU 结果处理进程现已实际登记并核验：冻结分析提交 `74f0fc2c`，
PID1354635 / start ticks745173678，nice19，状态
`waiting_complete_gpu_pair`，登记文件为
`review_worker_r1/registration.json`。2026-10-29 21:46:05 的独立进程
核验确认它和原 GPU 排队器均真实存活，唯一 GPU 使用者仍为
ThunderAgent 的 PID1345920；没有新增 GPU 比较任务。
