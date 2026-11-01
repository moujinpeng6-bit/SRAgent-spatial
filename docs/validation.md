# v0.1 验证记录

核查日期：2026-11-01。这里只记录公开来源编号与汇总结果；本地路径、完整盘点和原始响应不提交到仓库。

## 已知研究查询

| 来源 | 本轮读取范围 | 结果 |
| --- | --- | --- |
| [GSE250346](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE250346) | 研究 + 前两个关联样本 | 研究与样本描述均检出 Xenium；样本明确记录人类、肺组织 |
| [GSE264398](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE264398) | 研究 + 第一个关联样本 | 研究与样本描述检出 Visium；样本明确记录人类、胎肺组织 |

样本分别为 GSM7990532、GSM7990533 与 GSM8217624。只读了上述样本，不能声称核查了两个研究的所有样本。疾病字段缺少受支持的明确标签时保持未知，不由研究标题补出。

## 候选发现

实际查询式：

```text
"Homo sapiens"[Organism] AND "lung"[All Fields] AND "Visium"[All Fields] AND gse[ETYP]
```

本轮 API 报告 58 个命中，限制导出前 5 个研究。该数字随数据库更新会变化，不代表完整且去重的肺切片数。

| 候选研究 | 基于描述的自动平台识别 |
| --- | --- |
| [GSE318039](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE318039) | Visium |
| [GSE271370](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE271370) | Visium |
| [GSE302356](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE302356) | 同时出现 Visium、Xenium，标记 mixed_or_ambiguous |
| [GSE322553](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE322553) | Visium |
| [GSE292299](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE292299) | Visium |

以上为候选输出与描述检查，尚未逐样本人工复核；器官或技术关键词可能只描述研究的一部分，不代表每份样本都符合。

## 本地盘点与链接比较

对用户指定资料目录只读盘点 418 个文件条目，没有因数量上限截断。文件类型仅根据名字猜测，未打开图像像素或展开原始数据压缩包。

GSE250346 及两个所读样本的公开资产中，9 项与本地下载清单的来源链接对应：7 项有非空、非 `.part` 文件，2 项仍为 `.part`。这个比较不证明文件校验和、内容完整、供者身份或空间配准正确。零字节日志等也在完整盘点中，不能把空文件总数直接解释成损坏的生物数据文件数。

## 测试与限制

离线测试覆盖记录身份检查、多平台歧义、研究与样本标签隔离、缓存重放与损坏、搜索条件、未完成下载、盘点截断、CSV 公式保护，以及可选上游调用接口。付费模型未调用；图像和坐标的真实配对检查仍待实现。
