# E 盘肺组织空间验证（2026-11-03）

本次是明确指定样本的本地、只读验证，不是搜索 Agent 自动执行验证的端到端测试。输出在项目的 `outputs/local_lung_spatial/` 中，包含中文 HTML/Markdown 说明、两张科学绘图、JSON 核查结果和细胞空间索引 CSV。原始 E 盘文件未修改；生成文件已被 outputs 忽略规则排除出 Git。

## 实际发现和验证范围

在 E:/Xenium 的清单中定位 31 条 Lung 记录，检查各目录的真实文件并标记 `.part` 未完成文件。这与此前线上核查的 31 个 Visium GSM 是两组材料，不能合并通过数。

完整验证的样本是 [GSM7990538](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM7990538)，属于 GSE250346，来源记录为人类肺组织 Control，平台 Xenium。其已完成压缩包位于 E:/Xenium/samples/45_Lung/GEO_bundle。

19 项实际数据检查通过，包括：12,578 个细胞的 ID 唯一、表达矩阵条码集合和顺序一致、基因及全部特征计数分别与细胞表逐一相等、实验和图像像素大小一致、所有质心位于图像内、细胞/细胞核边界 ID 和坐标有效。配套焦平面图像的 SHA256 与同一原始表达数据包内图像一致。

矩阵原始形状为 541 特征 × 12,578 细胞。其中 Gene Expression 为 343，Negative Control Probe 为 20，Negative Control Codeword 为 41，Unassigned Codeword 为 137。不能称为“541 个基因”。

在同一套 µm 坐标上展示 EPCAM 计数、DAPI 图像和细胞边界；人为圈出半径 150 µm 的演示 ROI，包含 417 个细胞；计算中心细胞 aaaacccb-1 的五个最近邻。几何邻近不证明细胞通讯，演示 ROI 不属于作者病理分区。

另外检查独立的 E:/Xenium/samples/26_Lung：11 个来源提供的 H&E 多边形全部在该 H&E 图像范围内。使用配套 3×3 矩阵转换到 DAPI 坐标时，编号 1、9、11 的部分顶点超出 DAPI 覆盖范围。未测量独立关键点残差；本地该样本未发现表达矩阵和细胞表，因此未完成其细胞—病理区域对应，也不能借用 GSM7990538 的细胞补齐。

## 复现

以下命令在项目根目录运行，假定已有 Python 3.11 环境。图像依赖使用 scripts/requirements-local-lung.txt 中本次实际验证的版本。

```powershell
.\.venv-agent\Scripts\python.exe -m pip install -r scripts/requirements-local-lung.txt
.\.venv-agent\Scripts\python.exe -X utf8 scripts/inspect_local_lung.py
.\.venv-agent\Scripts\python.exe -X utf8 scripts/test_local_lung_spatial.py
.\.venv-agent\Scripts\python.exe -X utf8 scripts/report_local_lung.py
```

inspect 脚本会拒绝覆盖已提取文件；已有提取结果时只需运行后两个脚本。提取上限为每成员 64 MiB、合计 128 MiB，本次提取 7 个小文件、4,672,184 字节，没有复制巨大的原始图像。

验证脚本仅解码图像金字塔预览：样本45为 3,119×3,575；样本26 DAPI 为 4×534×1,599，展示第0通道。选层使用 Python math.prod，避免 Windows / NumPy 1.x 下 int32 乘积溢出而误读全图。图像哈希验证仍对同一压缩包内目标图像做完整流式读取。

本次真实数据脚本执行成功，输出图片已人工查看，报告图片链接及低分辨率层形状检查通过；原有 82 项离线测试全部通过，pip check 通过。没有进行模型训练或真实 LLM 调用。

## 对 spatial Agent 的下一段连接

上述记录描述最初的独立脚本实验。随后 v0.5 已将核心核查抽成输入路径可配置的 Xenium 验证器，并封装为原版总控可调用的工具；GSM7990538 的真实调用已完成。接口、运行证据与剩余限制见 [Agent 空间验证说明](local_validation_agent.md)。

坐标和变换约定参考 [10x 文件格式说明](https://cf.10xgenomics.com/supp/xenium/xenium_documentation.html)、[图像配准说明](https://www.10xgenomics.com/support/software/xenium-explorer/latest/tutorials/xe-image-alignment)及[坐标转换说明](https://testkb.10xgenomics.com/s/article/35386990499853-How-can-I-convert-coordinates-between-H-E-image-and-Xenium-data)。
