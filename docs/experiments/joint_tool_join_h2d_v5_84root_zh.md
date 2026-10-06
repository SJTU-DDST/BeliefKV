# v5：84-root 工具与 JOIN 预测恢复复核

复核日期：2026-10-06。实验于2026-10-05启动、2026-10-06结束。
原始目录为
`experiments/raw/qwen35_joint_wait_h2d_ab_84root_20261005_v5/`。
本次仅读取已完成实验，不启动重复pair、不调整运行代码或模型。

## 1. 配置与完整性

84-root单波、running=48、Qwen3.5-35B-A3B BF16、
SGLang 0.5.20、Device Mamba/FULL=0.9、Host 200 GB/NUMA node 1，
context=131072、completion=8192、graph=2048/预留32步、
workflow=14400秒、temperature=0、seed=21。
两侧均开启通知/收尾优先级、PREPARE及真实压力回收；
predictive额外开启语义预测控制和JOIN/工具提前H2D。
因此不是未经修改的native baseline，也不是仅DMA变化的严格消融。

启动commit为 `c219604`，包含修复 `1aba1be`。
实验中的文档提交和旧文件清理没有改变443个运行文件的指纹：
`4907f0437b65489812eca98b07035952cadf87cab6a00c6a1241cb63a72c4a55`。
2026-10-06 01:01:59至01:02:21在predictive阶段有低I/O优先级
旧文件清理；作为背景事件保留，不回填或修正性能指标。

两侧server telemetry均为dropped=0、failed=0、writer_error=null，
终态pending request/batch均为0，physical路径未disabled。
reactive退出0；predictive退出1来自一个incomplete，不是serving崩溃。
所有summary、ACK、first-use和窗口审计均已产出。GPU已释放。
脚本已删除84/83个已归档完成workspace，保留incomplete现场。

## 2. 整轮结果

| 指标 | Reactive | Predictive |
| --- | ---: | ---: |
| completed/incomplete/error | 84/0/0 | 83/1/0 |
| 整轮耗时，秒 | 4398.66 | 4773.55 |
| completed workflow/小时 | 68.75 | 62.59 |
| 共同完成83个任务的平均JCT，秒 | 1929.83 | 1759.18 |
| completed JCT P50，秒 | 1650.95 | 1550.62 |
| 原summary GPU利用率，% | 76.50 | 81.66 |
| output tokens | 3060568 | 3005725 |
| output token/s | 695.80 | 629.66 |
| LLM调用 | 16426 | 14781 |
| 工具调用 | 15961 | 14492 |
| submitted input tokens | 364191760 | 321060814 |

完成吞吐为-8.95%；共同完成任务平均JCT为-8.84%。
predictive的LLM/工具/input/output工作量分别减少10.01%、
9.20%、11.84%、1.79%，84条请求序列均有分歧。
不能把JCT降低直接归因于预取，也不能只用路径分歧解释机制失效。
没有独立patch grading；本表是completed-workflow吞吐，不称正确任务吞吐。
所有任务和负结果保留，不筛掉分歧、长尾或incomplete。

## 3. 多轮派发

两侧均有113个SPAWN、113个JOIN satisfied，child取消为0。
每轮仍只有一个child，尚未验证多child barrier预测。

| 派发轮数 | Reactive workflow数 | Predictive workflow数 |
| --- | ---: | ---: |
| 1 | 65 | 69 |
| 2 | 9 | 5 |
| 3 | 10 | 9 |
| 7 | 0 | 1 |

多轮workflow分别19/15个，JOIN后的再派发均29次。
predictive的django-10554为7轮。多轮已实际出现，但不能把
少量多轮或串行child称为充分的并行fanout覆盖。

## 4. H2D来源与提前量

| H2D来源 | Reactive | Predictive |
| --- | ---: | ---: |
| 全部batch/GB | 184 / 24.113 | 162 / 20.319 |
| 原生恢复batch/GB | 184 / 24.113 | 145 / 19.021 |
| 预测恢复batch/GB | 0 / 0 | 17 / 1.298 |
| JOIN预测batch/GB | 0 / 0 | 11 / 0.854 |
| 工具预测batch/GB | 0 / 0 | 6 / 0.444 |

