# BeliefKV 可编辑架构图

架构基线：2026-10-14 当前开发实现。源代码版本与逐文件指纹见
`manifest.json`。这里更新的是论文配图；2026-10-11 月报 PPT 保留当时的内容。

## PowerPoint

- `beliefkv_architecture_editable_zh.pptx`：三页中文机制图。
- `beliefkv_architecture_editable_en.pptx`：同样三页的英文论文图。

三页沿“谁即将执行、如何管理它的缓存、怎样及时消费”展开：
总体架构、事件与内容驱动的执行需求预测、工作流感知的缓存生命周期与准入。
图内以依赖图、模型分叉、区间、页块差分、状态转换、逻辑门与泳道时序表达，
只保留必要标签；完整解释、公式定义、代码对应和适用边界放在备注与本页。
文字、模块、页面、线条和箭头均为独立 DrawingML 对象，没有平铺图片。
折线为可编辑自由形状，直线为 PowerPoint 连接线；图形和文字可分别选择、
移动、改色或修改内容。每页备注包含图义和适用边界。

页面尺寸为 14.4 × 9.6 英寸，贴合配图比例。复制到报告幻灯片时可多选
或组合整张图后等比缩放，也可直接编辑布局。中文使用 Noto Sans CJK SC，
英文使用 DejaVu Sans；源字体在当前机器已安装，PPT 未嵌入字体。

`pptx_preview_zh.png` 和 `pptx_preview_en.png` 是从实际 PowerPoint
经 LibreOffice 渲染得到的三页预览，便于快速检查版面。
`powerpoint_validation.json` 记录文本与 SVG 一致性、页面边界、
原生对象数量、文件 SHA256 和办公软件渲染检查结果。

## 论文输出

每张图均提供 SVG、矢量 PDF 和 600 dpi PNG：

| 文件前缀 | 内容 |
| --- | --- |
| `01_system_overview` | RCCG、异步 CPU 预测、runtime 协同、原生缓存与恢复感知准入，以及 ACK / 服务反馈 |
| `02_prediction_and_overlap` | 文本表示与结构特征、阶段/工作量解耦、条件工具时机、启动窗口公式与部分重叠时序 |
| `03_pages_and_capacity_handoff` | FULL 增量备份、必要 Mamba、checkpoint 缺失页批量/部分恢复、有界驻留与终态 child 容量回收 |

`_zh` 与 `_en` 分别表示中文、英文。PDF 宽 7.2 英寸，PNG 宽 4320 像素，
适于论文双栏全宽图。`beliefkv_figures_bilingual.pdf` 合并全部六张矢量图；
`preview.png` 是英文快速预览。`manifest.json` 保存架构基线修订、
生成器和绘图 helper 的 SHA256、对应实现的逐文件 SHA256、架构日期、画布尺寸、输出 DPI
与 PowerPoint 原生对象数量。

## 代码对应与边界

### 图中符号

| 符号 | 含义 |
| --- | --- |
| `P`、`C1/C2` | Parent 与 child；圆形表达运行时依赖，不要求预先固定 DAG |
| `p(phase)`、`W=[Wlo,Wc,Whi]` | 阶段分数与条件剩余生成工作区间；两个头共用冻结文本表示 |
| 阶段柱 `W/N/F` | 继续工作 / 收尾通知 / 最终报告；柱高仅为输出形态示意 |
| `N(t)`、`ΔN`、`v(t)` | 已观测输出进度、预测之后新增的 token、实测生成速率 |
| `τjoin`、`τtool` | 近期完成时长提示；不等于含未来排队的精确墙钟 RETURN |
| `a(t)`、`T` | 工具已执行时长与工具总时长；CDF 以 `T>a` 为条件 |
| `H`、`L` | 实际启动窗口与允许的最大提前驻留时间 |
| `S90`、`Q90` | 同方向/形态/大小的实际 submit→ACK P90 与 enqueue→submit P90 |
| `Δobs`、`εearly` | 观测间隔与提前完成余量 |
| `c*`、`s*` | 下一请求可复用的输入 checkpoint 与对应必要 Mamba 状态 |
| `g` 与叉号 | 不作为安全输入恢复目标的生成后缀；新一轮 chat-template 序列化未必能复用该后缀 |
| `ΔFULL` | 目标 FULL 前缀中缺失的页集合；不同于整条 agent KV |
| `B≤Cfree+Ccold−Gnext` | 容量关系示意：恢复预算受空闲、可回收容量和下一批增长预留约束；FULL/Mamba 分池检查仍由原生执行 |
| `ℓ0/ℓ1/ℓ2` | 原生逐层数据依赖；图中缩写三层仅为示意 |
| 锁、勾、叉、`&` | 有界驻留/冻结表示，已完成/有效状态，不纳入某路径，以及联合准入条件 |
| 蓝 / 绿 / 橙 / 虚线灰页 | 已驻留 FULL、本次恢复或有效 Host 副本、必要状态或仍活跃对象、延后/缺失部分；具体以图中页图例为准 |
| 实线 / 虚线箭头 | 主要执行/数据路径；观测反馈、仍保留的依赖或老化路径 |

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
- 当前内容驱动的 `JOIN_ALL` 临近完成路径针对唯一未返回 child；较早通知可以在 sibling 返回后重新绑定。工作量区间不是完整 JOIN 的已校准联合分位数。
- `R / 实测生成速率` 是剩余生成时长提示，未来排队和服务间隙不作为在线输入。最新预测能在入队前前移或后移工作端点；EOS 与 harness RETURN 分别处理。
- `H = min(L, S90 + Q90 + Δobs + εearly)` 对应当前启动窗口实现；仅提前部分传输也可利用，不要求 H2D 必须在 RETURN 前全部完成。图中时序是机制示意，不是实测加速曲线。
- `PREPARE_HOST` 只备份稳定的缺失 FULL，不直接释放 HBM，也不投机备份 Mamba；真实压力下由原生路径回收已备份的冷页。恢复 Mamba 必须与目标 checkpoint 匹配。
- 准入依赖实际数据就绪。FULL 可通过原生逐层 pipeline 执行；必要状态未就绪时仍须等待。ACK、请求提交、原生准入和首次复用是不同事件。
- 终态 child 只释放已无用途、无活跃/共享引用且无在途依赖的私有后缀。容量交接经过原生空闲列表，不表示 parent 独占预留或复用相同物理页号。
- 事件/统计缓存不缓存物理执行许可。独立 CPU 推理和 forward 后规划不代表全部控制开销均异步，也不证明已获得稳定的 JCT 或吞吐优势。

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
