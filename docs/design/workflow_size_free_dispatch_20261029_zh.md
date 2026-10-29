# 工作量分档与有界事件补位

## 动机与证据

上一轮 workflow-size Full 与原 2s Full 的完整工作相同，但平均 JCT 只改善
0.0699%，全程输出吞吐下降 1.0201%。完整选择日志的 1,132 次周期控制中，
89 次改变排序、13 次改变局部选中集合。保留负结果，不推广该候选。

在原 2s Full 的运行中段（采样时间轴 15% 至 70%），逻辑预算时间加权占用约
94.65%，实际 allocator 分配占用约 99.67%，其中约 24.03% 可由原生缓存管理
回收。allocator 占用包含缓存、在途和活跃状态，不等于有用活跃 KV。
因此不能把问题统一解释成 HBM 空着却没有准入。

`workflow_work_training_20261029/eligibility_frontier_r1/free_budget_opportunities.json`
发现 2,217 个采样点中有 78 个满足必要条件：某个 paused agent 已实际提交、
等待至少 0.5 秒，其完整输入加 100-token buffer 的下界不超过剩余逻辑预算。
这些采样区间合计约 81.01 秒，占 3.57%；它们不是严格的原生可执行机会、
可消除 GPU 空闲或 JCT 节约。部分已生成输出及事件投递的差异仍需真实运行核验。

同目录的 sibling 审计验证原 Full 的 1,078 条服务日志、1,099 个请求 attempt，
其中 21 个原生 batch 含多名同组成员。共同服务已真实发生，不能只从逻辑资格
推断效果；该日志本身也不证明 sibling 机制改善 JCT。

## 选择与容量

本候选沿用已有的事件驱动补位和 500ms 兜底入口。完整 working-set control
仍为 2 秒，ACTING half-life 仍为 1 秒，压力处理仍走原生 reference 规则。
500ms 不重新切换完整工作集。

每次补位先收集原入口的真实 JOIN parent 以及最多 8 个旋转 pending agent。
对于相同 workflow，只读取一次后台已经发布的预测。只要窗口内有受支持预测，
按以下顺序选择：

1. 已观察且请求已就绪的 JOIN parent。
2. 原 reference 恢复类别：续调、新 program、ACTING 后备。
3. workflow 剩余工作 short、medium、long。
4. 既有上下文大小、入队序和稳定输入序。

窗口内所有预测都不可用时，保持原来的旋转窗口顺序。个别未知 workflow 用中档。
worker 错误、过期或不确定预测不使执行路径等待模型；不读取未来请求长度、
工具时长或 JOIN 事件。周期选择与事件选择分别读取缓存，事件读取不改写周期
选择的预测快照，防止审计日志把两次不同决策的预测混在一起。

只使用完整、未折扣的空闲逻辑预算，每次最多授予 4 个 agent，放不下则继续扫描
窗口中其他对象。每次授予前刷新已有输出增长并再次核对总预算。真实请求身份、
epoch、工具/子调用阻塞和 marked-for-pause 条件继续由原 `_candidate` 检查；
原生 allocator、输入增长、请求行和依赖检查仍决定最终物理准入。

该路径不指定 active victim、不驱逐正在等待的 agent 来预取另一个普通请求，
也不恢复工具/普通请求预取。JOIN/EOS、prepare_host、工具预测卸载、agent
连续性和原 sibling 协同继续生效。既有 1800 秒 pending 保险丝保留，不新增
全局老化函数。

## 控制成本与验证

补位只为已收集的小窗口增加缓存查询和稳定排序，不增加物理 prefix 读取、
KV 遍历或新模型推断。不改变旋转窗口的候选上限及推进方式；周期控制仍保留
原有空预算快速返回。排序变化与最终逻辑授予分别记录，不能把一次排序变化
当成真实 GPU 服务或 JCT 收益。

声明文件为 `configs/migration/predictive_workflow_dispatch_20261029.json`，
显式开启 500ms 补位及 workflow-size 排序，使用已修复流式输入的固定模型。
其余 18 个不同项目、10 初始加 8 个 mean120 到达、全部 3,674 次请求、
145,565,966 个输入和 549,671 个输出 token、工具时长、容量、seed21、
2048/mixed-off、running/decode24 均保持原样。

候选模型离线表现仍然混合，开启本次实验不表示推广或通过性能验收。先完成
当前源码的 CPU 检查、实际 token/session/原生停止和采样资格，再执行单次
Full；复用完整 ThunderAgent decay 与原 2s Full 对照，不再采集 native。
保留全部正常尾部和负结果。验收包括完整平均/P95 JCT、全程吞吐、decode
趋势/分布、补位后实际服务、重算/迁移及后台与控制线程成本。

## 本轮资格与启动

运行时和候选声明已提交为 `aedfe19b`。相关 115 项检查先通过，随后
`workflow_work_training_20261029/workflow_dispatch_r1/cpu_r1/evidence.json`
实际通过 5,008 项测试及 6 个子测试，完整保留此前 4,946 个测试身份，
无失败、错误或跳过，绑定 2,007 个源码/声明输入，原生引擎源码保持不变。

`eligibility_frontier_r1/dispatch_micro.json` 的 8 候选排序微基准：
候选线程 CPU 中位 12.79µs、P99 16.46µs，原空操作中位 0.54µs。
只测缓存元数据排序，不包含完整事件处理、日志、后台预测或 GPU 干扰；
不能外推为整轮新增 CPU 开销或 JCT 改善。

`workflow_dispatch_r1/preflight.json` 从干净 worktree 再次验证完整 ThunderAgent、
原 2s Full 和前一版 workflow-size Full 的工作完全一致，并确认正常尾部、
新鲜遥测排空、实际 owned exit、模型和当前 CPU 源码绑定。GPU 独占资源检查
通过，沿用原生树 `434c35dd66211d0a230717c88bf1452056769a40`。

`ops/workflow_dispatch_position_20261029.registration.json` 已实际启动资格检查，
watcher 1688208，执行源码冻结在 `aedfe19b`，端口 18894。
实际服务参数为 2048/mixed-off/running24/seed21、805198-token device pool、
200GB Host。当前进入真实 token/session/停止条件检查，尚未报告通过；
通过后才会注册 `ops/workflow_dispatch_20261029_r1.registration.json` 并启动
`experiments/raw/workflow_dispatch_20261029_r1/full`。本段不表示新 Full 已完成、
性能改善或已满足全局目标。
