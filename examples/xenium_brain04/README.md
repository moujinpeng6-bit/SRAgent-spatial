# 04_Brain real Xenium run

The local 04_Brain inventory identifies the healthy adult brain cortex output from [10x Xenium Human Brain Preview Data](https://www.10xgenomics.com/datasets/xenium-human-brain-preview-data-1-standard). Five small entries were fetched using HTTP byte ranges from the exact ZIP listed for the existing morphology image. Their sizes and CRC32 values matched the ZIP central directory. The existing 403,417,938-byte image also matched its entry. The file list, official ZIP URL and hashes are in [the run summary](run_summary.json).

For reproduction, stage the five named data entries in a local data directory and register it and the existing image in a manifest using [the manifest template](../../configs/local_xenium.example.json). Use sample ID `04_Brain`, platform `Xenium` and coordinate unit `um`. The provenance step checks the remote ZIP metadata separately, so leave the local TAR archive fields unset.

```powershell
python run.py validate-local-agent --manifest <local-manifest> --model deepseek-flash --out <output-directory>
```

DeepSeek called the local catalog and spatial validator. All 16 correspondence checks passed for 24,406 cells and 319 gene expression features. All centroids fell inside the image at the 0.2125 µm pixel scale recorded by the experiment. The OME image lacked explicit unit labels and the validator applied the micrometre default. The run checks file correspondence, not anatomical registration or segmentation accuracy.
