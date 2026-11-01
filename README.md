# SRAgent-spatial

自动发现公开空间转录组数据、整理实验背景，并追踪表达、坐标、图像与标注之间的对应关系。

## 当前状态

这是项目初始化版本，包含范围说明、字段规范、候选检索配置和开发路线。目前尚未实现自动联网检索、AI 资料提取或空间文件校验，也没有收录或验证真实数据集。

这是受 [Arc Institute SRAgent](https://github.com/ArcInstitute/SRAgent) 与 scBaseCount 启发的独立项目；当前未复制上游源码，也不是作者官方空间版本。

## 第一阶段要做什么

先跑通一个小闭环：检索人类肺组织相关 Visium 空间数据，整理 5–10 条真实候选记录，并人工核查每条记录的来源、组织、技术和可用资产。

输出首先是可追溯的数据目录。图像、分割或标注没有公开时应记录缺失；不把缺失推断为已经取得。

```text
检索公开来源 → 得到候选记录 → 读取实验说明
    → 提取字段并保留证据 → 人工核查 → 导出 CSV / JSON
```

第一阶段完成后，再实现文件 ID 对应、坐标单位、切片身份及图像变换检查。大规模下载、原始测序重处理和模型训练属于后续独立阶段。

## 目录

```text
configs/search_terms.json         候选搜索词与首批试点范围
docs/architecture.md             各模块的分工与原理
docs/fields.md                   字段的中文解释和记录规则
docs/roadmap.md                  分阶段开发与验收要求
schemas/spatial_record.schema.json  单条记录的 JSON Schema
templates/records.csv            空白人工整理模板（只有表头）
```

## 从哪里开始

1. 先读 [开发路线](docs/roadmap.md)，明确第一个版本的目标。
2. 对照 [字段说明](docs/fields.md)，使用 [空白模板](templates/records.csv) 整理真实候选。
3. 参考 [系统分工](docs/architecture.md) 实现检索、来源读取和结构化导出。
4. 用人工核查结果评价检索和提取质量，再决定是否引入语言模型。

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
