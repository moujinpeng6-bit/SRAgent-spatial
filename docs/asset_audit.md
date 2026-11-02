# 31 个肺 Visium 样本：完整元数据核查与文件受限报告

日期：2026-11-02。范围固定为此前发现的 GSE318039 和 GSE271370，不是重新扩大检索，也不是 GEO 全库。

## 结果

| 层次 | 本次结果 | 能说明什么 |
|---|---|---|
| GSM 元数据 | 保留此前 10 条，补完剩余 21 条，共 31 条完成、0 失败、0 未读取 | 31 条均通过人类肺组织 Visium 的保守元数据筛选 |
| 文件清单与命名 | 31 套均有独立的条码、features、MTX、坐标、比例因子、低分辨图像链接；另有 8 个 HDF5 | 文件 URL 均归属各自 GSM，去掉 filtered 等角色词后的切片名称一致；尚不是内容验证 |
| 下载可用性 | 194 个所选文件入口均返回 HTTP 403，本次下载字节数为 0 | 本次无法读取这些文件；不应解释为文件内容不匹配 |
| 文件内部对应 | 0 套实际完成内部验证，31 套标记 unavailable | 不能报告矩阵列数、条码交集、图像缩放或物理切片配准已经通过 |

两个研究分别列出 8 和 23 个 GSM，已全部核查；全局查询仍有更多研究未读取，因此元数据续跑保留 `completed_with_limits` 状态。续跑使用 DeepSeek `deepseek-flash`，耗时 212.953 秒；只对剩余样本调用模型，旧结果先重新检查身份、字段 SHA256 和引用后复用。没有传入开发集参考标签，也没有计算准确率。

## 可用性证据与原因边界

GSE318039：作者的 [Spatial-Transcriptomic 仓库说明](https://github.com/Lung-research-group/Spatial-Transcriptomic) 报告原始 Visium 数据的 embargo 截止为 2027-01-04。这是作者对数据发布状态的说明，不是从 HTTP 403 直接推断出的原因。

GSE271370：[论文数据声明](https://www.nature.com/articles/s41467-026-78188-3#data-availability) 指向 GEO，[作者代码仓库](https://github.com/carlosgarciaprieto/SpatialCOVID19) 中也未发现可替代的成套矩阵/坐标/组织图像文件；本次仍收到 403，具体原因未确定。不能把这一项也自动归为 embargo。

额外抽查了 GEO 官方下载接口和匿名 FTP，未得到可读取文件；一个此前可下载的独立对照文件当前也返回 403，因此不能排除本次网络环境或服务端访问条件的影响。

本机会话当前没有挂载此前提及的 E 盘。用户确认暂无这些样本的本地文件，要求先同步受限报告；因此文件内部核验留待资源可读时继续。

## 核查程序已实现的内容

`audit-assets` 从经过筛选的 GSM 自身文件链接构建文件组：拒绝其他 GSM 的 URL、多个切片名称混杂或同角色多个文件的歧义，不任意选一份。

文件可读取时执行：

1. 读取完整的小型条码、features、坐标、比例因子及低分辨 PNG，记录下载 SHA256。
2. 最多读取 MTX 文件前 64 KiB 压缩片段，解析 Matrix Market 头部；核对矩阵列数与条码数、行数与 features 数。该步骤不校验矩阵值或整份 gzip 完整性。
3. 核对每个表达条码能否在坐标表找到，检查重复 ID、非法坐标以及组织内点缩放后的图像边界。
4. 使用 Pillow 实际解码低分辨 PNG；这比仅检查文件名或 PNG 头部更强，但不证明组织形态学配准。
5. 若同样本 HDF5 文件不超过 32 MiB 且可读取，另外核对内部条码顺序和矩阵形状是否与 MTX/TSV 一致。

完整小文件上限 8 MiB；HDF5 上限 32 MiB；矩阵只读头部。不会因为设置上限而把未下载文件记为已经通过。`basic_correspondence_passed` 仅指上述内部结构与几何检查，不能证明同一物理供者、切片、病理区域或细胞分割。

这些读取/对应算法通过了本地合成 gzip/CSV/PNG/矩阵数据的正反例测试。此类测试不计入真实 31 个样本的内容通过数。

本轮 82 项离线测试全部通过；独立环境依赖检查通过，包含 HDF5 条码顺序不一致和 HTTP 403 的回归用例。

## 复现及续查

```powershell
.\.venv-agent\Scripts\python.exe run.py discover `
  --resume outputs/discovery_lung_visium/run.json `
  --model deepseek-flash --max-samples-per-study 50 --max-total-samples 50 `
  --timeout 900 --out outputs/discovery_lung_visium_complete

.\.venv-agent\Scripts\python.exe run.py audit-assets `
  --run outputs/discovery_lung_visium_complete/run.json `
  --out outputs/discovery_lung_visium_asset_audit
```

文件审计出现 unavailable 时退出 2；HTTP 状态、缺失角色、文件来源均保存在结果里。先安装 `.[validation]` 以启用 PNG 和 HDF5 读取。

有原始 GEO 文件名的本地副本后，可只读本地目录：

```powershell
.\.venv-agent\Scripts\python.exe run.py audit-assets `
  --run outputs/discovery_lung_visium_complete/run.json `
  --local-root "YOUR_DATA_DIRECTORY" --out outputs/local_asset_audit
```

本地模式不访问网络，不修改原始文件；同名文件多份会标为歧义，不任意挑选。目录扫描最多 50,000 个文件，不跟随目录符号链接。

公开结果见 [完整 31 样本目录](../examples/lung_visium_complete/)。完整原文输入与过程缓存留在本机 `outputs/`；仓库不包含密钥、原始测序、组织图像或大矩阵。
