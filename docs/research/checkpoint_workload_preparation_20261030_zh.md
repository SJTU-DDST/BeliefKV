# 源码对应的有界委派试采

前两批自然轨迹的短长差异、多成员JOIN和child长度仍不满足本轮目标，
见 `docs/design/workload_acceptance_20261030_zh.md`。本次先修改可定位的
任务组织问题；不把六项目试采当作持续压力实验，不自动扩成36项目。

## 新发现及证据边界

旧六项目的4/8次提醒已经出现，仍有child执行14--54次模型调用。根因
不能简单归为计数器未接入。实际root委派包含“检查service能否导入”
等开放描述，缺少路径和结束证据。LedgerLink root在已具备完整源快照
时还连续执行了八次目录列表。这些是低效行为证据，不是每一条长child
都由同一原因导致的证明。

另一个问题是公开任务描述与其生成代码中的名字或功能对应不足：

- GatherProof描述CivicNet/GlobalFeed；原始适配器文件实际定义
  TwitterAdapter/SlackAdapter。不得假定重现了原平台bug。
- PalettePulse要求从utils提取邮件工具；原始utils定义的是颜色、
  异步、限流等函数，send_email只出现在限流装饰器的文档示例。
- MediVerse任务使用Article/ArticleVersion；原始模型为Page/PageRevision。
  旧试采容易开始另建一套结构，并追逐缺失仓库接口。
- 长任务里的ModelRegistryClient/InferenceStrategy等描述也不能代替
  对实际RegistryClient/PredictionStrategy接口的确认。

这些结论来自冻结原始源码和公开任务文本，不读取参考答案，不修复
应用源码、测试或模块缺失。不能将名字不对应当作已定位全部业务bug。

## 本次改动

同一个并行workstream profile升级为checkpoint协议2，原冻结采集保持
原代码及协议。新协议纳入delegation contract，避免与旧源混为相同
prompt协议。task描述应含问题、已知路径/符号、已有证据、完成条件和
写入范围；root负责主要实现、整合和最终验证。child对独立问题返回
有证据的答案，额外业务失败作为精确handoff，不接管整个项目。

仍保留child4/8、root8/16软提醒、最大深度2、自动首工具、多成员root
JOIN最低要求及自然报告。没有新增模型步数硬上限、截断输出、固定DAG、
强制额外轮数或人工延长工具时间。已有工具及嵌套JOIN可以正常完成。

新候选保留原六个不同项目、上下文规模和二短/二中/二长的范围假设：

| 项目 | 明确声明的新交付物 |
| --- | --- |
| physics sandbox | 只读核对host/client状态生产、传输和消费边界，不预设已复现症状。 |
| social sharing | 只读核对现有payload及实际adapter的location兼容性，不添加虚构平台。 |
| dashboard | 提取实际存在的颜色工具，兼容旧导入并做聚焦验证。 |
| CMS | 在现有Page上实现有界review状态转换，不另建Article及完整审批服务。 |
| payments | 保留原三阶段功能要求，root负责主要实现和整合。 |
| model serving | 保留原shadow inference三阶段要求，按源码中实际接口完成。 |

前四条是明确的派生任务，不能声称完成了原始完整题目；后两条保留
完整公开要求，并记录接口映射说明。已知入口只从原始源快照及provenance
提供，逐个对照固定git commit中的文件字节；不提供修复答案或未来图。
范围档只是采集假设，必须通过自然完成后的必要模型工作重新分类。

## 验证及下一步

新生成器写出独立manifest、native配置和源绑定声明，旧候选及负面
结果均保留。CPU测试检查未改变源码/环境字段、完整长任务要求、不同
项目、已知入口的真实来源，以及root/direct child/grandchild的实际
中间件接入和自然返回。

GPU前仍需当前源绑定CPU、干净冻结checkout、预约/资源检查和新鲜
sampler。采集只做六项目形态验证，保留全部正常长尾、失败和guard标记。
结束后先检查原生服务/退出证据及完整token，再检查调用长度、root工作、
JOIN结构和实际共享服务；不按调度胜负筛选。

形态通过后才扩充不同项目的测量/背景库，校准持续到达与物理容量、
ready竞争和暴露恢复等待。保持JOIN/EOS预取，工具预取、逐层handoff、
mixed chunk继续关闭。此文档及CPU准备不证明新负载已合格或有性能收益。

本轮实际CPU检查：首批66项通过；随后源绑定共享集合5308项及12项
子测试通过，保留此前全部5268项身份，无失败、错误或跳过。绑定2086
个输入，应用文件在执行期间未变，原生引擎未变。另重新核对15项外部
输入及实际镜像身份。完整结果在
`experiments/reports/checkpoint_pilot6_20261030/bound_cpu_r1/`。
这是采集入口与实现检查，不是模型已遵守新委派边界的证据。
