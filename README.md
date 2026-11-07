# SRAgent-spatial

v0.16 新增人类 10x 官方数据卡的无人值守索引搜索、健康与患病筛选，以及 EMBL-EBI 的 Uberon / MONDO 向量检索。已实际查找并验证健康人心脏，原生 2 µm 空间对应与矩阵头通过。另提供仅含人类组织的第二轮独立评鉴和研究者盲审任务单。[数据源与本体验收](examples/human_source_acceptance/) · [评鉴与任务单](examples/independent_evaluation/)

v0.15 给 `research-spatial` 增加整个研究任务的断点续跑、样本与图像来源记录，以及严格的跨来源文件去重。实际终止进程后恢复，两张 BioStudies 肺切片都通过。另用冻结的 12 个 GEO 样本做了初次独立留出评鉴，并记录漏收、误收与文件限额。见 [续跑与验收](docs/research_spatial.md)、[逐样本评鉴](examples/independent_evaluation/)。

v0.14 新增 `research-spatial`：自然语言研究条件、样本级疾病/肿瘤状态证据，以及 GEO / BioStudies / 10x 共用的多 Agent 工具入口。统一分派 Xenium、Visium、Visium HD 验证，并增加 TAR、ZIP、独立附件、完整 MTX/gzip 和显式跨包配对。人类胰腺癌任务真实通过 190,965 个细胞的 Xenium 数据，另有 Visium、HD 实测。覆盖范围、受限结果与复现见 [四项改进说明](docs/research_spatial.md)、[真实验收](examples/research_acceptance/)。

v0.13 新增真实 **DeepSeek 总控 + 搜索/文件/验证三个子 Agent**。工具拆为查询、证据读取、文件清单、配套文件准备及空间验证；模型根据结果选择委派、补件和重试。两张真实人肺 Visium 切片通过，受控错误验收记录了模型重试及“验证 → 补件 → 再验证”。见 [架构、命令与边界](docs/multiagent.md)。

v0.12 新增 **BioStudies / ArrayExpress 官方公开接口**：不需要 Google Cloud / BigQuery，输入物种、组织和平台即可查询研究、定位官方附件，并对支持的 Space Ranger ZIP 自动准备同切片文件和执行空间检查。支持逐条状态、CSV、受限下载、哈希校验续跑，以及 DeepSeek 实际调用工具。见 [使用说明与边界](docs/biostudies.md)、[真实验收结果](examples/biostudies_acceptance/) 和 [当前进度](docs/roadmap.md)。

下方各版本是开发历程，不代表所有公开平台、文件格式或研究级效果均已验收。当前状态以开发路线顶部和相应真实报告为准。

v0.11: [开放组织与物种搜索、本地文件复用及原生 16 µm 核查说明](docs/open_scope.md)。

v0.10 增加 Visium HD 原始 GEO 压缩包和无分辨率文件名的 HDF5/空间附件支持。明确 GSE 搜索可回退到按编号读取，并保留样本级筛选。真实人肺原生 2 µm 和从真实表达计数聚合的 16 µm 已通过统一入口。详见 [来源、准备与验证](docs/visium_hd_archive.md)。

v0.9 已把 Visium HD 接入统一批量入口，按 2、8、16 µm 独立记录配套文件和验证结果。两个人肺 GEO 样本的 8 µm 公开文件实际通过核查。详见 [Visium HD 验证记录](docs/visium_hd_batch.md)。

v0.8 已统一 Xenium 与传统 Visium 批量核查入口。真实混合批次通过 3 个肺 Visium 和 1 个肺 Xenium 样本，Visium 检查条码、矩阵头、坐标及低分辨率图像。详见 [混合批次结果](docs/visium_batch.md)。

v0.7 已接通 GEO 筛选结果到 Xenium 批量文件核查。真实肺组织试点处理 7 个候选，本地模式记录 6 个缺少完整包，随后第 7 个样本通过 19 项检查。支持按单样本记录状态，导出微米与原始图像像素坐标。限额模式和模拟 HTTP 403 的边界见 [批处理与映射说明](docs/xenium_batch.md)。

