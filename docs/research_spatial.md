# 按研究条件搜索：疾病、样本状态、来源与平台

v0.14 的入口是 `research-spatial`。它把前四项开发目标接到了已有 DeepSeek 总控和搜索、文件、验证三个子 Agent 上。

## 1. 用自然语言表达条件

可以输入：

> 寻找人类胰腺癌空间转录组，Xenium、Visium 和 Visium HD，要求表达矩阵、空间坐标和配套组织图像，不要正常或癌旁样本。

模型先提取固定结构：物种、组织、平台、疾病和样本状态。实际运行中解析为 `Homo sapiens / pancreas / Xenium, Visium, Visium HD / pancreatic cancer / tumor`。程序校验字段、类型、平台名称和取值范围；模型不能借解析步骤添加下载 URL、本地文件路径或研究编号。

也可以直接使用 `--organism --tissue --platforms --disease --sample-state`。`--task` 与这些显式条件不能混用，避免条件冲突。

疾病字段支持任意明确名称的文本匹配，并包含癌症、胰腺癌、腺癌的一组常见别名。组织匹配支持 `pancreas/pancreatic`、`kidney/renal` 等名称变体。它还不是完整的医学术语本体系统：未识别的缩写、缺失诊断和语义不清的说明会保持未知。

## 2. 疾病必须核对到样本

读取 GEO 的具体 GSM 描述，或官方数据卡/配套样本表中的诊断、状态、病理和样本名称字段。结构化诊断和状态优先于宽泛的研究标题。每项判断保留字段名、原文和来源 URL。

| 判断 | 处理 |
|---|---|
| 明确的目标疾病和肿瘤状态 | 可以继续准备文件 |
| 正常或健康样本 | 肿瘤任务中排除 |
| 癌旁样本 | 与肿瘤样本分开；只有明确请求癌旁时才纳入 |
| 一个样本混合正常和肿瘤、说明冲突 | 标为混合并保留证据 |
| 缺少疾病证据 | 未知，不把它当成正常，也不凭研究标题纳入 |
| 只出现 Cancer Panel 等试剂名称 | 不当作癌症诊断 |

BioStudies 研究级标题只建立候选。带疾病约束时，必须从样本表得到样本/文件的明确连接；没有唯一连接会输出 `condition_unknown`。这是一项公开元数据筛选，不是看图诊断，也不意味着切片内每个细胞都是肿瘤细胞。

## 3. 统一来源与平台入口

三个子 Agent 共用 `search_catalog / read_study / inspect_files / prepare_files / validate_sample`。来源适配器分别处理：

- **GEO**：实时接口或已核验的元数据缓存；展开 GSE 的 GSM，核对双向关联，逐样本判断组织、平台与疾病。研究编号和样本编号分开保存。
- **BioStudies / ArrayExpress**：官方 API、附件清单及可识别的样本表；已有 Space Ranger ZIP 读取路径保持可用。
- **10x**：可调用已有实时目录入口，也可明确配置目录快照或 E 盘登记缓存。模式保存在报告中。缓存没有覆盖某个平台时记录 `catalog_not_configured`，不能把它说成官网没有数据。

三个验证器在同一入口按平台选择：

- **Xenium**：按细胞 ID 对齐表达和质心，检查计数、实验像素大小与 OME 元数据，以及坐标范围。
- **Visium**：按 spot 条码对齐表达与位置表，通过发布者比例因子检查图像位置。
- **Visium HD**：按 bin 和分辨率分组；2、8、16 µm 的文件不会合并为同一组。完整 HDF5/MTX 与仅矩阵头的验收范围分别记录。

本轮统一入口实际通过胰腺癌 Xenium 1 套、肺 Visium 2 张切片、心脏 Visium HD 1 套。HD 使用原生 2 µm 条码，完整核对坐标/图像，但本轮矩阵仅核对头部。HD 2/16 µm 分开处理另有合成文件回归测试，不能称为两种分辨率都完成了新的线上样本下载。

## 4. 文件准备覆盖与消歧

新增通用准备器支持本地或限额下载的 TAR/TAR.GZ、ZIP、独立附件，HDF5 或完整 MTX/条码/特征表，以及必要的 gzip 解包。保留来源、归档成员、哈希及下载/复用状态。既有 10x 图像通过官方 ZIP 的大小/CRC 校验后复用，不重下大图；相同来源的配套文件与分段缓存放进共享目录供后续任务使用。

文件按目录、切片前缀或 HD 分辨率分组。重复角色、缺少角色、多切片和跨样本链接分别记录。多个压缩包默认不自动拼接；只有明确配对清单中的 `pairing_key` 才能允许跨包合并，分辨率仍独立。GEO 的共享 GSE 或跨 GSM 附件，没有样本映射时停止配对。

