# 第二轮独立留出评鉴

范围仅为人类。GEO 检索任务覆盖人卵巢传统 Visium、健康人肝 Visium HD 和人肾 Xenium。人肾任务接受健康与患病组织。12 个公开样本在运行前冻结于本地提交 `cfc0f18`。这 12 个 GSM 未出现在前一版提交的代码、测试、文档或示例中。其中一条阴性样本的研究 GSE322553 曾作为搜索结果列在旧文档中。系统只收到研究条件，完成检索后才按 GSM 编号对照参考。

每项任务最多读 5 项 GEO 研究，每项展开最多 4 个样本，候选下载上限 96 MiB。用 DeepSeek Flash 运行。由于器官、参考样本与下载上限都改变，本轮比例和首次评鉴适合各自解读。

[冻结的发布者证据标签](reference_human.json) · [逐样本结果](human_summary.json)

## 实测结果

| 任务 | 发现 | 纳入 | 文件配齐／通过 | 参考阳性纳入／总数 |
| --- | ---: | ---: | ---: | ---: |
| 人卵巢传统 Visium | 20 | 8 | 0／0 | 0／3 |
| 健康人肝 Visium HD | 10 | 0 | 0／0 | 0／3 |
| 人肾 Xenium | 20 | 12 | 0／0 | 1／3 |
| 合计 | 50 | 20 | 0／0 | 1／9 |

按暂定参考，召回率是 **11.1%**，文件配齐率是 **0/20**。3 条参考阴性未被纳入。20 条入选中还有 19 条未在冻结标签中，整批的误收率无法由这个参考集给出。

- 卵巢的三个阳性样本所属研究均未进入本轮前五项候选。已入选的卵巢样本提供高分辨率 PNG，当前配套规则要求低分辨率 PNG。
- 三个健康肝阳性样本都被找到。研究论文明确三位供体健康，单条 GEO 样本未声明，故被标为未知。
- 人肾有一个阳性纳入，一个研究未进入前五项，一个样本超过每项研究四条的展开上限。入选文件中常见只有 HDF5，或缺少当前可验证格式的坐标、显微图像及实验标定。

健康肝任务耗时约 78 秒。卵巢与肾脏的准备步骤都完成并落盘，等待最终模型输出时连接重置。原进程约 1,242 秒与 1,086 秒后结束，通过断点重建报告，未再执行搜索、下载或模型调用。按已记录的 token 和峰值未缓存费率估计约 USD 0.052，两个中断任务的模型费用记录不完整。[公开费率](https://api-docs.deepseek.com/quick_start/pricing/)

## 研究者需要做什么

请交给一位没参与系统开发或初始标注的研究者。只发 [盲审任务单](reviewer_task_human.md)，在提交前请研究者先不看机器报告或参考标签。逐条核对 GEO 样本、研究及论文，记录物种、组织、平台、健康状态、纳入／排除／不确定和证据链接。请附上复核日期和研究者代号。

研究者提交后，若按任务单的 JSON 格式返回，直接读取保存的机器观测重新计算分数。标为不确定的样本单独计数。

```powershell
.\.venv-agent\Scripts\python.exe examples/independent_evaluation/score_review.py --summary examples/independent_evaluation/human_summary.json --review "<研究者返回的 JSON>" --out "<输出文件>"
```

重新跑同一套已公开参考条件的命令如下。用于下一轮独立验收时，须另外冻结未参与本轮分析的新样本。

```powershell
.\.venv-agent\Scripts\python.exe examples/independent_evaluation/run.py --reference examples/independent_evaluation/reference_human.json --out "<新输出目录>" --parallel 3
```
