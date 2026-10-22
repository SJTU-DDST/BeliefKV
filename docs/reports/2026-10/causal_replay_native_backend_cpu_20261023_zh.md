# 原生终态回执与因果回放 HTTP 后端

日期：2026-10-23，北京时间。应用基线 `c98a212`。V129 仍在使用
冻结应用 `e371f05` 采集一次预先登记的真实 native 轨迹。本次没有修改
该冻结运行、任务、prompt、引擎补丁、容量或采样配置。

## 已解决的接入问题

原生 telemetry 文件约每秒刷新一次。若 HTTP backend 等待日志可见才
返回原生完成回执，会给每个后继额外引入诊断写盘等待。新的辅助后端
不以文件刷新解锁依赖，使用现有原生 terminal output 扩展随响应返回
回执；日志和原生 telemetry 继续作为事后核对材料。

`causal_replay_native` 通过 SGLang 已有 general plugin/AFTER hook 扩展
`Scheduler.get_output_streamer_class`，继承现有输出 streamer。
终态元数据走既有 customized_info、detokenizer 和 tokenizer 响应路径。
没有修改 canonical engine patch，也没有增加正式策略的调度循环。

插件作为 `configs/causal_replay_plugin` 的小型本地包安装到独立目录，
仅辅助回放进程将其加入 Python 路径并设置
`BELIEFKV_CAUSAL_REPLAY_BRIDGE=1`。缺省不开启。
原生强制输出仍需要 `--enable-custom-logit-processor`。
未来操作器必须绑定冻结应用、已资格验证的引擎、插件配置和实际进程，
不能仅凭客户端报告认定启动成功。

## 回执与停止条件

回执在原生 request 已 terminal、生成输出即将发送时取得当前单调时钟。
这个 CPU 边界早于最终 HTTP 送达，不声称是 CUDA kernel 完成计时，
也不依赖较晚的 telemetry callback 或后台日志刷新。

回执读取原生对象自己的逻辑输入 SHA-256、输出 token IDs、六项停止参数、
原生 finish reason、因果身份及 session/generation/namespace。
它不把私有强制序列当作实际输出证据。后台端还独立核对 HTTP 中的
request ID、prompt/completion 用量和 finish reason。

输入或输出不同、缺失/重复回执、时间域不匹配、时间越界、原输出预算
改变、停止原因改变、session 改变都会拒绝。JSON 比较保留数值与布尔
类型差别。输出仍使用原 max/min、ignore_eos、stop 条件；没有将观察到的
输出长度设为新的固定预算，也没有合成一个 EOS 控制事件。

终态 receipt 的输入哈希和输出复制有成本，只发生于辅助回放的请求结束
边界。双方应使用相同桥接路径，并在 GPU 上检查强制采样、桥接与原生
批次流水线的影响；CPU 结果不能证明它们没有性能扰动。

## 因果执行与 session

`CausalHTTPBackend` 只处理执行器当前交付的事件和 ready 请求，不向
控制器发送整个未来 DAG、输出序列或未来工具结束时间。
精确输入通过 raw `/generate` 提交，避免重新模板化或合成 padding token；
私有录制输出只交给强制采样数据路径。

后端保持 source 的因果身份、采样 seed、物理 context 和 session。
同 context 并发、终态 context 重入和重复完成请求拒绝。
真实 RETURN/CANCEL 触发异步 session close；compaction 在下一条请求前
等待旧 close 的 HTTP 接受；workflow 结束和清理会排空所拥有的 session。
close HTTP 成功不等于 HBM 已物理回收，报告始终保留这个区别。

执行失败或 coroutine 取消会向原生服务端 abort 对应的已提交 request ID；
abort 和 close 失败不能静默变成成功。原始异常与清理异常分别记录。

运行入口 `scripts/run_causal_gpu_replay.py` 要求完整源总体，拒绝覆盖已有
结果。事件按本轮时钟记入权威日志；有 live event socket 时才发送给
原生控制器。后继按本轮原生回执解锁，工具保留原持续时间和并行关系。
各请求保留完整实际终态回执，便于独立复核；不以最终汇总用量替代逐请求
身份和 token 验证。

## CPU 证据

- V2938 保留首轮 143 通过、2 失败的结果：测试夹具的模块定位和 native
  fake request 缺少 cache 字段。修复夹具后 V2939 的 54 项通过；
  没有将原失败报告覆盖成成功。
- V2940：104 项聚焦检查通过，含完整 CPU HTTP mock、异步执行器、实际
  native 输出 payload、采样预算保留、session 压缩、取消/abort 和结果保护。
  HTTP mock 不执行模型，不是 GPU 回放证据。
- V2941：142 个模块、4673 项、原有 2 个 subtest 通过，无失败、错误或
  skip；保留 V2935 的全部 4612 项并新增 61 项原生桥接、后端和入口检查。
  本轮指定真实本地 tokenizer，沿用一个真实 Docker progress 测试的
  原范围外 deselect。
- V2942：最终应用、测试、配置和 canonical patch 的源码绑定。
- V2944：在独立目录本地安装插件包，未修改 Python 环境的全局安装。
- V2945：GPU 隐藏时使用真实 SGLang 插件发现、HookRegistry 和 Scheduler
  factory，确认插件被发现、hook 应用、选到回放输出类。没有实例化 scheduler、
  加载模型或执行 GPU。

## 剩余资格与性能目标

仍需源采集完整 token/停止和依赖审计、真实 GPU 下强制输出进度验证、
EOS 对齐以及 Full live control/session 生命周期检查，随后才运行相同
真实轨迹的 Full/native/简单调度对照。

入口的逐请求原生 token/停止验证与实际 GPU/源码/策略资格分开记录。
即使入口完整返回，也不独自置 `gpu_replay_qualified` 或
`performance_accepted` 为 true。不得把 CPU mock、HTTP 回执字段、
相同总 token 数或数据面接入当作性能验收。

受控回放固定原轨迹中已发生的模型决定，raw token 提交也不等同于
原始 frontend 路径，不能替代自主动态 spawn 的端到端实验。保留完整
JCT、普通长尾、guard 与质量限制，仍以同工作量吞吐/JCT及 KV 管理的
增量收益验收。当前目标 active，尚未达到性能要求。
