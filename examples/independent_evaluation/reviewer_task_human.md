# 人类空间转录组样本独立盲审

请由一位未参与系统开发和参考标签整理的研究者，逐条核对下列 12 个公开样本。请在提交判断前先不要阅读参考标签或系统结果。任务只需要查公开元数据和关联论文，无需下载表达矩阵、坐标或图片。

## 判定范围

- **人卵巢传统 Visium**。纳入人卵巢的传统 10x Visium 样本，健康或患病均可。排除单细胞 RNA 测序和 Visium HD。
- **健康人肝 Visium HD**。纳入发布者明确说明健康或非患病的人肝 Visium HD 样本。若明确说明整项研究的所有相关供体均健康，并能把该样本对应到其中一个供体，可记为研究级证据。癌旁、病变、混合或健康状态不明要分别记录。
- **人肾 Xenium**。纳入人肾 Xenium 样本，健康对照和患病均可。请尽量把状态标为健康、患病、混合或不明。

每个样本填写 `include`、`exclude` 或 `uncertain`。同时记录物种、器官、平台、健康状态，以及支持判断的字段或论文段落和直接链接。缺少明确证据时选 `uncertain`。

| 任务 | 样本 | 研究 | 判断和证据 |
| --- | --- | --- | --- |
| 人卵巢传统 Visium | [GSM7019835](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM7019835) | [GSE224335](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE224335) | |
| 人卵巢传统 Visium | [GSM7019836](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM7019836) | [GSE224335](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE224335) | |
| 人卵巢传统 Visium | [GSM8207500](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8207500) | [GSE263920](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE263920) | |
| 人卵巢传统 Visium | [GSM6506106](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM6506106) | [GSE211956](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE211956) | |
| 健康人肝 Visium HD | [GSM9324328](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM9324328) | [GSE311383](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE311383) · [论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC13658214/) | |
| 健康人肝 Visium HD | [GSM9324329](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM9324329) | [GSE311383](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE311383) · [论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC13658214/) | |
| 健康人肝 Visium HD | [GSM9324330](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM9324330) | [GSE311383](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE311383) · [论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC13658214/) | |
| 健康人肝 Visium HD | [GSM9554208](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM9554208) | [GSE322553](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE322553) | |
| 人肾 Xenium | [GSM8933094](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8933094) | [GSE294965](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE294965) | |
| 人肾 Xenium | [GSM9844161](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM9844161) | [GSE336890](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE336890) | |
| 人肾 Xenium | [GSM9844157](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM9844157) | [GSE336890](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE336890) | |
| 人肾 Xenium | [GSM9525962](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM9525962) | [GSE319763](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE319763) | |

请回传填写好的表格，并附复核日期和研究者代号。需要导入评分时可用下面的 JSON 结构，`rows` 中需包含上述 12 条记录。保留 `uncertain` 行用于报告不确定数量。

```json
{
  "reviewer": "reviewer-01",
  "reviewed_on": "YYYY-MM-DD",
  "independent_of_development": true,
  "rows": [
    {
      "query": "ovary_visium",
      "sample": "<GSM>",
      "decision": "include",
      "health": "healthy",
      "evidence": "<字段或论文段落，及判断依据>",
      "source_url": "<公开证据链接>"
    }
  ]
}
```

`health` 取 `healthy`、`diseased`、`adjacent`、`mixed` 或 `unknown`。
