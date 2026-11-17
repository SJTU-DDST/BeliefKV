# 本轮 GPU 实验结束后的线性整合

2026-11-16 PST，用户要求：本轮 GPU 实验完成后，把本轮修改合并到
同一个分支，保持干净、一致、线性的工作区。这是当前实验目标的后续
交付要求；仅完成 GPU 比较或准备整合计划，不代表已经完成整合。

## 执行时点

按最新用户顺序先完成简化 Oracle，再完成修改后的 D3 四组，保留完整 fixed18 自然 JCT、
fresh drain、实际退出及结果检查。确认相关协调器、监督器和服务 owner
实际退出后，再整合开发源码与整理分支。运行中和待启动实验的原源码、
注册、输入绑定及冻结目录保持当前身份。

- D3：`beliefkv-subagent-materialization-pressure-20261116`。
  原 `gpu_campaign_r4` 的 control 因 `ReadTimeout` 停在 17/18，
  原注册、冻结源码和失败保留；后续使用加入执行兜底的新匹配注册。
  新等待计划为 `experiments/reports/subagent_materialization_20261116/guarded_3600/plan.json`，
  完成当前 Oracle 后才创建 `gpu_campaign_guarded_3600`。
- 简化 Oracle：`beliefkv-simple-path-oracle-20261117` 的
  `experiments/reports/simple_path_oracle_20261117/ready_service_guard_3600/gpu_queue/plan.json`。
- 两条队列的最新状态必须结合实际进程身份和完成证书检查，
  不能只凭状态文件、GPU 短暂空闲或某个阶段完成就开始收尾。

## 整合范围与次序

统一开发入口为 `/home/longhao/experiment/BeliefKV` 的 `main`。
保留本轮必要的依赖修复、工具检查降本、工具权重与准入修复，
按以下次序形成单父线性提交：

1. 补齐 main 尚未纳入、但 D3 与 Oracle 实际依赖的源码修复。
2. 纳入 D3 首次 child KV 物化背压、工作量与容量准备、
   fixed18 停止协议、Host 容量和队列心跳修复。
3. 纳入简化 Oracle：含模型与工具等待的剩余关键路径升序、
   贪心装入、ready sibling 共同推进。按最新完整负结果，后续默认关闭
   额外工具边界抢占，保留普通预测式工具卸载；历史显式候选与证据保留。
4. 统一配置、脚本入口、设计与结果文档，明确已验证行为、
   历史负结果、显式候选开关和证据边界。

Oracle 中继续关闭 ready 连续老化、组合效用加总、JOIN 覆盖份额评分、
主动积累容量。按最新用户要求保留独立的 1800 秒连续 ready 无实际服务兜底，
一次只提升一个精确请求，不影响门槛前正常短优先；
保留原生物理约束，并单独记录触发与实际服务。
客户端读取超时按用户最新要求保持 3600 秒。
少量测量尾部未被背景到达覆盖作为诊断保留，不阻止实验继续。
整合源码不等于证明性能收益，
也不重新启动已经完成且严格匹配的 GPU 对照。

## 已完成的只读预检

本次记录时，D3 为 `7da8736791f9ff45afa01e5dfec2018e68c9bc35`，
简化 Oracle 为 `76f78938636c8172bf2f68f271f23209a7bfe14e`，
共同祖先为 `b07de6a51ca076232ef635f1cda50d4bd33f776f`。
main 为 `6d08c23c`，与 D3 的共同祖先为
`60dfad3c7200adde45251a7a1f26ec7dae8ff226`；
D3 侧有 32 个 main 尚未包含的非等价提交，不能只复制最后一个功能提交。

`git merge-tree --write-tree` 对两个功能分支的预检成功，无文本冲突，
结果树为 `8d8fd9121d4bec6fcbbd335c90b4c7a93eac763c`。
这只是 Git 对象级预检，没有移动分支、修改工作区或完成行为验证；
与 main 的整合还须结合当时最新状态重新审查。

主目录同时存在其他未提交的开发、研究文档和图表。收尾时逐项核对并
保留这些工作，形成与本轮策略修改边界清楚的可追溯提交。
用户随后明确允许将方向探索的本地实验文件与结果直接加入 `.gitignore`。
这类文件保留在原位置，按已声明的方向或日期范围忽略，无须为清空状态而
全部提交。正式调度源码、配置、设计、结果入口及必要测试继续跟踪；
已经被 Git 跟踪的修改仍需单独核对，不能依赖 `.gitignore` 隐藏。
已有实验原始数据、失败记录、注册和冻结证据路径继续可用；
后续开发只使用统一的主分支入口。

