# GEO and 10x cache batch

`validate-spatial-batch` accepts one or more saved GEO discovery reports and a list of registered 10x cache sample IDs. It runs the existing GEO platform validators and the 10x Xenium cache workflow directly. Use a fresh output directory for each combined run.

```powershell
python run.py validate-spatial-batch --geo-run visium_run.json --geo-run visium_hd_run.json --archive-root geo_assets --10x-cache E:/Xenium --sample-ids 21_Pancreas 22_Kidney --download-mib 512 --probe-remote --out outputs/unified_acceptance
```

The output `batch.json` includes rows and counts by platform and source. A GEO result counts as found when its accession appears in an input discovery report. A 10x sample counts as found when its registered source can be resolved. Repeated GEO accessions are deduplicated. Unselected GEO rows are retained and excluded from the failure count. Files ready means the validator has the inputs required for its configured correspondence checks. A Visium Matrix Market header can be checked with a bounded prefix, so this count does not certify full expression-matrix downloads. Exit code 2 indicates an empty or mixed-outcome batch. The source-specific reports remain in the output subdirectories.

For untagged Visium HD files, the publisher's scale-factor JSON can declare 24 µm bins. The validator still checks the declared size against barcode identifiers. The [live acceptance summary](../examples/unified_spatial_batch/run_summary.json) covers new kidney Visium and lung Visium HD accessions and two new 10x Xenium samples.

## Search by organism and tissue

```powershell
python run.py find-spatial --organism "Homo sapiens" --tissue kidney --platforms Xenium Visium "Visium HD" --10x-cache E:/Xenium --out outputs/kidney
```

The command queries GEO separately for each requested platform and screens each sample with the project's metadata rules. Supply `--model deepseek-flash` to require model agreement as well. Search limits default to two studies and two samples per study for each platform. The 10x cache lookup matches human tissue metadata to registered Xenium image rows. The cache path is a source configuration and the command derives the sample IDs automatically.

The output includes the frozen GEO reports, the cache selection and the combined per-sample `batch.json`. Add `--resume` to the same command after an interruption. Completed GEO sample records are reused from their checkpoints. The 10x batch skips successful samples and verifies cached companions before requesting their contents. An unfinished transfer can still require a new download.
## 10x public website discovery

Install the browser and HD validation extras and make Google Chrome available.

```powershell
python -m pip install -e ".[tenx-online,visium-hd]"
python run.py find-spatial --organism "Homo sapiens" --tissue kidney --platforms "Visium HD" --source 10x --download-mib 2048 --out outputs/tenx_kidney
```

Use `--source both` to include GEO. The catalog search matches the public cards by species, tissue and platform, then reads each candidate's output links. The website can require a browser access check. If the browser cannot load the catalog, the report records `access_failed` so it can be distinguished from a successful search with zero matches.

The public 10x file adapter currently supports a single unambiguous Visium HD binned archive with standard Space Ranger 2 µm paths. The archive is read in bounded HTTP ranges and the validation retains the filtered barcodes, Matrix Market header prefix, positions, scale factors and low-resolution image. The compressed archive may contain large members before these files. `--download-mib` caps the compressed bytes read per candidate. Catalog cards for Xenium and Visium can be discovered, with their online asset validation reported as unsupported.
