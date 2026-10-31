# 持续到达负载上的 ThunderAgent acting-decay 对照

## 执行顺序

用户要求在继续优化 oracle 的同时，先运行 ThunderAgent 对照，
比较持续到达 workload 下 predictive 的完整 JCT 与吞吐。
这次明确授权覆盖此前“oracle 明显改善前不运行 baseline”的顺序限制。
GPU 先运行 ThunderAgent；新 oracle 继续 CPU 开发，不登记自动 GPU 后继。

选择已经具备完整 predictive 对照的 12 初始 workflow、平均 70 秒到达表。
该组在固定共同窗口内有 507/1524 个 ready-paused 状态样本，
完整测量区间记录 135 次周期逻辑容量竞争，适合检查本设计的作用条件。
旧 8＋70s 记录的供给更少，但两次服务实现不同，不能把差异全归因于到达方式。
本次选择依据既有供给/压力及可复用对照，不按 ThunderAgent 输赢选择配置。

## 匹配条件

复用 56 个不同原项目、9858 个源请求、1305083 个源输出 token，
保留原任务、依赖、停止规则和现有工具时长映射，不修改或缩放工具时间。
固定 12 warmup / 18 measured / 26 background，完整测量原生工作为
2819 请求、362072 输出 token。

两臂使用相同到达原点偏移表、模型、引擎树、BF16 FULL KV、805198 个
Device token、2034506 个 Host token、200 GB Host、running/decode24、
2048 prefill 和 mixed-off。独立 live 验证已核对实际容量 census 完全相同。
共同吞吐窗口仍为 55.6927713505 至 1638.9548502657 秒，
包括全部背景输出；测量成员保留完整尾部。

比较使用既有完整 predictive 控制臂：
`ops/workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json`。
平均完整 JCT 1052.994585 秒、共同窗口输出 301.891902 token/s，
不是新增预测器或 calibrated oracle 的结果。

ThunderAgent 保留作者 acting-decay 版本的 invocation/program 身份、
五秒控制、一秒 half-life、仅周期恢复折扣以及完整原始容量检查。
关闭 Full 的预测卸载、prepare_host、JOIN/EOS 预取、工具预取、
工作量排序、sibling 扩展和逐层 handoff；不加载工作量 oracle。
共同原生免费页栅栏仍为两臂相同设置。

## 实际 GPU 启动

配置：
`experiments/reports/remaining_original_projects_20261030/thunder_initial12_mean70_prepare_r1/thunderagent_acting_decay.json`。
它由完整 predictive 配置通过既有 paper isolation API 生成，
清除实际工作量模型与 oracle 声明，不只是改 arm 名称。
实际构造及静态匹配检查通过。

新 GPU position/session/stop 检查 24/24 通过，全部为原生 stop，
batch 1/8/24 采样检查通过，原生引擎树仍为
`434c35dd66211d0a230717c88bf1452056769a40`。

正式注册：
`/home/longhao/experiment/ops/workflow_original56_initial12_mean70_thunder_decay_20261031_r1.registration.json`。
冻结服务源码仍为 `9316152914dc14fec678b1da4de0919b4ecf5a23`，
沿用其已绑定的 6135 项服务资格，端口 18936。
CPU 分析器修复不会热修改这份正在运行的服务。

`gpu_entry_thunder_initial12_mean70_r1/live_thunder_r1.json` 验证实际处于
`running_thunderagent_acting_decay`，87 个完成请求前缀收据、
全部 GPU 服务所有者 nice0、唯一 GPU scheduler 3138882、
acting decay 开启且 `future_information_used=false`。
前缀和启动资格不是完整 JCT 或性能提升。

## 相同测量组收尾

旧收尾与完整执行核验器仅接受 Full，此次扩展为显式单臂
Full 或 ThunderAgent acting-decay，不接受模糊多臂或其他未声明方法。
仍在全部 18 个固定成员自然完成后才停止非测量背景，
保留精确收据、两份新鲜 idle 快照、所有权进程退出和右删失证明。

