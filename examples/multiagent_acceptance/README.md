# 真实 DeepSeek 多 Agent 验收

日期：2026-11-06。一个总控模型图，加搜索、文件、验证三个独立子模型图。请求模型及最终普通轮次服务返回模型名均为 `deepseek-flash`。

| 验收 | 普通缓存轮次 | 实时元数据与受控故障轮次 |
|---|---|---|
| 官方元数据实时请求 | 0；明确复用缓存 | 7；新副本未复制元数据缓存 |
| 底层工具操作 | 10 | 15 |
| 模型响应：总控 / 搜索 / 文件 / 验证 | 4 / 3 / 3 / 2 | 7 / 3 / 7 / 6 |
| 通过切片 | 2 | 2 |
| 归档下载 | 已校验缓存复用 | 已校验缓存复用，缺失比例文件从缓存补齐 |

普通轮次真实执行 `search → assets → validation`。受控故障轮次真实执行 `search → assets → validation → assets → validation → validation`。每个箭头都是总控发出的委派工具调用；各子 Agent 再调用各自的细分工具。Python 没有预先串联这些委派步骤。

受控轮次有两处显式故障：目标 ZIP 研究的首次文件准备抛出模拟超时；第一次验证前删除新副本的比例因子小文件。模型先重试准备，再在看到缺件状态后委派补件并重新验证。两处均不是声称网站实际故障。原始文件保留，测试脚本有新目录和删除范围检查。

两个 Visium 切片的原始文件来自 S-BSST1410，实际重新检查 8,916 个条码/位置、18,462,295 条计数和配套图像，两轮均通过。一项 E-MTAB-11265 的 TAR 布局不受支持，保留 `unsupported_bundle`；小鼠研究排除。`workflow_complete=true` 表示任务均有结论，批次仍是 `completed_with_issues`，没有把不支持项判通过。

- [总摘要](run_summary.json)
- [普通结果](normal/batch.json)、[逐条 CSV](normal/samples.csv)、[模型消息和工具日志](normal/trace.json)
- [受控故障结果](controlled_repair/batch.json)、[模型决策与重试日志](controlled_repair/trace.json)、[故障声明](controlled_repair/controlled_faults.json)
- [受控验收脚本](run_controlled_acceptance.py)：使用本次已存在的 E 盘来源；重跑前改成新的测试目录，脚本拒绝覆盖已有目录。

调用日志是实际模型与工具返回的记录，包含用量及逐条结果，没有 API 凭证。原始图像、矩阵、缓存分段保留本地。132 项测试与依赖检查通过。范围和运行命令见 [多 Agent 文档](../../docs/multiagent.md)。
