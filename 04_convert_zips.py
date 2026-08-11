"""TIGER → ESRI Shapefile converter: one roads shapefile per state/year.

Handles both archive eras transparently:
  • RT line files (1992/1999/2000/2002/2003/2005se/2006se) — GDAL TIGER driver
    reads the extracted record-type directory; roads are the CompleteChain
    layer filtered to CFCC 'A*' (A = road; B rail, F boundary, H hydro, ...).
  • Modern shapefile zips (2007FE/2008 *_edges.zip) — the edges layer holds all
    edge types; roads are ROADFLG='Y' (equivalently MTFCC 'S*').

Filtering matters: measured on real Census data, an unfiltered CompleteChain is
~15% non-road (RI 44001/2002: 3430 road of 4018 chains) and an unfiltered 2008
edges layer is ~27% non-road (RI 44001/2008: 3908 road of 5341 edges).

Topology: TIGER county files are already node-clean — chains meet at exactly
coincident endpoints, there are no zero-length or duplicate geometries, and
TLIDs are unique. Reprojecting NAD83→WGS84 is a null transform in GDAL, so it
relabels the .prj without moving a vertex. Counties partition chains rather
than duplicating them along shared borders, so a state merge is nearly clean on
its own; the residual handful of cross-county repeats are removed by a TLID
dedup pass. That means state output needs no geometry rebuild — only the filter,
the merge, and the dedup.

Merging is done by appending each county straight into the state shapefile.
There is no intermediate per-county shapefile: it halves the I/O and removes a
full copy of the dataset from the temp dir.

Robustness contract: NOTHING aborts the whole run. Every zip, county, and state
is isolated in try/except; failures are logged and skipped; partial output is
still produced. ogr2ogr is retried w/ backoff. -skipfailures keeps bad features
from killing a file. Reruns resume: finished states are skipped, and a
part-built state remembers which counties it already absorbed.
"""
import os
import re
import time
import json
import shutil
import zipfile
import subprocess
from pathlib import Path
from collections import defaultdict

# ── Config ── (defaults match 03's hardcoded D:/roads pattern; env-overridable)
DL = Path(os.environ.get("TIGER_DOWNLOAD_DIR", "D:/roads"))
SHP = Path(os.environ.get("TIGER_SHAPEFILE_DIR", "D:/roads_shapefiles"))
TMP = Path(os.environ.get("TIGER_TEMP_DIR", "D:/roads_temp"))
KEEP_ZIPS = True
EPSG = "EPSG:4326"
OGR_TRIES = 3
FAIL_LOG = Path("conversion_failures.txt")
REPORT = Path("conversion_report.json")

# ESRI Shapefile hard-stops at 2 GB per component file. Measured ~110 B/feature
# in .shp and ~195 B/feature in .dbf for merged TIGER roads, so the .dbf is the
# binding constraint at roughly 11M features. Warn well before that.
WARN_FEATURES = 8_000_000

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

# USPS abbrev → FIPS. rd_2ktiger nests by abbreviation (rd_2ktiger/RI/) with no
# state number anywhere in the directory name, so the filename alone can't
# always resolve the state.
ABBR = {
    'AL':'01','AK':'02','AZ':'04','AR':'05','CA':'06','CO':'08','CT':'09',
    'DE':'10','DC':'11','FL':'12','GA':'13','HI':'15','ID':'16','IL':'17',
    'IN':'18','IA':'19','KS':'20','KY':'21','LA':'22','ME':'23','MD':'24',
    'MA':'25','MI':'26','MN':'27','MS':'28','MO':'29','MT':'30','NE':'31',
    'NV':'32','NH':'33','NJ':'34','NM':'35','NY':'36','NC':'37','ND':'38',
    'OH':'39','OK':'40','OR':'41','PA':'42','RI':'44','SC':'45','SD':'46',
    'TN':'47','TX':'48','UT':'49','VT':'50','VA':'51','WA':'53','WV':'54',
    'WI':'55','WY':'56','AS':'60','GU':'66','MP':'69','PR':'72','VI':'78',
}

