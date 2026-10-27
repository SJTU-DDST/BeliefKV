# Free-JOIN FULL 完整结果

`reference_join_free_admission_20261027_r1` 已完整结束。冻结应用
3a23fe1028ce、native 树 11b84f7c3198；复用完整 V345 工具衰减版
ThunderAgent，不重跑 native 或 reference。全部 18 个不同项目、
3674 请求、145565966 输入 token、549671 输出 token、原生停止、
V311 合成工具时间、依赖、容量、流式协议和种子均核对一致。

`experiments/reports/reference_join_free_admission_20261027/complete_review.py`
独立检查完整 token 身份、冻结源码、真实进程退出、两个新鲜 idle
遥测快照和图表输入摘要。最终 scheduler 留有已退出的 zombie PID；
没有活跃 serving/driver/watcher 或 GPU 工作。所有正常长尾保留。

## 完整性能

| 指标 | Free-JOIN FULL | V345 工具衰减 ThunderAgent |
| --- | ---: | ---: |
| 平均 workflow JCT/秒 | 1653.668345 | 1697.583645 |
| 全程 makespan/秒 | 2321.406016 | 2355.344551 |
| 全程输出 token/s | 236.783654 | 233.371801 |
| decode worker 输出 token/s | 347.328338 | 340.158312 |
| worker 区间加权 batch | 8.081555 | 8.108286 |
| 实际 H2D/十进制 GB | 2657.864417 | 2809.042010 |
| 实际 D2H/十进制 GB | 1078.612820 | 2255.862596 |
| workflow 最大 post-first ready 输出间隔的均值/秒 | 91.578837 | 73.449107 |

平均 JCT 改善 2.58693%，全程输出吞吐改善 1.46198%。17 个 workflow
更快，python_web_cms 慢 66.417252 秒。相较上一轮 consumer-fix FULL，
平均 JCT 和全程输出吞吐均略差。这些单次不同配置不能作为重复验证，
尚未满足明确、可重复的双指标优势，其他 baseline/消融继续延期。

decode worker 区间包含运行时及等待，不是纯 kernel 时间。相同固定
输出量下，全程输出吞吐是 makespan 的另一表达，不能当独立证据。
服务间隔约恶化 24.68%，保留此项，不能只报告首服务或平均 JCT。

## 迁移与等待

独立 JOIN 事件审计观察到 67 个 ticket；其中 56 个在完成前有正 FULL
计划，11 个实际提前提交并确认复用，共 53.658845184 GB。包含完成后
恢复的全部受控 JOIN 复制为 92.888236032 GB、449 个命令，均确认复用。
这是实际复用字节，不是被隐藏的 GPU 时间或可删除 JCT。

最终计数有 7 次 free-JOIN 新资格授权、195 次完整逻辑容量不足。
冻结代码没有逐授权身份日志，不能把 7 次授权直接对应 11 个提前恢复，
也不能把其他拒绝都归因于逻辑容量或工具预测。

工具预取没有实际复制。工具 offload 的额外预测路径仅释放 56 tokens，
即 5505024 字节；
原生分配压力工具回收与之分开，不应归因于新增 JOIN 授权。
完整工具后继中有 564 个排队至少 1 秒，其累计等待的采样持有区间
约 93.18% 为 inactive、6.55% 为 active，其余未知。这是状态样本，
不是精确转换日志、独占原因或可消除的 JCT。

FULL 的 21 个采样 load-consuming batch 合计等待 3959.456 ms，
V345 的 18 个为 2662.835 ms。样本数和请求组不同，既不能据此断言
全量恢复等待回退，也不能乘采样间隔扩张为全程开销。没有证据证明
增加预取字节已经减少了暴露层等待。

图表为 `experiments/figures/reference_join_free_admission_20261027_r1/`
下的 timeline、time-weighted distributions、geometry 三组 PNG/PDF，
已逐图检查，保留 60 秒固定窗口、尾部实际宽度及普通长尾。

## 后续边界

main 已在 CPU 反例中确认额外 JOIN 授权使用缓存的 streaming 大小可能
漏记新生成 token。冻结 3a23fe10 未包含随后“授权前刷新”的修复；
原生物理分配一直保留安全检查，但旧逻辑记账限制必须披露。

新 sibling 共同准入及刷新修复属于下一候选，只经过 CPU 验证。
本轮没有改动 engine allocator，没有逐层 D2H ACK 后复用同层空间，
也没有 B 尚未获得全部跨层槽位时开始计算的机制。
