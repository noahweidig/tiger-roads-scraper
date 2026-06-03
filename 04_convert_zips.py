"""Bulletproof TIGER → ESRI Shapefile converter (one shapefile per state/year).

Handles both formats transparently:
  • RT line files (1992/1999/2000/2002/2003/2005se/2006se) — GDAL TIGER driver
    reads the extracted record-type dir; we pull the CompleteChain (roads) layer.
  • Modern shapefile zips (2007FE/2008 *_edges.zip) — reproject the inner .shp.

Robustness contract: NOTHING aborts the whole run. Every zip, county, and state
is isolated in try/except; failures are logged and skipped; partial output is
still produced; reruns are idempotent (existing outputs are skipped). ogr2ogr is
retried w/ backoff and falls back (layer→whole-src, reproject→raw) before giving
up. -skipfailures keeps bad features from killing a file.
"""
import os
import re
import time
import shutil
import zipfile
import subprocess
from pathlib import Path
from collections import defaultdict

# ── Config ── (defaults match 03's hardcoded D:/roads pattern; env-overridable)
DL = Path("D:/roads")
SHP = Path(os.environ.get("TIGER_SHAPEFILE_DIR", "D:/roads_shapefiles"))
TMP = Path(os.environ.get("TIGER_TEMP_DIR", "D:/roads_temp"))
KEEP_ZIPS = True
EPSG = "EPSG:4326"
OGR_TRIES = 3
FAIL_LOG = Path("conversion_failures.txt")

for d in (SHP, TMP):
    d.mkdir(parents=True, exist_ok=True)

# Path/name fragments that mark RT (older) format vs modern shapefile zips.
RT_TAGS = ['tiger1992', 'tiger1999', 'tiger2002', 'tiger2003',
           'tiger2005se', 'tiger2006se', 'rd_2ktiger']
# All year tags we bucket output by (RT + modern).
YEAR_TAGS = RT_TAGS + ['tiger2007fe', 'tiger2008']

# Census state/territory FIPS (incl. terr. 60/66/69/72/78).
FIPS = {f"{n:02d}" for n in (
    1,2,4,5,6,8,9,10,11,12,13,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,
    31,32,33,34,35,36,37,38,39,40,41,42,44,45,46,47,48,49,50,51,53,54,55,56,
    60,66,69,72,78)}

# FIPS extractors for known layouts: tl_/fe_YYYY_SS[CCC]_*, tgrSSCCC, bare SSCCC.
_PAT = [re.compile(r'^(?:tl|fe)_\d{4}_(\d{2})(?:\d{3})?(?:_|\b)', re.I),
        re.compile(r'^tgr(\d{2})\d{3}', re.I),
        re.compile(r'^(\d{2})\d{3}(?:_|\.|\b)')]


def state_fips(name):
    for p in _PAT:
        m = p.search(name)
        if m and m.group(1) in FIPS:
            return m.group(1)
    return None


def year_tag(s):
    s = s.lower()
    for t in YEAR_TAGS:
        if t in s:
            return t
    return 'unknown'


def is_rt(s):
    s = s.lower()
    return any(t in s for t in RT_TAGS)


def rm(p):
    shutil.rmtree(p, ignore_errors=True)


def rm_shp(shp):
    """Drop a shapefile sidecar set so a clean retry can recreate it."""
    for ext in ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix'):
        Path(str(shp)[:-4] + ext).unlink(missing_ok=True)


def log_fail(item, why):
    with FAIL_LOG.open('a') as f:
        f.write(f"{item}\t{why}\n")


def safe_extract(zip_path, dest):
    """Extract, refusing path-traversal members. False on any problem."""
    if not zipfile.is_zipfile(zip_path):
        return False
    try:
        root = Path(dest).resolve()
        with zipfile.ZipFile(zip_path) as z:
            for m in z.namelist():
                if not (root / m).resolve().is_relative_to(root):
                    return False
            z.extractall(dest)
        return True
    except Exception as e:
        log_fail(zip_path, f"unzip: {e}")
        return False


def ogr(args):
    """Run ogr2ogr w/ retries+backoff. Returns (ok, stderr)."""
    err = ""
    for a in range(OGR_TRIES):
        try:
            r = subprocess.run(["ogr2ogr", *args], capture_output=True, text=True)
            if r.returncode == 0:
                return True, ""
            err = r.stderr.strip()
        except Exception as e:
            err = str(e)
        time.sleep(2 ** a)
    return False, err


def to_shp(out, src, layer=None):
    """Make `out` from `src` (+optional layer). Try reproject→raw fallback."""
    tail = [str(out), str(src)] + ([layer] if layer else [])
    base = ["-f", "ESRI Shapefile", "-skipfailures"]
    ok, _ = ogr(["-t_srs", EPSG, *base, *tail])
    if ok:
        return True
    rm_shp(out)                                   # ditch partial before retry
    ok, err = ogr([*base, *tail])                 # last resort: skip reprojection
    if not ok:
        log_fail(src, f"ogr2ogr: {err}")
    return ok


