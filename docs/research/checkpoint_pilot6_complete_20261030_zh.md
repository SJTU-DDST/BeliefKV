# 六项目 checkpoint 采集结论与接口修正

冻结bf75bbd7的六项目自然采集和CPU后处理均已完整结束，原进程已退出。
保留1145个请求、39454528个输入token、145284个输出token，完整token、
因果关系及fresh drain证据均保留。这里的输入总量包含重复完整prompt，
不等同于固有模型工作或实际prefill量。

## 形态仍不合格

| 指标 | 前一批parallel pilot6 | 本批source checkpoint6 |
| --- | ---: | ---: |
| 模型工作P90/P10 | 2.151 | 1.644 |
| child调用P50/P90 | 24 / 39.1 | 23 / 38.8 |
| 多成员JOIN比例 | 25.00% | 23.33% |
| root并行轮次中位数 | 1 | 1 |
| child合计模型工作占比中位数 | 70.23% | 76.40% |

本批37个child、30个JOIN，其中23个singleton、7个双成员JOIN、2个
嵌套JOIN。七个多成员组均有实际共享decode采样，但不足以证明整体
具有足够的并行组竞争。六个root均经历过最终返回前的并行委派提醒。
全部自然返回符合原来的终态协议，不代表任务行为完全符合新交付物。

这两批任务目标并不相同，表格只比较形态，不能作为策略性能对照。
本批不能扩成正式负载，不启动Full或Thunder比较；保留全部负面证据。

## 实际输入核对

`scripts/audit_checkpoint_prompt_delivery.py` 使用完整native token包、
原tokenizer和逐请求hash重建child prompt，而非只检查配置或audit hook。
对每个child检查首个、提醒后的及最后一个已完成调用边界。

37个child中36个超过8次软目标；这36个的第9次prompt全部包含第8次
提醒。所有抽查的child prompt均含checkpoint契约。由此可以排除这些
样本在对应边界没有收到新提示的解释；不能据此证明模型遵从提示或
已经核对了每一个中间prompt。

实际委派仍有“实现完整Session 1/2/3”这样的宽任务，三个child分别
执行47、46、56次调用，root将主要阶段交了出去。另一个只读诊断
workflow修改了原始scene.py。不能仅因最终自然返回便将其当作符合
只读交付物的成功样本。

## 结构化委派参数

下一候选保持同六项目和同交付物，改变task接口及明确的任务访问类型：

- task必填description、subagent_type、known_paths、existing_evidence、
  finish_condition、write_paths。首项只描述一个问题；其余字段将已知
  上下文和返回边界显式传入child，避免仅依靠parent写自由文本。
- supporting edit最多声明两个写入文件，更大的实现由root承担；这是
  委派范围约束，不是模型步数或输出限制，也不要求将一个大改动人为
  拆成固定DAG。child仍可完成在途工具、编辑和嵌套JOIN后自然报告。
- 两个只读任务显式声明task_access=read_only，移除root/child的显式
  文件编辑和patch工具及bounded-editor角色，task参数要求write_paths=[]。
  execute仍可运行必要检查，因此这不是OS级写隔离；完成后仍须审计
  实际源码变化，不能据工具列表直接宣称零写入。
- runtime使用同一实际task schema校验SPAWN/JOIN输入；缺少字段或越界
  的请求不能先变成虚构child/JOIN。原task的执行、并行、回调、私有状态
  隔离及原生session关系保持一致。

协议3与原协议2分别记录。仍用child4/8、root8/16软预算，自动首工具、
原最大深度和自然返回；不增加模型调用硬上限或固定轮数。接口是否
真的缩短child、增加自然多成员JOIN，必须由新采集验证。

## Patch导出问题

本批两份model.patch无法直接由git解析。原command_output对diff执行
strip()，丢掉尾部空白上下文，再补一个换行仍不足以恢复hunk。审计
验证了保留workspace的原始diff与旧文件在strip后完全相同，记录原始
文件hash、解析错误及未strip的diff hash；没有覆盖旧artifact。

新实现仅在diff采集的正常和恢复路径保留完整stdout，并原样写入
model.patch。新增真实Git回归验证尾部上下文及git apply --numstat。
该错误影响patch artifact，不改变已记录的请求token或上述形态结论。

完整源和prompt交付证据在
`experiments/reports/checkpoint_pilot6_20261030/finish_r1/`、
`experiments/reports/checkpoint_pilot6_20261030/prompt_delivery_r1.json`。
下一候选配置位于`structured_checkpoint_pilot6_20261030/`，仍需当前
完整CPU绑定、冻结源、新鲜GPU入口检查和采集后的形态审查。

结构化候选的完整CPU绑定已通过5571项及12项子测试，保留此前全部
5308项身份，无失败、错误或跳过；2094项绑定输入执行期间未变，
原生引擎未变。早期focused检查的1个Docker跳过已在完整集合中用
明确指定的缓存镜像实际执行，原focused与失败断言调试记录均保留。
原生Qwen3-Coder parser另通过8项CPU检查，覆盖两种schema、双调用、
1/7/64字符流式分块、数组及空写入范围；这不是模型行为合格的证明。

## 新候选实际采集

协议3候选从冻结d58efb67启动，注册为
`ops/workflow_structured_checkpoint_pilot6_source_20261030_r1.registration.json`，
端口18908，原生引擎仍为11b84f7c。新鲜1/8/24采样及BF16检查通过。
独立只读启动观察验证196份完整输出前缀、六个root语义边界及六个child
创建语义边界；实际协议3、4/8child、8/16root、2048分块、mixed关闭、
running24、seed21及原生准入关闭符合声明。测量进程nice0、GPU独占。

CPU后处理2205954在nice19且CUDA隐藏，确认为实际存活，等待完整源、
fresh drain和实际采集进程退出。其注册及启动证据保存在
`experiments/reports/structured_checkpoint_pilot6_20261030/`。
这仍是进行中的自然形态采集，不是完整或合格源；后续还要执行实际
prompt交付/只读diff审计。没有登记Full、Thunder或其他GPU后续。
