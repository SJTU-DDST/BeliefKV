# Qwen3.5 Native 训练数据重置记录

日期：2026-09-24

## 结论

此前 Qwen3.5 native-reactive 训练集导出全部退出本轮训练路径。旧批次合同中可见
`hard_limit=512`、`reserve=32`（约 step 480 即强制 FINALIZE）；部分后续批次将
总限改到 2048，但仍存在 middleware 收尾、重复工具抑制、tool circuit breaker
或格式修复。旧批次也没有本轮要求的 `eviction_attribution.jsonl`，无法按块追踪
FULL Host eviction 后的命中和重算，因此保留原始证据、废弃导出表。

新采集唯一保留的强制 agent 安全策略是 LangGraph `recursion_limit=2048` 与
32-step reserve：约在 step 2016 进入有界 FINALIZE。384-step soft budget 及重复/
停滞 pattern 只记录 telemetry，不改变 prompt 或屏蔽工具；重复调用抑制、tool
circuit breaker、completion gate、格式修复均关闭。reserve FINALIZE 所影响的标签
必须记为 intervention/censor，不可视作自然完成。

## 失效导出

以下目录中的 `dataset_manifest.json` 改名为 `dataset_manifest.invalidated.json`；
派生 JSONL 表删除。原始 `workloads/`、`server/`、逐 workflow trace/result、
`TRAINING_EXCLUSIONS.json`、容量 census 和校准配置保留。

- `experiments/raw/qwen35_native_reactive_128root_train_20260923_v1/qwen35-native-reactive-train-batch-01/dataset`
- `experiments/raw/qwen35_native_reactive_128root_train_20260923_v2/qwen35-native-reactive-train-batch-01/dataset`
- `experiments/raw/qwen35_native_reactive_128root_train_20260923_v3_pilot/qwen35-native-reactive-train-batch-01/dataset`
- `experiments/raw/qwen35_native_reactive_128root_train_20260923_v4_pilot/qwen35-native-reactive-train-batch-01/dataset`
- `experiments/raw/qwen35_native_reactive_128root_train_20260923_v5_formal/qwen35-native-reactive-train-batch-01/dataset`
- `experiments/raw/qwen35_native_reactive_128root_train_20260923_v5_guard_observe_pilot/qwen35-native-reactive-train-batch-01/dataset`
- `experiments/raw/qwen35_native_reactive_64root_65_35_20260923_v1/qwen35-native-reactive-overlapped-128root-train-r0/dataset`
- `experiments/raw/qwen35_native_reactive_overlapped_128root_train_20260923_70_30_v1/qwen35-native-reactive-overlapped-128root-train-r0/dataset`

另外，`experiments/processed/qwen35_native_reactive_join64_train_20260922_v2_reassessed`
中的派生 JSONL 表删除，其 `dataset_manifest.json` 改名为
`dataset_manifest.invalidated.json`；旧的未校准模型
`experiments/raw/qwen35_native_reactive_overlapped_128root_train_20260923_70_30_v1/frontier_qwen35_native_train_uncalibrated.json`
已删除。旧 workflow `workspace/` 是可从冻结镜像重建的 checkout，已清理；
trajectory、patch、sandbox audit 和 server telemetry 不是 workspace，不在清理范围。

清理执行结果：8 个 manifest 均已改名，8 个 dataset 下 21.31 GiB 派生 JSONL
已删除；586 个 workflow workspace checkout 已删除；reassessed 派生目录中
4.58 GiB JSONL 与旧的 31 MB uncalibrated model 已删除。workspace 数复核为 0，
旧标准 dataset manifest 数为 0；文件系统可用空间由 311 GiB 增至 456 GiB。
与 raw server 不完全相同的 `workloads/server` 副本全部保留。

## 新采集配置

使用冻结的 v5 overlapped 128-root plan：64 roots 在 `t=0`，另 64 在 `t=60s`；
单 SGLang v0.5.20 server、128 client inflight、running/graph48；Qwen3.5-35B-A3B
BF16；NUMA node 1 上 180 decimal GB Host pool，FULL/MAMBA=70:30；
`mem_fraction_static=0.94`。采集必须包含逐请求 telemetry 与
`eviction_attribution.jsonl`，并用按 workflow 分组的 frozen train split 导出。

## 128-root 批次后的 harness 修订

2026-09-24 首轮 128-root 原始采集留下 128 份结果：127 份 completed，
1 份 error。旧的任务 gate 仅放行 45 份；79 份带
`no_successful_test_command_observed`，因为命令白名单未识别 Django 原生
`python tests/runtests.py`。其中 50 份的唯一拒绝原因就是该字段，47 份原始
execute 输出实际包含非零个测试的 `Ran N tests ... OK`。原计数不代表只有
45 份具有有效性能/调度遥测，也不能据此认定其余任务修改都失败。

