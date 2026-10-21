# Native 与 Full 前置状态等待的采样归因

日期：2026-10-22，北京时间。全部验证仅使用 CPU；不改变 GPU 使用禁令。

## 根因

`LayerDoneCounter.wait_until_state()` 有两种现有依赖：

- 没有专用状态事件时，等待最后必要状态层的原生 layer fence。
- 有专用状态事件时，等待该状态事件，后续 FULL 层仍按层等待。

旧采样器仅将专用状态事件记作 `layer=-1/pre_forward_state`。
native 的第一种等待被记作普通的最后状态层，模型随后在同层发生的
等待又与该记录去重。因此 native 的
`gpu_pre_forward_state_dependency_wait_ms` 可能为零，而 Full 的
该分类为正，两者不能直接比较状态等待。

旧总量 `gpu_layer_dependency_wait_ms` 已包括前置 layer fence 的等待。
本次不推翻这个总量，也不把历史零分类解释为没有等待。
去重会忽略同层后续的独立残余等待；旧迹线无法凭空重建该时间。

## 修改及不变的执行语义

采样器增加 `wait_state_layer()`，将计时记录的 key 和实际等待的 layer
分开：记录仍用 `-1`，调用原生 `loading_event.wait(last_layer)` 时保留
真实 threshold。普通模型层使用自己的 layer key。
新增 `pre_forward_state_wait_kind` 区分 `layer_fallback/dedicated_event`。

只有已启用采样、且 callback 可用时采用该入口。未采样、旧 probe、
CUDA capture、重复等待和无 consumer 的路径均保留原有行为。
不增加 CPU 同步，不改变传输/计算的原有依赖，不增加新的执行 barrier。
采样仍为每 16 个合格 prefill 一次、最多 32 个待收集样本；
被采样的 native legacy 路径可能增加最后状态层的一对 CUDA timing event。
该测量成本不能声称为零，GPU 影响尚待验证。

## CPU 复现与回归

V2691 使用冻结旧代码与当前代码，加载两个真实引擎的
`LayerDoneCounter`，配合相同 fake stream/event 时钟：
前置依赖被设为 7ms，模型随后同层的依赖设为零。
旧 native 总等待 7ms、状态分类 0ms；修正后总等待 7ms、状态分类 7ms。
实际 layer wait 调用及顺序保持不变。专用状态事件路径作为对照。
这些数值是人为设置的时钟，仅验证分类，不是实际 H2D/GPU 时间。

应用回归还覆盖同层随后仍有残余等待时分开计量，以及 capture 和重复
等待仍使用真实 threshold。引擎回归验证 optional callback、
旧 layer-only probe 兼容和无 consumer 时不执行等待。

首轮 V2692/V2694 未显式绑定 engine/tokenizer：应用有 23 项跳过，
引擎导入了安装目录中旧源码导致失败。这两份报告继续保留，不计入资格。
最终回归显式使用新 checkout 的 `PYTHONPATH/SGLANG_SOURCE_CHECKOUT`、
本地 serving tokenizer 和 `CUDA_VISIBLE_DEVICES=99999`。
完整资格和登记由后续封存报告绑定，不能只凭定向测试宣称通过。

最终 V2695 应用 3781 项通过，保留 V2685 的全部 3779 项并新增 2 项；
无 skip，2 个 subtest 通过。与之前相同的一个 Docker 沙箱测试明确
deselect，不计通过。V2696 引擎 469 项通过，保留 V2663 的全部 467 项
并新增 2 项，无 skip，46 个 subtest 通过。V2697 绑定应用源码，
V2698 绑定引擎 tree、canonical patch 和完整前后测试集。

## 下轮比较

原 V119 仍未开始 GPU 实验，已因 pause marker 更新退出。
完成应用、引擎完整 CPU 资格后登记新冻结版本；主比较顺序和配置保持
Full、JOIN-only、工程对齐 native，最早北京时间 2026-10-22 06:00
且资源空闲后执行。

下轮同时比较总依赖等待、状态分类和后续 FULL 层等待。
不把修正前后的状态分类差额当成性能改善，也不把采样之和当成
workflow JCT 的可消除上界。
