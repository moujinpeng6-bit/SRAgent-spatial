# Xenium candidate batch

`discover --archive-root` now sends screened candidates to the existing preparation and validation tools. Alternatively, replay the file stage from a saved discovery run without invoking the language model.

```powershell
python run.py discover --study GSE250346 --platforms Xenium --max-studies 1 --max-samples-per-study 7 --max-total-samples 7 --refresh --model deepseek-flash --archive-root E:/Xenium --out outputs/lung_batch/discovery
python run.py validate-candidates --run outputs/lung_batch/discovery/run.json --archive-root E:/Xenium --out outputs/lung_batch/replay
```

Use a fresh output directory for each batch. Results are written per sample and to `batch.json` after each candidate. A missing file, HTTP failure, or download budget limit has its own status. The download allowance defaults to zero. `--download-mib` sets the ceiling per candidate. Other platforms are reported as unsupported in this batch entry.

The live GEO discovery screened seven samples from GSE250346. After fixing a report-writing error in the first integrated run, the file stage was rerun from the same saved candidates. The first six lacked complete local archives. With a 1 MiB allowance, all six remote packages were reported as exceeding the budget. In both reruns the final sample, GSM7990538, passed all 19 checks. An offline test injected an HTTP 403 and confirmed that the following candidate was still processed. The live batch did not encounter an HTTP 403.

Xenium cell IDs align matrix columns to cell coordinates. The native full-resolution morphology frame uses an upper-left origin. When experiment and OME pixel scales agree, the cell index records x and y in micrometres and in full-resolution pixels. The preview uses the image pyramid at the same physical extent. The live scale was 0.2125 micrometres per pixel and all 12,578 centroids were inside the image. H&E alignment remains a separate task.

[Aggregate run evidence](../examples/lung_xenium_batch/run_summary.json) excludes local paths, raw files and per-cell coordinates. 103 offline tests passed.