Paper 臂缺少 Full 的 ready-paused/periodic-grant 日志，
不能将其填成零或当成 Full workload 检查通过。
Paper 收尾只运行共享 JCT/输出审计，明确标记 Full 专属诊断未重算；
比较报告保留 predictive 原有背景覆盖和前缀丢失失败项。

新增 `review_predictive_thunder_cohort.py` 验证相同流量及完整测量原生工作，
报告正收益为 predictive 相对 ThunderAgent 的改善，
同时保留 P95、逐 workflow 差值、背景右删失和共同窗口吞吐。
它不使用 oracle/control 误导性标签，不选择最先完成成员或裁掉测量尾部。

77 项定向检查通过；最新源码绑定共享回归为 6236 项及 23 子项，
保留原有 6212 项、2252 个源码输入，执行中源码与原生树不变，
无失败、错误或跳过。GPU 服务仍使用原冻结版本。

CPU 分析提交 `68d37388` 已从干净 checkout 冻结，
两个独立观察器实际存活、nice19、CUDA 隐藏、OMP/OpenBLAS 单线程：

- 收尾观察器 PID 3146824，注册
  `ops/workflow_original56_initial12_mean70_thunder_decay_measured_completion_20261031_r1.registration.json`；
- 结果观察器 PID 3147250，注册
  `ops/workflow_original56_initial12_mean70_thunder_decay_results_20261031_r1.registration.json`。

首次独立状态复验为 17 个已到达 workflow、0/18 测量完成。
这只是前缀，不是吞吐或 JCT 结果。结果观察器在收尾、完整收据、
新鲜排空、实际退出和 cohort 报告完成后才比较。输出目录为
`experiments/reports/remaining_original_projects_20261030/predictive_thunder_initial12_mean70_comparison_r1`。

绘图使用单独固定 hash 的
`/home/longhao/experiment/ops/plot_workflow_cohort_comparison_20261031.py`，
明确标注 ThunderAgent acting-decay / Predictive，生成 decode/全部输出时间曲线、
完整配对 JCT、PNG/PDF/CSV。使用旧 predictive 对照两次的自检图已目视检查；
它只是绘图自检，不是 baseline 对比或新收益。
历史绘图程序和已完成 oracle 证据不改写。

后续固定共同窗口已经走完，封存遥测前缀
`thunder_initial12_mean70_prepare_r1/common_window_live_r1.json`
记录 ThunderAgent 全部输出 292.354630 token/s，
既有 predictive 为 301.891902 token/s，前缀差值为 +3.262227%。
该事件前缀仅有 11/18 个固定测量成员完成，完整 JCT 改善仍为 null。
窗口计数使用各阶段的 `output_token_delta`，包括背景输出；
最终收据核对、排空及全部测量成员完成后必须重新计算，不将它当成完整性能结论。

## Oracle CPU 后续

当前优化候选仍为 calibrated model/tool oracle 与 JOIN 组合选择：
默认六候选、按 sibling 扩展到八、0.5 ms 搜索软预算；
两秒普通窗口和即时 A→B 预测卸载保留。
参见 [组合设计](../design/calibrated_global_join_combination_20261031_zh.md)。

最新准备为 `calibrated_combination_initial12_mean70_prepare_r3`。
r1/r2 只保留为 CPU 准备；r3 修正继承的 qualification sidecar，
使 kind、模型路径和 hash 指向实际 v2 artifact，而不是旧 v1 文件。
没有改变工具时长、到达表或运行中的 baseline。
完整旧对照事件回放再次验证固定测量工作守恒；尚未登记该候选的 GPU 实验。

后续组合搜索的增量容量预检和已有 decode 成本缓存已通过独立参考比较，
CPU0 顺序微基准中六/八候选完整搜索 P50 缩短约 7%/18%，
八候选触及软截止的比例从 82.67% 降至 62.00%。
完整共享检查为 6240 项及 23 子项，保留先前 6236 项；
这仍是 CPU 候选结果，不是新 oracle GPU 或 JCT 收益。