无原生/预测混批。预测字节约占predictive全部H2D的6.39%。
不能把原生字节下降全部算作预测收益，因为需求和路径不同。
17个ACK均有后续同context首次服务记录；其中11个FULL首次复用，
6个确定未复用。10个Mamba有单请求COW forward复用证明，
另7个没有该证明，不全部自动计为浪费。
issue-to-submit P50约1.63 ms、最大4.38 ms，
enqueue-to-submit P50约1.43 ms、最大3.37 ms；
submit-to-ACK P50约6.50 ms、P90约86.28 ms。
上轮即时提交修复保持有效，没有重现数百毫秒的load_queue等待。

### JOIN：有效消费，但仍是EOS后信号

11次均在RETURN/JOIN前submit及ACK，FULL全部首次复用。
提交提前量如下，单位ms：

| workflow | 提前RETURN | FULL复用 |
| --- | ---: | --- |
| django-12754 | 1666.30 | 是 |
| pytest-5840 | 2387.33 | 是 |
| xarray-4356 | 1438.85 | 是 |
| pytest-10081 | 337.85 | 是 |
| pytest-6202 | 863.04 | 是 |
| xarray-2905 | 77.64 | 是 |
| pylint-4661 | 1248.13 | 是 |
| pytest-7236 | 1576.26 | 是 |
| pytest-8399 | 519.42 | 是 |
| xarray-6461 | 165.15 | 是 |
| pytest-10356 | 44.21 | 是 |

中位提前量863.04 ms；6/11在0-1000 ms内，4/11在0-500 ms内。
全部11次都在native生成结束记录之后启动：延迟中位22.60 ms、
最大58.41 ms。这是实际submit口径，不宣称每次均在50 ms内。
本轮0次生成结束前的预测H2D，不能将这些动作当作语义剩余长度
头已经达到亚秒精度的证据。所有动作的parent epoch均为0，
实际消费主要覆盖首次派发的parent输入。

113个完整JOIN中，仅19个在最终请求期间采到Host-only恢复目标；
17个有采样的容量/phase交集。PREPARE调用数不等于H2D机会数。

### 工具：下发打通，但被自身压力回收抵消

6次对应4个不同等待，其中django-14631与pylint-4604各重复一次。
全部早于TOOL_END，但实际提前3.70-7.11秒，0次进入目标1秒窗口。

| workflow/node | 下发预测剩余ms | 实际到TOOL_END ms | ACK后再回收ms |
| --- | ---: | ---: | ---: |
| django-14500/12401 | 219.25 | 3987.29 | 643.87 |
| django-14631/12505，第1次 | 306.41 | 7113.03 | 1652.03 |
| django-14631/12505，第2次 | 0 | 5122.05 | 619.86 |
| pylint-4604/12886，第1次 | 0 | 5593.87 | 115.76 |
| pylint-4604/12886，第2次 | 0 | 5255.72 | 94.16 |
| django-16938/14851 | 594.92 | 3695.54 | 338.71 |

逐项链路为：PREFETCH ACK -> 同context/epoch/node的
`parent_pressure_demoted`释放FULL及Mamba -> 该节点再次native H2D
-> 下一请求首次服务。6次均成立，回收中位约479 ms。
下一输入的目标前缀完整匹配、namespace一致、node identity匹配，
并非旧的安全checkpoint缺失或prefix不兼容问题。
6个目标FULL首次复用均为false，首次请求仍有Host hit。

源码中的两个口径相抵：

- `_tool_prefetch_ready`使用 `max(0, P50-age) <= 1000`；
- `_long_tool_wait`使用 `P10-age >= 2000` 或条件CDF的
  `P(release within 2000 ms) <= 0.1`，并继续允许pressure parking。
- H2D ACK后tool ticket清除，没有等价于JOIN final-stage的
  短驻留保护/回收候选排除。

由记录forecast及成对时钟复算，6次下发时两个条件均同时为true，
5次首次再回收时仍同时为true。例：django-14631下发预测306 ms，
条件CDF认为2秒内完成概率约6.79%，因此同一对象同时被视为
“马上要执行”和“长等待可回收”。重建值有数ms的时钟/记录边界
误差，但与数秒实际窗口、直接再回收记录不是同一个误差量级。
这不是增加独立CDF门禁的理由，而是需要统一条件时钟/分布和
residency状态，不能把过期的P50 clipping到0视为即将完成。

