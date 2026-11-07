# 人类数据源与本体验收

## 10x 数据卡

直连 10x 数据目录、中文目录、站点地图及一页公开支持文档，本机都收到 HTTP 429 浏览器验证。未找到 10x 公布的目录搜索 API。现在在没有显式缓存或快照的情况下，`research-spatial` 可以用已配置的 DeepSeek Search 从公开网页索引查询 10x 人类数据卡。运行不需要浏览器，也不需要输入数据集编号。报告分开保留直连状态与索引发现方式。[10x 数据目录](https://www.10xgenomics.com/datasets) · [DeepSeek Search API](https://api-docs.deepseek.com/api/search/)

索引候选只接受 10x 官方数据卡域名和路径。排除明确包含动物的卡、混合物种卡和不明确的多组织阵列。配套文件必须来自卡片上明确可见的官方链接，或由此前核实的同一张官方卡登记。卡片存在但索引没有公布配套文件时保留 `missing_files`。

| 真实任务 | 结果 |
| --- | --- |
| 健康人心脏 Visium HD | 索引发现 1 张 10x 数据卡。已登记的官方归档在 E 盘有可验证分段。网络新增数据下载 0 字节。原生 2 µm 条码、坐标、图像和矩阵头通过。 |
| 人结肠 Visium HD | 索引发现 1 张此前未纳入该验收的官方结肠癌数据卡。没有可核实的配套归档链接，记录缺件。 |

[两次命令的逐样本结果](tenx_index.json) · [10x 心脏数据卡](https://www.10xgenomics.com/datasets/visium-hd-cytassist-6p5mm-human-heart) · [10x 结肠癌数据卡](https://www.10xgenomics.com/datasets/visium-hd-cytassist-11mm-human-colon-cancer-HE)

另用发布者的 GEO 样本记录测试健康筛选。肾脏样本有 AIN 诊断，在仅患病查询中纳入。另一条肝样本只有研究和论文层面的供体健康证据，在样本级健康筛选中保持未知。[健康与患病规则实测](health_checks.json)

## 本体向量

`search-agent` 的组织和疾病模块默认接到 EMBL-EBI OLS4。查询会做真实向量搜索，同时保留精确标签证据。模型选择的编号必须出现在工具返回的证据中。保留 `--ontology-backend upstream`，供具备原作者 Chroma 数据和兼容凭证的人复现原始向量库。

DeepSeek 实际调用公开 OLS 后，组织模块返回胰腺 `UBERON:0001264`，疾病模块返回人胰腺导管腺癌 `MONDO:0005184`。两次都有真实向量查询。组织任务中精确标签纠正了向量相似度排名偏向胰腺局部结构的问题。[工具与编号记录](ontology_ols.json) · [OLS 向量接口](https://www.ebi.ac.uk/ols4/mcp)
