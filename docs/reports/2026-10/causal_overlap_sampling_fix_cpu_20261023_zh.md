# 回放采样的 overlap 进度与终态修复

日期：2026-10-23，北京时间。基线 `a3c3f1f`。
原 V130 只完成 CPU 准备，没有启动 GPU；原冻结源码和 registration
保留，状态记为 `superseded_before_gpu`，后续使用新冻结计划。
V129 的冻结真实轨迹采集不受本次修改影响。

## 原生执行顺序暴露的两个问题

当前引擎 `Scheduler.event_loop_overlap` 先 launch 当前 forward，再
process 上轮结果。普通 sampling 缺省在 worker forward 中执行，
因此用 `len(Req.output_ids)` 强制下一 token 会读到尚未提交的进度。
CPU 混批重排检查不足以覆盖这个跨轮次问题。

SGLang 已有 `SGLANG_ENABLE_DELAY_SAMPLE`：开启后 forward 仍保持 overlap，
sampling 被延后至上轮结果处理之后。它提供本后端需要的已提交进度，
无需增加全局同步或更换原生 scheduler。

原生流水线还允许已经结束的 request 对应一个未交付的额外 forward/
sampling。旧 helper 会在序列耗尽时对它报错；把额外采样一概当作
“native 未正确 EOS”是不准确的。新的 helper 对真实 `Req.finished()`
行保留原始 logits，仅强制仍活跃的行，不再为终态行造一个新 EOS。
非终态行越过录制序列仍拒绝；实际交付输出和 finish reason 继续由
独立 terminal receipt 完整验证。

启用回放插件现在要求原生 delayed sample flag 为 true。插件框架
可能捕获注册异常，所以还必须验证实际响应带有有效回执，不能只凭
服务端进程或 HTTP 健康接口判定桥接已启用。

## CPU 复现

V2953 使用原生 scheduler/worker 源码记录的 forward、previous-result、
sampling 顺序，保持同一 `[3, 4, 9]` 序列：

| helper / 原生采样顺序 | CPU 夹具结果 |
| --- | --- |
| 旧 helper / inline overlap | 采样 `[3,3,4]`，提交 `[3,3]` 后检测分歧 |
| 旧 helper / delayed overlap | 提交 `[3,4,9]`，在原生终态额外步骤上误报 |
| 修复 helper / delayed overlap | 提交 `[3,4,9]`，终态额外步骤原样通过 |

报告保留基线和候选 helper 哈希，以及对应 native scheduler/worker
源码哈希。这是 CPU 顺序夹具，没有执行模型、GPU 或性能测量。

V2951 的 85 项聚焦检查通过，新增原生 delayed flag 正/反例、混合终态
行、全终态行，以及完整跨轮进度/额外采样步骤。
V2954 的 143 个模块、4686 项及原有 2 个 subtest 通过，无失败、错误或
skip，完整保留上一轮 4681 项并新增 5 项。沿用原范围外一个真实 Docker
progress 测试的明确 deselect。V2955 绑定最终源码。

## 比较边界

后续受控 Full、native、简单调度组都必须使用相同
`SGLANG_ENABLE_DELAY_SAMPLE=1` 并记录该条件。真实源采集使用原来的
缺省采样配置；不能把受控回放与源采集的绝对性能差别归因于 KV 管理，
也不将原生 delayed sample 的收益当作自定义设计收益。

本次只改辅助回放 helper 和桥接要求；正式 end-to-end 的策略和
source 采集配置没有更改。GPU 检查仍需验证实际 token、EOS、session、
真实参数和进程身份，完整同轨迹性能比较与原始动态 end-to-end
验收仍未完成，目标保持 active。
