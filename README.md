# SRAgent-spatial

自动发现公开空间转录组数据、整理实验背景，并追踪表达、坐标、图像与标注之间的对应关系。

## 当前状态：可运行的 v0.1

已实现 GEO 已知编号查询、空间候选搜索、证据与文件链接提取、CSV/JSON 导出、响应缓存、只读本地文件盘点及来源链接比较。真实来源验证和离线测试见 [验证记录](docs/validation.md)。尚未实现表达与坐标 ID、图像配准或邻居关系检查；候选结果仍需人工核查。

这是受 [Arc Institute SRAgent](https://github.com/ArcInstitute/SRAgent) 与 scBaseCount 启发的独立项目，不是作者官方空间版本。基础空间查询代码是本项目新增实现，没有整库复制上游。可选 Agent 入口通过固定版本依赖调用原版代码，但尚未真实调用付费模型验证，详见 [上游复用说明](docs/upstream.md)。

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
tests/test_pipeline.py            无网络离线测试
docs/architecture.md             各模块的分工与原理
docs/fields.md                   字段的中文解释和记录规则
docs/roadmap.md                  分阶段开发与验收要求
schemas/spatial_record.schema.json  单条记录的 JSON Schema
templates/records.csv            空白人工整理模板（只有表头）
```

## 从哪里开始

1. 先读 [运行说明](docs/usage.md)，执行已知编号查询或候选搜索。
2. 对照 [字段说明](docs/fields.md)，使用 [空白模板](templates/records.csv) 整理真实候选。
3. 参考 [系统分工](docs/architecture.md) 实现检索、来源读取和结构化导出。
4. 用人工核查结果评价检索和提取质量，再决定是否引入语言模型。

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