# FIPS extractors for known layouts: tl_/fe_YYYY_SS[CCC]_*, tgrSSCCC, bare SSCCC.
_PAT = [re.compile(r'^(?:tl|fe)_\d{4}_(\d{2})(?:\d{3})?(?:_|\b)', re.I),
        re.compile(r'^tgr(\d{2})\d{3}', re.I),
        re.compile(r'^(\d{2})\d{3}(?:_|\.|\b)')]
# Directory-name fallbacks. Every archive year nests states differently:
#   TIGER1992        44/                  bare FIPS
#   TIGER2002        44_ri/               FIPS + lowercase abbrev
#   TIGER2008        44_RHODE_ISLAND/     FIPS + uppercase name
#   rd_2ktiger       RI/                  abbrev only, no FIPS anywhere
_DIR_NUM = re.compile(r'^(\d{2})(?:_|$)')
_DIR_ABBR = re.compile(r'^([A-Za-z]{2})$')


def state_fips(zp):
    """Resolve state FIPS from the filename, else from any ancestor directory.

    Directory fallback is required for rd_2ktiger, which nests by USPS
    abbreviation and names files without a state number.
    """
    name = Path(zp).stem
    for p in _PAT:
        m = p.search(name)
        if m and m.group(1) in FIPS:
            return m.group(1)
    for part in reversed(Path(zp).parts[:-1]):
        m = _DIR_NUM.match(part)
        if m and m.group(1) in FIPS:
            return m.group(1)
        m = _DIR_ABBR.match(part)
        if m and m.group(1).upper() in ABBR:
            return ABBR[m.group(1).upper()]
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
        log_fail(zip_path, "not a zip")
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


def run(cmd, timeout=1800):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def ogr(args):
    """Run ogr2ogr w/ retries+backoff. Returns (ok, stderr)."""
    err = ""
    for a in range(OGR_TRIES):
        rc, _, err = run(["ogr2ogr", *args])
        if rc == 0:
            return True, ""
        time.sleep(2 ** a)
    return False, err


def layers(src):
    """[(name, geom_type)] for a datasource, or [] if it can't be opened."""
    rc, out, _ = run(["ogrinfo", "-ro", "-q", str(src)], timeout=300)
    if rc != 0:
        return []
    res = []
    for ln in out.splitlines():
        m = re.match(r'^\s*\d+:\s+(\S+)(?:\s+\((.*)\))?', ln)
        if m:
            res.append((m.group(1), (m.group(2) or '').lower()))
    return res


def fields(src, layer):
    """Set of attribute field names (upper-cased) on a layer."""
    rc, out, _ = run(["ogrinfo", "-ro", "-so", str(src), layer], timeout=300)
    if rc != 0:
        return set()
    return {m.group(1).upper() for m in
            (re.match(r'^(\w+):\s+\w+\s+\(', ln) for ln in out.splitlines()) if m}


def road_filter(src, layer):
    """The -where clause selecting road features, chosen from the real schema.

    Probing the schema rather than keying off the year means a year we have not
    sampled still gets filtered correctly, and an unexpected schema degrades to
    'keep everything' instead of silently emitting an empty shapefile.
    """
    f = fields(src, layer)
    if 'ROADFLG' in f:                 # 2007FE / 2008 edges
        return "ROADFLG='Y'"
    if 'MTFCC' in f:                   # modern fallback: S* = road
        return "MTFCC LIKE 'S%'"
    if 'CFCC' in f:                    # RT CompleteChain: A* = road
        return "CFCC LIKE 'A%'"
    return None


