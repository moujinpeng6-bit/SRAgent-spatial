# 从 v0.3 到空间版第一版：任务顺序与验收标准

本文件是执行清单，不是设计文档。设计理由见 [architecture.md](architecture.md)、[deepseek.md](deepseek.md)、[v02.md](v02.md)。

## 0. 现在的准确位置

| 项目 | 状态 | 证据 |
|---|---|---|
| 上游 SRAgent 固定版本 | ✅ 已装 0.6.1 / commit `67b60c2` | `run.py doctor` |
| 离线测试 | ✅ 45 个通过（原 30 + 新 15） | `python -m unittest discover -s tests` |
| DeepSeek 路由代码 | ✅ 已写，未真实调用 | `deepseek_routing_installed: false` |
| 14 个搜索模块注册 | ✅ 已写，未真实调用 | `run.py modules` |
| 真实模型输出 | ❌ 一次都没跑过 | 所有结果都是 `not_run_*` |
| GitHub 上的版本 | ❌ 落后本地（本地多了 `test_launcher.py`、v0.3 改动） | 仓库文件树对比 |
| git 命令 | ❌ 系统里没有 | `Get-Command git` 失败 |

**结论：现在缺的不是代码，是"能推"和"能跑"这两个回路。**

---

## 1. 今天做完（约 90 分钟，全部由你执行）

### S0-A 装 git（10 分钟）

```powershell
winget install --id Git.Git -e --source winget
```

装完**重开一个终端**，验证：

```powershell
git --version
```

验收：输出 `git version 2.x`。
卡住：winget 不可用就装 <https://desktop.github.com/>，后面用它的 "Commit → Push" 也一样。

### S0-B 领 DeepSeek key（10 分钟）

1. <https://platform.deepseek.com/api_keys> 创建 key
2. 充值（先充最小额度即可，25 条样本的量很小）

### S0-C 设环境变量（5 分钟）

```powershell
setx DEEPSEEK_API_KEY "sk-..."
setx SPATIAL_PROVIDER "deepseek"
setx SPATIAL_MODEL "deepseek-flash"
```

**必须新开终端**才能读到（`setx` 不作用于当前窗口）。验证：

```powershell
cd C:\Users\38241\Documents\ChatGPT\scgpt\SRAgent-spatial
.\.venv-agent\Scripts\python.exe run.py doctor
```

验收：`"provider": "deepseek"` 且 `"deepseek_api_key_configured": true`。
卡住：显示 false → 环境变量没继承，重开终端；仍不行就临时设 `$env:DEEPSEEK_API_KEY="sk-..."` 再试。

### S0-D 单独 ping 模型（5 分钟，**这一步不能跳**）

```powershell
.\.venv-agent\Scripts\python.exe scripts\ping_model.py
```

验收：`[ok] provider=deepseek model=deepseek-flash latency=...s reply='pong'`

这一步把"key/余额/网络"和"框架"彻底分开：

| ping 结果 | 含义 | 下一步 |
|---|---|---|
| `[ok]` | 凭证和网络没问题 | 继续 S1 |
| 401 | key 错或已撤销 | 重新建 key |
| 402 | 余额不足 | 充值 |
| 超时/DNS | 网络或代理 | 检查代理，或用 `DEEPSEEK_BASE_URL` 指向可用网关 |

### S1 把 v0.3 同步回你的仓库并推送（30 分钟）

我改的是工作区副本，先拷回去（只增改、不删除）：

```powershell
robocopy "C:\Users\38241\Documents\deepseek-harness\default-workspace\SRAgent-spatial" `
         "C:\Users\38241\Documents\ChatGPT\scgpt\SRAgent-spatial" `
         /E /XD .venv-agent outputs __pycache__ .git
```

然后在仓库里跑一次测试，确认没拷坏：

```powershell
cd C:\Users\38241\Documents\ChatGPT\scgpt\SRAgent-spatial
.\.venv-agent\Scripts\python.exe -m unittest discover -s tests
```

验收：`Ran 45 tests ... OK`。

再提交推送：

```powershell
git status
git add -A
git commit -m "v0.3: DeepSeek routing, full SRAgent search-module registry, sragent comparison path"
git push
```

验收：GitHub 网页上能看到 `src/sragent_spatial/deepseek_model.py`。
注意：`git status` 里会同时出现你之前没推的 `tests/test_launcher.py` 等改动，这是正常的，一起提交即可。

卡住：`git push` 要账号 → 用 Personal Access Token 当密码，或装 GitHub Desktop 登录后推。

### S2 打通第一次真实 agent 调用（20 分钟）

**顺序很重要：从最窄的路径开始。**

第 1 步：只用 NCBI 页面抓取（v0.2 已经验证过的路径，最稳）

```powershell
.\.venv-agent\Scripts\python.exe run.py search-agent --module ncbi_fetch `
  --message "Fetch the GEO series record for GSE264398 and summarize tissue, platform and sample count" `
  --out outputs/v02/modules/ncbi_fetch.json
```

验收：命令退出 0，`outputs/v02/modules/ncbi_fetch.json` 里 `output` 是像样的元数据摘要。
失败：400/401 → 回到 S0-D；超时 → 加大 `--timeout 180`。

第 2 步：跨库关联（会触发 elink 的多次工具调用）

```powershell
.\.venv-agent\Scripts\python.exe run.py search-agent --module elink `
  --message "List GEO series and PubMed IDs linked to SRA experiment SRX1234567" `
  --out outputs/v02/modules/elink.json
```