v0.6 已跑通同一肺组织样本的真实完整流程：实时 GEO 目标搜索 → GSM 自身资料筛选 → 根据样本补充链接自动定位本地原始包 → 提取配套文件并自动登记 → 总控调用空间验证。GSE250346 / GSM7990538 耗时 38.89 秒，19 项对应检查通过；复用本地原始包，未重新下载大数据。见 [完整流程说明](docs/pipeline.md)、[真实运行摘要](examples/lung_pipeline/) 和 [最新进度](docs/roadmap.md)。这是指定目标的人类肺 Xenium 试点，不是无约束全库或所有平台的验证。

v0.5 新增本地 Xenium 空间验证工具，并接入固定版本原版 SRAgent 总控。真实肺组织 GSM7990538 已完成“模型列出样本 → 调用验证工具 → 读取表达/坐标/图像/边界 → 返回核查结果”的运行：12,578 个细胞、343 个基因表达特征，19 项对应检查通过。见 [工具与复现说明](docs/local_validation_agent.md) 和 [真实调用摘要](examples/local_lung_agent/)。这是本地样本工具链验证，不代表任意平台或新一轮 GEO 全流程已经验证。

v0.4 新增 `discover`：由原版总控调用空间搜索、自动展开 GSM 并逐样本核查，导出带原文证据的 JSON / CSV。人类肺 Visium 限定试点已实际完成 2 项研究、10 个样本，另有 21 个样本关联因上限未读取；不是完整召回或空间资产验证。运行与边界见 [空间发现流程](docs/discovery.md)，结果见 [公开示例](examples/lung_visium_discovery/)。

后续已补完上述剩余 21 个样本：两项研究共 31 个 GSM 的元数据核查完成。新增 `--resume` 和 `audit-assets`；31 套文件名/归属一致，但本次 194 个所选文件入口全部返回 403，文件内部对应尚未验证。完整样本清单与受限结果见 [31 样本结果](examples/lung_visium_complete/) 和 [核查报告](docs/asset_audit.md)。

自动发现公开空间转录组数据、整理实验背景，并追踪表达、坐标、图像与标注之间的对应关系。

## 此前状态：v0.3 模型接入与全量搜索模块

v0.3.1 已修正全部模块的参数适配，实际验证总控 → Entrez → esummary 调用链，安装并检查 SRA Toolkit；外部依赖仍有明确缺口。逐项证据、复现命令和未通过项见 [原版模块接入记录](docs/upstream_modules.md)。模块注册、离线接口通过和在线服务可用是三个不同状态。

v0.3 在 v0.2 之上做了三件事：

1. **接入 DeepSeek**：通过替换上游 `SRAgent.agents.utils.set_model` 在 import 前完成路由，不复制不修改上游源码；`deepseek-flash` / `deepseek-v4-pro` 走 OpenAI 兼容端点。配置见 [DeepSeek 说明](docs/deepseek.md)。
2. **接入完整 SRAgent 搜索模块**：`upstream_registry` 惰性注册 14 个上游 agent 模块（esearch/esummary/efetch/elink/entrez/entrez_convert/ncbi_fetch/tissue+disease ontology/papers/bigquery/sequences/find_datasets/sragent），`run.py modules` 列出每个模块的用途、空间适配状态与外部依赖。
3. **新增 `sragent` 对照路径**：除 `original_agent`（仅 NCBI Agent）与 `spatial_agent` 外，可用完整上游 supervisor 作为工具参与同一批样本对照。

已有真实调用记录（DeepSeek `deepseek-flash`），包括单模块、空间 Agent 和总控的 Entrez 分支。此前文档记录了规则与空间 Agent 的 25 条开发样本运行；完整原版总控尚未完成同批三路径对照，暂定标签也不代表独立金标准。未运行的情况仍明确标为 `not_run_missing_api_key`。

## 此前状态：v0.2 样本核查与 Agent 扩展

已实现 GEO 查询、空间候选搜索、样本级四类筛选、引文检查、本地来源比较，以及 Visium 条码/坐标和低分辨率图像边界检查。新增 [25 条待人工复核的开发样本](evaluation/lung_spatial_development.csv) 与规则/原版 Agent/空间扩展 Agent 三路径对照。真实模型结果取决于本机凭证和依赖，未运行时明确跳过。详见 [v0.2 原理、运行和限制](docs/v02.md)。

