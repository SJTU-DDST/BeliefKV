# V132 重放降本与源轨迹筛选

日期：北京时间 2026-10-23。性能目标尚未达成。

## 当前重放的具体路径

1. 原生动态采集记录实际 spawn、JOIN、工具开始/结束、模型请求、
   请求输入输出 token、停止条件和 session 生命周期。
2. 离线编译因果图，保留源中工具执行和客户端处理的外部延迟，
   将每次模型服务留作本轮真正执行的操作。
3. 重放驱动在本轮前驱实际完成后解锁后继。独立 workflow 可以并行，
   不照搬源运行的 GPU 排队或服务时长。
4. 请求使用 `/generate` 提交原始输入 token 和 session 身份；
   控制器只接收已经发生的可见事件。
5. 原生模型实际执行 prefill/decode。隔离的数据面固定输出 token，
   使不同 KV 和调度策略执行相同请求内容、相同输出长度和停止边界。
6. 原生终止回执带回输入 SHA、实际输出 token、原生停止原因及
   session 绑定。客户端逐请求验证后才确认完成。
7. 同时验证实际模型、running8、256K、seed21 和 FULL/Mamba/Host
   容量。辅助重放不构成独立任务质量评价。

V131b 原数据面根据 `Req.output_ids` 长度决定下一个输出位置。
原生 overlap 中该列表可能滞后一轮，因此启用 delayed sampling，
等上一轮结果提交后再采样。原生 custom-logit 入口又会查询 GPU
mask 并用 GPU 索引选择 Python 参数和 logits，增加同步。
V131b native 重放比源平均 JCT 高约 14%，这些差异需要校准，
不能因为两组共同使用就当作不存在。

## 新的可选采样路径

`BELIEFKV_CAUSAL_REPLAY_SAMPLING=position` 是隔离重放插件的开关，
要求 `SGLANG_ENABLE_DELAY_SAMPLE=0`。
普通服务未启用重放插件时不注册任何新钩子。

在原生 `ModelRunner.sample` 入口，使用当前 forward 的 CPU
序列长度减去本请求原始输入长度，确定该 forward 的输出位置。
该位置属于当前 forward，即使上一步结果尚未写回 `Req.output_ids`
也不会错用旧位置。分块 prefill 的中间样本和原生终止后的额外样本
仍由原生完成处理丢弃；请求回退使用实际当前长度重新定位。

原生 custom-logit 调用中，根据 CPU 参数识别重放行，
避免查询 GPU mask。目标索引用 pinned CPU tensor 异步传到
设备，然后照常执行原生 logits 处理、argmax、结果传递和停止判定。
没有跳过模型 forward，也没有把记录的 token 直接作为完成回执。

CPU 检查覆盖一轮滞后、分块 prefill、回退、混合健康检查、终态尾部、
参数身份、越界、错误进度和钩子状态清理。GPU 验证仍需检查真实
多请求/多轮精确 token、stop、session 以及实际服务区间。
微基准另增加 position 分发，保留 greedy 和旧分发作组件对照；
组件计时不能直接当成完整 workflow 的加速比。

## 历史候选与证据限制

只按 JOIN、完成资格与尾部形态比较源，不按 Full 加速比选源：

| 历史候选 | JOIN | 嵌套 JOIN | guard | 最后完成间隔 | 最大 JCT/中位数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| V108 predictive | 86 | 11 | 0 | 81.2s | 1.84 |
| V128 no_child_reclaim | 84 | 4 | 0 | 71.5s | 1.49 |
| V125 kv_only | 82 | 4 | 0 | 68.9s | 1.42 |
| V128 native | 78 | 10 | 0 | 97.7s | 1.45 |
| V129 native capture | 73 | 1 | 1 | 644.9s | 1.94 |

V3046 验证 V108 的 36 个 workflow、8461 个服务端请求和 86 次 JOIN
因果依赖完整，但没有完整原始 token 记录。当前 raw 中只有 V129
保存了 `request_tokens.jsonl`。历史文本 transcript 和 token 数量
不能证明准确恢复原始输入、reasoning/stop 特殊 token，因此不将
重新分词伪装成已验证的历史精确重放。

保持现有任务、prompt、动态委派方式、根数量和种子补采原生轨迹。
不强制模型采用固定 DAG，也不通过删除慢 workflow 修整一个源。

## 新源预先登记的选择条件

- 36 个 workflow 全部完成，全部保留。
- 至少 80 次 JOIN。
- 全部符合源 native JCT 资格，无 guard 干预。
- 最后两个 workflow 的完成间隔不超过源全程的 5%。
- 最大 workflow JCT 不超过中位数的 1.8 倍。
- 完整输入输出 token 与因果依赖都通过独立验证。

