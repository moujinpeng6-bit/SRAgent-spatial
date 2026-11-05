# 不用 BigQuery 的空间数据搜索

## 现在用哪个网站

新增来源是 EMBL-EBI 的 [BioStudies](https://www.ebi.ac.uk/biostudies/studies)，其中包括 ArrayExpress。它保存研究说明、实验附件以及外部数据链接。发布者确实向它提交了空间转录组数据；例如 [人和小鼠肺纤维化的 Visium 研究](https://www.nature.com/articles/s41588-024-01819-2) 在数据可用性声明中列出了 S-BSST1410 和 S-BSST1409。

BioStudies 的[官方帮助](https://www.ebi.ac.uk/biostudies/RH3R/help)公开了 REST API。API 可以理解成“网站给程序使用的查询入口”，这里不需要 Google Cloud 项目、BigQuery 授权或浏览器验证。

这替代的是当前任务所需的“查询公开研究并找到空间文件”能力。它不是 SRA/ENA 元数据库的完整副本，不执行 BigQuery SQL，也没有解决 10x 官网实时目录的访问限制。原有 GEO 和 10x 快照入口保留。

## 具体增加了什么

1. 输入物种、组织、平台，调用 `/api/v1/search` 搜索，不预先指定研究编号。
2. 读取 `/api/v1/studies/{编号}` 的研究说明，核对物种、组织和平台。检索先宽一些，再核对物种，避免把 `human` 和 `Homo sapiens` 的不同写法误当成没有数据。
3. 通过 `/api/v1/studies/{编号}/info` 返回的官方下载根目录拼接已登记的文件路径，不让模型猜下载地址。
4. 对支持的 Space Ranger ZIP，通过 HTTP Range 读取压缩包目录，只获取同一个切片目录中的过滤表达 HDF5、位置表、比例因子和低分辨率 PNG。不会为了小图而取得整套原始大图。
5. 如果研究另外提供了可识别的样本表，按切片 ID 核对样本物种和组织，保存表的来源和哈希。研究级说明和样本级证据在报告中明确区分。
6. 检查完整稀疏计数数组是否合法、矩阵列数与条码数是否相同、表达条码能否在位置表中找到、坐标按比例映射到图像后是否越界，以及图像能否完整解码。
7. 每个切片单独记录通过、缺件、错配、访问失败、不支持的格式或配对歧义；遇到一项失败继续下一项。
8. `--resume` 使用固定请求和文件缓存；已通过的切片重新核对本地 SHA-256，损坏或丢失的文件重新准备。分段缓存使用来源 URL、归档长度、强 ETag 和 SHA-256 核对，固定该次归档版本。

分段预取减少每个 ZIP 小字段都发起一次网络请求的开销。`--download-mib` 是每项研究本次运行的归档下载上限，多个切片共享；元数据请求另外有大小限制。断点缓存是该次来源版本的记录，续跑不是重新查询全库最新状态。想刷新候选请使用新的输出目录。

## 怎样运行

在项目目录使用已有的 `.venv-agent` 环境：

```powershell
.\.venv-agent\Scripts\python.exe -m sragent_spatial find-spatial --source biostudies --organism "Homo sapiens" --tissue lung --platforms Visium --max-studies 3 --samples-per-study 2 --download-mib 128 --out "E:/SRAgent-spatial/my-biostudies-lung"
```

中断后原命令加 `--resume`，其余条件不变。新目录名可以自行选择。

增加 `--model deepseek-flash`，可以由本机已配置的 DeepSeek 调用限定范围的 BioStudies 搜索与验证工具。使用项目原有模型工厂和 LangGraph；模型选择调用工具、读取工具返回结果，文件对应结论由实际检查计算得到。这个模式只开放 BioStudies 工具，不声称已经运行原版总控所有子 Agent。无需模型时省略该参数，完整程序流程仍可以运行。

输出包括：

- `request.json`：固定的物种、组织、平台及数量/下载限制。
- `batch.json`：查询 URL、实际返回数量、逐研究或逐切片状态、来源及验证结果。
- `samples.csv`：便于直接打开的逐条清单，`organism`/`tissue` 是请求条件，实际证据见 JSON。
- `metadata_cache/`：带哈希的官方响应，保存在本机。
- `研究编号/平台/切片哈希/`：取得的配套文件和独立结果。
- `.zip-cache/`：经过校验的归档分段，本地续跑使用。

注意：`found` 统计的是报告行，既可能是未展开的研究，也可能是具体切片。请同时看 `distinct_studies_found`、`slice_rows` 和 `count_scope`，不能把它当成全库样本数量。一次只读取查询前若干研究、每项前若干切片；未处理的切片目录列在报告中。存在入选但未通过的条目时退出码为 2，并保留成功条目和失败原因。

## 目前仍有边界

新来源已经支持搜索 Xenium、Visium 和 Visium HD，自动文件准备目前只支持可识别的 Space Ranger ZIP 文件组。BioStudies 的 Xenium 外部清单、ArrayExpress 的多 TAR 包、独立大图和任意定制文件夹，尚不能保证自动配齐。它们会被记为 `unsupported_bundle` 或歧义，而不会记为验证通过。已有 GEO、10x 的 Xenium 验证能力并不意味着新来源的所有下载布局都自动支持。

表达、坐标、图像对应通过，说明这些文件的格式、ID 和数值关系通过了所列检查。它不证明组织切片的生物学质量，不证明细胞分割正确，也不替代人工确认同一物理切片或病理精细配准。大规模召回率、跨来源去重和经过研究者独立复核的效果评价，仍需要专门的后续验收。

真实查询、逐切片结果与续跑证据见 [本次公开验收](../examples/biostudies_acceptance/)。模拟缺件、错配、访问失败及文件损坏的回归测试与真实在线结果分开记录。
