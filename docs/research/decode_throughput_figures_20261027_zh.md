# Decode 时序与分布图

这些图由 CPU 离线处理生成，没有启动 GPU 实验。最终输出位于
`experiments/figures/decode_diagnostics_20261027_v344/`。绘图脚本为
`scripts/plot_causal_decode_diagnostics.py`，定量口径仍遵守
`docs/research/throughput_metric_contract_20261027_zh.md`。

## 同条件历史对比

V286 Full 与 V295 ThunderAgent 工具衰减版均使用 V269 工具时间表、
完整 3674 个请求、145565966 输入 token、549671 输出 token、
相同 native tree、实际 Device/Host 容量、公共传输栅栏和 seed21。
图中橙线为 V286 Full，蓝线为 V295 工具衰减版。
这是历史策略的比较，不是最新 V330 的匹配对照。

- [时序图 PNG](../../experiments/figures/decode_diagnostics_20261027_v344/matched_v269/decode_timeline.png)
  / [PDF](../../experiments/figures/decode_diagnostics_20261027_v344/matched_v269/decode_timeline.pdf)
- [分布图 PNG](../../experiments/figures/decode_diagnostics_20261027_v344/matched_v269/decode_distributions.png)
  / [PDF](../../experiments/figures/decode_diagnostics_20261027_v344/matched_v269/decode_distributions.pdf)
- [共同 batch/context 分组图 PNG](../../experiments/figures/decode_diagnostics_20261027_v344/matched_v269/decode_geometry.png)
  / [PDF](../../experiments/figures/decode_diagnostics_20261027_v344/matched_v269/decode_geometry.pdf)
- [时间桶 CSV](../../experiments/figures/decode_diagnostics_20261027_v344/matched_v269/time_bins.csv)
  / [分组 CSV](../../experiments/figures/decode_diagnostics_20261027_v344/matched_v269/geometry.csv)
  / [完整证据 JSON.gz](../../experiments/figures/decode_diagnostics_20261027_v344/matched_v269/evidence.json.gz)

| 完整指标 | V286 Full | V295 工具衰减版 |
| --- | ---: | ---: |
| Decode 观测输出 token | 545997 | 545997 |
| Decode worker 区间/s | 1876.615 | 1647.692 |
| Decode worker 输出率/token/s | 290.948 | 331.371 |
| 普通平均 decode batch | 3.143 | 5.023 |
| Worker 区间加权 batch | 4.295 | 7.526 |
| 完整平均 workflow JCT/s | 1669.294 | 1700.062 |
| Makespan/s | 2903.095 | 2324.678 |

时序中约 10--25 分钟，Full 的 batch 通常明显更小，decode 输出率也
较低；后期 Full 更早完成一些 workflow，但最后一个完成更晚。因此
平均 JCT 与整批完成速度可以呈现不同方向，不能只用一个总输出率
替代二者。

共同 exact batch 与粗 context 区间的 worker 输出率没有普遍下降，
但这不是算子执行时间等价的证明：桶内上下文、批成员、恢复等待和
其他运行条件仍可不同。当前观察支持继续检查资格、并发和连续性，
不能据此认定唯一瓶颈或声称修复必然获得相同比例收益。

## 最新 V330 单组诊断

- [时序图 PNG](../../experiments/figures/decode_diagnostics_20261027_v344/latest_v330/decode_timeline.png)
  / [PDF](../../experiments/figures/decode_diagnostics_20261027_v344/latest_v330/decode_timeline.pdf)
- [分布图 PNG](../../experiments/figures/decode_diagnostics_20261027_v344/latest_v330/decode_distributions.png)
  / [PDF](../../experiments/figures/decode_diagnostics_20261027_v344/latest_v330/decode_distributions.pdf)
- [时间桶 CSV](../../experiments/figures/decode_diagnostics_20261027_v344/latest_v330/time_bins.csv)
  / [完整证据 JSON.gz](../../experiments/figures/decode_diagnostics_20261027_v344/latest_v330/evidence.json.gz)

