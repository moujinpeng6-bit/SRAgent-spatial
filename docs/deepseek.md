# 用 DeepSeek 跑 SRAgent：配置、命令与已知不兼容

本页说明如何把**固定版本上游 SRAgent（0.6.1，commit `67b60c2`）**的全部 agent 层级切到 DeepSeek，而不复制或修改上游源码。

## 1. 为什么需要一层 shim

上游 `SRAgent.agents.utils.set_model()` 按**模型名前缀**分派：

| 前缀 | 客户端 | 附带参数 |
|---|---|---|
| `claude*` | `ChatAnthropic` | `thinking` |
| `gpt-4*` | `FlexTierChatOpenAI` | `service_tier` |
| `o[0-9]*` / `gpt-5*` | `FlexTierChatOpenAI` | `reasoning_effort`、`service_tier` |
| 其他 | Google Generative AI | — |

`deepseek-flash` 不属于任何分支，而且上游会强行带 `service_tier` 这类 OpenAI 专有字段。

但上游 15 个 agent 模块、6 个 workflow 模块**全部**在 import 时执行 `from SRAgent.agents.utils import set_model`。因此只要在 import 任何 `SRAgent.agents.*` **之前**替换掉 `SRAgent.agents.utils.set_model`，整条层级（含 `create_sragent_agent`）就都走 DeepSeek。这就是 `src/sragent_spatial/deepseek_model.py` 做的事，约 150 行，不复制上游代码。

顺序检查由 `install()` 强制：若已有 `SRAgent.agents.esearch` 之类的模块被 import，它会直接报错，而不是悄悄退回 OpenAI。

## 2. 三步开始

### 第 1 步：拿 key

到 <https://platform.deepseek.com/api_keys> 创建，形如 `sk-...`。账户需有余额。

### 第 2 步：设环境变量（不要写进仓库）

```powershell
# 当前窗口临时生效
$env:DEEPSEEK_API_KEY = "sk-..."
$env:SPATIAL_PROVIDER  = "deepseek"
$env:SPATIAL_MODEL     = "deepseek-flash"

# 或永久写入用户环境（新开终端生效；不要写进 .env 后提交）
setx DEEPSEEK_API_KEY "sk-..."
setx SPATIAL_PROVIDER "deepseek"
```

可选项：

```powershell
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"   # 换成兼容网关时使用
```

### 第 3 步：自检（不调用模型、不花钱）

```powershell
.\.venv-agent\Scripts\python.exe run.py doctor
.\.venv-agent\Scripts\python.exe run.py modules
```

`doctor` 应显示 `"provider": "deepseek"`、`"deepseek_api_key_configured": true`、
`"deepseek_routing_installed": false`（尚未 import 上游时为 false，正常）、
`"upstream_agent_modules_imported": []`（必须为空，否则 shim 装晚了）。

## 3. 实际运行

### 3.1 单模块试跑（最便宜的验证）

```powershell
.\.venv-agent\Scripts\python.exe run.py search-agent `
  --module elink `
  --message "List GEO series and PubMed IDs linked to SRA experiment SRX1234567" `
  --out outputs/v02/modules/elink.json
```

每跑一个模块会写一份 `{module, model, elapsed_seconds, output, scope}`，`output` 只是**未经核验的 agent 输出**，不能直接当元数据写入记录。

### 3.2 25 条开发样本的三路径对照

```powershell
.\.venv-agent\Scripts\python.exe run.py benchmark `
  --methods rules spatial_agent --model deepseek-flash --limit 1 `
  --out outputs/v02/agent_benchmark.json
```

`--methods` 可选 `rules`、`original_agent`、`spatial_agent`、`sragent`。`sragent` 使用完整上游 supervisor（Entrez + ncbi_fetch + BigQuery + sequences），调用次数和费用都显著高于另外两条，**先跑 1 条**。

### 3.3 隐藏输入密钥的启动器

```powershell
.\.venv-agent\Scripts\python.exe scripts\run_agents.py --model deepseek-flash --limit 1
.\.venv-agent\Scripts\python.exe scripts\run_agents.py --model deepseek-flash `
  --module ncbi_fetch --message "Fetch the GEO record for GSE264398"
```

密钥只在交互终端里隐藏输入，进程结束后清除；不写磁盘、不进命令历史。

## 4. 模型可用性（先查，别猜）

不同账号/端点暴露的模型 id 不一样。**不要假定 `deepseek-chat` 一定存在**——本机实测的 `api.deepseek.com` 只返回两个：

```powershell
.\.venv-agent\Scripts\python.exe scripts\ping_model.py --list
```

| 模型 id | 显示名 | context | max output | 用途 |
|---|---|---|---|---|
| `deepseek-flash` | DeepSeek-V4.1-Flash | 1,048,576 | 393,216 | 默认，Agent 循环首选 |
| `deepseek-v4-pro` | DeepSeek-V4-Pro | 1,048,576 | 393,216 | 更贵更强，做对照或难样本 |

