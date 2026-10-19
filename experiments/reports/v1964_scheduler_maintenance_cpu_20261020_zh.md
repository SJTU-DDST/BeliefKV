# 常态调度维护降本与实际配置核对

日期：2026-10-20（北京时间）。BeliefKV 基线为 `b93290c`，原引擎 V87，
候选为独立重建的 V88。GPU 实验保持暂停，没有执行模型服务或 DMA。

## 修改及边界

本轮沿高频调度路径删除四类重复工作：

- post-forward 直接更新同一个 running batch 引用，省去传入 `adder=None`
  后立即返回的容量观察调用；真正传入 adder 时的容量观察保持原路径。
- 没有关键 parent 需求时不调用空回收；存在需求时仍按原路径验证和回收。
  判断位于 JOIN/tool/PREPARE handler 之后，能看到它们刚创建的需求。
- scheduler 的事件 drain 后，没有恢复租约时不再调用空租约刷新。
  新事件带来的租约仍在本轮处理；非空租约的周期和失效逻辑没有更改。
- 没有语义/工具预测 worker 时，直接返回空 fd 元组。原生 idle sleeper
  在 fd 库存不变时省去注册差集；没有 fd 时也省去旧单 fd 接口查询。
  worker 存在时每轮仍读取实时 fd，新增、关闭、重复 fd 和注销继续处理。

没有跨调用保存容量、恢复许可或「没有需求」的结果。post-forward 各处
期限检查仍在原位置读取时钟，未合并为可被前序 handler 过期的判断。
没有改变预测触发、恢复数据、Host/HBM 分配、驻留保护或动态委派策略。

## 实际配置纠偏

V85d predictive 的实际 `server/server_info.json` 为
`sleep_on_idle=false`，引擎 `init_idle_sleeper()` 因而保留
`idle_sleeper=None`。该文件 SHA256：
`52e162973406b6e454050712943808c3ab9c5b853ff7d6cbe50c73d99b00697a`。

最初 V1962 只测了启用 sleeper 的受控场景，因此不能用其中约 28% 的
降幅说明当前实验配置的收益。V1963 补齐了关闭 sleeper 的成对场景，
最终以 V1963 分层结果为准。V1962 作为初步记录保留。

没有因此在默认实验中启用 sleep-on-idle。原生空闲事件循环是否能在
保留预测刷新、传输完成处理及时性的同时少空跑，仍需单独验证。
V85d 的 1811270 次 post-forward 也不是 batch completion 次数；
不能把本轮循环的微秒降幅乘该计数推导 JCT 或累计可隐藏等待。

## 完整受测路径

使用 `benchmark_recurring_control_cpu.py`，完整冻结旧 event_channel、
observer、physical 和 runtime。原生适配部分直接提取各自引擎的
`get_next_batch_to_run` 内 idle sleeper block，包含真实配置下的空判断；
没有把整段原生调度器当成已测量对象。

每次计时依次经过：

1. `scheduler_step(defer_observations=True)`，包含真实空 Unix socket 的 drain。
2. 引擎的 predictor fd 注册块，分别设置 sleeper 为 None 或有 poller。
3. `dispatch_after_forward`。
4. `on_batch_completed`，请求均未结束。

共 192 组循环场景：12/24/156 个 workflow，分别 8/8/48 个 running
请求；0/16 条在途物理命令；profiling 开关；预测维护到期/未到期；
空/4或8条工具等待预测；0/2 个模拟 worker；sleeper 关闭/开启。
另有 2 组未改代码的空 socket 控制及 36 组事务登记校验差分。
每组 151 个样本、20 次预热，每样本 50 次调用，新旧顺序交替。
线程 CPU 原始均值、P50、P90、P99 均保留。

PREPARE 开启但沿用尚未到期的原有期限，不测备份执行。工具预测库存
受控且稳定，工具刷新期限未到期。模拟 worker 暴露真实空 pipe fd，
不运行预测模型，`poll()` 只返回空结果。图和请求构造、状态比较及
socket/pipe 建立均在计时外。

所有 192 组的 visible/context/token、semantic revision、pending
所有权与字节、预测库存、fd 注册、batch 引用和动作计数一致。维护
扫描/延后诊断计数另行保留。没有产生物理命令完成或模型执行证据。

