# BeliefKV 可编辑架构图

架构基线：2026-10-15 当前开发实现。源代码版本与逐文件指纹见
`manifest.json`。这里更新的是论文配图；2026-10-11 月报 PPT 保留当时的内容。

## PowerPoint

- `beliefkv_architecture_editable_zh.pptx`：四页中文机制图。
- `beliefkv_architecture_editable_en.pptx`：同样四页的英文论文图。
- `02_*_editable.pptx`、`03_*_editable.pptx`、`04_*_editable.pptx`：
  六份单图 PPT，分别匹配三张紧凑机制图的自然比例，适合直接编辑和组合。

四页为“一张总览 + 三张设计”。以对齐的网格、圆角分组和操作箭头组织内容，
不再用同一颜色同时编码系统层级、存储位置和上下文归属。
总览的控制面为蓝边浅蓝底，数据面为绿边浅绿底；两个存储层保持同宽同高，
但 HBM 的标题和页体使用示例中的紫色，Host 使用示例中的黄色。
前缀索引和请求准入有独立的紧凑分组。
三张机制图只为实际推断、存储和服务实体设置小型功能分组，
不重复总览的大分区；字段校验、重试、引用保护和传输 fence 放在备注中。
编号在总览和三张机制图中保持一致：

- D1：事件与内容驱动的执行需求预测。
- D2：工作流感知的 KV 生命周期管理。
- D3：准入与预取协同调度。

四张图贯穿同一示例：P 等待 C1/C2，C1 RETURN 后回收其无用私有 KV，
C2 收尾提供预测信号，D3 结合 D2 的副本与容量视图恢复 P；真实 JOIN 解除且
P 请求提交后，经原生准入消费恢复数据。图中时序是机制示意，不是性能结果。
总览页脚表达整体闭环；D1/D2 的输出箭头指向 D3，D3 在对应输入旁标出 D1/D2。
机制图不重复整套页脚。黑底白字序号位于实际操作箭头上，
表示读图时的典型操作顺序，不是分区标题编号，也不强制独立回收事件按编号串行发生。

总览将 RCCG 与完成预测合并在 D1 中，D2 展示缓存生命周期，D3 展示时机、
容量与消费协同；原生缓存/分配器/准入构成共同底座。完成预测仍分为两条路径：

- Child：正文、完成通知与生成进度 → 异步 CPU 模型输出阶段置信度及剩余 token
  → runtime 结合观测有效速率给出 RETURN 时机提示 → RCCG 对应的 parent 候选。
  当前图对应唯一未返回 child 的路径，不表示通用多 child 联合时间模型。
- Tool：命令历史与当前已耗时 → 以仍未完成为条件的剩余时间估计
  → TOOL_END 时机提示 → 工具所属 agent 候选。

两路结果都送往 D3 的缓存与准入规划；虚线表示未来候选关系或控制提示。
内容推理在独立 CPU 进程执行，工具历史估计不经过该模型；
主图只保留阶段/工作输出和相应时机路径，模型内部结构与执行位置放在备注。
模型没有直接输出“是否进行 H2D”或实际 GPU 首次服务时刻。

图内以依赖图、文本/通知、阶段置信度条、剩余工作区间、服务进度输入、
工具条件分布、增量缺页传输、终态释放和恢复/执行时序表达，
只保留必要标签；完整解释、公式定义、代码对应和适用边界放在备注与本页。
文字、模块、页面、线条和箭头均为独立 DrawingML 对象，没有平铺图片。
曲线为原生 DrawingML 三次贝塞尔曲线，保留控制点；没有用折线采样或图片代替。
直线为 PowerPoint 连接线。三角、凹尾和开口箭头头部按末段切线绘制为独立原生图形。
主要 KV 路径用封闭的宽曲线带，回环与反馈用曲线，短依赖用直线。
图形和文字可分别选择、
移动、改色或修改内容。每页备注包含图义和适用边界。

