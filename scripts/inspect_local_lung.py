"""Read-only inventory and bounded extraction of a local, complete Xenium lung bundle."""

import argparse
import hashlib
import json
import os
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("E:/Xenium"))
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/local_lung_spatial")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    metadata = json.loads((args.root / "sample_metadata.json").read_text(encoding="utf-8-sig"))
    selected = [row for row in metadata["samples"] if row.get("organ", "").lower() == "lung"]
    inventory = []
    for row in selected:
        folder = args.root / "samples" / (str(row["row"]).zfill(2) + "_Lung")
        files = []
        for base, dirs, names in os.walk(folder):
            for name in names:
                path = Path(base) / name
                info = path.stat()
                files.append({"path": str(path), "bytes": info.st_size, "incomplete": name.endswith(".part")})
        inventory.append({"row": row["row"], "dataset": row["dataset"], "slide": row["slide"],
                          "source_url": row["url"], "organ": row["organ"], "state": row["state"], "files": files})
    (args.out / "lung_inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    bundle = next((args.root / "samples/45_Lung/GEO_bundle").glob("*.tar.gz"))
    out = args.out / "GSM7990538"
    out.mkdir(exist_ok=True)
    wanted = {"cells.csv.gz", "cell_feature_matrix.h5", "experiment.xenium", "gene_panel.json", "metrics_summary.csv",
              "cell_boundaries.csv.gz", "nucleus_boundaries.csv.gz"}
    members = []
    written = []
    total = 0
    before = bundle.stat()
    with tarfile.open(bundle, "r|gz") as archive:
        for member in archive:
            members.append({"name": member.name, "bytes": member.size, "file": member.isfile()})
            base = PurePosixPath(member.name).name
            if not member.isfile() or base not in wanted:
                continue
            if ".." in PurePosixPath(member.name).parts or member.size > 64 * 1024 * 1024:
                raise ValueError("Unexpected archive member path or size")
            if (out / base).exists():
                raise ValueError("Extraction destination already exists; preserve existing output and use another --out")
            total += member.size
            if total > 128 * 1024 * 1024:
                raise ValueError("Extraction budget exceeded")
            stream = archive.extractfile(member)
            body = stream.read(member.size + 1)
            if len(body) != member.size:
                raise ValueError("Incomplete selected archive member")
            (out / base).write_bytes(body)
            written.append({"archive_member": member.name, "output": str(out / base), "bytes": len(body),
                            "sha256": hashlib.sha256(body).hexdigest()})
            print(json.dumps({"extracted": base, "bytes": len(body)}), flush=True)
    after = bundle.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Source archive changed during read")
    report = {"lung_records": len(selected), "source_archive": str(bundle), "archive_bytes": before.st_size,
              "members": members, "extracted": written, "original_data_modified": False,
              "scope": "Selected small non-image files copied into workspace; large image members read past, not extracted"}
    (args.out / "archive_inspection.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"lung_records": len(selected), "archive_members": len(members), "extracted_files": len(written), "extracted_bytes": total}), flush=True)


if __name__ == "__main__":
    main()
