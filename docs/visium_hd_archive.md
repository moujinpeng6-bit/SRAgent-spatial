# Visium HD acquisition and resolution verification

The batch entry supports sample-owned GEO supplements grouped by bin name, untagged HDF5 and spatial attachments with a declared bin size, and sample-owned tar.gz archives. Archive preparation associates each matrix and spatial file with its bin directory or explicit filename resolution. An untagged image can accompany the sole bin in the same archive directory. Duplicate roles and multiple sample roots require review.

A targeted discover query first searches the requested study with organism, tissue and platform terms. If those series-level terms miss the explicitly supplied GSE, it reads that GSE by accession and retains the same sample-level screening and citation checks. The report records the fallback and original search hit count. An empty eligible batch has the status no_candidates.

Use the existing batch entry after discovery or attach it with --archive-root. The archive resolver uses sample-owned URLs, selects a complete exact local filename, or downloads within --download-mib. It prepares only the matrix or barcode material, positions, scale factors and low-resolution image. Downloads are disabled by default.

    python -m pip install -e ".[agent,visium-hd]"
    python run.py discover --study GSE301973 --platforms "Visium HD" --max-studies 1 --max-samples-per-study 2 --max-total-samples 2 --refresh --model deepseek-flash --archive-root <source-root> --download-mib 2048 --out outputs/hd_archive_live
    python run.py validate-candidates --run <discovery-run.json> --archive-root <source-root> --out outputs/hd_archive_replay

The file checks cover expression barcode identity and bin resolution, matrix dimensions, Parquet coordinates, image decode, scale factors and image bounds. HDF5 checks cover barcode count and shape. Complete matrix values and independent anatomical registration remain outside the correspondence validator.

The 16 µm example uses real source counts from the two GSE286227 samples. The derivation script groups 2 × 2 adjacent 8 µm bins, sums counts, computes centers from constituent full-resolution positions, preserves the image coordinate frame and checks per-gene count conservation. The result is explicitly registered as a local derivative and checked through the same batch entry. Its report cannot be interpreted as native Space Ranger 16 µm validation.

To reproduce, provide the complete 8 µm Matrix Market files, barcodes, features, Parquet positions, scale JSON and low-resolution PNG under one source directory. A matrix prefix is insufficient for aggregation.

    $env:PYTHONPATH='src'
    python scripts/derive_hd16.py --run <saved-GSE286227-discovery.json> --source <source-root> --out outputs/derived16
    python run.py validate-candidates --run outputs/derived16/candidates.json --archive-root outputs/derived16 --out outputs/verified16

Sources are [GSE298732](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE298732), [GSE301973](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE301973), and [GSE286227](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE286227). The [10x fixed-frozen human lung dataset](https://www.10xgenomics.com/datasets/visium-hd-cytassist-gene-expression-human-lung-cancer-fixed-frozen) lists an 8.86 GB all-bin archive. A bounded probe read its beginning. The full native 16 µm files were not verified in this run.

The final combined batch passed all five inputs. GEO discovery selected the native samples before the archive or direct-file replay.

| Sample | Bin | In-tissue expression barcodes | Outside image |
|---|---|---:|---:|
| GSM9021740 | 2 um native | 4,218,944 | 0 |
| GSM9093278 | 2 um native | 2,319,753 | 0 |
| GSM9093279 | 2 um native | 10,547,274 | 0 |
| GSM8721365 | 16 um derived | 81,509 | 0 |
| GSM8721366 | 16 um derived | 33,891 | 0 |

107 offline tests passed.
