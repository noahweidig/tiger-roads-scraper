# check_urls.py
import requests
from bs4 import BeautifulSoup
import time
import random
from collections import defaultdict

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
base = "https://www2.census.gov/geo/tiger/"

def get_links(url, retries=5):
    for attempt in range(retries):
        try:
            time.sleep(random.uniform(0.5, 1.5))
            r = requests.get(url, headers=headers, timeout=60)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'html.parser')
            return [a.get('href') for a in soup.find_all('a') if a.get('href')]
        except Exception as e:
            wait = 2 ** (attempt + 1)
            print(f"  ATTEMPT {attempt+1} FAILED: {url} — {e} (retrying in {wait}s)")
            time.sleep(wait)
    print(f"  GIVING UP: {url}")
    return None

def clean_folders(links):
    if links is None:
        return []
    return [l for l in links
            if l.endswith('/')
            and not l.startswith('/')
            and not l.startswith('http')
            and '?' not in l
            and l.count('/') == 1]

def infer_format(filename):
    """Determine file format from filename"""
    f = filename.lower()
    if f.endswith('.zip'):
        if f.startswith('fe_'):
            return 'shapefile_fe'
        elif f.startswith('tl_'):
            return 'shapefile_tl'
        elif f.startswith('tgr'):
            return 'RT_linefile'
        elif f[0].isdigit():
            return 'RT_linefile'
    return 'unknown'

# ── Year configs ──
# (year_folder, depth, suffix_filter, format_note)
YEARS = {
    'TIGER1992':  ('TIGER1992/',  1, None,          'RT_linefile'),
    'TIGER1999':  ('TIGER1999/',  1, None,          'RT_linefile'),
    'TIGER2002':  ('TIGER2002/',  1, None,          'RT_linefile'),
    'TIGER2003':  ('TIGER2003/',  1, None,          'RT_linefile'),
    'rd_2ktiger': ('rd_2ktiger/', 1, None,          'RT_linefile'),
    'TIGER2007FE':('TIGER2007FE/',2, '_edges.zip',  'shapefile_fe'),
    'TIGER2008':  ('TIGER2008/',  2, '_edges.zip',  'shapefile_tl'),
}

report = defaultdict(lambda: {
    'states': {},
    'total_files': 0,
    'missing_states': [],
    'format': None,
    'errors': []
})

print("=" * 60)
print("TIGER LINE FILE INVENTORY CHECK")
print("=" * 60)

for year_key, (folder, depth, suffix, fmt) in YEARS.items():
    print(f"\n{'='*60}")
    print(f"YEAR: {year_key}  |  FORMAT: {fmt}  |  DEPTH: {depth}")
    print(f"URL:  {base + folder}")
    print(f"{'='*60}")

    report[year_key]['format'] = fmt
    year_total = 0

    state_folders = clean_folders(get_links(base + folder))
    report[year_key]['state_count'] = len(state_folders)
    print(f"  States/territories found: {len(state_folders)}")
    print(f"  {[s.strip('/') for s in state_folders]}")

    for sf in state_folders:
        state_key = sf.strip('/')

        if depth == 1:
            files = get_links(base + folder + sf)
            if files is None:
                report[year_key]['errors'].append(f"FAILED: {sf}")
                report[year_key]['states'][state_key] = {'file_count': 0, 'status': 'FAILED'}
                continue
            if suffix:
                zips = [f for f in files if f.endswith(suffix)]
            else:
                zips = [f for f in files if f.endswith('.zip')]
            
            formats = list(set(infer_format(f) for f in zips))
            count = len(zips)
            year_total += count
            report[year_key]['states'][state_key] = {
                'file_count': count,
                'formats': formats,
                'status': 'OK' if count > 0 else 'EMPTY'
            }
            print(f"  {state_key:30s} {count:4d} files  {formats}", flush=True)

        elif depth == 2:
            county_folders = clean_folders(get_links(base + folder + sf))
            if county_folders is None:
                report[year_key]['errors'].append(f"FAILED: {sf}")
                report[year_key]['states'][state_key] = {'file_count': 0, 'status': 'FAILED'}
                continue

            state_count = 0
            state_formats = set()
            county_report = {}

            for cf in county_folders:
                county_key = cf.strip('/')
                files = get_links(base + folder + sf + cf)
                if files is None:
                    report[year_key]['errors'].append(f"FAILED: {sf}{cf}")
                    county_report[county_key] = {'file_count': 0, 'status': 'FAILED'}
                    continue
                if suffix:
                    zips = [f for f in files if f.endswith(suffix)]
                else:
                    zips = [f for f in files if f.endswith('.zip')]

                formats = list(set(infer_format(f) for f in zips))
                state_formats.update(formats)
                count = len(zips)
                state_count += count
                county_report[county_key] = {
                    'file_count': count,
                    'formats': formats,
                    'status': 'OK' if count > 0 else 'EMPTY'
                }

            year_total += state_count
            report[year_key]['states'][state_key] = {
                'file_count': state_count,
                'county_count': len(county_folders),
                'formats': list(state_formats),
                'counties': county_report,
                'status': 'OK' if state_count > 0 else 'EMPTY'
            }
            print(f"  {state_key:40s} {len(county_folders):4d} counties  {state_count:5d} files  {list(state_formats)}", flush=True)

    report[year_key]['total_files'] = year_total
    print(f"\n  TOTAL FILES for {year_key}: {year_total}")

# ── Summary report ──
print("\n")
print("=" * 60)
print("SUMMARY")
print("=" * 60)
grand_total = 0
for year_key, data in report.items():
    errors = len(data['errors'])
    total = data['total_files']
    grand_total += total
    status = "✓" if errors == 0 else f"✗ {errors} errors"
    print(f"  {year_key:15s}  {data['format']:15s}  {data['state_count']:3d} states  {total:6d} files  {status}")

print(f"\n  GRAND TOTAL: {grand_total} files across all years")

# ── Write full report to file ──
import json
with open('tiger_inventory.json', 'w') as f:
    json.dump(report, f, indent=2)
print(f"\nFull inventory written to tiger_inventory.json")

# ── Write any errors ──
all_errors = []
for year_key, data in report.items():
    for e in data['errors']:
        all_errors.append(f"{year_key}: {e}")

if all_errors:
    with open('tiger_inventory_errors.txt', 'w') as f:
        for e in all_errors:
            f.write(e + '\n')
    print(f"{len(all_errors)} errors written to tiger_inventory_errors.txt")
