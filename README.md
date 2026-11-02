# SRAgent-spatial

v0.4 新增 `discover`：由原版总控调用空间搜索、自动展开 GSM 并逐样本核查，导出带原文证据的 JSON / CSV。人类肺 Visium 限定试点已实际完成 2 项研究、10 个样本，另有 21 个样本关联因上限未读取；不是完整召回或空间资产验证。运行与边界见 [空间发现流程](docs/discovery.md)，结果见 [公开示例](examples/lung_visium_discovery/)。

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
