import subprocess
import zipfile
import shutil
import re
from pathlib import Path
from collections import defaultdict

# ── Config ──
DOWNLOAD_DIR = Path("D:/roads/tiger_downloads")
SHAPEFILE_DIR = Path("D:/roads/tiger_shapefiles")
TEMP_DIR = Path("D:/roads/tiger_temp")
KEEP_ZIPS = True

for d in [SHAPEFILE_DIR, TEMP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

RT_FORMAT_PREFIXES = ['TIGER1992', 'TIGER1999', 'TIGER2002', 'TIGER2003', 'rd_2ktiger']

def is_rt_format(path_str):
    return any(p.lower() in path_str.lower() for p in RT_FORMAT_PREFIXES)

# US state/territory FIPS codes (2-digit). Source: Census Bureau.
VALID_STATE_FIPS = {
    '01','02','04','05','06','08','09','10','11','12','13','15','16','17','18',
    '19','20','21','22','23','24','25','26','27','28','29','30','31','32','33',
    '34','35','36','37','38','39','40','41','42','44','45','46','47','48','49',
    '50','51','53','54','55','56',
    # territories
    '60','66','69','72','78',
}

# Anchor to known TIGER filename layouts:
#   tl_YYYY_SSCCC_*  /  tl_YYYY_SS_*       (2007+ shapefile)
#   fe_YYYY_SS_*     /  fe_YYYY_SSCCC_*    (2007 FE shapefile)
#   tgrSSCCC.zip     /  tgrSSCCC*.zip      (RT linefile)
#   SSCCC_*.zip                            (RT linefile, bare digits)
_FIPS_PATTERNS = [
    re.compile(r'^(?:tl|fe)_\d{4}_(\d{2})(?:\d{3})?(?:_|\b)', re.IGNORECASE),
    re.compile(r'^tgr(\d{2})\d{3}', re.IGNORECASE),
    re.compile(r'^(\d{2})\d{3}(?:_|\.|\b)'),
]


def get_state_fips(filename):
    for pat in _FIPS_PATTERNS:
        m = pat.search(filename)
        if m and m.group(1) in VALID_STATE_FIPS:
            return m.group(1)
    return None

def get_year_tag(path_str):
    for prefix in RT_FORMAT_PREFIXES + ['TIGER2007FE', 'TIGER2008']:
        if prefix.lower() in path_str.lower():
            return prefix
    return 'unknown'

def extract_zip(zip_path, extract_dir):
    if not zipfile.is_zipfile(zip_path):
        print(f"  UNZIP FAILED: {zip_path} — not a valid zip file")
        return False
    try:
        extract_root = Path(extract_dir).resolve()
        with zipfile.ZipFile(zip_path, 'r') as z:
            for member in z.namelist():
                dest = (extract_root / member).resolve()
                if not dest.is_relative_to(extract_root):
                    raise ValueError(f"unsafe zip member path: {member}")
            z.extractall(extract_dir)
        return True
    except Exception as e:
        print(f"  UNZIP FAILED: {zip_path} — {e}")
        return False

def convert_rt_to_shp(zip_path, out_shp):
    extract_dir = TEMP_DIR / zip_path.stem
    extract_dir.mkdir(exist_ok=True)
    if not extract_zip(zip_path, extract_dir):
        shutil.rmtree(extract_dir, ignore_errors=True)
        return False
    cmd = [
        "ogr2ogr",
        "-f", "ESRI Shapefile",
        "-t_srs", "EPSG:4326",
        str(out_shp),
        str(extract_dir),
        "CompleteChain"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    shutil.rmtree(extract_dir, ignore_errors=True)
    if result.returncode != 0:
        print(f"  OGR2OGR FAILED: {zip_path.stem}\n  {result.stderr}")
        return False
    return True

def convert_shp_zip(zip_path, out_shp):
    extract_dir = TEMP_DIR / zip_path.stem
    extract_dir.mkdir(exist_ok=True)
    if not extract_zip(zip_path, extract_dir):
        shutil.rmtree(extract_dir, ignore_errors=True)
        return False
    shp_files = list(extract_dir.glob("*.shp"))
    if not shp_files:
        print(f"  NO SHP FOUND: {extract_dir}")
        shutil.rmtree(extract_dir, ignore_errors=True)
        return False
    cmd = [
        "ogr2ogr",
        "-f", "ESRI Shapefile",
        "-t_srs", "EPSG:4326",
        str(out_shp),
        str(shp_files[0])
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    shutil.rmtree(extract_dir, ignore_errors=True)
    if result.returncode != 0:
        print(f"  OGR2OGR FAILED: {zip_path.stem}\n  {result.stderr}")
        return False
    return True

def merge_and_zip_by_state(year_tag, state_fips, shp_list, out_zip):
    if out_zip.exists():
        print(f"    SKIP {out_zip.name} (already exists)")
        return True
    merge_dir = TEMP_DIR / f"{year_tag}_{state_fips}_merge"
    merge_dir.mkdir(exist_ok=True)
    merged_shp = merge_dir / f"{year_tag}_{state_fips}.shp"
    for i, shp in enumerate(shp_list):
        if i == 0:
            cmd = ["ogr2ogr", "-f", "ESRI Shapefile", str(merged_shp), str(shp)]
        else:
            cmd = ["ogr2ogr", "-f", "ESRI Shapefile", "-update", "-append",
                   str(merged_shp), str(shp), "-nln", merged_shp.stem]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            shutil.rmtree(merge_dir, ignore_errors=True)
            raise RuntimeError(
                f"MERGE FAILED for {year_tag}/{state_fips} on {shp}:\n{result.stderr}"
            )
    with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in merge_dir.iterdir():
            zf.write(f, f.name)
    shutil.rmtree(merge_dir, ignore_errors=True)
    print(f"    ZIPPED: {out_zip.name}")
    return True


def check_ogr2ogr():
    try:
        subprocess.run(
            ["ogr2ogr", "--version"],
            check=True, capture_output=True, text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        raise SystemExit(f"ERROR: ogr2ogr is required but not available — {e}")

def convert_all():
    check_ogr2ogr()
    failed = []
    zips = list(DOWNLOAD_DIR.rglob("*.zip"))
    print(f"\nConverting {len(zips)} zip files...")

    converted = defaultdict(lambda: defaultdict(list))
    county_shp_dir = TEMP_DIR / "county_shps"
    county_shp_dir.mkdir(exist_ok=True)

    for i, zip_path in enumerate(zips):
        print(f"  [{i+1}/{len(zips)}] {zip_path.name}", flush=True)
        state_fips = get_state_fips(zip_path.stem)
        year_tag = get_year_tag(str(zip_path))

        if not state_fips:
            print(f"    SKIP — could not parse state FIPS from {zip_path.name}")
            continue

        out_shp = county_shp_dir / year_tag / state_fips / f"{zip_path.stem}.shp"
        out_shp.parent.mkdir(parents=True, exist_ok=True)

        if out_shp.exists():
            print(f"    SKIP (already converted)")
            converted[year_tag][state_fips].append(out_shp)
            continue

        if is_rt_format(str(zip_path)):
            success = convert_rt_to_shp(zip_path, out_shp)
        else:
            success = convert_shp_zip(zip_path, out_shp)

        if success:
            converted[year_tag][state_fips].append(out_shp)
            if not KEEP_ZIPS:
                zip_path.unlink()
        else:
            failed.append(str(zip_path))

    print("\nMerging and zipping by state...")
    for year_tag, states in converted.items():
        year_out = SHAPEFILE_DIR / year_tag
        year_out.mkdir(exist_ok=True)
        for state_fips, shp_list in states.items():
            if not shp_list:
                continue
            out_zip = year_out / f"{year_tag}_{state_fips}.zip"
            print(f"  {year_tag} / state {state_fips} — {len(shp_list)} counties", flush=True)
            merge_and_zip_by_state(year_tag, state_fips, shp_list, out_zip)

    if failed:
        with open('conversion_failures.txt', 'w') as f:
            for p in failed:
                f.write(p + '\n')
        print(f"\n{len(failed)} conversions failed — see conversion_failures.txt")

    print(f"\nDone. Zipped shapefiles in: {SHAPEFILE_DIR}")

if __name__ == "__main__":
    convert_all()
