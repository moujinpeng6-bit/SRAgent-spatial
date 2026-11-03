# v0.5：让总控实际核对空间文件

后续 v0.6 已完成同一样本的实时搜索、自动文件定位/登记和空间验证，见 [完整流程](pipeline.md)。下文保留 v0.5 独立本地工具的接口与当时的验证范围。

2026-11-03。已完成真实模型调用：固定版本原版 SRAgent 总控，使用 DeepSeek `deepseek-flash`，调用本项目新增的本地空间验证工具。样本是 E 盘已下载的 Xenium 人类肺组织 GSM7990538，不是此前网上文件返回 403 的两项 Visium 研究。

## 原理和改动

```text
原版 SRAgent 总控（LLM 决定调用工具）
  → list_local_spatial_samples：读取用户事先登记的样本清单
  → validate_spatial_sample(sample_id)：按 ID 找到本地文件
      → 核对表达矩阵 / 细胞表 / 真实坐标 / 图像尺度与范围
      → 核对分割边界；可选核对原始压缩包成员 SHA256
      → 在同一物理参考系中计算区域细胞和几何邻居
      → 保存 JSON、CSV 和预览图
  → 模型根据程序返回的结果解释；模型文字不决定验证是否通过
```

继续复用上游 `create_sragent_agent(..., return_tool=False)` 的总控结构及已有子 Agent，未复制或修改已安装的上游源代码。构建图时临时扩展工具列表和任务提示，随后恢复上游构建器。`discovery.build_tool_supervisor()` 供搜索模式与本地验证模式共享。

`xenium_validation.py` 从上一轮样本专用脚本提取核心核查，输入改为配置路径，支持同格式 Xenium 数据；不要求细胞表与矩阵恰好同序，会按唯一细胞 ID 显式重排。未调用的验证、缺失文件或失败检查不能通过模型文字补齐。

`local_validation.py` 保存样本注册表、工具执行事件和结果。模型只能传清单中的 sample_id，不接受任意路径；输出目录须位于输入目录外。清单路径和原始文件留在本地，返回给模型的是核查统计、检查状态、几何区域和少量邻居信息。

当前工具仅适配 Xenium；Visium 的已有验证命令仍独立存在。H&E 配准、病理区域归属、细胞类型判定和分割准确率不在这次工具的验收范围。

本轮 93 项离线测试全部通过，包含 ID 重排、错误计数、坐标越界、压缩包哈希不符、大图尺寸溢出、拒绝任意路径、模型只写“完成”却未调用工具，以及搜索总控对待验证本地样本的完成状态检查。环境依赖检查通过。

## 实际运行结果

完整公开摘要见 [examples/local_lung_agent](../examples/local_lung_agent/)，含实际工具调用名称、样本 ID、运行时间及机器检查结果。原始文件、细胞级 CSV、大图和本地绝对路径没有提交到 GitHub。

- 总控实际调用 `list_local_spatial_samples`，再调用 `validate_spatial_sample("GSM7990538")`。
- `agent_tool_loop_verified=true`；没有仅凭模型最终文字认定完成。
- 12,578 个细胞，343 个 Gene Expression 特征；另有 198 个对照或未分配编码特征。
- 19 项机器检查通过，包括 ID、计数、坐标、图像尺度、边界及同一原始包的逐成员哈希。
- 几何演示区域半径 150 µm，包含 417 个细胞；最近邻距离按实际坐标计算。

这是一次真实的“本地工具—总控”测试，没有重新开展全网搜索，没有检验所有 31 条本地肺记录，也没有训练 Transformer 或其他模型。新加入 `discover --local-manifest` 的搜索→本地验证连接通过离线集成测试，尚未另做包含新一轮线上 GEO 检索的完整真实运行。

## 安装与配置

Python 3.11，项目根目录运行。模型服务沿用本机配置；密钥仅在本机环境中读取，不写入清单。真实模型调用会向所选服务发送提示和工具返回的核查摘要；无需模型时使用 `check-xenium`。

```powershell
python -m pip install -e '.[agent,xenium]'
```

复制 [本地清单模板](../configs/local_xenium.example.json) 到 `outputs/` 下再填写实际路径。相对路径以清单文件所在目录为基准。每项须登记 sample_id、Xenium 平台、um 坐标单位、表达目录和配套 morphology_focus 图像。可选登记原始 archive 与准确的 archive_prefix；不登记时会明确记录来源包身份未核对。

表达目录必须有 experiment.xenium、cells.csv.gz 和 cell_feature_matrix.h5；有 cell_boundaries.csv.gz、nucleus_boundaries.csv.gz 时自动加查，缺失则记录缺失而不伪造。相同 sample_id 在一次调用中只读取一次。

```powershell
# 完全本地，不调用模型
python run.py check-xenium --manifest outputs/local_lung_agent/manifest.json --out outputs/local_lung_agent/direct

# 原版总控实际选择、调用空间核查工具
python run.py validate-local-agent --manifest outputs/local_lung_agent/manifest.json --model deepseek-flash --out outputs/local_lung_agent/live

# 可选：给已有空间搜索总控同时登记本地数据
python run.py discover --platforms Xenium --model deepseek-flash --local-manifest outputs/local_lung_agent/manifest.json --out outputs/discovery_with_local
```

`discover` 中总控先搜索、展开和筛选，再对符合且在本地清单中登记的同 ID 样本调用验证工具。需要的本地核查尚未执行时，总任务不能报告完成；实际核查失败时报告 completed_with_failures。其他未登记样本仍只属于元数据候选，不自动下载大文件。

本地验证输出 run.json 含事件与工具调用记录；每个样本目录包含 validation.json、cell_spatial_index.csv、spatial_preview.png。验证模式要求工具调用覆盖全部注册样本，否则标记 incomplete，CLI 返回非零状态。correspondence_passed 只代表内部对应检查通过，不代表独立生物学真值。

## 资源与核查边界

最多 20 个登记样本；单表解压读取上限 64 MiB，H5 文件上限 256 MiB；最多 200,000 细胞、20,000 特征、20,000,000 非零项。只解码不超过 12,000,000 元素的已有图像金字塔层。使用 Python 整数计算尺寸，避免 Windows 下 32 位乘积溢出。

可选源包核对流式读取，不提取图像；精确匹配配置的成员路径，限制扫描字节和成员数。不同路径的图像不会仅因文件名相似而判定为同一个文件。当前只支持单一焦平面 YX 或 CYX morphology 图像，坐标单位须为 µm。

尚需扩展：Visium 工具统一接口、文件自动下载与平台识别、更大样本的分块读取、病理区域/图像变换检查，以及更多独立真实样本验证。
