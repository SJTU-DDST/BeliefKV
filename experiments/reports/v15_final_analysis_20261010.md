# V15 最终对照与客户端时延分析

日期：2026-10-10。两侧采集、审计、HTML及workspace清理均已完成。

## 结论

Predictive的整批完成时间缩短19.04%，完成workflow吞吐提高23.51%；
但平均、P50、P95 JCT均更差，同等饱和batch时GPU利用率和输出吞吐
仍低于native。本轮不证明已经取得稳定、可独立归因的KV调度收益。
客户端完成消息交付确实加快，不能因此认定预测模型误差已收敛。

## 固定配置与比较范围

两侧代码为feb5ee01a9f1340a694dcba442c439d08e4bd274，引擎完整patch
SHA256为dbde39b7f37977ecacd72dddf78b3da94a56fa6a0879afb1798ae55ab7f1fe63。
156任务，108个在t=0、48个在t=3600s到达，running=48，
Host200GB/FULL:Mamba=80:20，HBM Mamba/FULL=0.9，context131072，
completion8192，graph2048/reserve32，workflow14400s，fanout2--4，
seed21、temperature0。V15两侧工具命令上限仍为600秒。
顺序为predictive、native，各自冷启动。

同代码、引擎、任务和容量对照成立；全部156个workflow的观测请求
序列不同，同seed不保证同生成轨迹。提交顺序差异也不必然等于
正文内容差异。保留全部workflow，不以运行后选出的子集声称因果
加速。Completed表示运行终态，任务正确性未独立评测。

## 性能

| 指标 | Native | Predictive | Predictive变化 |
|---|---:|---:|---:|
| Completed | 156/156 | 156/156 | 相同 |
| 采集时间，s | 13410.271 | 10857.481 | -19.04% |
| 完成workflow/h | 41.878 | 51.725 | +23.51% |
| 平均JCT，s | 3295.114 | 3671.538 | +11.42% |
| P50 JCT，s | 2670.676 | 3119.630 | +16.81% |
| P95 JCT，s | 7487.913 | 7781.572 | +3.92% |
| 全程GPU均值 | 61.582% | 74.476% | +12.894百分点 |
| 输出token/s | 620.789 | 795.773 | +28.19% |
| LLM请求 | 42063 | 45361 | +7.84% |
| 工具调用 | 41555 | 45087 | +8.50% |
| 输出token | 8324950 | 8640088 | +3.79% |
| 输入token | 911336990 | 981728891 | +7.72% |
| 多轮spawn的workflow | 24 | 33 | 非同轨迹 |

配对JCT中95个predictive更慢，61个更快。多轮数量也不代表
predictive创造了更多相同任务下的缓存收益。

### 繁忙窗口

| 窗口，s | Native GPU | Predictive GPU | Native输出/s | Predictive输出/s |
|---|---:|---:|---:|---:|
| 0--3600 | 80.651% | 75.154% | 1055.933 | 977.964 |
| 3600--7200 | 68.786% | 64.491% | 868.720 | 823.048 |
| 0--6000 | 74.581% | 69.843% | 982.554 | 904.007 |
| 600--2400 | 82.328% | 75.900% | 1089.888 | 961.813 |
| 4200--6000 | 66.964% | 61.186% | 885.163 | 770.938 |

窗口使用600秒band聚合；输出token计入请求完成所在band，
不是逐decode步产出。600--2400秒time-weighted decode batch为
47.353/47.277，4200--6000秒为47.162/47.186。Batch几乎相同，
因此不能用“predictive batch较小”解释全部GPU利用率差异。
t=3600/5400/7200时两侧完成数分别77/71、91/82、134/126。

### 收尾长尾

Native末条pylint-dev__pylint-6528 JCT为13362.885秒，predictive
为5573.747秒。前者75个151--162秒命令都以31退出，执行锁等待
接近零；其中同一command hash重复64次、P50约155秒，
累计9961命令秒。随后另一个短命令重复164次、P50约0.511秒。
10200--12600秒native已完成155条，GPU利用率仅约0.11%--0.16%。
该workflow自然完成，没有deadline或强制终态。

整批完成吞吐优势主要由这种长尾差异支配，不能证明预测迁移使
高负载服务更快。180秒上限不会终止155秒调用，不新增重复调用
guard来追求本轮时长改善。

## Child结束到parent服务

实际路径是原生生成结束/EOS、HTTP流被客户端消费、LLM_RESULT、
child RETURN、最后child解锁JOIN、parent提交、原生到达、首次
worker服务。前几段是客户端交付及harness开销，最后一段含服务端
准入等待；原生到达不能当GPU已经执行。各段分位数不可相加。

去重后的V14/V15 JOIN-prefetch关联child请求数为74/75个，
不是全部child或相同请求的严格配对。

| 区间，ms | V14 P50/P90 | V15 P50/P90 |
|---|---:|---:|
| 原生完成→客户端result | 1375.965 / 6448.015 | 254.628 / 1074.243 |
| 原生完成→finish chunk | 1242.434 / 6353.589 | 209.522 / 1029.513 |
| 客户端result→RETURN | 75.352 / 268.003 | 15.829 / 72.362 |
| JOIN→parent提交 | 627.861 / 1668.027 | 25.357 / 179.675 |
| Parent提交→原生到达 | 466.850 / 1516.857 | 127.719 / 739.829 |
| 原生到达→首次worker服务 | 137.744 / 11162.522 | 160.298 / 14512.155 |
| Parent提交→首次worker服务 | 777.090 / 11950.681 | 352.471 / 14930.196 |

