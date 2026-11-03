# Visium HD batch validation

This page records v0.9. See [archive and resolution verification](visium_hd_archive.md) for v0.10.

The same `discover --archive-root` and `validate-candidates` commands handle Xenium, conventional Visium, and Visium HD. For HD, the adapter groups sample-owned, individually listed GEO files by explicit `002um`, `008um`, or `016um` filename tags. Each group has its own validation report. Unlisted bin sizes are recorded separately. A multi-sample archive or a bundle without explicit resolution tags requires a separate acquisition adapter.

The HD validator reads expression barcodes, the Matrix Market header, Parquet positions, scale factors, and the low-resolution PNG. It checks resolution in barcode names and in scale-factor metadata when available. It matches expression barcodes to positions and multiplies full-resolution x and y by the low-resolution scale before checking image bounds for in-tissue expression bins. The Parquet reader scans batches and retains coordinates for expression barcodes. All bins of one candidate share a download allowance.

```powershell
python -m pip install -e ".[agent,visium-hd]"
python run.py discover --study GSE286227 --platforms "Visium HD" --max-studies 1 --max-samples-per-study 2 --max-total-samples 2 --refresh --model deepseek-flash --archive-root <local-root> --download-mib 32 --out outputs/hd_lung_run
```

Both public human lung samples from GSE286227 provide 8 µm files. GSM8721365 and GSM8721366 had 317,761 and 133,878 matched in-tissue expression bins respectively. Both images decoded successfully and neither sample had bins outside the image bounds. The 2 and 16 µm branches were exercised with offline fixtures, including a 16 µm group containing 8 µm expression barcodes that failed. The live evidence covers 8 µm only. It covers matrix dimensions and file correspondence, with full matrix values and anatomical image registration outside the checks. 105 offline tests passed.

Sources: [GSE286227](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE286227), [GSM8721365](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8721365), [GSM8721366](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM8721366), [Space Ranger spatial output format](https://www.10xgenomics.com/support/software/space-ranger/latest/analysis/spatial-outputs).

[Aggregate run results](../examples/lung_visium_hd_batch/run_summary.json)
