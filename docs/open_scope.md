# Open-scope discovery and local asset reuse

Discovery accepts a scientific name and tissue keyword, or `*` to remove either filter. `--platforms all` searches the seven names in the GEO platform catalog. Sample-level screening still requires identifiable organism metadata and spatial assay evidence; a targeted tissue must match its sample-level tissue/source evidence. This is bounded GEO discovery, not exhaustive coverage. At most five studies and 50 samples are inspected per run, and results can include irrelevant parent studies or omit true matches. Xenium, Visium and Visium HD have file validators; the other catalog platforms can be searched but have no batch validator.

```powershell
python run.py discover --organism 'Homo sapiens' --tissue lung --platforms 'Visium HD' --max-studies 5 --max-samples-per-study 2 --refresh --model deepseek-flash --archive-root <local-files> --probe-remote --out outputs/open_lung
python run.py discover --organism '*' --tissue '*' --platforms all --model deepseek-flash --out outputs/open_scope
python scripts/inventory_xenium_cache.py --root E:/Xenium --out outputs/local_xenium/inventory.json
```

With the default zero download budget, candidate validation only reads local files. `--probe-remote` sends HEAD requests for up to four unresolved sample-owned GEO links, preferring expression and spatial roles. HTTP 200 means the metadata probe succeeded; it is not proof that the file was fetched or validated. Link failures and local file availability are recorded separately. Ambiguous candidate files stop that sample's pairing without stopping later samples.

The cache inventory joins existing download manifest entries to the local sample catalog, checks listed sizes where available and reads TIFF magic bytes. An image alone cannot certify barcode/coordinate correspondence. GEO and vendor samples are kept separate; files are not joined by organ or image basename.

The native 16 µm manifest identifies the publisher's filtered barcode file, positions, scale JSON and low-resolution image in one Dataverse sample directory. The companion checker reads a local staged copy, verifies all four against published sizes and MD5s, records SHA-256, and calls the same Visium barcode and image-bound checker used by the batch flow. It does not read expression counts or infer anatomical registration. This vendor-independent source check is separate from the GEO candidate batch.

```powershell
$env:PYTHONPATH = 'src'
python scripts/check_native_hd_bundle.py --root <staged-native-files> --manifest examples/open_scope/native16_manifest.json --out outputs/native16/report.json
```

In the untargeted GEO lung trial, 10 GSMs from 5 studies were screened, with 33 linked samples not read because of the configured limits. Of the 5 selected GSMs, the two exact cached archives passed and three had no local expression/spatial bundle. The 12 sample-owned links probed for those three returned HTTP 200. No asset access failure or pairing ambiguity occurred in this live batch; a mixed-outcome test verifies that missing files, access failure, ambiguity and a later success are all recorded separately.

The E-drive inventory covered 63 catalog entries across 13 organs. For example, the [10x Human Brain Preview](https://www.10xgenomics.com/datasets/xenium-human-brain-preview-data-1-standard) has three cataloged sections with nine local morphology TIFFs matching the listed file sizes and TIFF signatures. Those images do not supply the associated expression and cell-coordinate files. The separate untargeted GEO brain search returned four sample records and selected none; it should not be conflated with the vendor cache.

The [Dataverse native 16 µm lung sample](https://doi.org/10.34810/data3063) passed on the four checksum-verified attachments (4,551,526 bytes). All 158,361 filtered barcodes had matching coordinates in the 175,561-position table. All tested in-tissue coordinates fit the decoded 600 × 544 low-resolution image. No full expression matrix or all-resolution archive was downloaded for this check.

A separate local-only replay of the prior GEO Xenium discovery against the E-drive cache recorded six missing bundles and passed GSM7990538 (19/19 checks). All 110 offline tests passed.
