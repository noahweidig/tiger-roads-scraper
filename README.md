# roads

Download and convert historical TIGER/Line road shapefiles from the US Census Bureau (1992–2008) into per-state zipped shapefiles reprojected to EPSG:4326.

## Overview

The pipeline runs in four steps:

1. **`01_generate_urls.py`** — Scrapes the Census FTP index to build a list of all road zip file URLs and writes them to `urls.txt`.
2. **`02_check_urls.py`** — Audits the same index, reports file counts per state/year, and writes a full inventory to `tiger_inventory.json`.
3. **`03_download_zips.py`** — Downloads every URL in `urls.txt` concurrently with HEAD-based size verification, resume, retries with exponential backoff, and per-URL failure logging. Safe to rerun; already-complete files are skipped.
4. **`04_convert_zips.py`** — Extracts each zip, converts the contents to ESRI Shapefile format via `ogr2ogr`, merges county-level files by state, and produces one zipped shapefile per state per year.

Years covered: TIGER1992, TIGER1999, TIGER2002, TIGER2003, rd_2ktiger, TIGER2007FE, TIGER2008.

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.8+ | |
| GDAL / ogr2ogr | 3.x | `brew install gdal` / `apt install gdal-bin` |

The downloader is pure Python (`requests` + threads). No `aria2c` needed.

## Installation

```bash
git clone https://github.com/noahweidig/roads.git
cd roads
pip install -r requirements.txt
```

## Usage

Run all steps in order:

```bash
make all
```

Or run steps individually:

```bash
make urls      # Step 1: scrape URLs → urls.txt
make check     # Step 2: audit inventory → tiger_inventory.json
make download  # Step 3: download zips (concurrent, verified)
make retry     # Step 3b: retry only previously failed URLs
make convert   # Step 4: convert & merge → per-state shapefiles
make clean     # Remove generated text/log files
```

## Configuration

Edit the top of `03_download_zips.py` and `04_convert_zips.py` to set output directories:

```python
DOWNLOAD_DIR  = Path("tiger_downloads")    # raw zip files (override via env: TIGER_DOWNLOAD_DIR)
SHAPEFILE_DIR = Path("tiger_shapefiles")   # final output
TEMP_DIR      = Path("tiger_temp")         # working directory
```

The downloader honors `TIGER_DOWNLOAD_DIR` and accepts `--workers N` (default 16) and `--retry-fails`.

All output shapefiles are reprojected to **EPSG:4326** (WGS 84).

To keep original zip files after conversion, set `KEEP_ZIPS = True` in `04_convert_zips.py`.

## Output file structure

```
tiger_shapefiles/
├── TIGER1992/
│   ├── TIGER1992_01.zip   ← merged shapefile for state FIPS 01
│   ├── TIGER1992_02.zip
│   └── ...
├── TIGER1999/
│   └── ...
└── TIGER2008/
    └── ...
```

Each zip contains a single merged ESRI Shapefile (`.shp`, `.dbf`, `.shx`, `.prj`) covering all road features for that state and year.

## Troubleshooting

**`ogr2ogr: command not found`** — Install GDAL: `brew install gdal` or `sudo apt install gdal-bin`.

**Download failures / timeouts / partials** — All recorded under `logs/`:
- `logs/download_failures.txt` — URLs that exhausted retries (with reason). Retry with `make retry` or `python 03_download_zips.py --retry-fails`.
- `logs/download_missing.txt` — URLs that returned 404/403 (server-side missing).
- `logs/download_ok.txt` — completed URLs.
- `logs/download_summary.json` — final counts.
- `logs/download_progress.log` — live failure/miss stream.

Reruns skip files whose on-disk size matches the server's `Content-Length`. Partial `.part` files are resumed via HTTP `Range`.

**Conversion failures** — Files that fail `ogr2ogr` conversion are listed in `conversion_failures.txt` at the end of step 4.

**Slow scraping** — Steps 1 and 2 add random delays between requests to avoid rate-limiting. Large years (TIGER2007FE, TIGER2008) have two-level directory trees and take longer.