`django__django-11149` 的最后三次模型响应均以 `finish_reason=length` 截止，
每次 4096 个 completion token 全部计入 reasoning，无可见正文和结构化终态；
后两次是无效的格式重试。4096 是**单次**模型生成上限，而非整条工作流的上下文
窗口。新采集将 Qwen3.5 默认预算改为可覆盖长推理的 8192（可显式覆盖），
采集模式不再强制 root 的 `WorkflowCompletion`、不发起格式重试；
自然语言正常返回保留为 completed，空白或长度截断返回记为 incomplete，
不冒充任务成功。严格的结构化终态模式仅用于明确启用任务 completion gate
的其他 harness。`measurement_valid` 改为反映独立的系统测量/JCT 资格，
不再等同于任务自报告完成资格；测试命令及测试列表不再作为训练或测量 gate，
也不自动调用额外的测试验证器。实际工具调用和输出仍保留在轨迹中供审计。

该批次首次导出拒绝的直接原因是 `eviction_attribution` 计数契约错误：
status 的 700276 是队列逻辑输入条数；同一输入可展开多条物理归因记录，
JSONL 实有 859164 行。修订为归因流物理行数不少于逻辑条数，其余原生遥测流
仍逐行严格一致，writer 错误/丢记录/处理错误仍 fail-closed。此项是导出
读数修正，不会把旧的模型调用或受干预轨迹改写成新采集数据。

## 重新采集与旧批次处置

2026-09-24 的首轮 overlapped 128-root 采集存于
`experiments/raw/qwen35_native_reactive_overlapped_128root_70_30_20260924_v1/`。
其 128 个 workflow workspace checkout 已删除；逐 workflow 的 trajectory、
result、patch、sandbox audit 与原生服务遥测保留。该批次可用于排查原生调度、
传输和 eviction 归因，但 4096-token 单次生成预算、旧终态约束及其中一份
错误结束的 workflow 使其不能作为本次重置后的正式训练批次，也不能与新批次
混合拟合。旧导出的 `native_request_evidence.telemetry_complete=false` 是导出
计数修复前生成的陈旧结论；不要仅凭该旧 manifest 推断原始遥测全部丢失。

新批次从独立的
`experiments/raw/qwen35_native_reactive_overlapped_128root_70_30_20260924_v2/`
启动，tmux 会话为 `beliefkv-qwen35-train-20260924-v2`。沿用冻结的 64+64
到达计划与 graph48/70:30 容量标定；单次生成预算为 8192 tokens，
2048-step recursion limit 保留 32-step 收尾空间。完成后必须检查
采集合同、原生遥测完整性和每个预测头的可用标签，再决定正式训练资格。

## v2 暂停与 child 空终态修复

2026-09-24 暂停 v2：截至暂停约 50/128 个 root 有结果，其中 36 个 completed、
14 个 incomplete；20 组 JOIN 满足，30 组因 child 取消而结束。抽查约 378 条
initial child report，131 条是 `child returned neither structured data nor text`。
`JOIN_TIMEOUT` 在这里表示 child 取消，不是墙钟超时。14 个 incomplete root 的
最后回复均为空且 `finish_reason=stop`；其 12--417 个 completion token 均被记为
reasoning token。已停掉本批采集进程和服务，清理了挂载 v2 workspace 的残留
Docker 容器，保留原始 trace、patch 和遥测。**v2 为截断诊断批次，不导出
正式训练标签。**

Qwen3.5 的本地 chat template 默认开启 thinking；SGLang 原生响应将 thinking
放在 `reasoning_content`，不保证每次都会产生可见 `content`。独立端口实测：
相同请求在 1024-token thinking 预算下只有 reasoning、以 `length` 截止；
显式 `chat_template_kwargs.enable_thinking=false` 时返回正常正文且 reasoning
token 为零。这个对照说明解析后的字段并非 LangChain 凭空丢失的正文；v2
那些短 `stop` 响应的原始服务端字节未留存，不能据此断言所有空输出都由同一
模型停止机制引起。

运行时现只对 `stop` 或 `length`、正文为空、无工具调用、确有 reasoning 的
模型调用在同一 agent turn 内重试**一次**，重试请求关闭 thinking；root、
native child 和 planned child 使用同一规则。其他请求保持原模型配置。
重试原因与结果写入 sandbox audit，仍为空时保持原有失败语义，不得把空文本
算作成功 child；完整新批次不得与 v2 的测量混作正式训练集。

独立端口 `18001`、无 Host pool 的单 workflow 在线验证：
`pytest-dev__pytest-5262` 的 4 个 initial child 均正常返回（其中一条
reasoning-only `stop` 由关闭 thinking 的重试恢复），1 次 JOIN 满足、0 次
child 取消，root 自然完成。该验证说明修复可在真实 child/JOIN 链上运行，
**不能**凭一个低压样本推断正式 64+64 高压采集中的整体取消率。