第 3 步：完整层级（最贵，放最后）

```powershell
.\.venv-agent\Scripts\python.exe run.py benchmark --methods rules sragent `
  --model deepseek-flash --limit 1 --out outputs/v02/sragent_check.json
```

验收：`sragent` 的 `completed` = 1（不是 `not_run_*`、不是 `failed`）。
失败：若报 `json_schema`/`response_format` 400，是结构化输出不兼容，属已知问题，先绕开（见 [deepseek.md](deepseek.md) 第 4 节）。

**S2 通过 = "DeepSeek + 完整 SRAgent" 这件事成立。** 在此之前不要放量、不要写新查询。

---

## 2. 本周（S3–S4，约 4 小时）

### S3 小样本对照，判断 DeepSeek 够不够用

```powershell
.\.venv-agent\Scripts\python.exe run.py benchmark --methods rules spatial_agent sragent `
  --model deepseek-flash --limit 5 --out outputs/v02/bench5.json
```

看三件事（不是看准确率有多高）：

1. `failed` 有几条、`failure_type` 是什么 → 决定要不要换模型
2. 每个 `prediction.evidence` 的 `quote` 是否真是样本字段原文 → 你已有的校验会拦，拦下多少条
3. `elapsed_seconds` 的量级 → 乘以 100 倍就是扩展到全库的时间成本

**验收**：能得到一句话结论——"DeepSeek 在这条流水线上可用 / 需要换模型 / 需要改提示词"。

### S4 三个决策（必须先定，否则后面白做）

| 决策 | 选项 | 影响 |
|---|---|---|
| 第一平台 | Visium 优先 / Xenium 优先 | 决定 S5 的查询词和 S6 的校验器 |
| 是否做重处理 | 只做目录层（L0/L1）/ 加 Visium 重处理（L2） | 后者要 Linux+Docker+磁盘，周期翻倍 |
| 预算上限 | 单次实验多少 | 决定 `--limit` 和是否加 token 记账 |

我的建议仍是：**Visium 做 L2 试点、Xenium 做 L1 资产配对试点，两条并行**；预算先按 5 条样本试。

---

## 3. 之后（S5–S7，我写代码，你验收）

### S5 `esearch_spatial`（不依赖模型，可与 S2 并行）

现在 14 个模块里唯一"必须改"的一个：上游 `esearch_scrna` 硬编码了
`10x Genomics + paired + illumina + public + has data`，还 `NOT Smart-seq/MARS-seq`，
Xenium/MERFISH 这类成像平台会被直接筛掉。

要做的事：

1. 新增 `esearch_spatial`：按平台（Visium / Visium HD / Xenium / MERFISH / CosMx / Stereo-seq / Slide-seq）+ 组织 + 物种构造查询
2. 平台探测与"提到 ≠ 用了"的区分（沿用 `geo.py` 里已有的 `platform_candidates` 逻辑）
3. 建 10–20 条**已知空间数据集**做召回验证集，报召回率/精确率
4. 接进 registry，`run.py search` 增加 `--platform` 多选

**验收**：在验证集上，召回 ≥ 0.8 且不把普通 scRNA-seq 判成空间数据。

### S6 资产配对与校验（L1）

1. `platform_detector`：从 `geo.py` 抽出，扩到 7 个平台
2. `asset_pairing`：表达 / 坐标 / 图像 / 分割 / 标注 五类，缺失记缺失
3. `coordinate_check`：扩 Visium HD（bin 尺寸）与 Xenium（µm 与像素）
4. 输出"关系清单 + 缺失报告"

**验收**：随机 5 个数据集，人工核对配对结论，错误 ≤ 1。

### S7 评估与发布（v0.4）

1. 金标准从 25 条扩到 50–100 条，明确谁标、标了什么
2. 损耗漏斗：候选 → 有表达 → 有真实坐标 → 有图像 → 有标注
3. 文档写清"没做什么"（不下载、不重处理、不训练）
4. 打 tag `v0.4`

---

## 4. 绝对不要做的事

1. **不要在 S0-D（ping）通过前跑 benchmark**——分不清是 key、网络还是框架的问题，白花钱。
2. **不要 `robocopy /MIR`**——会把工作区里没有的 `outputs/`、`.venv-agent` 一并删掉。
3. **不要把 key 写进 `.env` 后提交**，也不要贴到任何对话/issue 里。
4. **不要用 `deepseek-v4-pro` 跑大批量 agent 循环**（更贵更慢），先用 `deepseek-flash`；难样本再单独切。
5. **不要把 `search-agent` 的 `output` 直接当元数据**——它是 `upstream_agent_output_not_verified_metadata`。
6. **不要在没定平台优先级时扩规模**——查询词写错，召回再高也是错的集合。

---

## 5. 每次交付的完成标准

一段改动算"完成"，必须同时满足：

- [ ] `python -m unittest discover -s tests` 全绿
- [ ] `run.py doctor` 不报错，不泄露密钥
- [ ] 新增能力有对应的离线测试（不依赖网络）
- [ ] 文档里写清"这不能证明什么"
- [ ] 提交信息说明了改了什么、为什么

## 6. 如果只有 30 分钟

只做这三件：

1. `winget install Git.Git`（S0-A）
2. `setx DEEPSEEK_API_KEY ...` + `ping_model.py`（S0-C/D）
3. `git add -A && git commit && git push`（S1）

S2 留给下一次完整时段——它一旦失败会牵扯排查，不适合碎片时间。
