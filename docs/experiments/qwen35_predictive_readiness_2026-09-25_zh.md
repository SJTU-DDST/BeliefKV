# Qwen3.5 预测式动作证据与上线门禁

日期：2026-09-25。本文记录原生 reactive 训练集和已使用过的校准集的只读诊断；
不改变 GPU 调度，不开启预测式物理传输。测试集仍未用于阈值选择。

## 1. 预测头与整组 JOIN

校准集是与训练项目隔离的 Astropy/Sphinx 66-root 批次，但它**已经用于当前
模型的区间校准**；下列诊断不等于新的独立测试。原有评估结果：
boundary accuracy 0.93445，与多数类基线 0.93444 几乎相同；FINAL 召回
0.00278，tool error 召回 0.00754。child RETURN 剩余时间的
episode-weighted MAE 约 816.6 秒；校准区间均宽约 3238 秒。

新增的 `scripts/diagnose_native_join_groups.py` 使用每个完整 JOIN 的首个
WAIT_JOIN 快照，把尚未返回的 child 边际 P10/P50/P90 按 ALL 取 max，
并和该 JOIN 的实际 parent reentry 比较。仅纳入实测 reentry 等于最后
一个 member RETURN 的 ALL-compatible 事件；数据没有明确 JOIN mode，
因此这不是 ALL mode 的严格证明。64/64 个 JOIN 具有完整提示；平均/中位
绝对误差约 1238.6/1016.4 秒，预测 P50 平均晚于真实时间约 748.7 秒。
边际分位数组合的 P10--P90 覆盖率为 87.5%，平均宽约 3966.9 秒；
**此覆盖率不是校准过的联合 JOIN 区间覆盖率**。64 个事件不是 44,258 个
独立 JOIN 样本，更不能用其调在线 latest-start。

诊断 JSON：`experiments/raw/qwen35_native_reactive_calibration_20260925_v1/`
`evaluation_calibration_diagnostic.json`、
`join_group_diagnostic_calibration.json`。

## 2. PCIe 时延证据

`scripts/fit_native_pcie_service.py` 对 train 的真实传输按方向分头拟合，
只在 calibration 上验证。保留三种**不混淆**的时延：提交至 ACK、
真实 DMA start-to-complete、提交至真实 DMA start 的排队。训练批次
H2D/D2H 的提交至 ACK 观测分别为 20,403/1,266,970 次，校准批次为
8,919/55,150 次。校准的 H2D/D2H ACK 绝对误差 P95 分别约 24.7/8.2 ms，
相对误差 P95 约 0.579/1.476；D2H 训练残差 P90/P95 覆盖仅
0.672/0.842。两批次 `start_ts_ms` 覆盖均为零；
`transfer_stream_elapsed_ms` 是无墙钟起点的设备 stream 观测，不能据此
拆出可用 DMA service 和排队时延。两方向 service/queue 都标记 unavailable。
PCIe 行缺乏可靠 workflow 归属，每个 split 实际只有一个 run 评价组，
几十万命令不能当成几十万个独立组。模型验收失败，未供在线 latest-start
使用；脚本以退出码 2 明确表示门禁拒绝。

结果：`experiments/raw/qwen35_native_reactive_calibration_20260925_v1/`
`native_pcie_service_diagnostic.json`。下一轮必须在原生传输路径采集与同一
时钟域对应的 DMA start/end 边界和提交边界、实际 extent/页形态、并发
负载及可验证的作用域归属；不能把 submit-to-ACK 或 stream elapsed
直接重命名为 DMA 服务时间。须按 workflow/episode 分组验证物理误差。

## 3. 动作级证据与缺口

旧 `action_targets.py` 的 v4 工具标签继续供旧 Qwen3 BF16 artifact 使用，
不把其中 `kv_bytes_per_token=98304` 和旧 transfer anchors 套用到 Qwen3.5
FULL/Mamba。`export_p6_action_targets.py --native-reactive-only` 导出独立的
reactive 观测：JOIN parent reentry、child 恢复、READY 至 LLM submit
的时间边界，以及能证明的资源快照、同期 native transfer。每行明确区分
估计、实测和未知。无新版可验证的混合池物理时延与 KV residency 时，
估计 H2D 耗时、latest-start、可用 KV 字节、物理动作资格、物理收益
全部为 null；旧 v4 训练读取器自动跳过这些诊断行。

校准批次的时间边界合格决策行：JOIN 44,258、child 恢复 106、
准入 20,547；去重后分别为 **64、53、18,940 个事件**。此前
`join_wait` 下一边界标签错误地把所有 JOIN 行排除；已改为同一 JOIN
完整 member RETURN 与 reentry 的资格，不把未来标签混入当前特征。
训练批次对应的独立 JOIN/child 恢复/准入事件分别为
123/51/32,774；其时间边界合格决策行分别为
86,290/102/37,224。两批次结果见各自目录下的
`native_action_observations_report.json`。这些是边界观测，**没有一例能
从未执行的 reactive 轨迹证明 PREPARE_HOST、COMMIT_CPU、PREFETCH_GPU
的反事实收益**。当前模型继续保持 `action_target_count=0`、
`predictive_action_eligible=false`、`online_eligible=false`；
不得将本诊断报告冒充动作拟合/校准产物。

## 4. 验证顺序

1. 先取得物理起止/排队、作用域、FULL/Mamba residency 和 KV
   字节/页形态的可复验遥测，并在 train 项目拟合、独立项目校准；
   按方向、负载及物理形态报告误差，不借用旧 BF16 anchor。
2. 以完整 JOIN 为单位报告时间误差和预取提前量，控制误报率；
   DONE/RETURN 确定性通知、child READY 与 scheduler 准入应各自
   单独报告命中/过时率。提升关键 boundary 与 child-return 时机，
   避免把宽区间解释成精确预测。
3. 在训练数据真实具备动作身份、可用 KV、物理 transfer/ACK、
   first service、useful/wasted 结果之后，才拟合动作头；
   使用项目隔离的校准与独立测试检查收益，censor/intervention
   轨迹不得视为自然结果。
4. 通过前述门禁后，再先跑只读 shadow（safe-point 新鲜度、语义
   intent 到可执行动作的转化、时间开销），之后做有界 GPU canary
   验证 D2H/H2D 和首次服务以及吞吐，对比同配置 reactive。
   **本次证据未通过第 1--3 步，因此不运行物理 canary。**

复现脚本：`scripts/diagnose_native_join_groups.py`、
`scripts/fit_native_pcie_service.py`、
`scripts/export_p6_action_targets.py --native-reactive-only`。