旧 checkout 清理：修复与单 workflow 在线验证完成后，按授权仅移除
2026-09-24 v1、v2 和上述 pilot 中 `workflows/.../workspace` 路径下的
1,039 个可重建 checkout（包含 planned child），复核三处剩余数均为 0；
原始 trace、`model.patch`、审计和服务遥测未删除。新的
`qwen35_native_reactive_overlapped_128root_70_30_20260924_v3` 使用原
64+64 高压计划独立重新采集，启动时 FULL/Mamba 容量校验通过；v3
workspace 不在清理范围内。v3 在完成之前不得视为合格训练集。

## v3 导出、离线训练与清理结果

2026-09-24 v3 自然结束并完成 dataset 导出：128 个 root 中 124 个
completed、3 个 incomplete、1 个 runner error；127 个不同 workflow
保留了可核验的训练行。dataset manifest 的完整性检查通过，原生逐请求
telemetry 无丢记录或 writer error；`formal_local_training_eligible=true`，
覆盖 7 个项目、127 个 task、134,235 条合格 frontier 决策、121 条合格
JOIN reentry、33,110 条合格 external wait。没有为了纳入错误 workflow
而放宽训练器门槛。`formal_training_eligible=false` 仍如实标记运行时干预
与尚无 held-out validation，不应将本地拟合视为正式评估。

使用 `scripts/train_qwen35_native_frontier.py` 的正式多样性门槛拟合并重新加载
核验了 `experiments/raw/qwen35_native_reactive_overlapped_128root_70_30_20260924_v3/frontier_qwen35_native_v3_offline_uncalibrated.json`。
模型元数据标记 `offline=true`、`online_eligible=false`、`uncalibrated`。
训练内 boundary/tool-terminal 分类准确率分别为 0.943/0.908，
remaining decode、next output、prompt growth 和 child RETURN 剩余时间的
加权 MAE 分别为约 145 token、80 token、546 token 和 725 秒。这些均为
**训练内指标**，尤其 725 秒不是 held-out JOIN 精度，不能用于声称
PREFETCH 时序已经准确。

虽然导出记录了 1,273,619 条 native HiCache transfer ACK，当前 hook
只有完成时刻和 pool units，没有物理提交时刻、实际字节、DMA 起始边界
及并发条件；PCIe service 合格标签为 0，训练器如实将该头标为
`not_fitted_no_verified_evidence`。简单重复本配置不会补上该证据；
需要先增强原生传输埋点，再另行采集并校准，不得从 ACK 伪造 DMA 服务时间。

本轮发现并修复两个后续采集故障：Docker `rm --force` 超时改为记录
`sandbox_stop.status=failed` 与容器身份，同时保留原 workflow result
及 `sandbox_cleanup_status`；超限 prompt preflight 改为发出
`ContextOverflowError`，交由已有上下文中间件尝试压缩后重试，
压缩仍不够时继续拒绝，不静默裁剪历史或放宽硬上限。两项修复是在 v3
结束后落地，不能追溯性地把 v3 的失败 child 改成成功样本。
v3 共 573 个 workflow/child workspace checkout 均已删除；trajectory、
patch、sandbox audit、server telemetry、dataset 和 checkpoint 保留。

## 原生传输埋点与独立校准的后续契约

v3 的 1,273,619 条旧 ACK **不能**事后补齐 PCIe 服务标签。后续源码
在 FULL/Mamba 合并传输提交时记录提交墙钟与单调时钟、当时尚未 ACK
的字节数；原生 ACK 携带包括 sidecar 在内的总字节数，完成后记录
ACK 墙钟、单调时钟计算的提交到 ACK 延迟和已同步的 CUDA transfer
stream 事件区间。后者是传输 stream 上逐层复制/内核的区间，
**不等于纯 PCIe DMA 时间**；尚未 ACK 的字节数也不是实时总线利用率。
导出器将这两种延迟分开持久化；只有真实字节数、提交/ACK 边界及
有效的 stream 计时齐备，才开放 `native_transfer_stream` 服务标签。
无计时事件、旧 ACK、缺失实际字节的记录仍拒绝训练。此代码尚需
新运行验证物理时序；v3 原有 manifest 与 checkpoint 保持不变。

校准不同于拟合：用未参与训练的完整 workflow 测量 JOIN 时间
P10/P50/P90 的偏差/覆盖率、工具返回概率和需求区间的覆盖率，
再对概率温度、区间 slack 及动作时序门槛进行修正。v3 所有合格
workflow 都在 train split；训练内 MAE 不能校准自己。正式校准必须
在冻结的独立 calibration split（当前脚本还要求项目不重叠），
使用相同模型、采样策略、负载压力和精确 runtime/patch 指纹，
保持 test split 不可见。现有 v3 checkpoint 缺少 `fit_projects`
来源且绑定旧 patch 指纹，不应直接对升级埋点后的新实验作正式
校准；下一批先以新 patch 重新拟合，再采相同条件的独立 calibration
批次。硬件池的容量标定与概率/时间区间校准是不同的步骤。
