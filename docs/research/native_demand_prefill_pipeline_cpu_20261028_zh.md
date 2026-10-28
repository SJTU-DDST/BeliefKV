# 原生需求恢复流水线 CPU 结果

2026-10-28。原生需求恢复与驻留 decode 流水线完成 CPU 实现及来源绑定，
完整共享执行通过 3839 项和六个子测试，保留此前 3718 个独立测试身份，
新增 121 个。没有启动或排队新的 GPU 实验；整体相对 ThunderAgent 的
明确、可重复收益仍未实现。

实现和边界见
[设计文档](../design/native_demand_prefill_pipeline_20261028_zh.md)。
候选默认关闭，不改变原有 agent 资格；只对 `PrefillAdder` 已批准且
需要原生 H2D 的实际 prefill 批次暂缓计算，等待期间推进驻留 decode。
支持多成员及 sibling，不新建影子执行队列、普通恢复 TTL 或工具预取。

本次补齐了容易遗漏的两项安全边界：

- ACK 后、重新 match/admit 前，恰好一次释放旧 request receipt。
  native DMA 的 Device/Host 锁与 ACK 继续独立持有，不能随请求取消提前释放。
- 原请求取消后，其他共享该在途前缀的请求仍须得到原生逐层依赖。
  对完整驻留 decode，则只依据现有原生路径证明清除无关 consumer。

新增独立 staged-demand ACK 审计，不修改 native Host-hit 计数。
它按实际 submit 时间、node IDs、FULL token 和字节匹配唯一 untagged
H2D；共享批次字节只计一次，不把整批流量归给单个成员。当前这些检查
使用真实原生 CPU 缓存与分配代码、copy/event stand-in，还不是实际
GPU 服务、隐藏传输或数值一致性的证明。

## 来源与保留的失败

证据目录为 `experiments/reports/prefill_pipeline_20261028/`：

- `qualify_cpu.py`、`shared_r3_cpu.xml/.evidence.json/.sources.json`：
  单次完整执行、前后源码一致、继承覆盖与实际构造器环境核对。
- `seal_native.py` 与 `native_seal.json`：真实 native commit `d3da19c1ca85`，tree
  `729aa0caa7a1ffac84463f46b3d1843d1a6a22e4`，CPU-tested scheduler
  SHA256 `1845ecdb0e97fd863ccacc2ff812f44e58cc93b6c84a4d690f1e6644f8b61407`。
- `native_seal.json` 记录独立补丁 SHA256
  `c9b3ae50490a5023e5d7fd1fb473c97b1c4c7f22923d06211bf1ea6173f86f0b`，
  从 base `fa5cb52a` 在独立 index 重建完全相同的 native tree。
- `initial_helpers_cpu.xml`、`native_helpers_r1/r2/r3_cpu.xml`、
  `configuration_r1_cpu.xml`、`targeted_r1/r2/r3_cpu.xml` 与
  `scheduler_r1/r2_cpu.xml` 保留开发中的设备解析、socket、
  scheduler fixture 及中间通过状态，不能替代最终来源绑定。
  首次 native fixture 的原始失败 XML 含 traceback 缩进，按原字节以
  `native_helpers_r1_cpu.xml.gz` 提交，本地原始 XML 同时保留。
- `shared_r1_cpu.xml` 保留旧 free-page checker 对新增接线的正确拒绝。
  新独立默认关闭 AST 投影通过后，旧 checker 继续拒绝未投影的新树及
  未知变化；生产 free-page checker 没有放宽。
- `shared_r2_cpu.xml` 全部通过，但旧汇总脚本漏用了 launcher 的
  `ONLINE_TOOL_TIMING` alias 映射，实际构造器因此拒绝带 prior 的配置。
  修正后的 `shared_r3_cpu` 重新完整执行并成功核对构造器。

`prefill_review.py` 与完整 `.json.gz` 保存历史 prefill 分析；压缩包与
本地原始 JSON 解压 SHA256 均为
`e0c7d7c32549ffd13bc4d2b70bd1f317eeabb63860f05b25f5a3011ae0de2f41`。
该分析说明总 prefill 工作接近，但关键路径覆盖不同，没有据此把
历史 24.98 秒解释为完整控制 CPU 或可直接扣减的 JCT。

## 服务身份与组件入口补充

后续完整 `service_gpu_r1_cpu` 通过 4060 项及六个子测试，保留
`shared_r3_cpu` 的全部 3839 个身份并新增 221 个。对应
`.xml/.evidence.json/.sources.json` 保留单次执行、前后源码 hash、
实际 launcher 环境及构造器证明；native 仍为 clean `d3da19c1`。
此前结果继续保留，不将不同执行的测试数相加。

新增观察同时检查 helper 已安装并绑定实际 native cache，防止仅开启
配置位却未执行。精确 native tree/patch 已加入 engine registry，
默认关闭投影及新 GPU 资格要求有独立拒绝测试。

完整 demand 审计复用旧 handoff 算子，分别对账 Host 命中、handoff、
暂存 H2D；暂存成员使用 attempt/session/generation 和单调时钟匹配
真实 prefill 及完成回执。它不宣称已隐藏 DMA。相关命令为
`scripts/audit_demand_prefill_pipeline.py`，正式完整运行后才能执行。

真实组件入口 `scripts/qualify_demand_prefill_pipeline.py` 已通过 CPU
证据验证器，但尚未真实执行四个 GPU 场景。该组件使用有限延迟拷贝，
不是模型 decode 推进或性能证明。

组件入口冻结在应用 `7e7e79e9` 后有两次未通过尝试，保存在同目录
`gpu_components_r1/r2.json/.log`。r1 在 native 导入时未指定现有
CUDA_HOME 而退出，场景数为零；r2 使用历史 CUDA13 工具链进入真实
native H2D，但延迟拷贝包装函数未接收 `is_draft` 关键字参数，首场景
因此退出。两者均保留 failed，不作为完成的组件或性能实验。修复仅让
验证脚本透传原生可选参数，不改变 serving 策略或 native engine；
修正来源在 `service_gpu_r2_cpu` 重新完整执行，4060 项及六个子测试
全部通过，保留 r1 的全部 4060 个测试身份，构造器及前后源码 hash
再次核对成功。r2 报告属于新的源码绑定，不与 r1 相加。

## 下一项资格

在正式 FULL 前，仍需冻结当前应用并验证
真实 DMA 与正常准入/取消、共享前缀消费者、恢复期间的实际 resident
decode、非强制模型输出和当前树的位置/session/stop。实际启动必须证明
helper 已绑定及被执行，只有 enable 位不够。

暂存时间继续计入原始提交等待及完整 JCT。后续复用完整 V230/V311 工作、
完整 V345 工具衰减 ThunderAgent，公开 engine 差异，保留全部正常长尾。
工具预取保持关闭；没有根据这次 CPU 通过宣布性能收益或启动其他 baseline。