合集 PPT 为 14.4 × 9.6 英寸，机制图在页内居中；单图 PPT 的页面直接贴合
机制图比例，约 14.4 × 5.94 / 5.76 / 5.58 英寸。复制时可多选
或组合整张图后等比缩放。中文使用 Noto Sans CJK SC，
英文使用 DejaVu Sans，少量英文条件标签使用 Liberation Sans 斜体；
数学表达使用 Liberation Serif。源字体在当前机器
已安装，PPT 未嵌入字体。

`pptx_preview_zh.png` 和 `pptx_preview_en.png` 是从实际 PowerPoint
经 LibreOffice 渲染后按图源自然边界裁出的四图预览，便于检查紧凑版面。
`powerpoint_validation.json` 记录文本与 SVG 一致性、页面边界、
原生对象数量、文件 SHA256 和办公软件渲染检查结果。

## 配色与排版

参考图的组织方式落实为：外层容器确定角色和层级，内层实体按行列对齐，
迁移箭头连接不同位置的同一份数据。颜色有固定含义，分组边框、背景和页内标记
分别承担不同职责。文字统一使用黑色或白色。
架构与机制仍对应 BeliefKV 实现。

此前版本包含自行调配的颜色，不应被称为示例图的取样色。当前全部显式绘图色
均取自用户提供的 1080 × 617 JANUS 原图，不做明度、饱和度或色相变换。
取样坐标、原图 SHA256 和 BeliefKV 中的用途保存在
`docs/figures/janus_reference_palette.json`，同时写入 `manifest.json`。
颜色的使用含义由 BeliefKV 图定义，不表示两套系统具有相同组件。
PNG 抗锯齿边缘会产生混合像素；取色约束针对 SVG/PPT 中显式指定的颜色。

| 视觉编码 | 色值 | 含义 |
| --- | --- | --- |
| 控制面边框与标题 / 背景 | `#156082` / `#DCEAF7` | 对应示例原图坐标 `(29,100)` / `(40,131)` |
| 数据面边框与标题 / 背景 | `#275317` / `#D9F2D0` | 对应示例原图坐标 `(606,30)` / `(614,60)` |
| HBM 标题与页体 / 背景 | `#D7AEFF` / `#F2F2F2` | HBM 驻留数据；边框为黑色 |
| Host 标题与页体 / 背景 | `#FFD579` / `#FBE3D6` | Host 副本；与 HBM 使用相同几何层级 |
| Parent owner 色条 / 请求票据 | `#A6CAEC` | Parent 归属；色条跨迁移保持不变 |
| Child 1 / Child 2 owner 色条 | `#84E291` / `#F6C6AD` | Child 归属 |
| 依赖 / 控制线 | `#156082` / `#275317` | 同时由三角 / 凹尾、直线 / 曲线区分 |
| 迁移曲线带 | `#F2AA84` 填充、`#35505D` 边线 | KV 数据移动，宽度不表示实测带宽 |
| 反馈或预测虚边 | `#215F9A` | 反馈与候选关系由相应标签区分 |
| 正文 / 反白文字 | `#000000` / `#FFFFFF` | 标签、公式、黑底流程编号和当前设计编号 |
| 白色虚边页 / 灰色归属条加锁 | `#FFFFFF` / `#A6A6A6` | 缺失或延后；共享且仍需保留的前缀，页体仍表示存储位置 |

页体颜色表示位置；顶部细色条和页号表示归属与身份。D2 的同一 p0..p5 列在
HBM/Host 行中逐列对齐，迁移后页体从紫色变为黄色，owner 色条和页号不变。
共享页使用中性色条和锁；缺失页为白体虚边；空闲容量为没有 owner 的白色空格。
请求票据的侧边色条和 GPU 执行槽表示请求归属，不表示存储层。
设计一仅展示 final 类的示意置信度及条件剩余工作区间，条长不表示实测结果。
不能把某一填充色等同于缺页或已恢复。
总览主标题为 37 画布单位；紧凑图标题为 31，核心标签约 24–29，
辅助标签约 21–23。机制图只保留一条剩余工作/有效速率简式，
次要预算和传输启动公式移到备注。
数学变量及少量英文条件标签使用斜体，中文条件保持正体。
关键操作的箭头上用黑色圆圈、白色数字标出步骤；D1/D2/D3
保持设计身份标识，不以圆圈序号替代分区标题。
矩形表示 FULL 页，六边形 S 表示 Mamba 状态，圆形表示 agent，
菱形表示 JOIN/条件，折角文档表示已送达文本，气泡表示通知；
不再用无标签网络节点、权重小矩阵或重复页块代指不同输入。

