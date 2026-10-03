# 36-root 修复后重启

本轮回到既定主场景，不延续重叠 64+64 的高压诊断。
只先完整运行 predictive，不自动启动无有效迁移机会的 baseline。
有效机会、首次消费和池压力明确后再决定同配置 reactive 对照。
不使用短 canary，也不人为逐出 KV 来制造传输。

## 固定配置

- Qwen3.5-35B-A3B，patched SGLang 0.5.20 staging，`beliefkv-next`。
- 原 36-root 对照的同一 manifest/选择方式，单波到达，
  `--max-workflows 36 --concurrency 36`；server running=48。
- FULL/Mamba BF16，静态 HBM memory fraction=0.94，
  Mamba/FULL 字节比例=0.9。
- Host 总预算 200 GB，NUMA node 1，比例匹配实际 Device 两池，
  原生 write-back；不改变 Host 原生驱逐规则。
- 14400 秒 workflow 墙钟预算，graph limit=2048、32-step
  FINALIZE reserve；单请求/工具 timeout 不因此扩大。
- `native_in_graph_1to4`，seed=21，completion=8192，
  context=131072，native-reactive guard profile，
  不要求结构化终态。
- 完成通知、有限收尾优先级开启，预测语义观察和 JOIN H2D 开启，
  PREPARE_HOST 关闭；不把 PREPARE 消费作为 H2D 必要条件。
- 阶段头/encoder/阈值冻结，工作头采用
  `child_semantic_work_frozen_phase_20261001_v1` 组合产物。
- 新安全输入 checkpoint、空 finished insertion 引用保留、
  原生压力 Host 保留、稳定 JOIN 预取预算和工具 ID 规范化。

## 观察项

检查真实安全 checkpoint、session/epoch 和 Host 可恢复副本，
将“状态仍在 GPU”与“Host 来源缺失”分开。分别统计模型时机、
容量拒绝、H2D issue/ACK、首次服务和 FULL/Mamba 实际复用。

观察 FULL/Mamba Host high-water、驱逐与可归因重访/重算，
不要把全部 uncached input 当作重算；检查是否依然频繁在首次
服务前重驱逐预取数据。模型评估使用 EOS 前因果快照，
RETURN 与 parent 首次服务单独计时，不以零剩余的 EOS 后
快照冒称准确预测。

有真实 worker、物理账本或遥测故障时停止调查。正常长任务
不因执行超过一小时停止。没有预取机会就报告来源/容量/时机，
不增加并发，不自动重复配置。终态后清理已归档完成 workspace，
保留全部失败现场。

## 产物

目录：
`experiments/raw/qwen35_semantic_h2d_36root_restart_20261003_v1/`。
启动前保存代码 commit、patch/model/manifest SHA256 和实际
运行配置至 `ab_plan.json`；单侧运行明确标为机制观察、非吞吐
对照，并保存按 manifest 原顺序选择的 task ID。此文记录预定
配置，不提前写入结果。
