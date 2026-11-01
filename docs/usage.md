# v0.1 运行说明

Python 3.11+，基础功能无第三方依赖。在仓库目录运行下列命令；所有示例输出均在 `outputs/`，默认不提交 Git。

## 已知编号查询与候选搜索

```bash
python run.py --help
python run.py lookup GSE264398 --max-samples 1 --out outputs/geo/GSE264398
python run.py search --organism "Homo sapiens" --tissue lung --platform Visium --limit 5 --out outputs/search/lung_visium
```

`lookup` 是已知编号查询；`search` 才是候选发现。样本资料与研究资料分开保存，不自动把疾病标签或平台信息向下传播。未找到的字段保留未知。

输出包括 `records.json`（嵌套证据与资产）、`records.csv`（平面字段）和 `run.json`（实际查询条件、命中数、样本读取范围及请求溯源）。平台识别是描述关键词启发式，多平台时标为不明确；资产类型由文件名猜测，不能代表内容已验证。CSV 对可能被表格软件当作公式的内容加保护，原始文字保留在 JSON。

## 缓存与网络

```bash
python run.py lookup GSE264398 --max-samples 1 --offline --out outputs/replay/GSE264398
```

默认缓存位于 `outputs/cache`，保存原始响应、原始请求日期和校验和。离线重放必须已有完整缓存。用 `--refresh` 更新缓存，不能与 `--offline` 同用。

Windows 若 Python 证书环境失败，`auto` 传输会尝试 PowerShell 的系统证书验证；也可设置 `--transport powershell`。其他系统默认使用 Python HTTPS。所有方式保留证书校验。程序只请求 NCBI 元数据，不自动下载补充文件或大图。

## 本地盘点与来源比较

将 `PATH_TO_DATA` 换为自己的资料目录：

```bash
python run.py inspect-local PATH_TO_DATA --out outputs/local/inventory.json
python run.py compare --records outputs/geo/GSE264398/records.json --inventory outputs/local/inventory.json --out outputs/local/comparison.json
```

只读取文件名、大小与可选的 `download_manifest.json` 列表；清单使用 `relative` 和 `url` 字段。默认最多盘点 5000 个文件，超出会报告截断；跳过链接目录。不会读取整张图像像素、展开压缩包或计算大文件校验和。`.part` 和空文件标为不完整或待核查，空日志也可能在此范围内。

来源 URL 匹配、仅文件名匹配分别报告。下载清单相符不证明内容一致或空间配准正确，记录保持候选状态。根目录、本地路径与详细结果均只保存在本机。

## 测试

离线测试不请求网络或模型，使用虚构的小型 SOFT 记录。

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

PowerShell 用户先执行 `$env:PYTHONPATH = "$PWD/src"`，再执行 `python -m unittest discover -s tests -v`。

可选安装 `python -m pip install .` 可获得 `sragent-spatial` 命令。上游模型功能的安装、费用与未验证范围见 [上游说明](upstream.md)。