图中的实体、动作与时间区间分别表示：

- HBM、Host、HiCache/DMA 为实体；D2H/H2D 是这些实体之间的有向传输行为，
  因而只作为箭头标签，不再画成独立设备方框。
- 主要 D2H/H2D 使用宽曲线带；短依赖用粗三角直箭头，控制用凹尾箭头，
  后续轮次和 ACK 反馈使用回环、圆角路径或开口虚线曲线。线宽不代表实测流量。
- D2 的释放箭头指向空页，表示归还原生容量，随后将容量视图交给 D3；
  不表示 child 的旧数据复制给 parent，也不表示独占预留相同物理页号。
- 时序图的 H2D 使用带箭头端的时间段，表示复制持续时间。
  准入条件表示真实可执行、必要数据依赖与 slot 同时满足；
  必要状态、原生逐层依赖和 ACK 生命周期的具体边界放在备注中。

有向路径比结构边框更粗，普通执行/控制线至少 4.8 画布单位，
反馈线至少 4，主要曲线迁移带宽 17；边界约 1.3–1.8。
交叉处以细白色隔离线分开路径和边界，不使用偏移阴影。
快照和历史记录均以单层平面图元表示。
导出时同时清理 PPT 主题、母版、版式、图形和文字对象中的阴影、
反射、发光与三维效果；每个图元和文字框显式覆盖效果设置。
校验器检查整个 PPT 压缩包，避免只检查当前幻灯片而遗漏主题阴影。
预览画布也改为白色。

公式不再使用一整行普通文本模拟上下标。变量为衬线斜体，下标和上标为
独立的可编辑文字对象，分式横线为原生线条；运算符和文字下标使用正体。
PowerPoint 中可以逐项修改 `τ`、`W`、`lo/hi`、`s(c*)` 等符号及位置，
无需重新生成公式图片。它们是组合文字与线条，不是 Office 公式对象。

## 论文输出

每张图均提供 SVG、矢量 PDF 和 600 dpi PNG：

| 文件前缀 | 内容 |
| --- | --- |
| `01_system_overview` | D1/D2/D3 的职责、接口与反馈，连接原生缓存和 GPU 服务 |
| `02_prediction_and_overlap` | D1：依赖绑定 → 内容/通知推断阶段与工作 → 滚动更新时机 → 输出 P/A 候选 |
| `03_pages_and_capacity_handoff` | D2：p4/p5 增量备份，已备份冷页 p2/p3 回收，C1 私有后缀释放，向 D3 提供恢复视图 |
| `04_prefetch_and_admission` | D3：D1 时机与 D2 容量 → 缺页 H2D 与 RETURN 部分重叠 → 有界驻留及原生准入消费 |

`_zh` 与 `_en` 分别表示中文、英文。总览为 1800 × 1200 画布，
三张机制图分别为 1600 × 660、1600 × 640、1600 × 620，
相较上一版显著降低高度。PDF 均宽 7.2 英寸，PNG 均宽 4320 像素，
高度随自然比例变化。`beliefkv_figures_bilingual.pdf` 合并全部八张矢量图；
`preview.png` 是英文快速预览。`manifest.json` 保存架构基线修订、
生成器和绘图 helper 的 SHA256、对应实现的逐文件 SHA256、架构日期、画布尺寸、输出 DPI
与 PowerPoint 原生对象数量、数学文字对象数量及色板。

## 代码对应与边界

### 图中符号

