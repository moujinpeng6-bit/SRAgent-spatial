# Reuse a 10x Xenium image and acquire its companions

Run the existing local agent with an exact sample ID from the Xenium image cache.

```powershell
python run.py validate-local-agent --10x-cache E:/Xenium --sample-id 05_Brain --model deepseek-flash --out outputs/brain05_auto
```

DeepSeek calls the catalog, `prepare_10x_companions`, and the spatial validator. Preparation resolves the exact image entry from the cache's `download_manifest.json`, then reads the 10x ZIP directory over HTTPS. The local image must match the official ZIP member's size and CRC32 before the tool reads any companion data. The three required files come from the same directory inside the ZIP. Already available companions are checked and reused. Only missing or invalid copies are retrieved by byte range into the output directory. The cache stays read-only.

This entry point supports registered 10x Xenium ZIP images. It uses the image's existing source record to locate the ZIP. Reports are under the output directory in `companions/provenance.json` and `validation/run.json`, with a per-sample overlay and coordinate index under `validation`.

The live 05_Brain run reused the 523 MB image and read 4,001,617 bytes by ZIP ranges. All 12 correspondence checks passed for 44,955 cells. See the [run summary](../examples/xenium_brain05/run_summary.json).
