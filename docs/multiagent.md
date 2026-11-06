# 总控和三个空间子 Agent

新增 `find-spatial --source biostudies --multi-agent --model deepseek-flash`。使用现有的 DeepSeek 模型工厂和 LangGraph，建立四个独立的模型调用图：一个总控，以及搜索、文件、验证三个子 Agent。同一模型服务扮演不同职责，不需要训练四个模型。

总控得到三个委派工具，由模型决定何时调用哪个子 Agent。每个子 Agent 都独立调用模型，并有自己的工具与消息上下文。

| 角色 | 可以调用的工具 | 实际职责 |
|---|---|---|
| 总控 | delegate_search / delegate_assets / delegate_validation | 根据状态委派任务，遇到缺件返回准备阶段，决定重试和结束 |
| 搜索子 Agent | search_catalog / read_study | 查询固定物种、组织、平台范围，逐研究读取证据 |
| 文件子 Agent | inspect_files / prepare_files | 查看官方文件清单，取得或修复同切片配套文件，不执行空间验证 |
| 验证子 Agent | validate_sample | 核查文件哈希并重新读取表达、坐标和图像；缺件时返回需要补件的状态 |

这次没有把整个流程包进唯一工具。模型会看到真实工具结果，下一次选择会随结果变化。程序仍约束科学判断和操作范围：没有搜到的编号不能调用；未通过筛选不能准备；未准备不能验证；缺失或哈希变化不能沿用通过结果。每个相同操作最多尝试三次，下载预算跨同一研究的模型重试累加。模型不能自行提供本地路径、下载 URL 或扩大用户范围。

```text
总控 → 搜索子 Agent → 查询候选、读取证据
     → 文件子 Agent → 文件清单、准备配套文件
     → 验证子 Agent → 完整计数、条码、坐标和图像检查
                       ↓ 如果缺件
     → 文件子 Agent → 补件
     → 验证子 Agent → 重新检查
```

## 实际验收

普通轮次由总控委派搜索、文件、验证三个子 Agent，完成 10 次底层工具操作。两张肺 Visium 切片通过；一项不支持的 TAR 布局独立记录。该轮复用已有元数据和配套文件缓存。

另一轮新建验收副本，没有复制元数据缓存，发生 7 次实时官方元数据请求。显式注入一次临时准备错误，以及验证前删除副本中的比例因子小文件。真实 DeepSeek 调用记录显示：

1. 文件子 Agent 第一次准备遇到受控超时，模型决定第二次调用准备工具。
2. 验证子 Agent 返回 `missing_or_changed_files`。
3. 总控再次委派文件子 Agent，恢复缺失文件。
4. 总控再次委派验证子 Agent，两张切片最终通过。

故障轮次共 15 次底层工具操作，委派顺序为 `search → assets → validation → assets → validation → validation`。最后两次验证委派分别核查准备后的切片。四种角色均有真实模型响应，故障轮次分别为搜索 3、文件 7、验证 6、总控 7 轮。归档网络下载字节为 0：配套文件复用已校验缓存，缺失小文件从归档分段缓存恢复。**两处错误均为明确标注的受控测试，不是本轮实际网站故障；原始文件未被删除。**

完整 JSON、工具调用、模型消息及受控故障脚本见 [验收记录](../examples/multiagent_acceptance/)。模型消息日志保留调用内容及用量，不包含 API 密钥。

## 运行

```powershell
.\.venv-agent\Scripts\python.exe -m sragent_spatial find-spatial --source biostudies --multi-agent --model deepseek-flash --organism "Homo sapiens" --tissue lung --platforms Visium --max-studies 3 --samples-per-study 2 --download-mib 128 --biostudies-assets "E:/SRAgent-spatial/biostudies-lung-20261106" --out "E:/SRAgent-spatial/my-new-multiagent-report"
```

`--biostudies-assets` 明确指定已有文件及缓存目录；省略时使用新报告目录下的 `assets/`，允许在预算内取新文件。`--out` 必须是新目录。真实验收已经覆盖缓存文件与实时元数据，尚未覆盖本轮所有资产均从空目录下载的多 Agent 场景。

输出 `batch.json`、逐样本 `samples.csv`、`trace.json` 和固定请求。`trace.json` 中每次工具操作有角色、参数、尝试次数、结果和时间；模型记录有角色、工具调用、用量，以及可用时的服务返回模型名。`workflow_complete` 表示候选均有处理结论，不代表所有候选均通过空间验证。

## 范围

这是 BioStudies 空间任务的一套完整总控/子 Agent 运行实例。原版 SRAgent 的全部数据库、论文、本体和序列模块，尚未在这套新总控里统一在线验收。Google Cloud / BigQuery 没有启用。

文件准备沿用可识别 Space Ranger ZIP 的覆盖范围；TAR、任意 Xenium 外部清单等仍记录限制。现阶段子 Agent 按依赖串行交接。多 Agent 会话没有实现 `--resume` 自动重建对话状态；中断后可以新建报告并复用资产缓存，既有普通批处理的续跑入口保持可用。更大规模、多平台和独立准确率评价仍需要后续验收。