这是受 [Arc Institute SRAgent](https://github.com/ArcInstitute/SRAgent) 与 scBaseCount 启发的独立项目，不是作者官方空间版本。基础查询和空间检查为本项目新增实现；Agent 路径复用固定版本上游的模型工厂、NCBI Agent 与 LangGraph，详见 [上游说明](docs/upstream.md)。真实接口调用已验证，但 Agent 效果仍需独立评估。

## 第一阶段要做什么

已跑通人类肺组织相关 Visium 搜索，导出 5 条研究候选，下一步逐样本人工核查组织、技术和可用资产。关键词出现在研究中，不代表研究里的所有样本都符合。

输出首先是可追溯的数据目录。图像、分割或标注没有公开时应记录缺失；不把缺失推断为已经取得。

```text
检索公开来源 → 得到候选记录 → 读取实验说明
    → 提取字段并保留证据 → 人工核查 → 导出 CSV / JSON
```

第一阶段完成后，再实现文件 ID 对应、坐标单位、切片身份及图像变换检查。大规模下载、原始测序重处理和模型训练属于后续独立阶段。

## 目录

```text
configs/search_terms.json         候选搜索词与首批试点范围
run.py                           无需安装即可运行的命令入口
src/sragent_spatial/              查询、解析、盘点、来源比较与可选上游桥接
src/sragent_spatial/deepseek_model.py     DeepSeek 路由（在 import 上游前替换 set_model）
src/sragent_spatial/upstream_registry.py  14 个上游搜索模块的惰性注册与调用
src/sragent_spatial/spatial_search.py     空间平台目录 + Entrez 查询构造 + 检索入口
tests/test_pipeline.py            无网络离线测试
docs/architecture.md             各模块的分工与原理
docs/deepseek.md                 DeepSeek 配置、命令与已知不兼容
docs/tasks_v03_v04.md            任务顺序、验收标准与排错（先看这个）
docs/fields.md                   字段的中文解释和记录规则
docs/roadmap.md                  分阶段开发与验收要求
schemas/spatial_record.schema.json  单条记录的 JSON Schema
templates/records.csv            空白人工整理模板（只有表头）
```

## 从哪里开始

1. 先读 [运行说明](docs/usage.md)，执行已知编号查询或候选搜索。
2. 对照 [字段说明](docs/fields.md)，使用 [空白模板](templates/records.csv) 整理真实候选。
3. 参考 [系统分工](docs/architecture.md) 实现检索、来源读取和结构化导出。
4. 用人工核查结果比较规则和语言模型的检索、提取质量。

Python 3.11+；基础功能只使用标准库，不需要模型密钥。仓库内运行：

```bash
python run.py lookup GSE264398 --max-samples 1 --out outputs/geo/GSE264398
python run.py search --organism "Homo sapiens" --tissue lung --platform Visium --limit 5 --out outputs/search/lung_visium
```

结果及本地盘点写入被 Git 忽略的 `outputs/`，不提交原始数据或本地路径。`configs/search_terms.json` 是参考词表，当前 CLI 使用显式参数而不自动加载此文件。

## 记录原则

- 候选记录、物理切片、供者和图像文件分别计数。
- 所有关键字段保留出处，区分原文明确陈述、模型推断和人工核查。
- 表达与坐标关联同一观测编号；坐标关联切片、单位和参考系。
- panel 外未测的基因与测过但未检出的零值分别处理。
- 数据使用范围遵循原始来源许可；仓库只保存代码、规范和可公开的索引信息。

## 参考项目

- [SRAgent：查询与资料整理](https://github.com/ArcInstitute/SRAgent)
- [scRecounter：单细胞原始数据重处理](https://github.com/ArcInstitute/scRecounter)
- [scBaseCount 数据资源](https://github.com/ArcInstitute/arc-virtual-cell-atlas/tree/main/scBaseCount)

后续复用上游代码时，需要记录版本、保留许可与署名。
