# 原版 SRAgent：检查结果与复用方式

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

需要按照原版说明配置模型凭证和设置；可能产生模型服务费用。密钥通过环境变量管理，不写入代码、命令参数或仓库。本次没有安装重型上游依赖，也没有调用付费模型；接口只用替身对象做了离线测试，真实模型兼容性尚待验证。

基础 GEO 查询、SOFT 解析、字段整理与本地检查是本项目新增实现。scRecounter 未复制、未运行；空间原始数据重处理尚未实现。本项目没有训练新的 Transformer。
