# 需求恢复流水线的同源码 R2 重复试验

2026-10-28。本轮已进入正式FULL请求执行，尚无完整JCT或吞吐结果。
它重复原R1的冻结方案，用于检验4.94374% JCT、2.64506%全程吞吐
收益是否稳定。当前main的ACK准入重试修复不包含在本轮。

## 相同的执行对象

注册为 `ops/demand_prefill_pipeline_20261028_r2.registration.json`，
输出为 `experiments/raw/demand_prefill_pipeline_20261028_r2`，端口18696。
应用与R1相同，仍为01510ed528c2；native仍为729aa0caa7a1。
重复直接使用原冻结supervisor、应用和native checkout。

`repeat_registration.py`仅改变登记时间、run root、端口、前置注册及
重复来源/额外输入证明六项元数据。其余生产源码、CPU/GPU资格、配置、
请求/token、工具时间、容量、种子与原始停止逐项相同。原来的所有
external inputs保留，没有以新manifest替换原资格。九项元数据边界
检查及真实R1完整工作/清理/来源核对通过，记录在
`repeat_entry_r2_cpu.xml`和`repeat_r2_entry_check.json`。

18个不同项目、3674请求、145565966输入、549671输出token、72 JOIN、
V311工具时间、Device805198/Host200GB、running/decode24和seed21均保持。
工具预取、优先窗口、普通frontier restore、shared profiling及逐decode
progress probe均关闭。保留原来的非确定性FA3/flashinfer性能配置、
全部正常尾部及固定token重放。没有再次运行native或ThunderAgent。

## 实际启动证明

独立 `repeat_live_start.py/.json` 实际验证：

- nice0 watcher1008187、server1008803、driver1010120均存活，且start ticks匹配；
- GPU上唯一compute进程是本轮拥有的scheduler1009132；
- pipeline已安装并启用，progress probe、工具预取、优先窗口、
  frontier restore和profiling均关闭；
- server实际使用原来的非确定性FA3/flashinfer及原生采样后位置分派；
- 171条已完成回执与完整R1逐请求token、身份及停止条件相同。

171是完整3674请求的启动前缀，不是完整正确性、数值等价或性能结论。
原普通数学长greedy等价性诊断仍未证明；旧确定性I/O资格只因本轮
执行字节和配置完全相同而复用，不将其冒称为新数学模式的验证。

## 与新准入修复的区别

main提交5cf3bb8b包含ACK完成时清除旧满批标志、请求一次正常native
准入重试的修复；它已通过4102项CPU及六个子测试，还没有GPU/完整性能
证明。R2执行的01510ed5未包含该修复，不能把R2表现解释为新修复收益，
也不能将R2作为新修复的GPU资格。新修复与R2的源码和证明分别保留。

## 后续边界

R2结束后必须重新验证全部请求、原始停止、两次新鲜idle、实际owned
退出、迁移字节及实际服务，并在相同完整口径下比较R1、R2和已完成
V345。关键路径若变化则按实际路径报告；固定历史工具后继分析另列。
保留所有正常尾部、真实时间趋势和重复失败，不按结果挑选运行。

没有排队其他baseline、消融或新方案GPU实验。明确、可重复的双指标
优势及所有baseline比较目标仍为active。
