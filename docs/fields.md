# 字段说明

一条记录代表来源中的一个候选实验或测量单元，不自动等于一个供者、一张切片或一个图像。

| 字段 | 含义 |
| --- | --- |
| record_id | 本项目内的唯一编号 |
| source_database | 资料来源，如 GEO、SRA、官方平台网站 |
| source_accession | 来源使用的记录编号；尚未确定时为 null |
| source_url | 原始资料的完整网址 |
| retrieved_at | 查询日期，YYYY-MM-DD |
| organism / tissue / disease | 物种、组织和疾病相关描述；未知时为 null |
| platform | 测量平台；未知时为 unknown |
| observation_unit | 表达行代表 cell、spot、bin，或 unknown |
| study_id / sample_id / section_id | 研究、样本与切片身份；来源未说明时为 null |
| coordinate_unit / coordinate_system | 坐标单位与参考系；未知时为 null |
| status | candidate、metadata_reviewed、assets_verified 或 excluded |
| assets | 已找到的公开资产链接及类别；没有找到时保留空列表 |
| evidence | 字段的来源、原文摘录或人工核查说明，以及取得方式 |

`metadata_reviewed` 表示人工复核了元数据；`assets_verified` 只在完成对应资产核查后使用，必须有 evidence 记录检查范围。Schema 只能检查结构，不能证明生物含义或空间配准正确。

`assets` 的类别包括 expression、coordinates、image、segmentation、annotation、transform 和 other。发现链接不等于文件已下载或已验证。

`evidence.method` 使用 source_explicit（来源明确陈述）、inferred（推断）或 manual_check（人工检查）。推断需要复核；同研究的疾病描述不能直接作为每个样本的疾病状态。

JSON 格式遵循 [Schema](../schemas/spatial_record.schema.json)。CSV 模板只覆盖适合人工填写的平面字段，空单元格表示暂缺；完整的多资产与多证据结构使用 JSON 保存。
