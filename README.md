# roads

Download & convert historical TIGER/Line road shapefiles (1992–2008) from US Census Bureau into per-state zipped shapefiles, reprojected to EPSG:4326 (WGS 84).

## Overview

**What is TIGER/Line?** National digital map data of US roads, water, railroads, administrative boundaries from Census Bureau. Multi-year snapshots capture road network evolution.

4-step pipeline:
1. **`01_generate_urls.py`** — Scrape Census FTP index → `urls.txt`
2. **`02_check_urls.py`** — Audit inventory → `tiger_inventory.json` (counts per state/year)
3. **`03_download_zips.py`** — Concurrent download w/ size verification, resume, exponential backoff, exhaustive logging
4. **`04_convert_zips.py`** — Extract, ogr2ogr to ESRI Shapefile, merge by state → zipped output

**Years:** TIGER1992, TIGER1999, TIGER2002, TIGER2003, rd_2ktiger, TIGER2007FE, TIGER2008 (7 snapshots).

## Prerequisites

| Item | Version | Notes |
|------|---------|-------|
| Python | 3.8+ | |
| GDAL / `ogr2ogr` | 3.x | `brew install gdal` (macOS) / `apt install gdal-bin` (Linux) |
| Dependencies | — | `pip install -r requirements.txt` (requests, beautifulsoup4, urllib3) |

Downloader: pure Python async via `requests` + ThreadPoolExecutor (no `aria2c`).

## Installation

```bash
git clone https://github.com/noahweidig/roads.git
cd roads
pip install -r requirements.txt
```

## Usage

**Run entire pipeline:**
```bash
make all  # Steps 1→2→3→4 in sequence
```

**Run individually:**
```bash
make urls       # Step 1: scrape URLs
make check      # Step 2: audit inventory
make download   # Step 3: download (16 workers default)
make retry      # Step 3b: retry failed URLs only
make convert    # Step 4: extract, convert, merge
make clean      # Remove urls.txt, inventory, logs
make clean-logs # Remove logs/ only
```

**Direct invocation:**
```bash
python 03_download_zips.py --workers 32 --retry-fails
python 04_convert_zips.py
```

## Configuration

**Environment variables** (override defaults):
- `TIGER_DOWNLOAD_DIR` — Raw zip storage (default: `tiger_downloads/`)
- `TIGER_SHAPEFILE_DIR` — Final output (default: `tiger_shapefiles/`)
- `TIGER_TEMP_DIR` — Working dir (default: `tiger_temp/`)

**Script configs** (edit source):
```python
# 03_download_zips.py
--workers N           # Concurrent downloads (default 16, tuned for Census FTP)
--retry-fails         # Retry only previously failed URLs (resume from failure state)

# 04_convert_zips.py
KEEP_ZIPS = True      # Preserve raw zips post-conversion (default: delete)
```

**Output projection:** TIGER2007FE+ use NAD83; older formats vary. All reprojected to **EPSG:4326** (WGS 84 lat/lon).

## Download mechanics

| Feature | Details |
|---------|---------|
| **Verification** | HEAD request → Content-Length check; skip if local size matches |
| **Resume** | HTTP `Range` header resumes `.part` files; safe to re-run |
| **Retry** | Exponential backoff (2s, 4s, 8s, ...) up to ~2 min; skips 404/403 |
| **Concurrency** | ThreadPoolExecutor; default 16 workers, adjust per Census FTP rate limits |
| **Logging** | All outcomes recorded; see Troubleshooting |

## Conversion process

1. **Unzip** each downloaded archive
2. **ogr2ogr** → ESRI Shapefile format:
   - Detects format (TIGER92-era `RT` records vs. TIGER2007+ `RD` records) automatically
   - Filters to road features only
3. **Merge** county zips by state (e.g., all CA counties → single CA shapefile)
4. **Reproject** to EPSG:4326
5. **Re-zip** per-state output
6. (Optional) delete raw zips per `KEEP_ZIPS` setting

## Output structure

```
tiger_shapefiles/
├── TIGER1992/              (year directory)
│   ├── TIGER1992_01.zip    (state FIPS 01=AL)
│   ├── TIGER1992_02.zip    (state FIPS 02=AK)
│   └── TIGER1992_56.zip    (state FIPS 56=WY)
├── TIGER1999/
│   └── ...
└── TIGER2008/
    └── ...
```

Each zip contains merged ESRI Shapefile set (`.shp`, `.shx`, `.dbf`, `.prj`) for all road features in one state/year.

**State FIPS codes:** 01-56 (includes territories: 60=AS, 66=GU, 69=MP, 72=PR, 78=VI).

## Logging & recovery

**All outcomes in `logs/`:**
- `download_failures.txt` — URLs exhausted retries (reason logged)
- `download_missing.txt` — 404/403 (server-side missing)
- `download_ok.txt` — Successfully downloaded
- `download_summary.json` — Final tallies (bytes, counts)
- `download_progress.log` — Real-time stream

**Recovery:**
- `make retry` retries failed URLs (same session or fresh); skips already-complete files
- Partial `.part` files auto-resume next run
- Conversion failures logged to `conversion_failures.txt` at end of step 4

## Performance notes

- **Step 1–2:** Slow due to rate-limiting (random delays between Census FTP reqs); TIGER2007FE/2008 have 2-level directory trees
- **Step 3:** Typically 4–8 hrs for full dataset (~7000 files, 200+ GB); scales with worker count & network
- **Step 4:** 1–2 hrs (CPU-bound ogr2ogr); merges are sequential per state
- **Rerun cost:** Minimal (size verification skips complete files)

## Troubleshooting

**`ogr2ogr: command not found`**
```bash
# macOS
brew install gdal

# Ubuntu/Debian
sudo apt install gdal-bin
```

**Download stalls / hangs**
- Census FTP may throttle; reduce `--workers` or increase delays in code
- Check `logs/download_progress.log` for real-time status
- `make retry` to resume from failure point

**Conversion failures**
- See `conversion_failures.txt` for file list
- Common causes: malformed TIGER2007FE records, missing coordinate data
- Retry conversion (idempotent); failures are cataloged, non-fatal

**Disk space**
- Raw zips: ~200 GB (removable if `KEEP_ZIPS=False`)
- Shapefiles: ~150–180 GB
- Temp: ~5–10 GB peak (auto-cleaned)

**Memory**
- Downloader: <500 MB (streaming)
- Converter: ~2 GB peak per state (ogr2ogr merge operation)

## Data notes

- **TIGER1992, 1999, 2002, 2003, rd_2ktiger:** RT format (older record structure)
- **TIGER2007FE, 2008:** RD format (modern TIGER spec)
- Auto-detected; conversion handles both transparently
- Some years have incomplete state coverage; see `tiger_inventory.json`

## Caveats

- No validation of topology or geometry (raw Census data)
- County-level zips merged spatially without conflict resolution (overlaps possible)
- Projection metadata (`.prj`) reprojected but may differ from original; validate for critical work
- Network failures can orphan partial downloads; `make retry` or delete `.part` files manually
