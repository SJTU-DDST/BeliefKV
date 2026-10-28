# Full在线预测刷新修复的同工作量实验

独立运行：
`/home/longhao/experiment/ops/tool_online_refresh_20261027_r1.registration.json`。
执行冻结在29c58c8b678fd6615e183a2ddb95e7f185807ebf，端口18672。
共同原生引擎仍为11b84f7c31986782be3fbe699bae2574ef092389。
保留V230的18个不同项目、3674个请求、145565966个输入token、
549671个输出token、全部正常尾部和V311类别工具时间表；不重跑
native或完整V345 ThunderAgent工具衰减控制。

本轮只评价应用侧在线预测刷新及其统计维护修复。Full启用原有
JOIN/EOS预取、增量备份、预测工具卸载及准入策略，普通frontier恢复
和layer handoff关闭。在线预测不再只在工具开始时生成；其150/500/
1000ms重算间隔、100ms至多8个上下文的维护界限、过期和尾部无支持
撤销规则保留。统计刷新不逐次扫描KV物理祖先路径。

在共同引擎上单次通过3392项及4个子测试，保留3387项并新增5项。
首次注册因遗漏显式max-running24而被采样校准守卫拒绝，未生成注册
或启动GPU；补充24后注册成功，没有修改门槛、源轨迹或校准证据。

`experiments/reports/tool_online_refresh_20261027/live_start.py/.json`
核对与完整V345相同的13个工作量、工具时间、容量、引擎及服务字段，
核对实际watcher498770、server499268、GPU scheduler499614、
driver500431均存活且nice0，NVML仅有该scheduler。实际runtime确认
online_tool_timing=True、tool_timing_only=False、
layer_handoff_enabled=False及physical_disabled=False。首次独立封存
前缀验证942个完整请求与控制的token/停止证据相符，2197份预测中
101个相同实际阻塞状态取得了多次预测。它证明刷新已执行，不是最终
性能、机会召回率或可消除JCT。

nice19且隐藏CUDA的CPU绘图等待器504790已核对存活，使用专门的
`ops/cpu_plotting_20261027/bin/python`，只在完整成功和实际进程退出后
生成同工作量的decode时间趋势、分布和批次几何图：
`ops/tool_online_refresh_20261027_r1.plots.state.json`。
本轮完整JCT及全程吞吐尚未完成，不据此前缀宣称净收益或启动其他
baseline/消融。原生逐层候选在隔离分支继续CPU修复，不能改动冻结
Full及其外部输入。
