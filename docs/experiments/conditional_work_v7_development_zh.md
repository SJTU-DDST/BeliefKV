# v7 工作头与近端动作时机开发

本页记录v6结束后的CPU改进与下一对live实验依据，不是密封测试，
也不是已经完成的吞吐收益验证。

## 输入与拟合

phase head、MiniLM encoder和phase threshold保持部署值不变。
只拟合条件剩余生成工作，不拟合parent首次服务或action净收益。
输入均来自已送达正文、既有通知、历史和已有decode进度；
EOS、RETURN和未来服务仅用于标签与评价，不是在线输入。

新增v6两侧真实100 ms快照，按明确的FINALIZE/协议干预记录
分开处理自然工作。两侧各8个允许的2048步收尾workflow仍留在
端到端结果，不改变吞吐分母。有效训练快照17546条/83个task；
Astropy校准20个workflow，10个selector、10个interval；
Sphinx开发评价9个workflow/31个终态请求。快照、请求和workflow
不是同一独立单位。Astropy/Sphinx多次用于开发，不称新密封测试。

比较原部署总长度头、重拟合线性剩余头、log-work分位数头、
正文进度加末段权重分位数头。最后一类单独估算正文进度，字符/4
只是模型特征，不是物理KV token数。末段加权只用于训练损失，
不能称为真实未加权分布的P50。

旧产物保持clip后加margin语义；新signed候选扩张后clip并重新
校准。该修正本身未改善点误差，也未补出v6早期动作。比较器已读取
真正部署的work head，导出器保持原有encoder路径和SHA。

## 同一集合结果

| 31个Sphinx终态请求末段 | Token MAE中位数 | P90绝对误差 |
| --- | ---: | ---: |
| 原部署头 | 101.92 | 201.36 |
| 重拟合线性头 | 55.05 | 141.08 |
| 未加末段权重log分位数 | 26.63 | 159.60 |
| 正文进度/末段加权 | 43.73 | 166.86 |

未加权log头在剩余不超过64 token的16个请求上，MAE中位数
由124.04降至21.44。32-token末段仅两请求，不能称充分末端统计，
也不能按固定TPS换算成墙钟精度达标。

| v6自然训练回放96个终态请求末段 | Token MAE中位数 | P90绝对误差 |
| --- | ---: | ---: |
| 原部署头 | 53.00 | 229.75 |
| 未加权log分位数 | 47.44 | 128.55 |
| 正文进度/末段加权 | 31.71 | 65.17 |

V6已进入拟合，上表不是新泛化证据。phase分数逐位相同。
正文加权改善压力末段尾部，但Astropy selector log pinball为
0.16504，劣于未加权0.14608；下一GPU pair选择未加权，不按
Sphinx结果挑版本。候选及不足/负结果保留。

## 首次触发

未加权头用1秒center窗口，自然首次触发42次、无工具轮次误报，
提前到RETURN中位2.91秒，28次超过2秒。500 ms窗口13次，
无工具误报；快照距native EOS中位471.84 ms，距RETURN中位
1.88秒。无服务与协议回流仍留在墙钟标签，不称RETURN已预测准确。

11个v6观察到Host-only parent的合格请求队列内，500 ms未加权
只覆盖一个，距EOS/RETURN为471.84/577.61 ms。正文加权覆盖两，
但一个提前4.75秒，数量增加不等于更适合加载。250/100 ms没有
覆盖该队列。此回放未计推理/传输延迟，无连续Host驻留或live
容量证明，不是实际controller action。

## 观测信号修复

旧路径正常生成结束后仍要求近期高分forecast、足够生成数和TPS，
短报告可能无候选。新路径独立记录正常stop、无native工具标记和
非空正文证据；来源为已收到正文frame，或已完成reasoning分隔符
后的有界64-token尾段。不记录原文/token，不添加GPU算子。
reasoning-only、空白、length、abort和internal不作为完整正文证据。
该判断不终止agent、不修格式、不改变自然语言回复有效性。

有证据时建立仅H2D阶段，不改变收尾准入优先级，不等待NN。
仍检查live身份、完整ALL JOIN只剩一个child、真实Host-only
安全输入、容量、传输服务和节点预算。日志标记
`observed_no_tool_eos`，不计作pre-EOS语义预测成功。
已知生成工作为零不意味着已知RETURN时刻，提前量仍按实际动作审计。

默认协议窗口50 ms；v7显式250 ms，写入plan/runtime state。
165项CPU回归、bash语法和diff检查通过；GPU效果待验证。

## 单pair配置

计划：`configs/migration/qwen35_v7_work_development.json`。
84-root单波，reactive后predictive；running48、Host200 GB/NUMA1、
Device Mamba/FULL0.9及Host匹配比例不变。context131072、
completion8192、workflow14400秒、graph2048/32步reserve、
prompt及seed21不变。不加Agent guard、canary、人工驱逐、额外重复
或旧eligibility提升。

部署候选为 `child_semantic_work_live_v7_quantiles`，composite SHA：
`126e9447789c4361df436383a42afc8db092ca7cf085e8af24eb83aa39feb74d`。
center500 ms，独立协议窗口250 ms。两侧共用通知、收尾优先级、
PREPARE和真实压力回收，预测侧早加载。这是开发组合修改，不分离
各组件因果收益。一对live结果仍不足以证明独立吞吐收益。

## 产物

- `experiments/models/child_semantic_work_live_v7_linear/`。
- `experiments/models/child_semantic_work_live_v7_quantiles/`。
- `experiments/models/child_semantic_work_live_v7_body/`。
- `experiments/analysis/work_quantile_project_comparison.json`。
- `experiments/analysis/work_quantile_v6_comparison.json`及逐快照CSV。
- `experiments/analysis/work_quantile_v6_windows.json`。
- `experiments/analysis/work_body_v6_comparison.json`及窗口回放。
