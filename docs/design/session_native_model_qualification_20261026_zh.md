# Session 恢复的独立模型验证

此入口为 free-page H2D fence 候选补齐 session 模型验证，不替代完整
workflow JCT 与全程吞吐验收。北京时间 2026-10-27 11:00 前禁止 GPU，
目前未登记或执行该模型检查。旧 AgentX 无 session 模型验证器及其冻结
队列保持原样；不重启中止的实验。

## 为什么使用独立小池

空闲的生产大池可能使两个 session 全部驻留，得到相同输出也不能证明
发生过恢复。本 fixture 使用 Qwen3-Coder-30B-A3B-Instruct、BF16、
49152 Device token、8 GB Host、context 49152、running/decode24、
chunk4096 和 seed21。两个不同 source workflow 的真实输入各截取前
32768 token，每个 session 的 FULL KV 约 3.22 GB，二者无法同时驻留。
输入仅用于这个已声明的正确性 fixture，不改写完整重放的请求或 token。

独立配置为
`configs/migration/session_native_model_qualification_20261026.json`。
Native 与 Full 都开启同一个 free-page fence。正式性能实验仍使用
v230 的原请求、完整输入/输出、工具时间、生产容量与普通长尾。
该小池检查不提供生产压力下的进度保证或性能收益结论。

## 实际执行与证据

每个 arm 重启模型并依次执行 cold parent、resident parent、child、
recovered parent，共四个请求。所有 parent 请求输入与 cache namespace
相同，使用真正的 native radix session；child 使用不同 session 和
namespace。独立 session helper 分配 handle，在最终返回后发送 HTTP
close，并保留 native generation 证据。HTTP close 成功仍不等于物理
页面回收已经完成。

Full 使用受控、如实标注的后台 SPAWN 与阻塞 JOIN。先确认增量备份
结算，再运行无法与 parent 同时驻留的 child，使原生容量检查触发回收。
child 的模型请求真实完成后发出 RETURN，随后等待实际 JOIN H2D ACK，
再运行 parent。Native 用原生 write-back/需求恢复，不安装 Full 控制器。
这不是采集新自然 workflow，也不把八 token 的 length stop 伪装成 EOS。

八 token 输出来自原生 greedy sampler，不读取来源的输出作为强制目标，
不安装 replay plugin，不启用 custom logit processor。要求各 arm 的
cold/resident/recovered parent 实际输出 token 全等，最大 logprob
差值不超过预先声明的 0.05。

恢复证据还必须同时满足：

- Native recovered 请求至少有 8192 token 来自真实 Host 恢复。
- Full parent 首次服务时至少 32767 token 已在 Device；此前至少
  8192 token 对应的 JOIN H2D 获得原生 ledger ACK。
- ACK 的同一 command 必须在该 parent 首次服务时提供 native allocation
  位于实际 prefix 的 FULL 复用证据，并有同 agent 的恢复准入消费。
- 重新采集的八个模型请求实际 session ID、generation、输入、输出、
  namespace、停止条件与有效采样种子均与提交记录对应。重复建 session、
  无传输的缓存命中或仅有逻辑传输计数不能通过。

每个 arm 收到两个新鲜的 telemetry 空闲快照后才关闭 owned server。
执行入口先重新运行真实 native writer/页污染/拷贝组件检查；该组件与
模型检查均通过前，不将候选当作 GPU 已验证的 engine。

## 启动与来源

`scripts/qualify_session_native_model.py run` 在读取 source 和冻结之前
检查固定预约截止时间与最新 hold/pause；`execute` 重新检查，旧登记不能
绕过预约。GPU 忙时等待两次新鲜空闲观察，不终止外部进程。新 hold、
失败或超时只清理自身进程，所有原始结果保留。

冻结时保存原始 source 注册、输入证据、选定请求、实际 prompt 和完整
来源 hash；启动组件前重新从不变 source 验证。Launcher 默认仍校验
原 base commit；此候选通过显式 `SGLANG_SOURCE_BASE_COMMIT` 声明新的
base，并保留 HEAD 精确匹配及 canonical patch 反向检查。独立组件还会
核对完整实际 native tree，指定 base 不意味着任意补丁获得资格。

预约结束、资源可用且没有更新限制后，入口为：

```bash
python scripts/qualify_session_native_model.py run \
  --reference /home/longhao/experiment/ops/v232_locobench_agent_cold_tools_20261026.registration.json \
  --engine-checkout /home/longhao/experiment/.worktrees/sglang-free-page-h2d-fence-exec-20261026 \
  --canonical-patch patches/sglang_v0520_free_page_h2d_fence_20261026.patch \
  --port 18648 \
  --output-dir experiments/raw/session_native_model_20261027
```

CPU 测试使用 HTTP/event stand-in 和真实 token capture parser，检查
协议、采样、generation、ACK/首次复用证据、来源冻结、预约与启动脚本。
其通过不等于实际 CUDA、模型数值或完整策略生命周期已经通过。

共享报告 `v241_session_native_model_shared_cpu_20261026.xml` 通过
2083 项及四个子测试，无失败或跳过；完整保留 v240 的 1857 项，包含
52 项新 session 检查及旧模型验证器、launcher/环境回归。
`v241_session_model_probe_inputs_cpu_20261026.json` 已从原 v230 capture
验证两个真实 session 输入与每 token 98304 字节的 Host 几何。
最终来源绑定为 `v241_session_native_model_sources_cpu_20261026.json`。
