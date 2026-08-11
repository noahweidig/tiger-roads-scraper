# roads

Download & convert historical TIGER/Line road shapefiles (1992–2008) from US Census Bureau into per-state zipped shapefiles, reprojected to EPSG:4326 (WGS 84).

## Overview

**What is TIGER/Line?** National digital map data of US roads, water, railroads, administrative boundaries from Census Bureau. Multi-year snapshots capture road network evolution.

4-step pipeline:
1. **`01_generate_urls.py`** — Scrape Census FTP index → `urls.txt`
2. **`02_check_urls.py`** — Audit inventory → `tiger_inventory.json` (counts per state/year)
3. **`03_download_zips.py`** — Concurrent download w/ size verification, resume, exponential backoff, exhaustive logging
4. **`04_convert_zips.py`** — Extract, ogr2ogr to ESRI Shapefile, merge by state → zipped output

**Years:** TIGER1992, TIGER1999, TIGER2002, TIGER2003, rd_2ktiger (2000), tiger2005se, tiger2006se, TIGER2007FE, TIGER2008 (9 snapshots).

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
- `TIGER_DOWNLOAD_DIR` — Raw zip storage (default: `D:/roads`, written as `D:/roads/tiger<year>/...`)
- `TIGER_SHAPEFILE_DIR` — Final output (default: `D:/roads_shapefiles`)
- `TIGER_TEMP_DIR` — Working dir (default: `D:/roads_temp`)

Step 3 (download) and step 4 (convert) share the same `D:/roads` default, so the pipeline runs end-to-end with no env vars set.

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

1. **Group** every downloaded zip by (year, state FIPS)
2. **Unzip** each county archive
3. **Filter to roads**, using the layer's real schema rather than the year:
   - `ROADFLG='Y'` on 2007FE/2008 edges (equivalently `MTFCC` `S*`)
   - `CFCC` `A*` on the RT-era `CompleteChain` layer
   - no filter if neither field exists (degrade to keeping everything, never to
     emitting nothing)
4. **ogr2ogr append** straight into the state shapefile, reprojecting to
   EPSG:4326 — no intermediate per-county shapefile is written
5. **Dedup** on `TLID` to drop the few chains repeated across county lines
6. **Re-zip** per-state output
7. (Optional) delete raw zips per `KEEP_ZIPS` setting

Filtering is not cosmetic. `CompleteChain` and the modern `edges` layer both
carry every edge type, not just roads — measured on Rhode Island county 44001,
an unfiltered extract is **15% non-road in 2002** (3430 roads of 4018 chains)
and **27% non-road in 2008** (3908 roads of 5341 edges): rail, hydrography, and
invisible boundary lines.

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
- Per-state conversion tallies (counties merged, features, dups removed,
  counties failed) written to `conversion_report.json`
- Conversion resumes at two levels: a finished state is skipped outright, and a
  state interrupted mid-merge records which counties it already absorbed in
  `<temp>/states/<year>_<fips>/absorbed.json`, so a rerun neither redoes them
  nor double-counts them

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

- **TIGER1992, 1999, 2002, 2003, rd_2ktiger (2000), tiger2005se, tiger2006se:** RT line-file format (older record structure; GDAL TIGER driver reads the extracted record-type dir, `CompleteChain` layer = all chains, `CFCC` `A*` = roads)
- **TIGER2007FE, 2008:** modern shapefile zips (`*_edges.zip`)
- Auto-detected; conversion handles both transparently

**Every year nests and names its files differently.** These are the layouts on
`www2.census.gov`, all confirmed against live directory listings, and all
resolved by the converter's FIPS extractor (filename first, then any ancestor
directory):

| Year | State directory | County filename |
|------|-----------------|-----------------|
| TIGER1992 | `44/` (bare FIPS) | `44001.zip` (no prefix) |
| TIGER1999 | `RI/` (abbrev only) | `tgr44001.zip` |
| rd_2ktiger | `RI/` (abbrev only) | `tgr44001.zip` |
| TIGER2002 | `44_ri/` | `tgr44001.zip` |
| TIGER2003 | `44_RI/` | `tgr44001.zip` |
| tiger2005se | `44_RI/` | `tgr44001.zip` |
| tiger2006se | `RI/` (abbrev only) | `TGR44001.ZIP` (uppercase) |
| TIGER2007FE | `44_RHODE_ISLAND/44001_Bristol/` | `fe_2007_44001_edges.zip` |
| TIGER2008 | `44_RHODE_ISLAND/44001_Bristol_County/` | `tl_2008_44001_edges.zip` |

The abbreviation-only years (1999, rd_2ktiger, 2006se) carry no state number
anywhere in the path, so the converter maps USPS abbreviations to FIPS. TIGER1992
additionally stores records as `.F51`/`.F52` rather than `.RT1`/`.RT2`; the GDAL
TIGER driver reads both, including the RT2/F52 shape points that give chains
their intermediate vertices.
- Converter never hard-fails: each zip/county/state is isolated, ogr2ogr is retried with backoff (layer→whole-source, reproject→raw fallbacks), bad features are skipped (`-skipfailures`), and all issues are logged to `conversion_failures.txt` while valid output is still produced. Reruns are idempotent.
- Some years have incomplete state coverage; see `tiger_inventory.json`

## Caveats

- Geometry is Census-authored and not rebuilt. It does not need to be: TIGER
  county files are already node-clean. Verified on RI samples from all nine
  years — chains meet at exactly coincident endpoints, and every output has
  zero zero-length geometries, zero duplicate geometries, and zero duplicate
  TLIDs.
- ~17–25% of nodes are single-degree (dangles). That is inherent to the source
  — cul-de-sacs, stub ends, and roads cut at the state line — not a conversion
  artifact.
- County files *partition* chains rather than duplicating them along shared
  borders, so a state merge is nearly clean on its own; the handful of
  cross-county repeats that do occur are removed by the `TLID` dedup pass
  (1 of 70,058 for RI/2002, 3 of 70,967 for RI/2008).
- ESRI Shapefile caps each component file at 2 GB. Merged TIGER roads run
  ~110 B/feature in `.shp` and ~195 B/feature in `.dbf`, so the `.dbf` binds
  first at roughly 11M features per state. The converter warns above 8M.
- Reprojection NAD83 → WGS84 is a null transform in GDAL: it rewrites the
  `.prj` without moving a vertex, so node coincidence survives it exactly.
- Network failures can orphan partial downloads; `make retry` or delete `.part` files manually