V330 使用 V311 时间表。匹配 ThunderAgent 因用户预约中断，
不将其部分运行或 V295 的不同时间表叠加为正式对比。
用户随后释放 GPU，匹配的完整 V345 工具衰减版已单独启动；
其[运行记录](v345_v311_thunder_decay_control_20261027_zh.md)
和 CPU 后处理任务将保留 V344 原图，并在 V347 输出新的匹配比较。
V330 前中段 batch 较宽，约 31 分钟后加权 batch 接近一，
末段墙钟输出率下降，worker 输出率也随 batch 变化。
全程普通平均 batch 为 6.251，worker 区间加权为 9.023，
decode worker 输出率为 354.557 token/s。这些数值不能与上表
直接组成新策略的匹配加速比。

## 图形口径

时序横轴统一为各自第一条 workflow_start 后的真实分钟，完整覆盖
到最后 workflow_end，不按完成比例拉伸时间。固定非重叠 60 秒分桶，
无平滑、预热裁剪或共同尾部裁剪；结束后不延长曲线补零。最后不足
60 秒的桶使用实际长度，以空心圆标记。

四个时序面板分别为：

1. 实际 decode token delta / 墙钟桶长度，包含该桶内的非 decode 时间。
2. 同桶完成的 decode batch 输出 / 这些 batch 的 worker 区间之和。
3. 同一组 batch 的 worker 时间加权 batch size。
4. 实际 workflow-end 事件的累计完成数。

Token delta 和完整 worker 区间均归入该 batch 的完成时间桶；它不是
在一段长响应结束时把整条请求所有 token 放入一个桶。没有 decode
的墙钟桶为零，worker 输出率为空值，而不是虚构的零效率。

分布图左侧按每个桶的实际墙钟长度加权，包含零产出时段；
右侧按每个 batch 的 worker 区间加权。右侧横轴采用 symlog，
20 token/s 以下为线性区域，保留零和全部正值范围。两者的分母不同，
不能直接比较两条图中的数值或当作独立重复实验。

共同 geometry 图横轴为 exact batch size，纵轴为 batch 内请求
平均上下文的区间：<32K、32K--64K、64K--128K、>=128K。
只有双方各至少 20 个 batch 的单元格显示比值，缺失单元格不代表
零性能或无限加速；全部组的原始计数及实际平均上下文均保留在 CSV。

脚本检查完整回执、token hash、原生停止、全部 workflow、计时表、
实际服务参数/容量、公共栅栏、原生 clock/sample 覆盖及输入文件
字节/hash。每组全部 545997 个 decode token 均逐桶对账。
每种策略仍只有一轮，不绘制跨实验置信区间，不接受可重复性能结论。

## 复现

CPU 绘图环境位于
`/home/longhao/experiment/ops/cpu_plotting_20261027`，
Matplotlib 为 3.9.4，继承的 NumPy 为 2.3.5。
使用 `CUDA_VISIBLE_DEVICES=99999 PYTHONHASHSEED=21`，
并以 `nice -n 19 ionice -c 3` 运行；不修改 serving 环境。
输出目录必须不存在，避免覆盖旧证据。
脚本输出普通 JSON；归档使用 `gzip -n` 无时间戳压缩，解压后内容与
原始证据逐字节相同，PNG/PDF/CSV 的内部摘要不受压缩影响。

```bash
env CUDA_VISIBLE_DEVICES=99999 PYTHONHASHSEED=21 nice -n 19 ionice -c 3 \
  /home/longhao/experiment/ops/cpu_plotting_20261027/bin/python \
  scripts/plot_causal_decode_diagnostics.py \
  --arm 'Full V286=experiments/raw/locobench_network_join_admission_20261027_v286/full' \
  --registration 'Full V286=/home/longhao/experiment/ops/v286_locobench_network_join_admission_20261027.registration.json' \
  --arm 'ThunderAgent decay V295=experiments/raw/locobench_network_thunder_decay_20261027_v295/thunderagent_acting_decay' \
  --registration 'ThunderAgent decay V295=/home/longhao/experiment/ops/v295_locobench_network_thunder_decay_20261027.registration.json' \
  --bin-seconds 60 \
  --title 'Matched V269 timing: Full vs ThunderAgent with tool decay' \
  --output-dir /tmp/beliefkv-decode-figures-reproduction
```

V344 的 32 项针对性 CPU 检查通过，无失败或跳过，覆盖部分尾桶、
零产出、时间权重、比值汇总、时钟越界、条件不匹配，以及已完成
文件的部分尾行、追加或等长修改。图形已人工查看。
V343/V343b 为保留的早期绘图稿，已移至
`/home/longhao/experiment/ops/decode_plot_drafts_20261027/`，正式引用 V344。
