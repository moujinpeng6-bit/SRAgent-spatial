# 开发核查材料：需要研究者复核

这里的 25 条 GSM 材料来自真实公开 GEO 样本，参考标签由助手依据样本组织、物种与实验协议整理，尚未由独立研究者确认。用于调试规则、引文验证和 Agent 接线；不要据此报告独立测试准确率。

- `lung_spatial_development.csv`：样本编号、来源与暂定标签，便于逐条复核。
- `lung_spatial_development.json`：模型可见的样本字段和不可见的参考标签分开存储。
- `reference_decisions.json`：暂定标签、定义和已知覆盖缺口。

当前 17 条符合、5 条非肺、3 条不能确认为空间测量；没有真实的 mixed 类样本，不能评价该类真实召回能力。多个样本来自同一研究，彼此并非完全独立。

建议由你或学姐按来源逐条复核：实际取材组织是否是肺、是否为人类、样本自身是否接受空间测量、一个研究是否混有普通单细胞数据。不要用研究标题代替样本证据。

模型预测只收到 `input`，不接收 `reference`。当前规则与参考标签均在开发阶段使用这批资料，存在选择与调试偏倚。后续应增加未参与开发的研究，按研究划分留出集，而非仅随机拆分同研究样本。

重建公开材料的步骤：

```bash
python scripts/collect_review_materials.py --out outputs/review_materials/samples.json
python scripts/build_development_set.py
```

前一个命令只取得输入材料，不自动生成参考答案。后一个命令使用明确维护的参考决定，过滤无关临床和联系人字段。各公开样本以原始 GEO 来源为准。
