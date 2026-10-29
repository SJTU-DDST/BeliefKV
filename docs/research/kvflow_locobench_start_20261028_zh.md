# KVFlow 当前 LoCoBench 完整对照启动

2026-10-28，服务器本地时间。KVFlow 从冻结应用 `6dc4b89223cd`、
原生 common tree `11b84f7c3198` 启动唯一正式对照，不重采 native、
不重跑 V345，也没有登记其他 GPU 任务。

登记为
`ops/kvflow_locobench_v230_v311_20261028_r1.registration.json`，
端口18701，完整结果目录为
`experiments/raw/kvflow_locobench_v230_v311_20261028_r1/kvflow_observed_graph/`。

## 配置与来源

完整 V230/V311 保持18个不同项目、3674请求、145565966输入、
549671输出token、72次JOIN、Device805198/Host200GB、
running/decode24、seed21、原生停止、streaming及全部正常尾部。
源轨迹没有嵌套，18个root都使用过记录在源中的最终返回委派提醒。
V311是按可观察类别构造的合成工具时间，不称为自然工具耗时。

KVFlow继续使用已观察的动态调用图和已完成同context输入的固定前缀；
这与论文的应用预声明未来图属于不同信息合同，不声称两者完全等价。
FULL 的agent资格、JOIN优先、工具预取、预测卸载、prepare、
layer handoff和需求恢复流水线均关闭。baseline保留其自己的前缀
优先级、预取、原生容量预留与需求回退。

CPU入口单次726项通过，完整保留此前379项，1871个源码文件执行前后
相同。新增启动流程在模型前真实执行当次paper prefix GPU资格；
本次也全部通过session、各层字节、共享peer、ACK及容量检查。
旧的24请求position/停止及sampler成本证明只在四个相关模块和原生
sampler内容逐项不变后复用，不伪称为新执行。native CPU726项也是
原来相同内容引擎的来源复用，与本次应用CPU726项分别记账。

## 实际启动证据

独立 `observe_live.py` 已实际执行，`live_start.json` 保存封存前缀：

- watcher1083607、server1084292、GPU scheduler1084624、driver1085740，
  均按PID/start ticks/argv核对存活、实际拥有关系和nice0。
- NVML只有本轮scheduler的GPU context；不是仅依据登记文件判断启动。
- 九个完成回执逐项保持原输入/输出token、哈希和停止条件。
- 实际runtime已注册27个原生session请求，观察到新兼容入口运行；
  FULL策略隔离和原生服务参数检查通过。
- 与V345的九项完整工作/容量/引擎字段及工具时间合同相同。

这些是独立读取的封存前缀，不是原子全局快照、完整3674请求证明、
模型通用数值等价或最终性能结论。不计算完成子集JCT，不把早期
decode统计与完整FULL/V345直接比较。运行中的应用、原生树及登记
保持冻结。完成后仍需实际退出、两次新鲜idle、精确完整工作、
session停止、迁移/ACK与完整JCT/吞吐审计。

## 后续顺序

本轮运行期间继续CPU适配和资格准备，后续GPU资格或baseline必须等
本轮真实supervisor退出及owned清理完成，并重新核对预约。
TokenCake尚需同session入口的显式检查，Agentix仍是qualification-only，
不能把小fixture当正式baseline。当前整体目标保持active，尚未证明
超过全部baseline。