| 符号 | 含义 |
| --- | --- |
| `P`、`C1/C2` | Parent 与 child；圆形表达运行时依赖，不要求预先固定 DAG |
| `P/A`、`old`、`hit` | 下一 parent/工具 agent、普通老化请求、已驻留请求 |
| `D1 / D2 / D3` | 执行需求预测、KV 生命周期、准入与预取协同；通过对应输入/输出连接 |
| `p_final`、`Wlo … Whi` | 最终报告阶段置信度与条件剩余生成工作区间 |
| `τ ≈ W / v_eff` | 当前剩余工作换算为时机提示；W 已扣除预测快照之后新增进度 |
| `v_eff`、进度箭头 | 包含已发生服务间隙的观测有效速率，以及滚动更新 |
| `τtool`、CDF 阴影 | 以工具仍未完成为条件的剩余时间提示 |
| `c*`、`s(c*)` | 下一请求可复用的输入 checkpoint 与匹配的 Mamba 状态；匹配状态不一定是时间上最新的状态 |
| `ΔFULL` | 目标 FULL 前缀中缺失的页集合；不同于整条 agent KV |
| `p0..p5` | P 的稳定输入前缀，p0 为共享页；D2 回收 p2/p3 后可为 D3 提供缺页恢复机会 |
| 锁、勾、叉、`&` | 有界驻留/冻结或共享保护，已完成/有效状态，不纳入某路径，以及联合数据依赖/准入条件 |
| 三角直线 / 回环或虚线曲线 | 执行路径 / 后续轮次、普通请求老化与反馈；灰色开口依赖为已完成或不选择分支 |
| 锁与 `bounded` | 保留共享/活跃 KV，或对恢复页提供待首次消费期间的有界保护 |

| 图中机制 | 实现 |
| --- | --- |
| 已观测依赖与受影响成员增量更新 | `beliefkv/control/causal_graph.py` 的 `GraphDelta`；`beliefkv/runtime/sglang_v0520_runtime.py` 的 `on_events`、`_update_wait_context_cache` |
| 异步内容推理与阶段/剩余工作头 | `beliefkv/runtime/semantic_report_worker.py`；`beliefkv/predictor/child_semantic_work.py`；`beliefkv/predictor/conditional_work.py` |
| 唯一阻塞 child、通知保留、滚动工作端点 | runtime 的 `_resume_remaining_child_report_stage`、`_roll_final_stage`、`_wait_prefetch_timing_valid` |
| 工具历史与存活条件下的剩余等待 | `beliefkv/predictor/project_tool_history.py`；`beliefkv/runtime/sglang_v0520_prediction.py` 的 `NativeToolWaitHint`；runtime 的 `_roll_tool_prefetch` |
| 实测整批传输成本与启动窗口 | `beliefkv/runtime/native_transfer_service.py`；`beliefkv/runtime/native_transfer_policy.py` 的 `transfer_start_window`；runtime 的 `_h2d_start_window` |
| 安全输入 checkpoint 与缺失页规划 | `beliefkv/runtime/sglang_v0520_physical.py` 的 `plan_prefetch_gpu_steps`；runtime 的 `_wait_prefetch_opportunity` |
| 增量 PREPARE 与压力释放 | runtime 的 `dispatch_join_prepare`、`dispatch_tool_prepare`；staging patch 的 `prepare_host_session_nodes`、`demote_backed_full_preserving_mamba` |
| 批量/部分 H2D、逐层依赖与必要 Mamba | staging patch 的 `prefetch_gpu_session_nodes`、`split_beliefkv_prefetch_full_prefix`、`beliefkv_prefetch_can_admit`；runtime 的 `defer_prefill_for_prefetch` |
| 有界驻留、恢复/HBM 命中排序和老化 | runtime 的 `_register_prefetch_service_lease`、`_refresh_prefetch_service_leases`、`plan_native_prefill` |
| 终态 child 回收与 parent 重新规划 | runtime 的 `_apply_terminal_child_reclaim`、`_rearm_parent_capacity_restore`；staging patch 的 `retire_beliefkv_child_session` |
| forward 后有界迁移规划与物理回执 | runtime 的 `dispatch_after_forward`、`on_native_transfer_commit`；staging patch 的 scheduler hook 与 HiCache controller |

这组图对设计总结作了如下约束，避免把目标画成已实现的保证：

