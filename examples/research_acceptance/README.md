# 前四项改进：研究条件、样本疾病、统一总控和文件准备

2026-11-06，使用真实 DeepSeek 总控及搜索/文件/验证三个子 Agent。没有手填候选 GSE/GSM；只输入研究条件，配置来源与已有数据根目录。下表各轮次均保存了逐条 JSON、CSV、解析记录和模型/工具消息。

| 实测任务 | 结果 | 范围 |
|---|---|---|
| 中文人类胰腺癌，三平台，GEO/BioStudies/10x | 11 条结果，5 条入选；1 条通过，2 条超预算，2 条缺件，6 条未纳入 | 190,965 个 Xenium 细胞通过 12 项检查；保留样本诊断原文；健康样本排除 |
| 人类胰腺癌 Xenium，零新增数据下载预算 | 2 条结果，癌症样本通过、健康样本排除 | 同一生物样本再次验证，非另一个独立样本；共享资产和 ZIP 分段全部复用 |
| 人肺 Visium，BioStudies | 2 张通过、1 项超预算、1 项小鼠研究排除 | 4,034 + 4,882 个位置；完整读取 18,462,295 条计数 |
| 人心脏 Visium HD，10x 官方目录快照 | 1 套通过 | 原生 2 µm、10,615,406 个条码；完整坐标/图像对应；矩阵头检查，未读完整计数 |

胰腺癌综合任务的 10x 来源使用登记缓存，当前只覆盖 Xenium；Visium 与 Visium HD 返回 `catalog_not_configured`。GEO/BioStudies 的三平台查询仍执行。它不是 10x 官网九类筛选全部实时成功的声明。缓存模式、来源错误和未纳入原因均保留。

- [总摘要](run_summary.json)
- [综合胰腺癌：解析后的研究条件](pancreatic_cancer/parsed_request.json)、[逐条结果](pancreatic_cancer/batch.json)、[CSV](pancreatic_cancer/samples.csv)、[真实调用日志](pancreatic_cancer/trace.json)
- [零新增数据下载的缓存验收](pancreatic_cancer_cache/batch.json)、[调用与传输记录](pancreatic_cancer_cache/trace.json)
- [Visium](lung_visium/batch.json)、[Visium HD](heart_hd/batch.json)

`found` 包含未展开研究和具体样本/文件组，不是全库样本总数。`workflow_complete` 表示限额内结果都有结论，不能当成所有样本通过。数值空间对应通过不等于病理精细配准或癌细胞识别。

新增格式测试覆盖安全 TAR、ZIP、切片分组、显式跨包配对、HDF5/gzip、完整 MTX/特征表、Xenium 微米到像素映射，以及 HD 2/16 µm 分组。它们是有明确构造的合成回归测试，与本目录真实运行分开；没有声称所有文件布局或原生 16 µm 都在本轮完成了新的在线下载。

144 项测试通过，依赖检查通过。原始图像、表达文件和缓存留在 E 盘，不提交到 GitHub。详细架构、命令、临床证据政策与限制见 [四项改进说明](../../docs/research_spatial.md)。
