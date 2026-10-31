# 复用70秒控制的1秒合并 oracle 诊断

## 用户最新决定

2026-10-31，用户要求先试1秒或2秒候选合并窗口的oracle，复用已经完成的
70秒到达控制。本轮选择1秒。不再运行12初始/70秒校准，也不再测试80秒。
这是显式的诊断授权，取代此前必须先通过供给门槛才能启动oracle的顺序。
原负载拒绝结论保持不变，不把这轮解释为已经合格的稳态主实验。

复用的控制是8初始、平均70秒到达，56个完整原项目、12预热/18固定测量/
26背景，seed35/permutation54。18个测量成员完整完成，2819条请求、
362072个输出token，平均JCT为931.500654秒；共同墙钟窗口输出吞吐
260.459582 token/s。完整回执、新鲜排空和实际退出均已有证明。

此前12初始/80秒控制已按用户要求停止：14个到达、256条完成请求，
0/18测量完成，完整测量JCT为null。其排空及实际退出已核对，保留原
中断证据，不用于本次性能对照。随后12初始/70秒仅生成配置，没有启动。

## 合并与排序

首次注册、普通续接和sibling通知积累到同一1秒准入周期；窗口由首个
通知开启，重复通知不顺延截止时间。周期恢复也检查这个窗口，避免
2秒工作集控制提前授予普通候选。每次到期在下一个native边界选择，
保持已有active agent连续性，不为短任务引入新的主动抢占。

已ready的受保护JOIN parent可以绕过普通窗口；JOIN/EOS的提前恢复、
原生容量及DMA安全检查保留。窗口关闭时，受保护JOIN的补位不能顺便
授予其他普通候选。相同窗口内跨原恢复类别按oracle剩余模型与工具
关键路径排序，类别只用于后续平局。R5组合评分是另一独立分支，
本次没有宣称已经验证完整R5。

默认窗口仍为0，即保留原来的下一native边界合并。只有显式oracle
artifact可以选择1000或2000ms；普通Full和纸面baseline不受影响。
工具overlay、预测先验、Host200GB、Device805198 token、
running/decode24、chunk2048、mixed关闭、2秒控制及500ms兜底不变。
工具预取、普通恢复绑定及逐层handoff继续关闭。

## 证据与解释边界

声明文件`configs/experiments/oracle_mean70_coalescing_diagnostic_20261031.json`
绑定同一完成控制、负载决策路径和哈希，只允许此次已知的背景尾部覆盖
不足与ready-paused供给不足。完整测量JCT、token守恒、原题身份、
容量、原生安全检查及退出证据不豁免。准备、启动与最终比较均检查
同一声明，原始`accepted_for_oracle_diagnostic=false`文件不改写。

主比较包含1秒候选合并和oracle排序两个变化，不能将全部差异归为
工作量排序收益。观察实际竞争、集合变化、压力后保留及同attempt的
新有效native batch。旧控制没有新增的周期外竞争观测，相关计数保持
unknown，不能当作0；oracle的新记录可与真实服务关联。

固定18测量成员完整结束后停止非测量工作；JCT不截尾，吞吐使用同一
412.944491--1841.814938秒窗口并计入背景。既定诊断数值目标为平均JCT
至少降低5%、共同窗口吞吐不少于控制的98%；背景覆盖与稳态局限另外
列出，即使数值达标也不自动接受正式性能结论。

初次定向126项通过。完整源码绑定CPU检查随后通过6011项及23个子测试，
保留全部5964项既有身份和2229个源码输入，执行中应用与原生源码保持
不变，无失败、错误或跳过。证据目录为`oracle_mean70_window1s_cpu_r1`。
oracle进度核验匹配原对照9403条已完成请求、51个自然结束workflow，
并证明测量组2819条请求/362072输出守恒，5个未完成背景没有伪装为完成。

配置位于`oracle_mean70_window1s_r1/full.json`，进度证据为同目录
`progress.json`。干净冻结、GPU组件检查及实际oracle运行仍按顺序完成；
CPU准备不代表已有GPU收益。

## 实际启动与自动结束

从干净冻结提交`020527dd`启动，原生树仍为
`434c35dd66211d0a230717c88bf1452056769a40`；与旧控制的原生源码、补丁
字节和状态逐项相同，仅规范补丁所在路径及派生状态摘要改变。
GPU入口、绑定复核和启动证明在`gpu_entry_oracle_mean70_window1s_r1`。
24条token/session/native-stop检查全部通过，1/8/24采样检查通过，
随后oracle Full已经实际服务。

登记为：
`/home/longhao/experiment/ops/workflow_original56_mean70_oracle_window1s_full_20261031_r1.registration.json`，
端口18930。独立前缀证明已核对216条完成回执，实际GPU owner2961718、
nice0服务、完整原采样/停止/token/session核验，以及运行时1000ms窗口、
JOIN绕过与active连续性。工具预取和逐层handoff均关闭。此为启动前缀，
没有完整oracle平均JCT或性能结论。

固定测量结束观察器已实际运行，登记后缀为
`workflow_original56_mean70_oracle_window1s_measured_completion_20261031_r1.registration.json`；
结果观察器后缀为`workflow_original56_mean70_oracle_window1s_results_20261031_r1.registration.json`。
两者实际PID分别2962116/2962442，均验证nice19、CUDA隐藏和OMP/OpenBLAS
单线程。结果任务只在完整测量回执、排空、实际退出及测量审查结束后执行：

1. 使用`review_sustained_oracle.py`核对同一18个完整JCT与共同窗口吞吐。
2. 关联周期外选择与实际有效native服务，并保留原控制缺少观测的限制。
3. 分开统计共同窗口与整个测量跨度的周期选择。
4. 生成相同30秒分桶的decode/全部输出吞吐趋势和18项配对JCT图。

结果文件预定为`oracle_mean70_window1s_r1/comparison.json`，图目录为
`common_window_figures`；目前尚未产生真实比较结果。绘图自检使用原控制
与其自身，只证明读取/绘图路径可用，不是oracle结果。自动任务不启动
任何后续GPU实验。
