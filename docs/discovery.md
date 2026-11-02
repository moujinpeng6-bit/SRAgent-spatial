# 空间总控：搜索研究、展开 GSM、逐样本核查

`discover` 从明确的物种、组织和平台请求出发，由原版 SRAgent 总控调用新增空间工具，输出带证据的样本目录。当前样本判断器只支持人类肺组织试点。

## 架构与原版的关系

创建固定版本原版总控时，保留 Entrez、NCBI Fetch、BigQuery、Sequences 四个子工具，并追加以下工具与空间任务提示：

1. `search_spatial_datasets`：按本次固定请求查询 GEO，返回有限数量的 GSE 研究。
2. `expand_spatial_study`：读取该研究列出的 GSM，各样本单独获取自己的 SOFT；校验身份和研究归属。
3. `screen_spatial_study`：依次将已读取 GSM 交给空间 Agent，检查模型返回的样本编号和逐字引文。

追加发生在同步构造阶段，构造后立即恢复上游函数绑定；不复制或修改安装目录中的原版源码。本试点提示总控使用新增空间工具，不使用单细胞搜索过滤、BigQuery 或序列下载。原版其他分支仍保留，并不代表它们都在本次试点被调用。

每个 GSM 的模型输入只包含该样本的资料，不提供开发集参考标签，也不把研究的肺组织或平台描述传播到样本。重复 GSM 只读取并核查一次，保留多研究归属；重复归属也需要样本自己的记录支持。

## 输出的判断含义

`label` 保留模型对人类肺空间样本的四类判断：`eligible`、`non_lung`、`mixed`、`unknown`。

`include_in_catalog` 的要求更严格：模型和保守规则均为 `eligible`，且样本自身检出本次请求的平台。这样，一条明确的肺 Xenium 样本可以是一般空间筛选的 `eligible`，但不会被选进 Visium 请求的目录。

这是规则和模型共同筛选的原型，不是独立验证的准确分类器。引文存在不保证模型理解正确，平台提及也不能独立证明实际使用。`Visium` 请求包含其 HD 家族，不能据此精确区分 spot/bin；HD 的网格尺度仍需文件核查。

## 运行

使用已在当前终端配置的模型服务凭证，例如：

```powershell
.\.venv-agent\Scripts\python.exe run.py discover `
  --organism "Homo sapiens" --tissue lung --platforms Visium `
  --model deepseek-flash `
  --max-studies 2 --max-samples-per-study 5 --max-total-samples 10 `
  --out outputs/discovery_lung_visium
```

当前项目环境和终端应能读取对应的 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`，`SPATIAL_PROVIDER` 指定服务。不要在命令参数或仓库中写密钥。模型 ID 必须由该端点实际支持。DeepSeek 路径沿用任务上下文，将显式模型传给子 Agent。

Windows DeepSeek 客户端直接使用 `truststore.SSLContext(PROTOCOL_TLS_CLIENT)`，避免每个嵌套客户端重复加载 certifi 文件；仍强制证书和主机名验证，并遵循显式 `SSL_CERT_FILE` / `SSL_CERT_DIR`。相关用法见 [Truststore 文档](https://truststore.readthedocs.io/en/latest/)。

可设置 `--cache`、`--refresh` 和 `--timeout`。缓存保留公开请求的来源与校验和；默认允许复用缓存。超时只约束图执行的等待，构造阶段、网络线程或 SDK 内部调用可能使总耗时更长，不能把它视为金额上限。

可用 `--resume PREVIOUS_RUN_JSON` 继续固定研究，重新验证并复用已完成样本结果；增加 `--max-samples-per-study` 与 `--max-total-samples` 可补读剩余 GSM。研究列表、组织、物种和平台沿用存档，模型必须一致。每研究上限可设 1–50、全局 1–50。两项研究的 31 个样本续跑及文件访问结果见 [完整核查与受限报告](asset_audit.md)。

## 输出文件

| 文件 | 内容 |
|---|---|
| `run.json` | 请求、实际查询、总命中数、研究清单、全部样本处理状态、未读取 GSM、失败项、工具调用记录 |
| `samples.json` | 每个 GSM 的样本字段、SHA256、规则结果、模型结果、原文证据与文件链接 |
| `samples.csv` | 适合人工核查的样本清单，保留研究归属、目标平台判断、筛选结果及引文 |

输出由工具实际处理的状态生成。总控最终的文字总结单独标记为未核验，不能凭一句“已完成”补造样本或覆盖表格。逐样本保存检查点，中途失败仍保留已完成结果。

## 上限与失败

- 研究数上限限制搜索返回范围，不表示全库已搜完；保存数据库总命中数和截断状态。
- 每项研究保留完整列出的 GSM 编号，再按每研究及全局上限读取；未读取编号明确记录，不算已核查。
- 单项研究获取失败或单个 GSM 失败不抹去其他结果，失败记录只保存安全的错误类型/错误码。
- 模型没有调用搜索、漏展开研究、漏核查已读取样本、引文验证失败或记录读取失败，最终状态为 `incomplete`，命令退出 2。
- 有意设置上限但已完成上限内全部工作，状态为 `completed_with_limits`；完整完成本次返回范围则为 `completed`。两者都不代表全库完整召回。

## 文件资产范围

本版仅收集公开补充文件链接，并根据文件名粗分表达、坐标、图像等类型。没有下载矩阵/大图、展开压缩包、完成切片配对、校验分割或重处理测序数据。元数据符合后，仍需要独立的空间文件核查。

## 真实人类肺 Visium 试点（2026-11-02）

DeepSeek `deepseek-flash`，上述命令，71 项离线测试通过。真实图执行结束，耗时 111.781 秒；此前证书加载延迟的中断尝试不计入这个已完成运行的耗时。

| 项目 | 结果 |
|---|---|
| GEO 查询总命中 | 58 项研究（当次返回，不是切片或样本数） |
| 本次读取研究 | GSE318039、GSE271370 |
| 研究列出的样本数 | 分别 8、23 |
| 本次各研究读取并核查 | 各 5 个，共 10 个唯一 GSM |
| 完成 / 失败 | 10 / 0 |
| 保守元数据筛选通过 | 10；均有样本自身 Homo sapiens、lung 和 Visium 原文证据 |
| 未读取研究—样本关联 | 3 + 18 = 21，全部编号保留在报告 |
| 最终状态 | `completed_with_limits` |

工具进入记录包含 `search_spatial_datasets`、两次 `expand_spatial_study`、两次 `screen_spatial_study`，以及逐样本的读取/规则辅助工具。总控本次没有调用原版四个通用子模块，因为任务通过新增空间工具完成；四个原版子工具保留在总控中。

该运行没有传入开发集标签，没有计算准确率或召回率。它验证了从查询到样本证据清单的限定流程可执行；10 条均为正例，不能据此评价困难负例上的表现。

可公开的运行摘要、CSV 和引文摘录在 [示例目录](../examples/lung_visium_discovery/)。摘录仅保留模型原始引文中与物种、肺组织和 Visium 相关的项目，不包含完整模型输入、缓存、密钥、本机路径或原始数据。完整逐样本结果仍留在本机 `outputs/discovery_lung_visium/`。
