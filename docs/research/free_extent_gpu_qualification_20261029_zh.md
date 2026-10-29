# 新到达对照与 free extent 验证

2026-10-29，时间采用 Asia/Shanghai。正式 trace 仍为完整 V230/V311，
18 个不同项目、3674 个请求、145565966 输入及 549671 输出 token。
工具预取继续关闭，保留原工具时间、依赖及全部正常长尾。

## 新到达 ThunderAgent 已完成

`warm_start_thunder_20261029_r1` 于 19:43:44 完成收集，原始退出码 0。
独立完整 reader 复核实际进程退出、两个新鲜空闲快照、原注册源码的
配置观察器、全部工作及到达时刻后，得到：

| 指标 | 工具衰减 ThunderAgent |
| --- | ---: |
| 平均完整 workflow JCT | 1599.890513 秒 |
| 首次到达到最后完成 | 2323.047855 秒 |
| 全程输出吞吐 | 236.616305 token/s |
| workflow 首输出等待均值 | 75.363380 秒 |
| workflow 最大 ready 服务间隔均值 | 67.508718 秒 |
| decode worker 时间加权 batch | 8.100356 |

这些结果采用 10 个初始 workflow，其余 8 个平均间隔 60 秒的冻结到达表。
不得用旧突发 Full 的 JCT 计算相对收益。完整 reader 与可供后续匹配
比较的 decode 数据在 `experiments/reports/warm_start_thunder_20261029/`。

## CPU 与真实 GPU 组件

原生测试 103 项通过。冻结应用 `1fe85898` 的第一次联合回归
4710 项通过、2 项失败：旧 AST 测试写死了 `third_party/sglang-v0.5.20`
路径，而新私有副本未提供该依赖。保留失败报告；将忽略目录链接到
同一被测 C5 原生提交后，两项定向检查通过，完整重试 4712 项及
6 个子测试全部通过，保留此前全部 4694 项身份。没有修改测试断言、
缓存保护或生产实现来消除这两项错误。

随后真实 GPU 四个场景全部通过：旧 donor 较大场景的正常/延迟两组，
以及新增 24-token donor 与 8-token 新分配页承接 32-token 消费者的
正常/延迟两组。增长预留始终保留 4 token，逐层 KV 字节、驻留第三方、
旧 donor reader 和 ACK 所有权均核验。延迟末组中首层消费者读取早于
donor 最终 ACK。17 层、D2H8/H2D4，仅是组件资格，不是完整模型收益。

## 模型 R1 保留为未通过

使用未强制输出的真实模型调用。该次确实完成一次原生交接，但实际
donor 和恢复量同为 12296 token，新增 free extent 为 0，因而被新
验证器拒绝。原生 prefill 的 4096-token 分块使较大的 B 上下文只有
部分路径缺失，A 的私有路径又恰好覆盖该范围，不能从 prompt 总长度
推断实际发生了新增容量路径。

R1 的报告、请求、原生事件及已完成清理均保留，没有将它认作模型资格。
后续仅给模型验证用的 A 增加一次普通 6144-token 前缀调用，再延续到
16384 token，让真实 radix 路径自然形成不同边界；原模型输出、页锁、
容量判定、正式 trace 及其所有工具时间均不强制改写。后续仍要求实际
提交事件证明新增页大于零、完整恢复守恒，并比较独立上下文中的
真实生成 token 与 logprob。

C5 原生累计补丁与资格用 flavor 已单独绑定精确树；模型、位置及采样
检查通过前不注册完整 Full。整体相对所有适用 baseline 的目标仍未完成。

## 模型 R2：新路径已覆盖，数值检查仍未通过

正常前缀续写使实际 donor 为 10247 token，新分配 2049 token，
共同恢复 12296 token，输入与增长预留分别为 8/24。B 恢复与独立参考
的八个生成 token 和 logprob 全部相同。A 恢复的八个生成 token 也相同，
但第六个 logprob 相差 0.186333701，其余七个差异至多约 5.96e-7。
该次严格按原 0.05 门槛保留为未通过，不能据此归因为传输损坏或数值噪声。

R2 的 A 是 6144-token 缓存前缀再续写到 16384 token，参考则单次
prefill 完整历史，分块和初始 decode 路径不同。下一次独立参考也执行
同样的 seed、initial、continuation 请求序列，仍使用不同 cache namespace。
除了恢复后的两次输出，还比较 B initial、A seed 和 A initial 三对真实
生成结果；保留 token 完全相同、logprob 差异不超过 0.05 的原门槛。
新 CPU 用例确保仅后续输出相同不能掩盖初始独立历史错误。
这是对参考序列的修正，不修改正式 workload、原生传输或采样算法。

## 模型 R3 及新 Full

冻结应用 `e5f77eff` 的完整回归 4713 项及 6 子测试通过，保留此前全部
4712 个身份。模型 R3 实际完成 11 次普通贪心调用，新增容量交接仍为
10247+2049→12296 token。五组比较 B 恢复、A 恢复、B initial、A seed、
A initial 的生成 token 均完全相同，最大 logprob 差异全部为零。
原始请求与响应的独立 reader 另外核验同组输入、采样参数和历史序列相同，
参考与被测方的 extra_key、cache_salt、native session 均不同。
R2 的差异在匹配参考序列后不再出现，未放宽门槛。

随后 `free_extent_position_20261029_r1` 完成 24 请求的真实 GPU
token/session/native-stop 检查，24 次停止均验证；batch1/8/24 的普通
native sampler 和 position 路径检查也通过。原采样器保持不变，
不是使用冻结输出声明数学正确。

正式 Full 已注册并进入运行：
`/home/longhao/experiment/ops/warm_start_free_extent_20261029_r1.registration.json`，
端口 18861，冻结应用 `e5f77eff` / 原生树 `434c35dd`。
总请求、token、依赖、931 项分类工具时间、种子、805198-token HBM、
200GB Host、running/decode24、prefill4096/mixed-off、全部正常尾部
和新到达表均与完整 ThunderAgent 对照相同。
不重跑 native、不启动 Agentix/TokenCake，也不据运行前缀宣称 JCT 收益。

独立 `full_live_start.json` 已核验 251/3674 个完成请求的输入、输出及
原生停止条件，与新 ThunderAgent 的完整回执逐项相同；15 个 workflow
实际到达，包含 10 个 offset0 的初始组。实际 nice0 watcher1325143、
server1325666、driver1326959 和唯一 GPU scheduler1326025 由 PID、
start ticks、所有权及原始进程查询核验。新的 free-extent 开关、
demand pipeline 均实际生效，工具预取和普通前沿预取均关闭。
这些是本次运行的启动证据，完整 JCT、吞吐、迁移与重算比较仍待结束。
