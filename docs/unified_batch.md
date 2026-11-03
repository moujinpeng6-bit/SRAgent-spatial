# GEO and 10x cache batch

`validate-spatial-batch` accepts one or more saved GEO discovery reports and a list of registered 10x cache sample IDs. It runs the existing GEO platform validators and the 10x Xenium cache workflow directly. Use a fresh output directory for each combined run.

```powershell
python run.py validate-spatial-batch --geo-run visium_run.json --geo-run visium_hd_run.json --archive-root geo_assets --10x-cache E:/Xenium --sample-ids 21_Pancreas 22_Kidney --download-mib 512 --probe-remote --out outputs/unified_acceptance
```

The output `batch.json` includes rows and counts by platform and source. A GEO result counts as found when its accession appears in an input discovery report. A 10x sample counts as found when its registered source can be resolved. Repeated GEO accessions are deduplicated. Unselected GEO rows are retained and excluded from the failure count. Files ready means the validator has the inputs required for its configured correspondence checks. A Visium Matrix Market header can be checked with a bounded prefix, so this count does not certify full expression-matrix downloads. Exit code 2 indicates an empty or mixed-outcome batch. The source-specific reports remain in the output subdirectories.

For untagged Visium HD files, the publisher's scale-factor JSON can declare 24 µm bins. The validator still checks the declared size against barcode identifiers. The [live acceptance summary](../examples/unified_spatial_batch/run_summary.json) covers new kidney Visium and lung Visium HD accessions and two new 10x Xenium samples.
