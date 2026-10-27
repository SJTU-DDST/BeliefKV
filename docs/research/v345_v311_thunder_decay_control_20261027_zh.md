# V345：补全 V311 工具时间场景的 ThunderAgent 对照

用户最新通知 GPU 暂时空闲，允许补全最新工具时间场景的 ThunderAgent。
2026-10-27 20:28 Asia/Shanghai 记录本次显式释放，原
20:00 至次日 08:00 预约保存为
`ops/gpu_hold_before_user_release_20261027_2028.json`，
SHA256 为
`562ed53a0af550c65c2904cc4f2b806840ace400a85c6f49150871d3d100f753`。
后续新的预约或暂停仍然生效。

## 实际运行

本轮于 2026-10-27 20:32:56 Asia/Shanghai 注册，只执行
`thunderagent_acting_decay`。模型服务就绪后已进入真实重放：

- 注册：`ops/v345_locobench_categorized_thunder_decay_20261027.registration.json`。
- 原始数据：`experiments/raw/locobench_categorized_thunder_decay_20261027_v345/thunderagent_acting_decay/`。
- 应用提交：`0b1ca5de0933ddc5ba20328fccbf013c998ef361`。
- 原生引擎树：`11b84f7c31986782be3fbe699bae2574ef092389`。
- 端口：18655。
- 已独立验证的进程：supervisor 27137、server 27645、
  GPU scheduler 28011、replay driver 29479。

PID 同时核对 start ticks、实际 argv、父子关系和 GPU context，
不是仅根据状态文件声称运行成功。首批封存的 141 条完整回执与
V330 Full 的请求、输入/输出 hash 和原生停止条件一致。
这仍是部分正确性证据，不能计算完整性能改善。

## 与 V330 Full 的一致条件

直接使用已完成的 V330 Full，不重复跑 Full，也不恢复之前中断的
847 条 ThunderAgent 回执。旧运行的预约中断状态和原始数据保留；
本轮从完整冷启动开始重放全部工作。

| 条件 | 本轮与 V330 Full |
| --- | --- |
| 项目/workflow | 相同的 18 个不同项目 |
| 请求 | 3674 |
| 输入 token | 145565966 |
| 输出 token | 549671 |
| JOIN / SPAWN / 工具调用 | 72 / 92 / 3474 |
| 工具时间表 | 相同 V311 observable-category map |
| 工具时间表 SHA256 | `0a7b7cc72f3d41de83117c0bd4857574a5a1c70c941e8a3ee7e2e15c401f624d` |
| 模型 | Qwen3-Coder-30B-A3B-Instruct，BF16 |
| Device / Host FULL token 容量 | 805198 / 2034506 |
| running 上限 | 24 |
| 采样 / server / hash seed | 21 / 21 / 21 |
| 重放 | position，保留原生停止，启用流式计时 |
| 请求传输超时 | 3600 秒，不修改实际任务长尾 |

V311 仍然只替换 931 次 `execute` 的时长，其余 2543 次保留原始工具
时间。它与旧 V269 的具体抽样时间不同，因此 V295 只能作为历史
同条件比较，不能直接成为 V330 的性能对照。

新提交增加离线绘图和文档，没有修改本轮运行时策略。
注册前已逐文件验证 V330 的全部已验证运行时源码快照，沿用其
2916 项共享 CPU 检查和共同原生 GPU 前置证据。
原生源码证据仅重新绑定了 canonical patch 的绝对位置，所有其他
源码状态及原生树完全相同。

实际运行状态确认 ThunderAgent 使用作者的 ACTING-token 衰减，
半衰期为一秒，仅影响周期 resume 选择；不折扣物理容量，不改变
直接准入的原始容量检查。本臂没有 Full 的 agent admission、
JOIN 预取、工具预取、提前卸载或 prepare_host。

独立启动审计核对 17 个公共注册字段、运行时构造结果和实际服务配置。
证据分别为：

- `experiments/reports/v345_matched_thunder_native_source_binding_cpu_20261027.json`。
- `experiments/reports/v346_v345_collecting_exact_work_20261027.json`。
- `experiments/reports/v348_v345_matched_control_live_start_20261027.json`。

## 完成后的图与评价

CPU 后处理任务 31182 已启动，等待本轮完整成功、原有实验进程退出后，
才调用已提交的绘图脚本。若实验中断则保留部分证据并停止后处理，
不发起或重启任何 GPU 实验。

- 脚本：`ops/plot_v345_after_completion_20261027.py`。
- 状态：`ops/v347_matched_decode_plots_20261027.state.json`。
- 日志：`ops/v347_matched_decode_plots_20261027.log`。
- 计划输出：`experiments/figures/decode_diagnostics_20261027_v347/`。

新图将叠加 V330 Full 和 V345 工具衰减版的 decode 墙钟输出率、
decode worker 输出率、时间加权 batch 和实际 workflow 完成数，
另给出时间加权 CDF 和共同 batch/context 分组。
保留全部启动、空闲和正常长尾，固定 60 秒分桶，不截共同尾部。
沿用[吞吐口径](throughput_metric_contract_20261027_zh.md)：
完整平均 JCT 和 makespan 为主要指标，固定输出/makespan 称为
端到端输出吞吐；decode worker 输出率不等同于纯 kernel 吞吐。

当前只确认本轮实际启动和首批正确性，完整性能及重复实验结论仍待验证。

2026-10-27 20:56 Asia/Shanghai 再次按实际 PID/start ticks 检查，
原 supervisor、server 和 replay driver 均存活，CPU 绘图任务仍在等待。
V351 新封存前缀中 2088/3674 个完整回执均与 V330 的原始工作及停止条件
一致。当前前缀的 batch 或输出率不能与 V330 全程平均值组成加速比。
证据为 `experiments/reports/v351_v345_collecting_exact_work_20261027.json`。
