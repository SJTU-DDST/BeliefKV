# Agentix 迁移到共同原生引擎

2026-10-29。正式 Agentix baseline 尚未启用；本页记录适配与验证，
不把先前六请求模型检查视为完整性能结果。

## 变更范围

先前通过字节、暂停恢复及模型数值检查的 c489 原生树，与共同 baseline
树 11b84 相差 11 个文件，包含缓存、传输及空闲页栅栏差异。
不能直接将它作为只有调度政策变化的共同引擎对照。

新树 `25155f0701c4c3707add0901437833281590016b` 保留 11b84 的全部
计算、KV 缓存、Host 传输、空闲页栅栏与采样实现，仅修改两个文件，
新增 9 行、替换 2 行：

- `Scheduler.init_beliefkv_runtime()` 在绑定 cache 后绑定 scheduler，
  使 Agentix 接管已有批次选择入口和模型执行计时。
- 原生 `retract_decode()` 增加默认空的回收前回调，覆盖普通 retraction
  与 OOM abort。Agentix 拒绝没有检查点的回收；不改原生 victim 排序。
  正常原生路径及其他未提供回调的政策保留原行为。

精确补丁为 `patches/sglang-agentix-common-runtime-20261029.patch`，
SHA256 `0da0df6abd9ee3be9b0b7b4e3a905d23b166d451fb022f782bc6528735d2839e`。
`beliefkv/experiments/agentix_engine.py` 将这些可选语句投影移除后，
要求完整 AST 与共同引擎相等，并校验两个原生文件的精确字节。
正式 replay 兼容声明同时保留从自然源到共同引擎的既有校验，
不能用这一声明替代新的实际 GPU、模型和位置采样检查。

## 当前证据

`common_engine_focused_r1.xml` 有 24 项检查通过、无失败及跳过，
覆盖精确投影、错误绑定拒绝、真实 native 构造器顺序、Agentix 生命周期、
模型 qualifier 与配置隔离。它是 CPU 验证，执行与传输使用替身。

完整 CPU 入口位于
`experiments/reports/agentix_common_engine_20261029/run_bound_cpu.py`。
它合并已有 782 项 Agentix 与 726 项共同 causal/paper 用例，按完整测试
身份去重并验证全部保留，再加入本次适配检查。结果记录实际用例数，
不能将两个历史数字直接相加。

`run_cycle.py` 将冻结源码上的完整 CPU、四项实际小池 GPU 迁移检查、
roomy 与 pressure 的非强制模型数值检查顺序执行，逐项保留退出和清理。
新的 qualifier 接受且仅接受原 c489 和本次精确原生树，不放宽到任意引擎。
实际 `cycle_r2` 的完整 CPU 回归通过 1124 项，保留旧 782 项 Agentix
和 726 项共同对照的全部身份；它们去重后的并集是 1109 项。
1889 个应用输入文件及精确原生树在运行前后均一致。
四项实际 GPU 字节及压力检查也已通过。roomy 模型入口因旧事件 socket
存在而在启动模型之前拒绝，保留原失败及空进程清理，另选全新端口执行，
不删除旧 socket 或重复已经通过的 GPU 组件。后续周期在运行 CPU/GPU
之前检查两个不同模型端口的事件 socket，避免复用同一地址。

新树的两组定长贪心模型检查随后在新端口完成：

| 模型 fixture | 输入/输出 token | Device token / Host | 实际暂停/恢复 | token / logprob |
| --- | --- | --- | --- | --- |
| roomy | 24576 / 64 | 805198 / 64GB | 45 / 45 | 全相等 / 最大差 0 |
| pressure | 24000 / 512 | 32768 / 8GB | 227 / 227 | 全相等 / 最大差 0 |

两组分别有三次并发实际调用和三次串行独立参考，共 12 次真实模型调用，
均没有使用输出 token 强制器或人为拷贝延迟；固定生成长度只服务于数值检查。
`review_model.py` 另从原始请求/响应复算，检查相同输入、不同 session、
不同缓存 namespace、程序归属、源码及组件哈希、两次新鲜 idle 和实际
owned 进程退出。pressure 的 91 次 backing drain 是控制轮数，不是
91 次独立 Host 耗尽。原始记录在 `roomy_fresh_port_r1/` 和
`pressure_fresh_port_r1/`，独立复核为其中 `model_review_r2.json`。
这些检查通过不代表正式 batch24、采样位置协议或完整 3674 工作量合格，
更不证明 Agentix 或 predictive 的性能优劣。

另保留 `frozen_r1.json` 对应的启动前导入失败：操作工作树位于
`.worktrees` 中，导致冻结目录嵌套，超出现有机器辅助模块的根目录约定。
调整操作工作树位置后生成独立 `frozen_r2.json`；没有修改旧冻结源码。

## 后续约束

正式参数已单独预声明于
`configs/baselines/agentix_atlas_locobench_policy_20261029.json`：
三档队列，已获服务边界为 10/100 秒，时间片为 0.5/1/2 秒，
等待/服务比门槛为 10，每 16 个 decode step 重新选择，
context/running/growth 为 262144/24/100 token。
这些是明确披露的原生移植参数，不是已核验的作者默认值或实测最优值。
已读论文 4.2.2 给出离散队列、beta 比值和 N-step 策略，但没有查到这组
参数的作者默认数值；不能把策略重实现写成原作者 artifact 的直接运行。
半秒起步的时间片使调度尺度明显大于为了触发暂停而设置的 1/2/4ms
fixture，保留多步执行并用分档服务史控制续接优先级。
新 `load_formal()` 拒绝验证 fixture、未声明参数、未来输出长度及不足的
增长预留；11 项 CPU 检查通过，正式工厂入口仍未启用。
完整实验继续使用同一 V230/V311 的 18 个不同项目、3674 请求、
549671 输出 token、工具计时、容量、seed 和全部正常尾部。
不重采 native、不重跑 V345，不恢复 TokenCake。

到达方式讨论已收敛到先复用 18 条轨迹、初始 10 个加剩余平均 120 秒
泊松到达的短周期建议；这不是已登记 GPU 配置，也不证明稳定容量。
新的到达实验仍待 Agentix 完整正式运行结束之后。
