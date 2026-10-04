# v4 工具等待与 JOIN 联合恢复

## v3 的真实传输规模

v3 的 87 个原生控制器 H2D batch，共传输 10.8041216 GB。
其中 10 个带 BeliefKV PREFETCH_GPU 来源标记的 batch，共
814.940160 MB；其余 77 个 batch 为原生恢复，共 9.989181440 GB，
没有原生/预测混批或未知字节记录。预测标记只是来源分类：
v3 中两次实际 submit 晚于 child RETURN，不能算提前动作。

原生部分 FULL 为 3577 token，即 73.256960 MB；Mamba 为
154 个物理状态单位，即 9.915924480 GB。次数按控制器 batch
统计，不冒充 agent 数或请求数。不能直接把全部原生字节当作
可提前恢复的空间，还需区分工具等待、JOIN、压缩和首次请求。
可复核脚本为 `scripts/audit_native_h2d_sources.py`。

v3 没有加载工具等待 worker，12050 次
`tool_wait_predictor_unavailable` 是未启用而不是工具执行报错。
当时只有 JOIN parent 准备和 JOIN 前 H2D。

## 修复

1. 启动窗口放宽为 1000 ms。它是 runtime 的估计窗口，真实
   submit 到 child RETURN/TOOL_END 的提前量独立审计，不宣称
   修改上限就达到了真实 1 秒精度。
2. 纯预测 load_queue 在安全点立即启动。保留已有 load fence、
   producer/layer-done event，后续命中待完成页的 prefill 仍等待
   同一 producer；队列含原生加载时不抢走其 consumer index。
   容量和生命周期在 enqueue 前再次检查。记录 issued、enqueue、
   controller submit 与 ACK，区分排队和实际服务。
3. ACK 固定/轮询开销不按字节比例放大。按 FULL/Mamba/hybrid
   形态和相近大小选取样本，报告服务 P50/P90；缺相近证据时
   回退，不将几 KB 的延迟线性推成几十 MB 的数十秒。
   v3 回放的 80 MB hybrid H2D 为约 50/81 ms；不是新 GPU 验收。
4. 原生已生成 `<tool_call>` token 时立即撤销该轮的终态预取
   信号，不等客户端完成解析；announce 调用轮与其后报告轮
   分开。未通知的自然回复仍可 RETURN，保留无工具原生 EOS
   窗口，不添加格式修复、返回门禁或 agent 取消。
5. P50 剩余工作在 EOS 前塌缩到零时使用已有上界，不将数值
   clipping 当作生成结束证明。阶段/encoder 权重不变。

## 工具路径

独立加载已校准的工具事件时间模型。验证权重 SHA、训练源
manifest、当前模型 revision 与 SGLang 0.5.20。使用已标注的
“所有活跃工具返回”时间/CDF，不拟合离线不可识别的预取净收益。
原有 `online_eligible=false`、`predictive_action_eligible=false`
不被改写；旧 admission 模型的授权条件也不被放宽。

长等待依据剩余 P10 或短时返回概率选择；PREPARE_HOST 在
有分配压力、Host 空闲可容纳时备份安全输入，native allocator
只在真实短缺时回收独占、未锁定、备份 ACK 已完成的 FULL/Mamba。
优先回收不再使用的缓存规则保留，不人为清池制造机会。

临近工具完成时，runtime 根据滚动 CDF/残余时长、有效 Host-only
副本、当前物理 free-list 和服务证据发起 H2D；原生 D2H 副本
同样可用。TOOL_END、新 epoch、失效 session 或预测变化撤销旧
intent。单等待上下文最多两个加载节点，ACK 仍由物理账本确认。
预算、状态、收益判断均不是模型动作收益输出。

并行工具使用稳定 tool_run_id 跟踪，短工具先结束时保持
WAIT_TOOL。未解析为单活跃工具的并行组暂不进行时间驱动的
投机动作，不改变 agent 的工具并发或强制任何工具返回。
工具/语义两个 worker 的结果 FD 都注册，idle 时保留有界刷新，
避免 GPU 空闲时睡眠一秒错过返回窗口。

## 实验配置与口径

64-root 单波、running=48、BF16、HBM static=0.94，
Mamba/FULL=0.9、Host 200 GB 与 Device 比例匹配、NUMA node 1。
context=131072、completion=8192、seed=21、workflow=14400 秒、
graph=2048/预留32、native-reactive guard profile、自然语言终态。

两侧均启用完成通知/收尾控制、JOIN 与长工具等待的准备及真实
压力回收；只有 predictive 侧启用提前 H2D。因此比较隔离提前
恢复，不是未经修改的 native baseline 或完整算法消融。
启动命令固定加载 tool timing、service seed 和 1000 ms 窗口，
记录所有 SHA。无 canary、无扩大并发、无人工驱逐。

分别报告 JOIN/工具动作、真实提前量、ACK/首次 FULL/Mamba
复用、提前驻留、Host churn、可归因重算与完成吞吐/JCT。
`audit_tool_transfer_windows.py` 对齐实际 controller submit 和
工具完成，不使用 agent 后续首次 GPU 服务代替 TOOL_END。
未经独立 grading 不称正确任务吞吐；配置修复不代表精度或收益
已经达标。
