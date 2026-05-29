# roads

Download and convert historical TIGER/Line road shapefiles from the US Census Bureau (1992–2008) into per-state zipped shapefiles reprojected to EPSG:4326.

## Overview

The pipeline runs in four steps:

1. **`01_generate_urls.py`** — Scrapes the Census FTP index to build a list of all road zip file URLs and writes them to `urls.txt`.
2. **`02_check_urls.py`** — Audits the same index, reports file counts per state/year, and writes a full inventory to `tiger_inventory.json`.
3. **`03_download_zips.py`** — Downloads every URL in `urls.txt` in parallel using `aria2c`.
4. **`04_convert_zips.py`** — Extracts each zip, converts the contents to ESRI Shapefile format via `ogr2ogr`, merges county-level files by state, and produces one zipped shapefile per state per year.

Years covered: TIGER1992, TIGER1999, TIGER2002, TIGER2003, rd_2ktiger, TIGER2007FE, TIGER2008.

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.8+ | |
| aria2c | any recent | download manager — `brew install aria2` / `apt install aria2` |
| GDAL / ogr2ogr | 3.x | `brew install gdal` / `apt install gdal-bin` |

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
make download  # Step 3: download zips via aria2c
make convert   # Step 4: convert & merge → per-state shapefiles
make clean     # Remove generated text/log files
```

## Configuration

Edit the top of `03_download_zips.py` and `04_convert_zips.py` to set output directories:

```python
DOWNLOAD_DIR  = Path("D:/roads/tiger_downloads")   # raw zip files
SHAPEFILE_DIR = Path("D:/roads/tiger_shapefiles")  # final output
TEMP_DIR      = Path("D:/roads/tiger_temp")        # working directory
```

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

**`aria2c: command not found`** — Install aria2: `brew install aria2` (macOS) or `sudo apt install aria2` (Debian/Ubuntu).

**`ogr2ogr: command not found`** — Install GDAL: `brew install gdal` or `sudo apt install gdal-bin`.

**Permanent download failures** — URLs that fail after all retries are written to `failed_urls.txt`. Re-run `make download` to resume; aria2c will skip already-downloaded files.

**Conversion failures** — Files that fail `ogr2ogr` conversion are listed in `conversion_failures.txt` at the end of step 4.

**Slow scraping** — Steps 1 and 2 add random delays between requests to avoid rate-limiting. Large years (TIGER2007FE, TIGER2008) have two-level directory trees and take longer.
