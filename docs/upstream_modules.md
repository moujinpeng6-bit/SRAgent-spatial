# 原版模块接入与验证（2026-11-02）

固定上游 SRAgent 0.6.1 / `67b60c24a49385d3eed8e5121be58d13c82839cd`。本次完成接口修正、依赖检查、官方 SRA Toolkit 安装与部分真实调用；没有证明全部模块都可在线使用。BigQuery 按用户要求暂缓，未创建项目、登录账号或运行云端查询。

## 接口修正

统一入口现在支持上游的真实参数：普通模块的 `message`、总控的 `messages`（传入 `HumanMessage` 对象）、组织的 `tissue_description`、疾病的 `disease_description`、论文的 `accession`。论文 Agent 返回 DOI 字符串列表；列表不再被文本转换器丢失。它自身不下载 PDF，下载是上游另一个辅助函数。

DeepSeek 显式模型选择通过任务上下文传给嵌套工厂，避免顶层使用新模型、子层仍读取旧环境变量。模块命令使用项目专用设置；Windows 应用入口使用系统证书校验。调用记录增加工具进入、返回、异常事件，不保存工具参数或响应；工具返回也不代表生物学结论正确。

## 验证证据分层

本轮 60 项离线测试通过，覆盖真实参数名、HumanMessage 类型、DOI 列表保留、嵌套模型继承、依赖前提和失败信息脱敏。

| 能力 | 已验证 | 未验证或阻碍 |
|---|---|---|
| 14 个模块接口 | 实际安装的工厂、Pydantic 参数校验和包装器调用均通过 | 外部响应使用替身，不是 14 项在线验证 |
| 总控嵌套调度 | 离线真实 LangGraph 调度进入总控 → Entrez → esummary Agent → esummary；6 次模型生成由替身提供 | 不代表模型自主策略有效 |
| 总控真实调用 | DeepSeek `deepseek-flash`，实际进入上述四层工具，99.093 秒，返回 GSE264398 胎肺 Visium 标题与说明 | 单条接口任务，不能外推搜索召回率；未调用 BigQuery 或序列分支 |
| SRA Toolkit | 官方 Windows 3.4.1 已安装，fastq-dump / sra-stat 版本通过；test-sra 成功解析 SRR390728 | 安装本身不证明所有工具的远程读取可用 |
| 序列读取 | fastq-dump-orig 和原版实际使用的 fastq-dump 驱动均最多读取 SRR390728 的 2 个 spot，退出 0，返回 8 行 FASTQ；数据仅在进程内核对 | sra-stat 首次远程 quick 统计 60 秒超时，不能声称该分支已通过 |
| Sequences Agent 真实调用 | DeepSeek `deepseek-flash`，71.875 秒，调用记录实际进入 invoke_sequences_agent → fastq_dump，返回前 2 个 spot 的两端文件头 | 仅验证小样例 fastq_dump 分支，未验证 sra_stat |
| 组织本体 OLS 子工具 | 原版 `query_uberon_ols` 返回 lung / UBERON:0002048 | 不等于完整向量检索 Agent |
| 疾病本体 OLS 子工具 | 原版 `query_mondo_ols` 返回 asthma / MONDO:0004979 | 不等于完整向量检索 Agent |
| 两个本体向量库 | 已实际检查 HEAD 和单字节 GET | 原版两个 GCS 下载地址均返回 HTTP 403；本机无缓存、OBO 图和 OpenAI embedding 凭证 |
| BigQuery | 参数/包装器离线验证 | 未配置项目、ADC 或服务账号，用户要求暂缓 |

其余 esearch、efetch、elink、entrez_convert、papers、find_datasets、ncbi_fetch 本次只计入接口层验证；此前的运行记录不能代替这一轮每条路径的在线验证。esearch/find_datasets 的单细胞过滤改写仍属于后续空间适配，本次没有改写原版搜索逻辑。

本地原始报告在被 Git 忽略的 `outputs/module_validation/`：`offline_interfaces.json`、`live_sragent.json`、`live_sequences.json`、`dependencies.json`、`ols_tools.json`、`sra_stat_check.json`、`fastq_small_check.json`、`fastq_driver_check.json`。不提交密钥、原始序列或工具二进制。

## 复现

```powershell
.\.venv-agent\Scripts\python.exe scripts/setup_upstream_dependencies.py --install-sra --probe-resources
.\.venv-agent\Scripts\python.exe scripts/check_upstream_modules.py
.\.venv-agent\Scripts\python.exe -m unittest discover -s tests -q
.\.venv-agent\Scripts\python.exe run.py doctor
.\.venv-agent\Scripts\python.exe run.py modules
```

第一个脚本只安装官方 NCBI Windows 工具包到 `outputs/tooling/` 并运行版本命令，向量库仅检查地址，不下载。本项目仅修改当前进程 PATH，不修改用户或系统 PATH；也可设置 `SRA_TOOLKIT_BIN` 指向同时包含两个程序的 bin 目录。

`setup_ready` 仅表示本地前提是否被检测到，不代表在线认证、API 权限或模型兼容已验证。总控即使部分依赖缺失，仍能执行 Entrez 等可用分支；此时总控 `setup_ready=no` 表示完整分支尚未全部具备条件，不阻止部分任务运行。

## 本体模块剩余条件

原版向量库使用 OpenAI `text-embedding-3-small`，DeepSeek 聊天凭证不能替代这项 embedding。需有与原版兼容的 Uberon / MONDO Chroma 数据，以及同一 embedding 模型的可用凭证；不能用不同 embedding 生成的向量直接查询原索引。

原版公开地址：

- `https://storage.googleapis.com/arc-scbasecount/2025-02-25/tissue_ontology/uberon-full_chroma.tar.gz`
- `https://storage.googleapis.com/arc-scbasecount/2025-02-25/disease_ontology/mondo_chroma.tar.gz`

本次访问均为 403。需要作者提供可访问的兼容资源，或另建本体索引并明确记录这是重建资源；没有静默改成 OLS 后声称复现了原版向量流程。

## BigQuery 后续配置

用户尚无 Google Cloud 项目，本轮不配置。准备好项目后，按 [Google 官方认证说明](https://docs.cloud.google.com/bigquery/docs/authentication) 在本机使用 ADC 或指定服务账号凭证，凭证文件放在仓库外：

```powershell
gcloud auth application-default login
$env:GOOGLE_CLOUD_PROJECT = "YOUR_PROJECT_ID"
```

实际还需该身份拥有查询任务权限、项目允许使用 BigQuery，并确认数据访问和计费条件。项目名或环境变量存在不等于认证成功。本项目兼容旧的 `GCP_PROJECT_ID`，调用时仅在进程内映射到 Google 客户端标准变量。不要在聊天或 GitHub 上传凭证 JSON。

NCBI 下载来源和两条 spot 样例见 [官方安装说明](https://github.com/ncbi/sra-tools/wiki/02.-Installing-SRA-Toolkit)。
