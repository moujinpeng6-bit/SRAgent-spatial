# 两项研究的 31 个 GSM：元数据完成，文件内容受限

- `samples.csv` / `samples.json`：全部 31 个样本的元数据筛选、来源和引文摘录。旧的 10 条结果复用，仅新增 21 条模型核查。
- `summary.json`：两项研究范围、续跑来源 SHA256、计数、数据可用性说明。
- `asset_audit.csv` / `asset_audit.json`：每个样本及每个所选文件的读取状态。194 个文件入口均返回 403；31 套均为 `unavailable`，没有实际内容检查通过的样本。

`metadata_selected=true` 只表示元数据条件符合。`basic_correspondence_verified=false` 和 `ready_for_spatial_use=false` 在这里表示尚未验证，不等于证明文件不匹配或数据本身不可用。

GSE318039：8 个 GSM；GSE271370：23 个 GSM。各研究已列出的样本均核查完毕，但未扩大到其他 GEO 研究。作者对 GSE318039 的原始数据另有 embargo 说明；GSE271370 本次下载失败的具体原因仍未确定。

这些是原始模型引文的相关摘录，不是独立人工金标准。完整过程与后续本地文件核查命令见 [报告](../../docs/asset_audit.md)。