V15两侧共用的修复：

- 已转为wire schema的消息直接合入请求JSON，省去SDK重复遍历
  完整对话历史。
- 保留SDK SSE解码、错误处理和关闭机制，省去每帧SDK对象构造
  后立即转换回字典的往返。
- 工具参数的非对象碎片省去不可能成功的逐字符JSON修复，
  最终合并参数仍沿用原语义。
- Child终态session关闭交给独立共享线程池，最多32 worker，
  HTTP不阻塞child future和parent下一请求。Workflow收尾仍完成
  本次关闭任务与审计；HTTP200只证明派发被接受，不是物理回收ACK。

这组修改没有重新训练预测权重。V14/V15提前RETURN0--500ms的
节点动作由23/105变为57/99，V15为95次提前RETURN、82次ACK
提前JOIN，submit lead中位数309.66ms。但是87/99 JOIN动作
在原生EOS之后启动，只有12次estimated-work早于EOS。
交付延迟改善和动作时机改善已观察到；自然RETURN的独立语义
预测误差是否下降仍需按EOS前因果输入单独检验。

## 迁移和控制开销

| 来源 | 节点命令 | 总字节，GB | FULL传输/确认复用，GB | ACK→服务P50/P90，s |
|---|---:|---:|---:|---:|
| JOIN提前恢复 | 99 | 5.335 | 0.893 / 0.743 | 0.755 / 17.686 |
| 工具提前恢复 | 77 | 0.724 | 0.338 / 0.293 | 1.035 / 15.326 |
| 已提交需求handoff | 15132 | 118.265 | 67.205 / 67.047 | 0.0257 / 0.1038 |

真正JOIN/tool提前恢复176次、6.059GB，其中FULL1.230GB、
确认复用1.036GB；handoff不能作为预测提前量。
Predictive原生H2D3.664TB、高于native3.268TB；加上受控恢复
共3.788TB。提前FULL仅占predictive原生FULL1781.703GB的0.069%。
首次服务前原生再次加载预取FULL为JOIN11、tool2、handoff23个
节点目标；共享前缀和node事件数量不能换算独立请求数。

PREPARE846 ACK、8.361GB FULL，196次关联后续恢复；
62个压力回收事件中58个关联先前PREPARE。715次后续同node/pool
D2H前均存在Host FULL驱逐，支持回收后补传，不证明覆盖有效
Host副本。没有观察到恢复的备份不能全部叫作浪费。
旧前缀缺失代理native/predictive为0.15318%/0.15376%，
uncached prompt比例4.7835%/4.7336%，尚无明确旧有用KV重算改善。

Predictive被埋点的exclusive Python wall累计1283.355秒，
其中JOIN PREPARE225.677、reentry164.802、opportunity sampling
140.213、control drain114.597、admission113.393、semantic updates
76.482、terminal sampling66.802秒。它们避免嵌套重复计算，
但不等于CPU cycles或GPU全部空闲时间，不能直接从JCT扣除。
H2D transfer-stream累计native/predictive101.699/113.504秒，
submit→ACK累计也含排队，不能用累计迁移秒数推导oracle上界。

## 后续180秒配置与当前优先级

所有后续实验单条sandbox命令执行上限为180秒，超过上限的
启动参数、配置和模型显式timeout统一按180执行。实际上限与
requested timeout写入审计，新对照计划冻结180并核对双侧。
原V15 manifest仍为600；LLM请求600和workflow14400是独立预算。
同一sandbox的执行锁等待单独统计，不能当作命令执行时间。

最终自然结束命令native/predictive P50为0.343/0.330秒，
P95为2.094/2.222秒，P99为6.776/4.132秒，最大162.381/115.295秒；
exit0最大96.902/115.295秒。180不会截断本次观测的自然结束
命令，但超时改变仍可能改变未来模型轨迹。12/16条124/137候选
单列，137本身不证明timeout；未来工具标签按180秒预算处理删失。

下一轮先验证已提交的控制路径减负、issue到ACK驻留保护和
FULL缺失前缀合批，重点看高负载GPU利用率、JCT与实际FULL消费，
同时保持模型只预测完成/剩余工作、runtime决定迁移时机的分工。
不把全程吞吐改善、CPU基准或ACK数量当作最终收益已达标。

## 证据

实验根目录：
`experiments/raw/qwen35_native_predictive_replenished_108plus48_20261010_v15`。
包含comparison.json、native_policy_comparison.json、
workflow_trajectory_comparison.json、timelines/native.html和
timelines/predictive_h2d.html。两侧workspace各删除156、保留0，
审计与轨迹保留。

补充报告：
`experiments/reports/v14_join_pipeline_first_service_v2_20261010.json`、
`experiments/reports/v15_join_pipeline_first_service_v2_final_20261010.json`、
`experiments/reports/v15_prefetch_lifecycle_residency_final_20261010.json`、
`experiments/reports/v15_prepare_host_lifetime_final_20261010.json`、
`experiments/reports/v15_tool_timeout_distribution_final_20261010.json`。