`deepseek-flash` 还声明支持图像输入与 `effort` 档位（low/high/max）。本项目当前**不转发** `reasoning_effort`，用默认档；需要时再显式加。

## 5. 已知不兼容与取舍

| 项目 | 情况 | 处理 |
|---|---|---|
| 模型 id | 本机端点没有 `deepseek-chat` / `deepseek-reasoner` | 用 `ping_model.py --list` 确认后设 `SPATIAL_MODEL` |
| **thinking mode（默认开）** | 带 `tools` 的多轮请求必须回传 `reasoning_content`，否则 400 | shim 默认传 `extra_body={"thinking":{"type":"disabled"}}`；见下方说明 |
| `service_tier` / flex 重试 | DeepSeek 无这些字段 | shim 不转发，相关分支自然失效 |
| `workflows/convert.py:181` | `with_structured_output(..., strict=True)` 走 OpenAI json_schema | 可能报 400；`entrez_convert` 若失败，改用 `entrez` + `ncbi_fetch` 组合 |
| `find_datasets` workflow | 内部用结构化输出选 Entrez ID | 属"rewrite"模块，先在单条上验证再批跑 |
| 并发 | 上游默认 `max_concurrency=6` | 降到 1–2，避免 429 |
| 超时 | 上游 flex 超时 180s | 本项目的 `--timeout` 是**单次调用**上限，不是账单上限 |

## 6. 成本控制

- 先 `doctor` → `search-agent` 单模块 → `benchmark --limit 1`，再考虑放量。
- 每个 agent 都是 ReAct 循环，一个任务可能产生多次模型调用；`recursion_limit`（默认 16）和 `--timeout` 只限制步数与时长，**不等于金额上限**。
- DeepSeek 有上下文缓存，重复的前缀（系统提示、工具描述）比首轮便宜，但长对话仍会累积。
- 本项目**不记录** token 用量，因此不能声称"某次实验花了多少"。若要记账，需在 shim 里加 callback（尚未实现）。

## 7. 故障排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `DEEPSEEK_API_KEY is missing` | 环境变量没设或没继承 | 重开终端；`run.py doctor` 确认 |
| `Authentication rejected` / 401 | key 错、被撤销、或用了 OpenAI 的 key | 重新输入；确认 `SPATIAL_PROVIDER=deepseek` |
| 402 / `Insufficient Balance` | 账户余额不足 | 充值后再试 |
| 429 | 并发或频率过高 | 降 `max_concurrency`、减小批量 |
| 400 与 `response_format`/`json_schema` 相关 | 结构化输出不兼容 | 换 `entrez`/`ncbi_fetch` 路径，避开 `convert` |
| `DeepSeek routing must be installed before ...` | 先 import 了上游 agent 模块 | 保证 `configure()` 在 import 上游之前调用（本项目已如此） |
| `importing SRAgent.agents.tissue_ontology failed` | 该模块在 import 时加载 Chroma 向量库 | 属预期；先跳过本体论模块或用 OLS 工具 |
| 400 `reasoning_content ... must be passed back` | 某个 agent 走了多轮工具调用，而 thinking 开着、LangChain 没回传 `reasoning_content` | 默认已关闭 thinking；若手动 `SPATIAL_DEEPSEEK_THINKING=enabled`，改回 `disabled` |

## 8. 安全边界

- 密钥只经环境变量或隐藏输入进入进程，不写仓库、不写日志、不进命令行参数。
- `doctor`、`benchmark`、`search-agent` 的输出都不回显服务端原始报错、请求体或密钥。
- 上游 agent 输出标记为 `unreviewed_agent_output` / `upstream_agent_output_not_verified_metadata`，不能直接当作已核验元数据。

## 9. 首次真实运行记录

2026-11-02，本机 Windows + 固定上游 `67b60c2`，DeepSeek `deepseek-flash`：

| 项目 | 值 |
|---|---|
| 命令 | `run.py search-agent --module ncbi_fetch --message "Fetch the GEO series record for GSE264398 ..." --timeout 300` |
| 耗时 | 51.5 秒（单模块，含多次模型调用与 NCBI 访问） |
| 输出长度 | 851 字符 |
| 内容核验 | 标题、组织（GW15/GW18 胎肺）、平台（Visium）、样本数（2，GSM8217624/GSM8217625）与既有资料一致 |
| 附注 | agent 自述"direct GEO page fetch was unavailable"，即网页抓取路径失败后退回 Entrez/GDS；说明 HTML 抓取通道在本机不可靠，空间元数据应优先走 Entrez 与 SOFT |

**这只证明"链路可用且单次输出正确"，不证明批量准确率、召回率或成本。** 复现时需自行配置凭证，并在 `outputs/` 查看原始输出（该目录不入库）。
