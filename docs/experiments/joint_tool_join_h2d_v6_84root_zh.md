# v6：84-root 联合等待与预测 H2D 对照

## 配置与结果

目录：`experiments/raw/qwen35_joint_wait_h2d_ab_84root_20261006_v6`。
启动代码 `573f32c`，运行源码指纹
`c65caec44ecc934cd5cff9d740ec96f19459f48527505c85927bd4ae969fd6b9`。
沿用 v5 的模型、84-root 单波、running=48、Host 200 GB/NUMA 1、
Device Mamba/FULL=0.9 和匹配的 Host 字节比例。
两侧共用通知、收尾优先级、PREPARE 和真实压力回收；仅预测侧早期加载。

| 指标 | Reactive | Predictive |
| --- | ---: | ---: |
| Completed workflow | 84/84 | 84/84 |
| 总时长（秒） | 4310.75 | 4120.43 |
| Completed workflow/h | 70.150 | 73.390 |
| 平均 JCT（秒） | 1837.86 | 1690.56 |
| 中位 JCT（秒） | 1605.92 | 1533.04 |
| GPU 利用率，原报告采样均值 | 82.07% | 81.63% |
| Output token/s | 717.20 | 727.26 |
| LLM 调用 | 15652 | 14884 |
| 工具调用 | 15169 | 14550 |
| 输入 token | 352929439 | 329119568 |
| 输出 token | 3091690 | 2996638 |

单轮完成吞吐高 **4.62%**，平均 JCT 低 **8.01%**。但预测侧
LLM/工具/输入/输出量分别少 4.91%/4.08%/6.75%/3.07%，
84 个 workflow 的请求序列全部不同。不能归因为预取的独立收益。
没有独立 patch grading，不称正确任务吞吐；保留全部任务和路径分歧。
正式阶段再按既定要求多轮配对报告均值与方差。

两侧 measurement-valid 均84、guard干预和语义强制收尾计数均0，
serving遥测无 dropped/failed/writer error。动态child/完整ALL JOIN
为110/105，多轮root为18/13，最多5/3轮。每个JOIN仍只有一个child，
不能把root累计child数当作单轮并行fanout。Completed不等于没有
普通工具错误或单个length-ended生成轮次。
另须单独记录：每侧各8个workflow触发了既定graph=2048、预留32步
的budget FINALIZE。这是用户允许的安全收尾，不包含在上述通用
guard计数中。它们仍留在端到端结果；工作头自然轨迹拟合分开处理，
不能从guard计数为0推出整轮不存在运行时干预。

## 真实动作与复用

预测侧7个JOIN H2D ACK，合计 **532582400字节**。FULL首次服务
复用全部确认；6个Mamba单请求COW forward复用确认，另一个未证明，
不是已证明浪费。工具H2D为0。

7个目标在ACK与首次服务间均无再次pressure parking或native H2D
重载。5个策略租约因首次服务释放，2个因2秒期限释放，后者仍实际
复用，不把租约过期等同于miss。没有工具动作，不能声称工具链路
修复已经得到GPU验证。

| 链路 | P50（ms） | P90（ms） | 最大（ms） |
| --- | ---: | ---: | ---: |
| issued → controller submit | 1.33 | 1.51 | 1.61 |
| enqueue → submit | 1.15 | 1.25 | 1.31 |
| submit → ACK | 4.67 | 23.16 | 48.68 |
| ACK → 首次GPU launch | 618.68 | 3059.15 | 4351.98 |

submit为真实controller提交，不是缺少墙钟锚点的DMA开始时刻；
首次launch也不是独立识别的exposed restore stall。

| Workflow | submit距child RETURN提前量（ms） |
| --- | ---: |
| django-11820 | 2295.55 |
| pytest-8399 | 847.55 |
| xarray-2905 | 1044.00 |
| pytest-10356 | 196.02 |
| pytest-5840 | 463.50 |
| pytest-7432 | 70.88 |
| seaborn-3069 | 282.39 |

7个提交与ACK均早于RETURN/JOIN，中位提前量463.50 ms。
**全部提交发生在native EOS后**：EOS后提交P50为17.49 ms，
最大23.24 ms。没有生成结束前的语义预测H2D，不能把协议窗口加载
称为准确预测了剩余生成工作。

预测侧PREPARE issued4694次（JOIN3987、工具707），pressure
demotion15次且全部JOIN。两侧各15次真实压力释放均有先前
PREPARE ACK，涉及各15个节点；issued数不等于被消费备份数。

## 容量与覆盖

Reactive原生H2D：120个controller batch/15.654 GB。
Predictive：115个原生-only batch/14.914 GB，另7个预测batch/
0.533 GB，无混合batch。预测字节占该侧H2D约3.45%。
FULL/Mamba须分账，不能假定每一笔原生迁移都能提前预测。

105个完整JOIN中，15个EOS前有采样Host-only目标，12个有
容量fit与phase分数交集。django-10554、django-13964和xarray-4356
的短最终请求无已接受预测；其余目标的末段剩余工作仍常高估。
5154次前EOS上界检查全部被超过2秒的判断挡住。
采样交集不是连续驻留证明或容量预留。

两侧FULL Host均达105.358 GB、Mamba均达94.652 GB。FULL Host
驱逐205.589/174.175 GB，Mamba驱逐85.122/87.698 GB。
FULL device+host token hit为95.64%/95.76%，但块级归因的长prefix
probe仍overflow，不能证明全量useful recompute很低。

NVML近似时间积分利用率82.37%/81.93%，非空serving需求期间
82.76%/82.52%，没有v4式大幅利用率差距。native enqueue→首次
scheduler服务P50为65.92/66.28 ms，P90为142.90/535.35 ms，
预测侧仍有较大排队长尾。机会census P50为19.05/14.39 ms，
不是整个控制面的CPU profile。语义推理均值30.24 ms、
观测年龄P50为116.18 ms。

## 独立工作头改进

旧总长度头先非负截断、再加106.25 token上界余量，末段存在
正数下限。新候选先扩张signed residual、再截断，并按workflow
重新校准。旧产物保持原语义，不默默解释成新版本。
仅修正投影没有改善点MAE，也没有在v6回放补出提前触发。

比较工具已读取composite中真正部署的work head，不再错用phase
文件附带的旧工作参数，并补首次触发、过早、工具误报与区间覆盖。
新计划 `configs/migration/child_semantic_work_live_v7.json` 加入v6
真实100 ms观测；只拟合intrinsic remaining tokens，不拟合parent
首次服务或action净收益。phase/encoder/phase threshold冻结。

另比较轻量log-work分位数头。Runtime显式比较center和upper
动作时机，旧配置默认upper；不把整段保守区间当作唯一动作门槛，
也不取消身份、有效Host副本、容量、节点预算和短策略租约检查。
零估计不是EOS。V6进入拟合后仅属训练回放，Astropy/Sphinx仍是
重复使用的项目隔离开发集合，不是新密封测试；候选需检查末段
误差和首次触发，不能仅凭MAE或增加ACK数声明吞吐收益。

## 证据入口

- 原始comparison、workflow trajectory comparison与三类窗口审计。
- `experiments/analysis/v6_prefetch_lifecycle.json`。
- `experiments/analysis/v6_reactive_prepare_lifecycle.json`。
- `experiments/analysis/v6_gpu_accounting.json`。
- `scripts/audit_prefetch_lifecycle.py`，通用链路审计。