## 5. PREPARE与压力

两侧PREPARE分别6274/5700次，物理事务已settle。
predictive有38次真实pressure demotion（JOIN 19、工具19），
38次均可关联同context/epoch/node的先前PREPARE D2H ACK，
涉及32个不同node，说明有备份被实际回收使用，不是全部no-op。
其中含工具预取后再回收，不能把38次都计为正收益。
两侧共用PREPARE，无no-PREPARE对照，不能独立证明PREPARE吞吐贡献。

Host FULL/Mamba两池在两侧均达到100%高水位。
累计Host eviction字节（十进制GB）为：

| Host池 | Reactive | Predictive |
| --- | ---: | ---: |
| FULL | 219.148 | 192.468 |
| Mamba | 103.023 | 98.709 |

FULL输入Device+Host hit为95.669%/95.531%，没有大幅崩溃；
uncached input为15.772M/14.349M，不能全部称为重算。
块归因probe overflow为6.550M/4.166M，Mamba hit location仍不完整。
被观测到的FULL重算3087/132单位不是全量重算量。
84-root确有更多真实迁移与容量竞争，但Host churn显著，尚不能
冻结为“有用KV丢弃后重算很少”的理想正式负载，也不因此继续升并发。
这些缺口限制重算/收益结论，不是拒收整轮逐请求或child事件的门禁。

## 6. GPU与长尾

v4的低利用率现象没有重现。活跃时段NVML近似积分为：
GPU busy 3351.75/3882.03秒，idle 1018.68/855.33秒。
有running/queue时利用率77.27%/82.57%；predictive没有额外大段
无GPU请求空转。它却有约899秒只剩两个workflow的尾段，
reactive约162秒；predictive尾段利用率91.17%，不是v4式工具空转。
最后两项为pylint-8898与django-10554，分别有628/882次LLM调用、
630/862次工具调用；前者有允许的graph-budget收尾，后者7轮派发。

低batch服务可以保持高kernel-active比例，却没有高批量吞吐。
两侧decode batch=1数量23603/94746，batch 2-8数量121076/244972。
这解释为什么“更忙”不自动等于completed workflow更快，但不是
同context/kernel的反事实profile。不能用它算出每个CPU函数的占比。
机会采样累计108/92秒，语义推理约49.7秒在独立CPU process；
不能把后者直接加作scheduler stall。
工具hint接受全部推迟物理inspection、重复refresh跳过约59k次，
没有重现v4的每次hint立即扫物理闭包；不据此单pair宣称确定加速。

全部H2D stream累计1.067/0.871秒，submit-to-ACK累计17.245/
12.572秒；D2H stream累计106.750/89.133秒。这些可重叠，
不是exposed stall，不能直接解释整轮375秒差值或当收益上界。

## 7. 一个incomplete及结论边界

predictive的pytest-7324最后响应35075字符、8192 output token，
反复论述 `None` 与AST表达式，finish_reason=length，无工具调用。
该workflow唯一child已RETURN，JOIN链闭合，deadline未到、没有
取消、格式修复或重复工具抑制。自然语言仍有效；这里是未生成
完整终态的长度截断，不是要求WorkflowCompletion格式。
保留该任务、patch、workspace及trace。其逐请求服务与child/JOIN
样本不因root incomplete自动全部作废；root终态时间应标censor。

两侧重复工具抑制、semantic forced、protocol repair均为0。
允许的2048/预留32步graph finalization有9/5次，涉及7/4个workflow；
该干预要按事件标注，不偷偷称全量自然轨迹，也不重新加agent guard。

本轮直接证实目标驻留与pressure parking相抵、时间口径不一致；
未证明pre-EOS预测或端到端净收益。修复执行顺序维护在
`docs/implementation_plan.md`，不是本报告的新实验队列。
不能用增加并发、guard或删除失败任务掩盖这些结果。

完整数据在原pair的 `comparison.json`、三个窗口审计，
以及 `experiments/analysis/v5_gpu_accounting_20261006.json`、
`experiments/analysis/v5_transfer_lifecycle_20261006.json`。
