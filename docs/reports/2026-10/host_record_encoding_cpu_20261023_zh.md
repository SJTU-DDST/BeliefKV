# Host 驱逐与重访观测编码降本

日期：2026-10-23，北京时间。开发位于 main，GPU 目标尚未验收。

## 修改与证据范围

最新完整同工作量对照仍为 V133b。后台 writer 的采样计时显示，
Host 块驱逐与请求重访处理的局部成本高于普通服务记录。采样不能
外推为全程控制成本、GPU 空闲时间或可消除的 JCT。

`v0520_native_telemetry.py` 的四处 Host 块记录仍使用标准库 JSON，
而其他高频记录已使用公共 `encode_record`。现在统一这四处编码：
身份不可用、驱逐、重访归因和重访摘要。保留相同的事件、字段、
完整前缀哈希、容量与待匹配计数、Host 压力及精确源 token 采集。
采用已有严格编码器，不增加新缓存、线程、扫描或运行时开关。

这是 native 与自定义策略共用的观测路径修改。后续正式对照必须
同时使用相同新代码，不能把降低 native 观测成本前后的差异算作
自定义 KV 管理收益。V133b 原始数据和冻结源码保持原样。

## CPU 测量

固定基线为 `9f3f5e0deef49b4f2e776281a06472b850f52d57`。
已有 benchmark 新增 `--host-records-only`，对完整 writer 方法测量，
在每一对基线/候选执行后检查 JSON 文本、哈希、计数器、历史和
待匹配状态完全相同。正常 GC，交替执行，10 次预热、101 个有效样本；
报告保留全部 wall/thread CPU 样本和源码摘要。

| 场景 | token 数 | 原 CPU P50，ms | 新 CPU P50，ms | 变化 |
| --- | ---: | ---: | ---: | ---: |
| 单块驱逐 | 1,024 | 0.052031 | 0.049162 | -5.51% |
| 多块重访命中 | 1,024 | 6.725862 | 4.496312 | -33.15% |
| 普通重访 | 32,768 | 0.658986 | 0.585546 | -11.14% |
| 分支重访 | 32,768 | 1.290936 | 1.194078 | -7.50% |
| 单块驱逐 | 131,072 | 1.623407 | 1.621742 | -0.10% |
| 普通重访 | 131,072 | 1.782263 | 1.714154 | -3.82% |

长前缀单块驱逐基本不受益，其输入处理和哈希仍占主导，不能将
多块重访的 33% 降幅推广到所有事件或端到端性能。benchmark 不含
文件 I/O、并发 writer/scheduler、GIL 争用、DMA 或 GPU 计算。

复现命令：

```bash
CUDA_VISIBLE_DEVICES=99999 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
PYTHONHASHSEED=21 \
/home/longhao/miniconda3/envs/beliefkv-next/bin/python \
scripts/benchmark_native_prefix_writer_cpu.py \
  --baseline-revision 9f3f5e0deef49b4f2e776281a06472b850f52d57 \
  --iterations 101 --host-records-only \
  --output experiments/reports/v3148_host_record_encoding_cpu_20261023.json
```

相关 native telemetry/record JSON 回归 187 项通过。完整保留 V3146
全部测试的 V3149 通过 5248 项及 2 个 subtest，没有失败或 skip；
既有 Docker 用例不在继承集合中，未扩大验证声明。
V3150 由真实 `qualify_sources` 生成，`qualify_report` 验证通过。
没有修改引擎，未重新运行未受影响的引擎资格集合。

证据文件：

- `experiments/reports/v3148_host_record_encoding_cpu_20261023.json`
- `experiments/reports/v3149_host_record_encoding_qualified_cpu_20261023.xml`
- `experiments/reports/v3150_host_record_encoding_qualified_sources_20261023.json`

## 实验接续

V134d 的 native 自然源采集继续使用已冻结且资格匹配的 `9f3f5e0`，
不因公共观测编码修改替换正在等待的队列。它不是正式性能对照组。
预约守护和 CPU 等待进程均已核验存活，状态仍为禁用期等待；
北京时间 2026-10-24 06:00 后还需 marker 解除和 GPU 空闲。
新源完成后，先验证真实复用、压力与 GPU replay，再在新冻结代码
上做同源比较。目标继续 active，不把局部 CPU 下降宣称为 JCT 收益。
