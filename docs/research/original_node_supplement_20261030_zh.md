# 扩展完整原题候选：Node 工具环境准备

2026-10-30。为增加持续背景人口和可能缺少的短、中档任务，复用已经
采集的53个Python原项目，并从同一LoCoBench原始压缩包准备新的候选。
这不是备用数据集，不是改写任务，也不是另一轮六题正式评价。
当前52项目GPU控制保持冻结；本准备没有登记新的GPU任务。

## 原题与选择

原始包包含十种语言各100个项目。只读清点发现此前来源准备只覆盖了
Python；新增候选还保守排除已采集项目在其他语言中的相同编号家族。
在尚未覆盖的JavaScript项目中，选择此前已声明的18个完整原题，
六个easy、十二个medium，均为原始code_comprehension场景。完整上下文
按当前Qwen3-Coder分词器核对，范围20385--81972 token。

`scripts/prepare_original_node_projects.py`核对压缩包、场景与公开任务
hash，复用原项目导入和完整source/context核验。源码、测试、原题全部
保留。生成文件在
`experiments/reports/remaining_original_projects_20261030/node_preparation_r1/`；
私有reference不进入模型输入。小上下文、原题标称难度与真实工作量
分别记录，不提前把这些候选标成已验证短workflow。

## 实际工具环境

保留旧Python工具镜像，只从固定Node镜像复制真实node/npm：

- 原镜像：`sha256:a0d821c46d8f12f52660a084241f9ad5e75a1c058e3c30e77808757ef35ae8ab`。
- Node来源：`node@sha256:c9063968e5c359c7430892bd48448f8368aa0a76527b816e03bce59fe08bfd93`。
- 新工具镜像：`sha256:ae7747bcc4719f63672af1947af26126982180a8e02ac154e091edfd318d8593`。

实际版本为Node20.19.0、npm10.8.2。选择Node20是为了满足一个原项目的
`>=18.18 <22`要求；每个项目的原始node/npm范围均由真实semver检查。
首次构建用原镜像ID拼成远程RepoDigest而被registry拒绝，未改动旧镜像。
之后给已经核对ID的本地镜像增加专用构建标签，成功完成无网络RUN构建。
直接docker登录shell的默认Python缺pytest；实际工具后端原本就显式
使用`/opt/miniconda3/envs/testbed/bin/python`，不以这个错误入口改写镜像。

18项真实root/child工具检查全部通过：共享工作区写读、非零退出码、
同一环境传播和容器清理均验证。检查还真实执行各项目的Node版本范围
前置条件。报告为`toolchain_live_root_child.json`，仅建立工具链和工具
基础执行，不建立项目依赖、测试成功、自然轨迹或调度性能证据。

## 项目依赖与后续边界

`scripts/provision_original_node_projects.py`只在独立私有复制中按原始
package.json安装依赖，并记录原始日志、lock身份及失败。使用真实依赖
安装脚本，不添加替代模块，不修改原项目测试/接口。HUSKY=0和固定一核/
4GB属于环境准备；网络只用于这一步，实测agent工具保留原沙箱网络契约。
没有原始依赖清单、需要额外本地workspace绑定或原生ML/GUI运行栈的
候选先记录为不属于这批有界准备，不通过补全项目来凑数量。

安装成功后还需冻结依赖镜像，并在真实root/child中重新验证其模块路径；
链接必须幂等且拒绝意外已有路径。随后才可生成采集配置，完成源码绑定
CPU回归、干净冻结、现有GPU实际退出和资源检查。31项定向CPU检查通过；
此前29项报告也保留。当前不能以此替代完整采集入口资格。

新的原题采集仍保留完整自然尾部、真实工具时间、独立项目、原有4/8
child及8/16 root软预算和自然并行协议。是否用于新的持续负载，取决于
真实工作量、JOIN结构、容量和供给；不能按哪条策略获胜来选择。