def list_layers(src):
    try:
        r = subprocess.run(["ogrinfo", "-ro", "-q", str(src)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return []
        return [m.group(1) for m in
                (re.match(r'^\s*\d+:\s+(\S+)', ln) for ln in r.stdout.splitlines()) if m]
    except Exception:
        return []


def convert_rt(zip_path, out):
    ed = TMP / zip_path.stem
    rm(ed); ed.mkdir(parents=True, exist_ok=True)
    if not safe_extract(zip_path, ed):
        rm(ed); return False
    lyrs = list_layers(ed)
    layer = (next((l for l in lyrs if l.lower() == 'completechain'), None)
             or next((l for l in lyrs if 'chain' in l.lower()), None)
             or (lyrs[0] if lyrs else 'CompleteChain'))
    ok = to_shp(out, ed, layer)
    if not ok:                                    # fallback: let GDAL pick layer
        rm_shp(out); ok = to_shp(out, ed)
    rm(ed)
    return ok


def convert_shp(zip_path, out):
    ed = TMP / zip_path.stem
    rm(ed); ed.mkdir(parents=True, exist_ok=True)
    if not safe_extract(zip_path, ed):
        rm(ed); return False
    shps = list(ed.rglob("*.shp"))                # rglob: tolerate nesting
    ok = to_shp(out, shps[0]) if shps else False
    if not shps:
        log_fail(zip_path, "no .shp in archive")
    rm(ed)
    return ok


def merge_state(tag, fips, shps, out_zip):
    """Merge all county shps for a state into one shapefile, then zip it.
    Bad/missing counties are skipped; the state output is still produced."""
    if out_zip.exists():
        print(f"    SKIP {out_zip.name} (exists)")
        return True
    md = TMP / f"{tag}_{fips}_m"
    rm(md); md.mkdir(parents=True, exist_ok=True)
    msh = md / f"{tag}_{fips}.shp"
    created, nfail = False, 0
    for shp in shps:
        if not Path(shp).exists():
            nfail += 1; continue
        if not created:
            ok, err = ogr(["-f", "ESRI Shapefile", "-skipfailures", str(msh), str(shp)])
            created = ok
        else:
            ok, err = ogr(["-f", "ESRI Shapefile", "-update", "-append",
                          "-skipfailures", str(msh), str(shp), "-nln", msh.stem])
        if not ok:
            nfail += 1; log_fail(shp, f"merge {tag}/{fips}: {err}")
    if not created:
        rm(md); log_fail(f"{tag}/{fips}", "no county merged"); return False
    try:
        with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in md.iterdir():
                zf.write(f, f.name)
    except Exception as e:
        rm(md); log_fail(out_zip, f"zip: {e}"); return False
    rm(md)
    print(f"    ZIPPED {out_zip.name}" + (f" ({nfail} counties skipped)" if nfail else ""))
    return True


def check_tools():
    for t in ("ogr2ogr", "ogrinfo"):
        if shutil.which(t) is None:
            raise SystemExit(f"ERROR: {t} (GDAL) required but not found on PATH.")


def main():
    check_tools()
    FAIL_LOG.unlink(missing_ok=True)
    zips = list(DL.rglob("*.zip"))
    print(f"\nConverting {len(zips)} zips from {DL.resolve()} ...")

    done = defaultdict(lambda: defaultdict(list))
    cdir = TMP / "county_shps"
    cdir.mkdir(parents=True, exist_ok=True)

    for i, zp in enumerate(zips, 1):
        try:
            print(f"  [{i}/{len(zips)}] {zp.name}", flush=True)
            fips = state_fips(zp.stem)
            tag = year_tag(str(zp))
            if not fips:
                print(f"    SKIP — no state FIPS in {zp.name}")
                log_fail(zp, "no FIPS"); continue
            out = cdir / tag / fips / f"{zp.stem}.shp"
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.exists():
                print("    SKIP (already converted)")
                done[tag][fips].append(out); continue
            ok = convert_rt(zp, out) if is_rt(str(zp)) else convert_shp(zp, out)
            if ok:
                done[tag][fips].append(out)
                if not KEEP_ZIPS:
                    zp.unlink(missing_ok=True)
        except Exception as e:                    # absolute backstop per file
            log_fail(zp, f"unexpected: {e}")
            print(f"    ERROR (logged): {e}")

    print("\nMerging + zipping by state ...")
    for tag, states in done.items():
        yo = SHP / tag
        yo.mkdir(parents=True, exist_ok=True)
        for fips, shps in states.items():
            if not shps:
                continue
            print(f"  {tag} / {fips} — {len(shps)} counties", flush=True)
            try:
                merge_state(tag, fips, shps, yo / f"{tag}_{fips}.zip")
            except Exception as e:                # backstop per state
                log_fail(f"{tag}/{fips}", f"merge unexpected: {e}")
                print(f"    ERROR (logged): {e}")

    if FAIL_LOG.exists():
        n = sum(1 for _ in FAIL_LOG.open())
        print(f"\n{n} issues logged → {FAIL_LOG} (run is still complete; rerun to retry)")
    print(f"\nDone. Per-state zipped shapefiles in: {SHP.resolve()}")


if __name__ == "__main__":
    main()