- 模型提供阶段、剩余工作和工具时机提示，runtime 决定物理动作；模型不学习离线不可识别的迁移净收益。
- 内容分数条和工作量区间表达预测结果，省略模型结构与特征展开；阶段头与工作头共享冻结编码器权重，工作头可配置独立文本窗口。
- 当前内容驱动的 `JOIN_ALL` 临近完成路径针对唯一未返回 child；较早通知可以在 sibling 返回后重新绑定。工作量区间不是完整 JOIN 的已校准联合分位数。
- 额外文本结构特征由 `structural_work_features` 送入工作量头，共享阶段表示只使用其实际数值/语义输入。
- `[W0−ΔN]+ / v_eff(t)` 是时机提示；当前 `_semantic_rate` 使用 0.5/2/5 秒窗口，包含已发生间隙，不能称为纯 GPU 服务速率。未来排队不作为在线输入；EOS 与 harness RETURN 分别处理。
- JOIN 图的虚边只表示候选未来执行需求。NOTICE、阶段分数和 EOS 都不直接替代实际 child RETURN 或把 parent 改为 ready。
- `H = min(L, S90 + Q90 + Δobs + εearly)` 对应当前启动窗口实现；仅提前部分传输也可利用，不要求 H2D 必须在 RETURN 前全部完成。图中时序是机制示意，不是实测加速曲线。
- `PREPARE_HOST` 只备份稳定的缺失 FULL，不直接释放 HBM，也不投机备份 Mamba；真实压力下由原生路径回收已备份的冷页。恢复 Mamba 必须与目标 checkpoint 匹配。
- D2 的虚边 Host 页表示尚待增量备份；D3 右侧实边页表示恢复后的 HBM 视图。
  准入依赖实际数据就绪，FULL 可按原生逐层依赖执行；必要状态未就绪时仍须等待。
  ACK、请求提交、原生准入和首次复用是不同事件。
- 页体的位置色与 owner 色条分开编码；自动校验同时检查 HBM/Host 位置色、
  迁移中的 owner 标记、两个大平面的边框/背景差异，以及原生可编辑性。
- 全部显式绘图色必须属于取样色集合；传入原图时，校验器同时核对图像 SHA256
  与每一个记录坐标上的实际 RGB，避免把自配颜色描述为示例原色。
- 终态 child 只释放已无用途、无活跃/共享引用且无在途依赖的私有后缀。容量交接经过原生空闲列表，不表示 parent 独占预留或复用相同物理页号。
- 事件/统计缓存不缓存物理执行许可。独立 CPU 推理和 forward 后规划不代表全部控制开销均异步，也不证明已获得稳定的 JCT 或吞吐优势。
- 当前 root 可多轮 spawn/JOIN，但 child harness 未提供嵌套 task 委派；图中没有把尚未接通的 grandchild 路径画成现有能力。
- 短文本、分数条、页数量、工作区间、CDF 和传输/计算条带均为机制示意；没有把示意值当作测量结果。

图中没有把旧版完整 JointPlan、选择性 running retraction 或未部署的通用
在线学习策略画成当前主路径。架构取自最新开发代码，不能用于证明某个冻结实验已部署全部机制。

## 重新生成

生成器：`docs/figures/generate_monthly_paper_figures.py`。
依赖 Python 3.11+、Pillow、CairoSVG、python-pptx，以及系统 `pdfunite`
和上述字体。当前依赖隔离在临时目录，没有修改实验环境：

```bash
PYTHONPATH=/tmp/beliefkv-monthly-render-packages-20261011 \
  /home/longhao/miniconda3/envs/beliefkv-next/bin/python \
  docs/figures/generate_monthly_paper_figures.py
```

若临时目录已清理，可在独立环境安装依赖后运行同一生成器。
办公软件预览和校验回执是本次交付的检查产物；修改图源后应重新渲染检查。
渲染出两份四页合集 PDF 和六份单图 PDF 后，使用
`docs/figures/validate_monthly_paper_figures.py --office-pdf-directory <目录>`
重新检查原生对象、字体与公式、逐项文本、文件指纹和图片分辨率，
并更新办公软件预览及 `powerpoint_validation.json`。
若原始示例图仍可访问，再传入 `--reference-image <原图路径>`，
即可核对原图指纹和所有记录取样点；SVG 和 PPT 图元的显式色集合始终会检查。