## 完成证据

整合后检查配置的生产构造、D3 与 Oracle 的接口协作、
JOIN/sibling、工具让位、实际候选过滤、停止协议和相关 CPU 回归。
必要时解决组合后的行为缺口；文本无冲突不是接口正确的证明。
记录原实验提交到最终整合提交的映射，保持原实验结果对应原冻结版本。

运行期只读检查另见
[简化 Oracle CPU 检查](simple_path_oracle_live_20261117_zh.md)。
其中真实原生终结回调已复现执行兜底漏计最终服务的边界，保留原失败证据。
精确终结请求确认与陈旧身份修复已准备为 `49ce22f7`，101 项相关回归通过。
独立 CPU 工作树 `beliefkv-ready-service-terminal-20261117` 随后将 D3
`e117908e` 以 squash 方式预整合，解决四处文本冲突并保存为单父提交
`1356a093`。791 项相关 CPU 用例实际通过，无失败或跳过，包含首次物化
扣留与终结计时的跨模块检查；失败的初次报告另行保留。
该候选内的说明为 `docs/research/oracle_D3_preintegration_cpu_20261117_zh.md`。
这仍是主目录最终整合前的准备，未纳入 main，也没有改变正在运行或待启动
的绑定源，不能算作当前 GPU 结果的一部分。

CPU 分析脚本的后续修复 `770289a8` 位于
`beliefkv-materialization-readout-20261116`，19 项相关检查通过。
它使分析器识别 `stopped_preserve_*` 等真实终止状态，避免继续空等失败实验。
原失败 D3 队列遗留的只读 observer `8209/start92968391` 已核对身份后
用 SIGINT 终止并确认退出；旧报告、失败状态和 retirement receipt 保留。
这不涉及当前 D3 等待进程或 GPU 服务。新的 D3 campaign plan 创建后，
应针对 `gpu_campaign_guarded_3600/plan.json` 启动新的匹配 CPU readout，
不再跟随旧的 `gpu_campaign_r4`。

随后 Oracle 已完成完整 fixed18、fresh drain、实际退出和匹配比较；
平均 JCT 1276.770573 秒，对照 1274.833468 秒，回退 0.151950%，
共同窗口输出吞吐下降 2.663618%，不保留额外抢占为后续默认。
D3 已实际进入 `gpu_campaign_guarded_3600` 的 control 回放，
后续仍按 pressure、ThunderAgent、native 顺序继续。
新的 `runtime_readout_r2` 已绑定该 plan 并核实实际进程与状态心跳，
初次未产生状态的尝试与启动收据另行保留。

CPU 预整合候选继续增加单父提交 `ff7eae70`，准备脚本默认关闭额外
工具边界抢占，并把普通替换的逻辑与 donor 筛查放在排序之前，
保留动作前复核。最终 413 项相关 CPU 用例实际通过，无失败或跳过。
该候选的说明为 `docs/research/oracle_boundary_off_followup_20261117_zh.md`；
不改动当前 D3 绑定，不插入新的 GPU 消融。不能把此 413 项执行与先前
791 项预整合执行相加，也不宣称已完成 main 最终整合。

随后的单父提交 `20e6332d` 修复 JOIN 恢复 choice 仍记录旧请求/epoch 的
观测问题，将实际授予使用的 grant_keys 传给同一观测函数。
181 项相关 CPU 用例实际通过，包含新后继尚未原生可见时的严格授予关联；
尚未可执行的恢复仍归为 warming，未更改调度或恢复权限。
原 Oracle 的事件审计失败和实际旧记录保留，当前 D3 冻结源码也不热改。

CPU 候选随后以单父提交 `ea577bb1` 增加 Host-block 实际 prefill 审计、
相关测试和证据说明，没有修改服务源码。218 项相关 CPU 用例通过；
完成版 Full/Oracle 均与最终原生 counter 对齐。D3 的独立活体前缀已
确认实际丢失前缀输入执行，完成版两组对比仍待实际退出。
整合时保留该分析入口，以及 `host_block_prefill_evidence_20261117_zh.md`
中区分测量组、背景右删失和不可归因 worker 区间的约束。

最终需确认主开发目录 `git status --porcelain` 为空，
整合起点之后没有新增 merge commit，文档与实际默认配置一致，
本轮源码、必要脚本和验证结果都能从同一分支找到。
提交继续使用 `fix:`、`perf:`、`docs:` 等前缀。
