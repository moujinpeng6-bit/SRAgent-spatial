# 原版 SRAgent：检查结果与复用方式

## v0.2 扩展

新增 `agents.py`：`original_agent` 直接调用上游 NCBI Agent，`spatial_agent` 使用同一个 Agent 作为子工具，并调用上游 `set_model` 与 LangGraph 接入冻结样本读取、空间规则核查工具。没有复制上游内部源码。输出必须通过 GSM 身份和引文检查。真实 OpenAI 对照结果仍需本机 API 凭证。

详见 [v0.2 运行和限制](v02.md)。下文 `upstream.py` / `agent-lookup` 是 v0.1 通用原版查询入口，不执行新的四类样本核查或对照。

检查版本：`67b60c24a49385d3eed8e5121be58d13c82839cd`，上游包版本 `0.6.1`。

来源：[ArcInstitute/SRAgent](https://github.com/ArcInstitute/SRAgent/tree/67b60c24a49385d3eed8e5121be58d13c82839cd)。上游采用 MIT 许可，Copyright (c) 2025 Arc Institute；依赖安装保留其许可。此项目当前未复制或修改上游源文件。

原版 `esearch_scrna` 组合单细胞关键词，还要求双端、Illumina、公开数据等条件。空间成像技术不应自动继承这套筛选。因此基础工具使用 GEO 官方 SOFT 与 Entrez API，按空间查询条件寻找候选，并单独保留证据。

## 哪部分真正调用上游

`src/sragent_spatial/upstream.py` 在显式调用时导入：

```python
from SRAgent.agents.ncbi_fetch import create_ncbi_fetch_agent
```

将空间任务与 GEO 编号通过原版工具的 `ainvoke({"message": ...})` 入口传入。返回内容标为 `unreviewed_agent_output`，不直接当作已验证元数据。固定提交写在 `pyproject.toml` 的可选 `agent` 依赖中。

```bash
python -m pip install ".[agent]"
sragent-spatial agent-lookup GSE264398 --out outputs/agent/GSE264398.json
```

需要按照原版说明配置模型凭证和设置；可能产生模型服务费用。密钥通过环境变量管理，不写入代码、命令参数或仓库。v0.2 已在独立环境安装固定上游依赖并完成真实框架、替身模型的离线联调；尚未实际调用付费模型，真实模型输出仍待验证。

基础 GEO 查询、SOFT 解析、字段整理与本地检查是本项目新增实现。scRecounter 未复制、未运行；空间原始数据重处理尚未实现。本项目没有训练新的 Transformer。