`run_causal_source_capture.py --source-profile balanced_join` 将
统计与拒绝原因写入 `source_selection.json`。
未达条件的采集及原始退出码全部保留，不进入后续正式对照。
选源条件不读取 Full 的 JCT、吞吐或预测效果。

新源确定后先验证新的原生精确重放路径，再运行同一轨迹的
native、关闭工具等待期 H2D 的 Full 和 JOIN-only。
Full 保留工具预测卸载、prepare、JOIN/EOS 预取、resident-first、
阶段优先；普通 execution-frontier restore/handoff 和短工具连续性
保持关闭。现有 V131b 冻结结果保留，不与新采样路径直接计算加速比。

## CPU 资格与 GPU 接续

V3053 保留原有完整资格，4934 项及原有 2 个 subtest 通过，
沿用原有 Docker progress 范围外 deselect。V3050 保留一次旧容量
用例的偶发窗口过期；V3052 未修改代码单独重跑通过。该用例随后
固定虚拟时钟，仅验证容量和节点上限，原断言完整保留。
生产路径的工具期限和时间判断没有改动。

新采集可在 `--wait-for-gpu --after-comparison` 下提前冻结，
等待前序监督器、服务、驱动和实际资源全部释放，避免误用两组
切换期间的 GPU 空档。等待期间不分配模型或 Host pool。
旧 JOIN-only 完整结果保留，随后停止旧采样矩阵的剩余组，
接续本轮源采集。尚不能根据 CPU 结果宣称重放成本已经消除。

## 接续实现与最终资格

新增 `run_position_replay_calibration.py`，等待完整且合格的源结束，
并验证源监督器和驱动已退出，再分配 GPU。依次执行：

1. 相同容量、模型参数的 8 context、每个 3 请求精确 GPU 检查；
   验证原生 stop/session 及每个回执的实际 `sampling_path=position`，
   并核对服务进程归属和 GPU context。
2. GPU 采样组件校准，保留 greedy、旧 forcing、原生旧分发及新
   position 分发，包含 pinned CPU 目标索引异步 H2D。
3. 独立冷启动 native 完整重放。相对源运行的完整平均 JCT 和全程
   时长额外减速均不得超过 5%，超出则保留结果并停止后续长对照。
4. 门槛通过后接续关闭工具等待期 H2D 的 Full、JOIN-only，
   所有组使用相同 position 路径、容量、输入输出和完整正常尾部。

V3057 的 CPU 复现发现，带已生成历史的请求在回退后分块重算，
中间 prefill 长度可能尚未覆盖已保留输出。现仅在这种 extend
样本上使用保留位置，最终样本仍按 forward 长度定位；decode
向后错位继续拒绝。原生丢弃中间样本的行为保持不变。
该路径是可选重放插件，原生源采集未启用插件。

V3060 最终资格保留 V3053 的全部 4934 项，4972 项及原有
2 个 subtest 通过，原 Docker progress 范围外 deselect 保留。
CPU 检查不替代真实 GPU 校准或完整性能验收。

## V132 采集与 V131b 保留结果

V132 采集冻结为 `fadd80f3c9540d6f5fe8c1f4b4cdb5131922b5e9`，
登记 `ops/v132_balanced_native_source_20261023.registration.json`。
V131b JOIN-only 完整结束后，行政接续操作请求原监督器停止，
未完成的 `no_background_prepare` 不参与完整性能比较。

| V131b 相同原始工作量 | 平均 JCT | 全程时长 |
| --- | ---: | ---: |
| Full | 2928.221s | 5965.544s |
| native | 2802.152s | 5785.155s |
| JOIN-only | 2813.611s | 5828.258s |

三组均为 8165 请求、2352172 输出 token，原始退出码均为 0。
JOIN-only 比 native 平均 JCT 高约 0.41%，Full 仍慢约 4.50%。
这些是旧采样路径的负结果，不因新源选择而删除或改写。

首次 V132 登记的相对 `RUN_ROOT` 使实际输出落在冻结工作区：
`/home/longhao/experiment/.worktrees/beliefkv-v132-capture-fadd80f3c954/experiments/raw/`。
没有重启或迁移正在写入的数据；主 raw 下的 `native_capture` 入口
链接到相同目录 inode，使原监督器可以正常验证实际记录。
操作记录为登记旁的 `.artifact_alias.json`。后续环境构造已统一
将输出路径转为绝对路径，并增加回归。该冻结工作区在数据归档前
不得作为无用工作区删除。
