# Reuse a 10x Xenium image and acquire its companions

Run the existing local agent with an exact sample ID from the Xenium image cache.

```powershell
python run.py validate-local-agent --10x-cache E:/Xenium --sample-id 05_Brain --model deepseek-flash --out outputs/brain05_auto
```

DeepSeek calls the catalog, `prepare_10x_companions`, and the spatial validator. Preparation resolves the exact image entry from the cache's `download_manifest.json`, then reads the 10x ZIP directory over HTTPS. The local image must match the official ZIP member's size and CRC32 before the tool reads any companion data. The three required files come from the same directory inside the ZIP. Already available companions are checked and reused. Only missing or invalid copies are retrieved by byte range into the output directory. The cache stays read-only.

This entry point supports registered 10x Xenium ZIP images. It uses the image's existing source record to locate the ZIP. Reports are under the output directory in `companions/provenance.json` and `validation/run.json`, with a per-sample overlay and coordinate index under `validation`.

The live 05_Brain run reused the 523 MB image and read 4,001,617 bytes by ZIP ranges. All 12 correspondence checks passed for 44,955 cells. See the [run summary](../examples/xenium_brain05/run_summary.json).

## Batch and resume

```powershell
python run.py validate-10x-batch --10x-cache E:/Xenium --sample-ids 14_Heart 15_Skin 23_Kidney --model deepseek-flash --out outputs/tenx_batch
```

The batch processes up to 20 explicit sample IDs in order. The output contains an atomic `batch.json` checkpoint and one directory per attempted sample. Missing images, ambiguous sources, mismatched images, access errors and spatial validation failures appear as separate outcomes. The batch continues to the next sample and returns exit code 2 when any sample fails.

Add `--resume` to the same command after an interruption. The checkpoint must match the cache path, ordered sample list and model. Passed samples are skipped. Pending, interrupted and failed samples are attempted again. Before requesting a companion payload, the preparation tool checks the existing copy against the ZIP size and CRC32. The official ZIP directory may be read again to obtain these checksums.

A verified file is installed atomically. An interruption during a transfer can leave a temporary file in the sample output. Resume reuses verified final files and starts unfinished companion transfers again.

Use `--local-tools` in place of `--model deepseek-flash` to run the same preparation and spatial checks directly without a model round trip. Keep the same execution mode when resuming.

The acceptance run covered heart, skin, kidney and breast. A missing-image fixture and an image-mismatch fixture ran before the successful heart sample. The breast companion cache survived a terminated process. Its 365,604 cells exceeded the current 200,000-cell validator limit. See the [per-sample report](../examples/tenx_batch_acceptance/run_summary.json).
