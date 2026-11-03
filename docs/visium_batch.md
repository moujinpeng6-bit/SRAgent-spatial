# v0.8 Shared Visium and Xenium validation

This page records v0.8. See [Visium HD validation](visium_hd_batch.md) for v0.9.

`discover --archive-root` and `validate-candidates` route screened samples by platform. Traditional Visium matches GEO supplement filenames within the same sample and bundle, uses local files when available, and can retrieve missing files within the per-sample `--download-mib` allowance. Xenium continues to use the archive and native morphology checks. Visium HD is outside the tested adapter.

For Visium, the validator aligns expression barcodes to the tissue positions table, checks the Matrix Market header dimensions, decodes the low-resolution PNG, and multiplies full-resolution image pixel coordinates by `tissue_lowres_scalef` before checking image bounds. The pixel bounds check covers expression barcodes marked as in tissue. Matrix Market values and independent anatomical registration were outside the scope of this run.

The mixed batch used saved GEO discovery selections from two previous runs. The three GSE271370 samples were read afresh from GEO with an 8 MiB per-sample allowance. This resolves file accessibility for these three samples on this run. The earlier access failure report describes the earlier run.

| Sample | Platform | In-tissue expression spots or cells checked | Outside image |
|---|---|---:|---:|
| GSM8375640 | Visium | 4683 | 0 |
| GSM8375641 | Visium | 3649 | 0 |
| GSM8375642 | Visium | 4768 | 0 |
| GSM7990538 | Xenium | 12578 | 0 |

All four samples passed their platform-specific checks. The Xenium case reused its existing local source archive. The Visium run checked the matrix header, not the entire compressed matrix. 104 offline tests passed.

```powershell
python run.py validate-candidates --run <saved-discovery-report.json> --archive-root E:/Xenium --download-mib 8 --out outputs/new_mixed_batch
```

[Mixed-batch evidence](../examples/lung_mixed_batch/run_summary.json) includes source links, file read scope, image scale and bounds, and aggregate validation results.