BioStudies 官方条目明确声明的 JSON 文件清单，可以解析受支持的列表或 `files` 结构；只接受官方文件域和安全相对路径。未识别的清单模式、失效链接或缺少样本对应关系保留原因，不猜链接。未知图片、任意 TIFF 配准和自定义坐标变换仍不自动推断。

准备和验证是独立工具。缺件返回准备阶段，达到重试上限会保留失败终态；失败不覆盖已有通过结果。下载量、解压后大小、归档成员数和候选数量都有上限。`workflow_complete` 表示本次限额内的条目都有处理结论，不表示文件全部可用。

## 复现命令

```powershell
.\.venv-agent\Scripts\python.exe -m sragent_spatial research-spatial --task "寻找人类胰腺癌空间转录组，Xenium、Visium 和 Visium HD，要求表达矩阵、空间坐标和配套组织图像，不要正常或癌旁样本" --sources geo biostudies tenx --10x-cache "E:/Xenium" --assets "E:/SRAgent-spatial/research-pancreas-assets-20261106" --local-roots "E:/Xenium/shared_GEO" "E:/SRAgent-spatial" --max-studies 2 --samples-per-study 2 --download-mib 128 --out "E:/SRAgent-spatial/my-new-pancreatic-task"
```

输出目录必须是新的。上述 E 盘参数是本机已有文件与目录缓存的位置，不是手填样本 ID。省略它们时程序使用新资产目录；10x 实时目录依然可能受官方浏览器验证限制。GEO/BioStudies 不依赖 Google Cloud。

输出包括完整条件、解析记录、`batch.json`、逐条 `samples.csv`、模型与工具 `trace.json`。疾病证据、文件来源和检查范围在 JSON 中；新版 CSV 额外列出临床状态、来源样本编号和矩阵检查范围。

## 实测与尚未包含的后续工作

胰腺癌综合任务记录 11 条结果：5 条通过元数据筛选，1 套完成空间验证（190,965 个细胞、12 项检查），2 条受下载预算限制，2 条缺件，其余 6 条未纳入。健康胰腺样本被排除。10x 选用缓存没有覆盖 Visium/HD，报告明确记录配置缺口；这两平台的 GEO/BioStudies 查询仍执行。另一次仅 10x 缓存、零新增数据下载预算的真实模型任务，再次通过同一癌症样本。

## 续跑与来源追溯

任务会在每个工具完成时原子保存 `progress.json`，包括已搜到的候选、配套文件、已完成检查和剩余步骤。重启时指定原来的输出目录。

```powershell
.\.venv-agent\Scripts\python.exe -m sragent_spatial research-spatial --resume --out "E:/SRAgent-spatial/my-new-pancreatic-task"
```

原始筛选与预算取自任务记录。恢复前重新计算已准备文件的哈希，只有文件完整且指纹一致的步骤可复用。资产缺失或改动时，相关样本返回准备阶段。任务已经全部结束时，可重建报告，无需再次调用模型。正在执行的步骤可能重新开始，文件分段能否复用取决于该来源的缓存。保留原输出和资产目录，避免同时运行两个进程处理同一任务。

每条记录的 `lineage` 保存来源链接、研究与切片标识，以及来源明确提供的 DOI 或 PMID。已准备图像还会记录哈希、下载链接或归档成员名。同一篇论文下的切片分别保留。跨来源验证通过的记录，只有完整表达、坐标、图像及映射文件指纹一致时才聚为一组。仅有矩阵头的样本不参与精确去重。原始记录仍可追溯。

真实进程中断和恢复记录见 [双切片验收](../examples/research_resume/acceptance.json)。首次留出集覆盖 GEO 的三项真实检索，见 [逐样本结果与研究者复核表](../examples/independent_evaluation/)。10x 无人值守实时目录仍受官网访问验证限制。BigQuery 保持停用。

## 仅检索人类的器官和疾病状态

显式条件使用 `--organism "Homo sapiens"`、`--tissue <器官英文>` 和 `--platforms Xenium Visium "Visium HD"`。健康和患病都接受时用 `--sample-state any`。仅收健康或正常对照用 `normal`，仅收有明确样本诊断的患病组织用 `diseased`，仅收肿瘤用 `tumor`。输出另有 `observed_sample_state`，在 `any` 模式也会记录来源明确标注的状态。未知和混合保留独立状态。

人类任务已接入不需要浏览器的 10x 公开索引入口。没有显式 10x 缓存或目录快照且已配置 DeepSeek 时，系统查找官方 10x 数据卡，并为有可信官方归档链接的记录继续准备文件。没有链接的候选保留缺件。官网直连的访问状态另行记录。[10x 和本体实测](../examples/human_source_acceptance/)

[逐样本报告、来源模式与调用日志](../examples/research_acceptance/)。TAR、跨包配对、外部清单、gzip、完整 MTX 和 HD 多分辨率的格式测试，见 `tests/test_research.py`；其中合成文件测试与真实在线运行分开记录。