## CPU 结果

下表均为 24-root/8-running、无在途命令、维护未到期、profiling 关闭的
线程 CPU P50，单位 us：

| Sleeper | 工具预测 | 模拟 worker | 旧版 | 新版 | 降幅 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 关闭，实际配置对应 | 0 | 0 | 4.801 | 4.331 | 9.8% |
| 关闭，实际配置对应 | 8 | 0 | 5.342 | 4.901 | 8.3% |
| 开启，受控扩展 | 0 | 0 | 6.305 | 4.480 | 28.9% |
| 开启，受控扩展 | 8 | 0 | 6.834 | 5.114 | 25.2% |
| 开启，受控扩展 | 8 | 2 | 7.835 | 6.976 | 11.0% |

关闭 sleeper 的 96 组 P50 全部更低，范围 2.7%–15.1%；开启的 96 组
全部更低，范围 4.2%–29.6%。较小的百分比出现在维护强制到期、仍有
实际库存要扫描的场景。保留这些场景，不只报告空库存。

两组未改的空 socket 控制 P50 分别为 0.639→0.643us、
0.920→0.928us，略慢约 0.004/0.007us；不删掉，也不归因为本轮的
socket 优化。它们提示微秒级结果仍有计时波动。

这些是上述控制循环及 fd block 的局部结果。它们不包含整个
`get_next_batch_to_run`、真实预测计算、活跃恢复租约检查、PREPARE、
H2D/D2H 或 GPU forward，不能换算成吞吐、JCT、预测准确率或预取覆盖。

## 引擎、配置与回归

两引擎均基于上游 `94602c9c2b7cbdb8efd5c52802dac6a1c180089e`：

| 来源 | 完整引擎树 |
| --- | --- |
| V87，保留 | `db73b95512687e956e94d08389829ad20098295e` |
| V88，新 CPU 候选 | `1db6d8846499beba8f963db9ba8abc7a832ecda8` |

两树仅相差 scheduler.py 和 test_beliefkv_scheduler_hook.py。
完整 staging patch SHA256 为
`2cbb0b4699e000ffc28bd80f6ee70732fb13b4950e40aa303b9afbc7d5a418ef`，
可在干净上游 index 应用，在 V88 反向核对。V87 的树再次确认未变。

启动器和 24-root 配置绑定 V88，显式 `SGLANG_SOURCE_CHECKOUT` 仍有效。
24 个不同原始任务、16+8/900s、running8、256K、95% 压缩、Host80:20、
动态嵌套委派和逐 arm 前端开关保持原值。V85d 冻结来源未改。

主仓 2050 项 CPU 回归通过，无 skip；引擎 lifecycle、scheduler hook、
shadow dispatch 共 87 项通过，另有 41 个 subtest。首批 122 项与
12 个 subtest 是这些集合中的部分，不相加重复计算。检查覆盖 fd
新增/稳定/替换/全部关闭、去重、前序 handler 新建 parent 需求、
预取/租约/ACK、到期维护、终态释放及真实 CPU radix/allocator。
启动脚本通过 bash 语法检查。全部显式禁用可见 GPU。

`/tmp/beliefkv-experiments.paused` 保留，V85d 状态仍为
`paused_by_user`，`automatic_resume_allowed=false`。没有重启实验。

## 剩余工作

本轮只消除了有明确前提的空维护工作。active 预测维护、恢复租约的
实时检查、真正 PREPARE 和大恢复计划仍有成本，不能宣布控制优化完成。
下轮应继续针对实际非空路径测量，而不是以未启用的 fd 分支或空场景
的较大百分比代替主配置收益。GPU 恢复后仍需用去长尾吞吐、平均 JCT、
有效提前复用及需求恢复等待验收。

原始结果：
[V1963 完整分层对照](v1963_scheduler_maintenance_scope_cpu_20261020.json)，
SHA256 `2c10ac99354567e53fc730675e1cc4ec80c495ba844193ae5544b00f4ba53ec4`。
[V1962 初步对照](v1962_scheduler_maintenance_cpu_20261020.json)，
SHA256 `546b4fe776537a2e5eb78c69ad8b0a35ae79e8788186fe2f23f00acf21683020`。
