"""Build a beginner-readable local report from completed spatial checks."""

import csv
import html
import importlib.metadata
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "outputs/local_lung_spatial"


def main():
    cells = json.loads((OUT / "cell_validation.json").read_text(encoding="utf-8"))
    annotations = json.loads((OUT / "annotation_validation.json").read_text(encoding="utf-8"))
    inventory = json.loads((OUT / "lung_inventory.json").read_text(encoding="utf-8"))
    with (OUT / "cell_spatial_index.csv").open(encoding="utf-8", newline="") as stream:
        anchor = next(row for row in csv.DictReader(stream) if row["cell_id"] == cells["anchor_cell_id"])
    passed = sum(cells["checks"].values())
    partial = [row["id"] for row in annotations["regions"] if row["vertices"] != row["mapped_dapi_vertices_in_bounds"]]
    packages = {name: importlib.metadata.version(name) for name in
                ["numpy", "scipy", "matplotlib", "tifffile", "imagecodecs", "h5py"]}
    save = lambda name, value: (OUT / name).write_text(value, encoding="utf-8")
    versions = "，".join(f"{k} {v}" for k, v in packages.items())
    region_rows = "\n".join(f"| {row['id']} | {row['label']} | {row['vertices']} | {row['mapped_dapi_vertices_in_bounds']} |"
                            for row in annotations["regions"])
    check_rows = "\n".join(f"| {name} | {'通过' if value else '未通过'} |" for name, value in cells["checks"].items())
    distances = "、".join(f"{row['distance_um']:.2f}" for row in cells["five_nearest_neighbors"])
    image1 = (OUT / "lung_spatial_demo.png").as_posix()
    image2 = (OUT / "lung_pathology_regions.png").as_posix()
    text = f"""# E 盘肺组织：实际空间对应测试

这次在 E:/Xenium 的清单和文件中找到 {len(inventory)} 条标记为 Lung 的记录。它们主要是 Xenium 空间转录组，不是此前网上核查的 31 个 Visium GSM。目录条数也不等于独立供者数或已验证样本数。

本次完整核对 1 套“表达—细胞—坐标—图像”材料，另检查 1 套肺癌图像、区域标注和图像变换。原始 E 盘文件只读，未修改；生成物位于本报告所在目录。定位肺组织依据来源元数据以及 experiment.xenium 的 Human/Lung 字段，不是让图像分类模型猜测器官。

## 先看结果

GSM7990538 / GSE250346，来源记录为人类肺组织、Control，平台 Xenium。原始文件在 E:/Xenium/samples/45_Lung/GEO_bundle。本次 {passed}/{len(cells['checks'])} 项对应检查通过，但这不是组织分类准确率或所有细胞分割正确率。

| 检查对象 | 实际结果 | 为什么与空间有关 |
|---|---|---|
| 细胞 | {cells['cells']:,} 个，ID 唯一 | 每个位置要知道属于哪个细胞 |
| 表达矩阵 | 541 个特征 × 12,578 个细胞，其中 343 个是真正的基因表达特征 | 其余 198 个是检测对照/未分配编码，不能当成基因 |
| ID 对应 | 表达矩阵条码与细胞表 ID 集合及顺序均一致 | 可以把某细胞的表达准确放回它的位置 |
| 计数核对 | 基因计数合计、全部特征计数合计分别与细胞表逐个一致 | 对应不只依赖相近文件名 |
| 坐标 | 单位 µm，全部 12,578 个质心位于图像范围内 | 能计算真实物理距离 |
| 图像尺度 | 0.2125 µm/pixel，实验文件和 OME 图像元数据一致 | µm 除以像素大小，才得到图像像素位置 |
| 图像来源 | 本地图像 SHA256 与同一表达数据包内图像一致 | 有证据把这张图与这套表达数据连接起来 |
| 细胞/细胞核边界 | 两类边界 ID 均与细胞表一致，顶点有限且在图像内 | 细胞的区域轮廓也在同一参考系 |

## 图一怎样看：这就是“spatial 思想”的实际内容

![真实肺组织的表达、坐标、图像与邻居]({image1})

左上：灰白底图是 DAPI，即用于显示细胞核的荧光图像。青色点是细胞质心，即代表细胞位置的中心点。组织图像与细胞位置大体吻合；这次没有测量独立真值下的分割误差。

右上：从表达矩阵读出每个细胞的 EPCAM 计数，通过细胞 ID 找到该细胞坐标，再把表达画在原来的组织位置。颜色显示 log(1+计数)，用于压缩数值范围；没有做细胞类型诊断。灰点表示这次未检出 EPCAM，不能直接解释为某种细胞不存在。

左下：ROI 是 Region of Interest，意为“感兴趣区域”。这里人为在坐标上画了半径 150 µm 的圆，得到 {cells['demo_roi']['cells']} 个细胞。橙点是质心，绿线是数据提供的细胞边界。这个圆只是测试程序能否按物理区域取细胞，不是作者提供的病理分区。

右下：选定真实细胞 {cells['anchor_cell_id']}，按同一切片上的欧氏距离寻找最近 5 个细胞。距离依次为 {distances} µm。它们是在组织中靠近，不是因为基因表达相似而被排到一起。这些连线不能直接证明细胞相互通讯。

这一个中心细胞的真实记录：X={float(anchor['x_um']):.2f} µm，Y={float(anchor['y_um']):.2f} µm；EPCAM={anchor['EPCAM']}，SFTPC={anchor['SFTPC']}，PTPRC={anchor['PTPRC']}。表达值、坐标、图像位置都能经同一个细胞 ID 联系起来。本样本全体细胞到最近其他细胞的距离中位数为 {cells['nearest_neighbor_distance_um']['median']:.2f} µm，不能外推为所有肺组织的固定间距。

## 图二：组织区域也需要有位置和参考系

独立样本 26_Lung 是来源提供的 humanLung_Cancer_FFPE。这里本地有 H&E、DAPI、11 个多边形标注，以及 3×3 图像变换矩阵。H&E 是 Hematoxylin and Eosin，即苏木精—伊红染色；DAPI 的全名是 4′,6-diamidino-2-phenylindole，常用于显示细胞核。

![独立肺癌样本的病理区域与图像变换]({image2})

上图：作者标注的 Tumor（肿瘤）、Normal lung（正常肺）、Lymphoid aggregate（淋巴细胞聚集区）、Blood vessels（血管）、Benign bronchial epithelium（良性支气管上皮）都有具体多边形边界。标签来自原标注，不是本程序重新诊断。

下图：按配套矩阵把 H&E 像素位置转换到 Xenium 图像像素位置。不同显微镜拍摄的图像会旋转、平移或缩放，不能直接用相同的数字坐标叠加。这正是需要图像配准的原因。本程序使用已有变换，没有重新训练配准模型。

11 个区域的原始顶点全部落在 H&E 图像范围内。但是编号 {', '.join(map(str, partial))} 的区域转换后有顶点超出 DAPI 图像范围，因此不能说全部区域都在 DAPI 内有完整覆盖。图像预览也能看到 H&E 的右端超出 DAPI 的覆盖范围。未用独立关键点测量配准误差。

| 图中编号 | 原始标注 | 原始顶点数 | 转换后落在 DAPI 内的顶点数 |
|---|---|---|---|
{region_rows}

本地 26_Lung 尚未发现这套样本的 cells.csv.gz 和表达矩阵，所以还没有完成“细胞表达属于哪个病理区域”的核验。它和 GSM7990538 是两个样本，不能借用前者的区域标注解释后者的细胞。

## 对你的 spatial 版本意味着什么

普通检索可以告诉你“某项研究提到肺”。你的 spatial 版本要继续回答：是哪一个样本？表达和坐标能按 ID 对应吗？坐标是什么单位、属于哪张切片？图像比例是多少？对应区域是否完整覆盖？缺哪个文件？

这次已经在真实本地数据上证明，可以回答其中一部分，并实际做按区域取细胞、按距离取邻居和空间表达展示。这里的空间不是把图片附在表达表旁边，而是把它们接在有单位、有切片身份的同一个位置系统中。

这份图文说明描述最初的独立脚本实验：当时未调用大语言模型，也未训练 Transformer，未完成所有 31 条本地记录的内部核对。后续 v0.5 已将核心核查封装为输入可配置的 Xenium 工具，接入原版总控并完成 GSM7990538 的真实调用；详见项目 docs/local_validation_agent.md 和 examples/local_lung_agent。图中两个样本的专用绘图脚本与 Agent 通用验证器分别保留，不应把单样本通过外推为任意空间数据都已验证。

## 文件与复现

- inspect_local_lung.py：只读清单，有限提取已经完成的 GSM7990538 压缩包中的小文件。
- test_local_lung_spatial.py：读取表达矩阵、坐标、边界与图像金字塔；计算邻居并绘图。
- report_local_lung.py：生成本说明。
- cell_validation.json：逐项核查结果与图像哈希。
- annotation_validation.json：区域边界、变换及覆盖情况。
- cell_spatial_index.csv：每个细胞的坐标、演示基因计数、最近邻距离和 50 µm 邻居数量。
- nearest_neighbors.json：图一中心细胞的 5 个邻居及距离。
- lung_inventory.json：本次找到的 31 条 Lung 记录及文件清单；.part 文件明确标记未完成。

测试只解码预先存在的低分辨率金字塔层，不整张加载多 GB 图像；没有把预览图当成新增的原始数据。重新运行时先确认 E 盘路径及完整压缩包仍在，inspect 脚本为了防止覆盖会拒绝已提取的目标文件。已有提取结果时直接运行第二、第三个脚本即可。

本次环境：{versions}。这些是本地演示依赖，不代表已经进入项目通用安装依赖。

来源：[GSM7990538](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM7990538)；[10x Xenium 文件格式与坐标单位](https://cf.10xgenomics.com/supp/xenium/xenium_documentation.html)；[10x 图像变换说明](https://www.10xgenomics.com/support/software/xenium-explorer/latest/tutorials/xe-image-alignment)；[H&E 与 Xenium 坐标转换](https://testkb.10xgenomics.com/s/article/35386990499853-How-can-I-convert-coordinates-between-H-E-image-and-Xenium-data)。

## 机器检查明细

| 检查项 | 结果 |
|---|---|
{check_rows}
"""
    save("肺组织空间验证说明.md", text)
    # Minimal Markdown rendering for this controlled, local report; no external JS/CDN.
    import re
    body, table_open, list_open = [], False, False
    for line in text.splitlines():
        if not line.startswith("|") and table_open:
            body.append("</tbody></table></div>"); table_open = False
        if not line.startswith("- ") and list_open:
            body.append("</ul>"); list_open = False
        if line.startswith("|"):
            if set(line.replace("|", "").strip()) <= {"-", " "}:
                continue
            if not table_open:
                body.append('<div class="scroll"><table><tbody>'); table_open = True
            body.append("<tr>" + "".join("<td>" + html.escape(item.strip()) + "</td>" for item in line.strip("|").split("|")) + "</tr>")
        elif line.startswith("!["):
            match = re.match(r"!\[(.*?)\]\((.*?)\)", line)
            filename = Path(match.group(2)).name
            body.append(f'<figure><a href="{filename}"><img src="{filename}" alt="{html.escape(match.group(1))}"></a><figcaption>点击图片可查看大图</figcaption></figure>')
        elif line.startswith("## "):
            body.append("<h2>" + html.escape(line[3:]) + "</h2>")
        elif line.startswith("# "):
            body.append("<h1>" + html.escape(line[2:]) + "</h1>")
        elif line.startswith("- "):
            if not list_open:
                body.append("<ul>"); list_open = True
            body.append("<li>" + html.escape(line[2:]) + "</li>")
        elif line.strip():
            escaped = html.escape(line)
            escaped = re.sub(r"\[(.*?)\]\((https://[^)]+)\)", r'<a href="\2">\1</a>', escaped)
            body.append("<p>" + escaped + "</p>")
    if table_open:
        body.append("</tbody></table></div>")
    if list_open:
        body.append("</ul>")
    style = "body{margin:0;background:#f5f7fb;color:#18263a;font-family:'Microsoft YaHei',sans-serif;font-size:17px;line-height:1.9}main{max-width:1060px;margin:30px auto;padding:36px;background:white;border-radius:14px}h1{font-size:32px;line-height:1.5}h2{font-size:24px;margin-top:42px;color:#135973}p{margin:18px 0}table{border-collapse:collapse;font-size:14px;width:100%}td{border:1px solid #d7e0eb;padding:10px}tr:first-child{background:#eaf2f7;font-weight:bold}.scroll{overflow-x:auto}figure{margin:24px 0}img{width:100%;height:auto}figcaption{font-size:13px;color:#65758b}a{color:#126781}li{margin:10px 0}@media(max-width:750px){main{margin:0;padding:18px}body{font-size:16px}}"
    save("肺组织空间验证说明.html", '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>E盘肺组织空间验证</title><style>' + style + "</style><main>" + "\n".join(body) + "</main></html>")
    save("runtime_versions.json", json.dumps(packages, indent=2))
    print(json.dumps({"report": str(OUT / "肺组织空间验证说明.html"), "passed": passed,
                      "partial_regions": partial}, ensure_ascii=True))


if __name__ == "__main__":
    main()
