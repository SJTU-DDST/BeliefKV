# MARS 与 MORI 基线适配

2026-11-23。论文评测以压力感知的首次物化、增量备份与按需卸载、JOIN 父 KV 恢复为主。
短 workflow 优先与 R5 组合评分记入历史调度实验。55 秒档优先复用论文执行手册列出的
已归档同配置结果。[论文评测手册](../implementation_plan.md#paper-evaluation-runbook)

## 共用 SGLang v0.5.20

两臂应用独立补丁，共用 BeliefKV SGLang 提交 `c5ad39a1e1285532335139fa3bc33d49c7eabcf6`
和 Git tree `434c35dd66211d0a230717c88bf1452056769a40`。准备器用该父版本、规范补丁及
工作区重建引擎 tree，并固定作者源码、回放代码、工作负载、模型和执行配置哈希。
[MARS v0.5.20 补丁](../../patches/mars-sglang0520-paper-replay.patch) ·
[MORI v0.5.20 补丁](../../patches/mori-sglang0520-paper-replay.patch)

| 系统 | 作者规则与移植差异 |
| --- | --- |
| MARS | 从作者提交读取调度函数、队列和配置语法树，复用 30 请求窗口、时间片、反馈、老化和抢占对象选择。workflow 层运行作者 root 准入，使用当前请求的已捕获输入长度，并以 SGLang 原生 KV 用量替代 vLLM 查询。TraceLab 回放以工具计时器提供因果就绪，CPU 工具池队长固定为零。 |
| MARS 原生执行 | 用 SGLang paged radix 执行作者选定的批次，支持多个部分 prefill 与 decode 混批。按 16-token 页结算容量，按实际短缺回收页，forward 完成后登记可复用的完整页。vLLM 的块哈希、同轮已分配前缀可见性与 SGLang 的 radix 匹配、驱逐时序存在差异。当前移植使用同步调度，关闭 SGLang overlap schedule。记录这些差异和 MARS 适配的控制 CPU 成本。 |
| MORI | 运行同一作者 `MultiBackendRouter`，保留默认 5 秒调度 tick、相对空闲度和分级放置规则。每个并发 agent session 独立 program。`mark_program_type`、`release_program` 和 `batch_program_ops` 接入 v0.5.20，保留 HBM/Host 类型顺序、驱逐 pass 记账和 H2D ACK 后的 Host 回收。使用原生锁、容量检查和传输队列。 |
| MORI 回放输入 | 两个引擎版本共用已提交请求的精确 prompt 长度，并以其前 256 个 token 的 SHA-256 分组。作者在线入口用聊天 JSON 估算长度，以 system 文本和工具定义分组。冻结请求库提供 token、停止参数和会话身份。精确长度路径跳过作者的在线字符比更新和基于该比例的等待请求重估，报告中需注明这一系数和分组输入的差异。 |

作者 README 和运行入口对新数据集未规定离线训练步骤。MARS 直接使用作者 CLI 默认参数和
在线 KV/工具队列反馈。MORI 使用运行中观测的 program 空闲度、容量和共享前缀命中反馈。
两侧依赖同一原有 trace 和 frozen overlay，不用测量结果拟合门槛。
[MARS 复现说明](https://github.com/Afterglow231/MARS_preview/blob/d2e34d007ad2013b4a2513efdb9b2f9b606369f6/README.md) ·
[MORI 复现说明](https://github.com/cblmemo/mori/blob/b4f6a1d50fe50a09b522f85333fa432871533690/README.md)

共用准备入口使用 `--engine sglang0520`。`--mori-write-back author` 保留作者原生批写回，
`--mori-write-back matched` 启用与现有 55 秒归档相同的单节点上限。匹配模式沿用旧
MORI 回放的每节点 D2H ACK 等待，运行时会校验生效值。MARS 使用 FA3、归档 MoE 配置、
2,048 每步 token 上限、24 个运行请求和 805,184 个可用 HBM KV 槽位。MORI 使用相同模型、
kernel profile、原生请求上限和 2,048-token chunked prefill，保留 805,198 个 HBM KV 槽位
及 200 GB Host。两边使用原生位置采样及终态回执。

[55 秒匹配写回计划](../../experiments/reports/d3_reclaim_r5_20261118/paper_author_baselines_20261123/mean55_sglang0520_matched.json) ·
[55 秒作者写回计划](../../experiments/reports/d3_reclaim_r5_20261118/paper_author_baselines_20261123/mean55_sglang0520_author.json)

```bash
python -m pip install --no-index --no-deps --no-build-isolation ./configs/causal_replay_plugin
env BELIEFKV_MARS_AUTHOR_SOURCE=../references/MARS-official-20261123 \
  python scripts/run_paper_author_baseline.py --engine sglang0520 \
  --mori-write-back matched --prepare "$PAPER_BASELINE_PLAN"
python scripts/run_paper_author_baseline.py --plan "$PAPER_BASELINE_PLAN" --print-launch mori
```

两份计划均通过 133 个 workflow、20,895 个请求、4,620 条工具时长覆盖及固定 18/38 请求量
核验。两臂通过真实 v0.5.20 CLI 解析和六个回放插件 hook 的加载检查。66 项专项 CPU 测试、
394 项共享回放测试和 23 项 MORI 关闭时的原生驱逐测试通过。MARS 测试使用原生批次、
radix、请求行和 CPU 模拟的分页分配 kernel。MORI 对拍覆盖两级驱逐、共享前缀所有权、
控制 RPC 和 H2D ACK，以受控事件模拟传输完成。

恢复 GPU 实验后，先检查模型及实际 KV 槽位、MARS 分块混批与抢占、MORI 两级传输及
写回上限、原生 token/停止原因和取消排空。记录移植的实际开销，再采集正式对比。

## 保留的作者引擎

MORI 作者发布以 `b4f6a1d50fe50a09b522f85333fa432871533690` 固定，上游 SGLang
标注为 `1264a2e9f`，本地分支 `mori-paper-sglang-v0.5.10` 指向原发布提交。原 fork 的
setuptools 版本探测会回退为 `0.0.0.dev0`，用 Git 提交标识这份原始引擎。
`--engine legacy` 保留基于作者引擎的准备与回放。

| 系统 | 公开源码与本地适配 |
| --- | --- |
| MARS | 作者 `d2e34d007ad2013b4a2513efdb9b2f9b606369f6`。运行同一 root 准入函数和 vLLM `mars` 调度。root 准入后，其 child 和后继继承资格，root workflow 完成时归还名额。请求就绪时用实际输入长度替换初始 prefill 估计，TraceLab 回放的工具计时器对应无额外 CPU 工具排队。 |
| MORI | 作者 `b4f6a1d50fe50a09b522f85333fa432871533690`。复用作者 `MultiBackendRouter` 和 SGLang 两级放置。每个 agent session 使用独立 program，按原有因果依赖提交和等待。结束或上下文压缩时释放旧 program。token 输入直接给出可见长度，按实际输入的前 256 个 token 对共享前缀分组。 |

[MARS 公开仓库](https://github.com/Afterglow231/MARS_preview) ·
[MORI 公开仓库](https://github.com/cblmemo/mori) ·
[MARS 引擎补丁](../../patches/mars-paper-replay.patch) ·
[MORI 引擎及调度器补丁](../../patches/mori-paper-replay.patch)

两套发布版位于 `../references/` 下的 `MARS-official-20261123` 和 `MORI-official-20261123`。
适配位于 `../.worktrees/` 下的 `mars-paper-replay-20261123` 和
`mori-paper-replay-20261123`。准备器结合发布 commit 和补丁验证 Git tree，并固定工作负载、模型配置、
Python 适配源码、MARS 编译产物及包元数据的哈希。回放入口记录引擎原生输出 token 和时间戳，校验提示前缀摘要、输出
token 及 EOS/长度原因。网络合并多个流式输出时保留各原生时间戳与增量。

## 匹配设置

- 同一 Qwen3-Coder-30B-A3B-Instruct 检查点、H200 和 24 个运行请求上限。MARS 的 2,048 token
  是每步 batch token 上限，MORI 的 2,048 token 是 SGLang chunked prefill 大小。使用同一批
  133 个项目、20 个初始 workflow、seed 35 的原始到达表和 4,620 条工具时长
  overlay。CPU 已核验 20,895 个请求的精确 token 来源、原生停止和生成参数。最大输入加生成预算
  为 261,968 token。
- MORI 与 55 秒参考配置均为 805,198 个 HBM KV 槽位、200 GB Host 和写回上限 1。MORI 的
  适配在每次单节点写回后等 ACK，保留作者的驱逐顺序、program 标记及 H2D 后释放 Host 副本。
  原生回执记录正在生效的值。两套 SGLang 实现的传输流水线在报告中注明版本。
- MARS 作者系统使用纯 HBM 前缀缓存，16-token block 向下对齐到 805,184 个槽位。写回上限
  对它不适用。MARS 用于端到端系统对比。隔离首次物化门控贡献用同引擎、同两级预算的 D3
  root-only 与 root+child 消融。
- JCT 从计划到达计时，包含门控等待。固定 18 条均自然完成后取消后台运行。固定 38 条包含
  预热组，在该组全部自然完成时计算整组 JCT。对应原生回执应有 2,991 / 6,519 个请求与
  362,157 / 834,692 个输出 token。回放摘要记录两个组的完成范围和每条测量 workflow 超过
  最后一次计划到达的时长。共用窗口为 `[43.75860606112181, 1287.750239494445)`
  秒，计入其中所有 workflow 的输出。跨引擎的 decode-only 吞吐、实际重算和物理迁移归因需要
  进一步补齐可比的原生执行记录。

## 作者引擎运行入口

已生成[冻结的 55 秒回放计划](../../experiments/reports/d3_reclaim_r5_20261118/paper_author_baselines_20261123/mean55_r2.json)。
`scripts/run_paper_author_baseline.py --prepare <新计划路径>` 验证整套 CPU 输入并写入两套
`server_argv` 和 `server_environment`。启动登记由共享 GPU 的预约与 owned-process 生命周期
管理。每套引擎首先做有界 GPU 资格，核对模型、实际槽位、token/停止、准入及两级迁移，再运行
完整回放。已启动专用服务时，`--replay mars|mori --plan <计划> --base-url <本地服务 URL>
--output <新回放路径>` 保留回执、生命周期事件、准入台账、共同窗口输出、固定 18/38 的 JCT 与
取消后的空队列读数。正式性能结果还需服务端实际退出、原生流水线排空和复核。

## 作者引擎验证状态

MARS 的 `mars` 环境使用 Python 3.12.16 与 PyTorch `2.8.0+cu128`。CUDA 12.8 构建完成后，
vLLM 核心、MoE、内存分配器、FA2、FA3 和 FlashMLA 扩展均通过加载检查。MORI 的 `mori-paper`
环境使用 Python 3.11.15、PyTorch `2.9.1+cu128` 和 `sglang-kernel 0.4.1`，适配引擎、调度器
及 kernel 均通过加载检查。两套环境均通过 `pip check`，实际 CLI 均成功解析冻结计划。

291 项 CPU 回归通过，覆盖作者准入和释放、child 继承、采样替换位置、流式合并后的原生回执、
固定组完成与尾部覆盖。GPU 实验仍暂停。恢复后先用有界真实请求核对长上下文分块、原生 EOS 和
长度停止、实际容量，以及 MORI 的单节点写回与读回，再执行两条完整的 55 秒回放。
