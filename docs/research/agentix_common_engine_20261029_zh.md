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
上述新周期尚未运行，不宣称 GPU 或正式工作量合格。

## 后续约束

正式 MLFQ band、quantum、starvation ratio 与多步调度参数仍需独立声明，
不得沿用为了强制触发暂停而设置的 1/2/4ms fixture quantum。
完整实验继续使用同一 V230/V311 的 18 个不同项目、3674 请求、
549671 输出 token、工具计时、容量、seed 和全部正常尾部。
不重采 native、不重跑 V345，不恢复 TokenCake。

到达方式讨论已收敛到先复用 18 条轨迹、初始 10 个加剩余平均 120 秒
泊松到达的短周期建议；这不是已登记 GPU 配置，也不证明稳定容量。
新的到达实验仍待 Agentix 完整正式运行结束之后。