def road_source(zip_path, ed):
    """(datasource, layer, where) for the roads in an extracted archive."""
    if is_rt(str(zip_path)):
        lyrs = layers(ed)
        names = [n for n, _ in lyrs]
        layer = (next((n for n in names if n.lower() == 'completechain'), None)
                 or next((n for n in names if 'chain' in n.lower()), None))
        if not layer:
            # Last resort: first line layer in the record-type directory.
            layer = next((n for n, g in lyrs if 'line' in g), None)
        if not layer:
            log_fail(zip_path, f"no CompleteChain layer (saw {names})")
            return None
        return str(ed), layer, road_filter(ed, layer)
    shps = list(Path(ed).rglob("*.shp"))            # rglob: tolerate nesting
    if not shps:
        log_fail(zip_path, "no .shp in archive")
        return None
    src = shps[0]
    return str(src), src.stem, road_filter(src, src.stem)


def append_county(zip_path, state_shp):
    """Extract one county zip and append its roads into the state shapefile.

    The output layer name is pinned to the state shapefile's stem. With an
    ESRI Shapefile datasource, GDAL treats the containing directory as the
    datasource on -update, so a -nln that does not match the filename silently
    writes a SECOND shapefile beside it and the county is lost from the merge.
    """
    ed = TMP / "extract" / zip_path.stem
    rm(ed)
    ed.mkdir(parents=True, exist_ok=True)
    try:
        if not safe_extract(zip_path, ed):
            return False, 0
        got = road_source(zip_path, ed)
        if not got:
            return False, 0
        src, layer, where = got
        args = ["-t_srs", EPSG, "-f", "ESRI Shapefile", "-skipfailures"]
        if where:
            args += ["-where", where]
        if state_shp.exists():
            args += ["-update", "-append", "-nln", state_shp.stem]
        args += [str(state_shp), src, layer]
        ok, err = ogr(args)
        if not ok:
            # Fall back to leaving the data in its native CRS rather than
            # dropping the county entirely; the .prj records what it really is.
            ok, err = ogr([a for a in args if a not in ("-t_srs", EPSG)])
        if not ok:
            log_fail(zip_path, f"ogr2ogr: {err}")
            return False, 0
        return True, 1
    except Exception as e:
        log_fail(zip_path, f"unexpected: {e}")
        return False, 0
    finally:
        rm(ed)


def feature_count(shp, layer):
    rc, out, _ = run(["ogrinfo", "-ro", "-so", str(shp), layer], timeout=300)
    if rc != 0:
        return -1
    m = re.search(r'Feature Count:\s+(\d+)', out)
    return int(m.group(1)) if m else -1


def dedup_tlid(shp, layer):
    """Drop cross-county repeats of the same TLID. Returns (kept, removed).

    Counties partition chains, so this removes only a handful per state, but
    those few would otherwise be exact duplicate geometries in the output.
    """
    if 'TLID' not in fields(shp, layer):
        return feature_count(shp, layer), 0
    before = feature_count(shp, layer)
    tmp = shp.parent / f"_dd_{shp.stem}.shp"
    rm_shp(tmp)
    ok, err = ogr(["-f", "ESRI Shapefile", "-skipfailures", str(tmp), str(shp),
                   "-dialect", "sqlite",
                   "-sql", f'SELECT * FROM "{layer}" GROUP BY TLID',
                   "-nln", tmp.stem])
    if not ok:
        log_fail(shp, f"dedup skipped: {err}")   # non-fatal: keep the merge
        return before, 0
    after = feature_count(tmp, tmp.stem)
    if after <= 0:
        rm_shp(tmp)
        log_fail(shp, "dedup produced empty output; keeping original")
        return before, 0
    rm_shp(shp)
    for ext in ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix'):
        s = Path(str(tmp)[:-4] + ext)
        if s.exists():
            s.rename(str(shp)[:-4] + ext)
    return after, max(0, before - after)


def build_state(tag, fips, zips, out_zip):
    """Merge every county zip of one state/year into a single zipped shapefile."""
    if out_zip.exists():
        print(f"    SKIP {out_zip.name} (exists)")
        return None
    wd = TMP / "states" / f"{tag}_{fips}"
    wd.mkdir(parents=True, exist_ok=True)
    shp = wd / f"{tag}_{fips}.shp"
    manifest = wd / "absorbed.json"
    # Resume: a state interrupted mid-merge remembers which counties it already
    # absorbed, so a rerun neither redoes them nor double-counts them.
    try:
        done = set(json.loads(manifest.read_text())) if manifest.exists() else set()
    except Exception:
        done = set()
    if not shp.exists():
        done = set()

    added, nfail = 0, 0
    for zp in zips:
        if zp.name in done:
            continue
        ok, n = append_county(zp, shp)
        if ok:
            added += n
            done.add(zp.name)
            manifest.write_text(json.dumps(sorted(done)))
        else:
            nfail += 1

    if not shp.exists():
        rm(wd)
        log_fail(f"{tag}/{fips}", "no county merged")
        return None

    kept, removed = dedup_tlid(shp, shp.stem)
    if kept > WARN_FEATURES:
        print(f"    WARNING {kept} features — approaching the 2GB .dbf "
              f"shapefile limit for {tag}_{fips}")

    try:
        with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(wd.iterdir()):
                if f.suffix.lower() in ('.shp', '.shx', '.dbf', '.prj', '.cpg'):
                    zf.write(f, f.name)
    except Exception as e:
        log_fail(out_zip, f"zip: {e}")
        return None
    rm(wd)
    print(f"    ZIPPED {out_zip.name} — {kept} roads from {len(done)} counties"
          + (f", {removed} dup TLID dropped" if removed else "")
          + (f", {nfail} counties failed" if nfail else ""))
    return {"tag": tag, "fips": fips, "counties": len(done),
            "features": kept, "dup_removed": removed, "counties_failed": nfail}


def check_tools():
    for t in ("ogr2ogr", "ogrinfo"):
        if shutil.which(t) is None:
            raise SystemExit(f"ERROR: {t} (GDAL) required but not found on PATH.")


def main():
    check_tools()
    FAIL_LOG.unlink(missing_ok=True)
    # Case-insensitive: tiger2006se ships uppercase TGR01001.ZIP.
    zips = [p for p in DL.rglob("*") if p.is_file() and p.suffix.lower() == ".zip"]
    print(f"\nFound {len(zips)} zips under {DL.resolve()}")

    groups = defaultdict(lambda: defaultdict(list))
    for zp in zips:
        fips = state_fips(zp)
        if not fips:
            print(f"  SKIP — no state FIPS in {zp}")
            log_fail(zp, "no FIPS")
            continue
        groups[year_tag(str(zp))][fips].append(zp)

    n_states = sum(len(s) for s in groups.values())
    print(f"Grouped into {n_states} state/year outputs\n")

    report = []
    for tag in sorted(groups):
        yo = SHP / tag
        yo.mkdir(parents=True, exist_ok=True)
        for fips in sorted(groups[tag]):
            zl = sorted(groups[tag][fips])
            print(f"  {tag} / {fips} — {len(zl)} counties", flush=True)
            try:
                r = build_state(tag, fips, zl, yo / f"{tag}_{fips}.zip")
                if r:
                    report.append(r)
                    if not KEEP_ZIPS:
                        for zp in zl:
                            zp.unlink(missing_ok=True)
            except Exception as e:                # backstop per state
                log_fail(f"{tag}/{fips}", f"unexpected: {e}")
                print(f"    ERROR (logged): {e}")

    rm(TMP / "extract")
    REPORT.write_text(json.dumps(report, indent=2))
    if FAIL_LOG.exists():
        n = sum(1 for _ in FAIL_LOG.open())
        print(f"\n{n} issues logged → {FAIL_LOG} (run is still complete; rerun to retry)")
    print(f"\nDone. {len(report)} state/year outputs, summary → {REPORT}")
    print(f"Per-state zipped shapefiles in: {SHP.resolve()}")


if __name__ == "__main__":
    main()
