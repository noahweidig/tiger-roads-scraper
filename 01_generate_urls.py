# generate_urls.py
import time

from tiger_utils import clean_folders, get_links, matches_suffix

base = "https://www2.census.gov/geo/tiger/"

def retry_failed(failed_list, suffix_filter=None):
    urls = []
    if not failed_list:
        return urls
    print(f"\n  Retrying {len(failed_list)} failed folders...")
    time.sleep(30)  # longer pause before retry batch
    for item in failed_list:
        yf, path = item[0], item[1]
        url = base + yf + path
        print(f"  RETRY: {path}", flush=True)
        files = get_links(url, retries=5)
        if files is None:
            print(f"  PERMANENT FAILURE: {url}")
            with open('failed_urls.txt', 'a') as f:
                f.write(url + '\n')
            continue
        for f in files:
            if matches_suffix(f, suffix_filter):
                urls.append(url + f)
    return urls

def scrape_year(year_folder, suffix_filter=None):
    print(f"\nScraping {year_folder}...")
    failed = []
    urls = []
    state_folders = clean_folders(get_links(base + year_folder))
    for sf in state_folders:
        print(f"  {sf}", flush=True)
        files = get_links(base + year_folder + sf)
        if files is None:
            failed.append((year_folder, sf))
            continue
        for f in files:
            if matches_suffix(f, suffix_filter):
                urls.append(base + year_folder + sf + f)
    urls += retry_failed(failed, suffix_filter=suffix_filter)
    return urls

def scrape_year_two_level(year_folder, suffix_filter=None):
    print(f"\nScraping {year_folder}...")
    failed = []
    urls = []
    state_folders = clean_folders(get_links(base + year_folder))
    for sf in state_folders:
        print(f"  {sf}", flush=True)
        county_folders = clean_folders(get_links(base + year_folder + sf))
        for cf in county_folders:
            print(f"    {cf}", flush=True)
            files = get_links(base + year_folder + sf + cf)
            if files is None:
                failed.append((year_folder, sf + cf))
                continue
            for f in files:
                if matches_suffix(f, suffix_filter):
                    urls.append(base + year_folder + sf + cf + f)
    urls += retry_failed(failed, suffix_filter=suffix_filter)
    return urls

# clear failed log
open('failed_urls.txt', 'w').close()

urls = []

# One-level RT line-file years (year/state/tgrSSCCC.zip). 2000=rd_2ktiger,
# 2005=tiger2005se, 2006=tiger2006se. Each year scraped independently so a
# single failing year can't lose the rest.
ONE_LVL = ['TIGER1992/', 'TIGER1999/', 'TIGER2002/', 'TIGER2003/',
           'tiger2005se/', 'tiger2006se/', 'rd_2ktiger/']
# Two-level shapefile years (year/state/county/*_edges.zip).
TWO_LVL = ['TIGER2007FE/', 'TIGER2008/']

def safe(fn, yf, **kw):
    try:
        return fn(yf, **kw)
    except Exception as e:                       # never let one year abort all
        print(f"  YEAR FAILED: {yf} — {e}")
        with open('failed_urls.txt', 'a') as f:
            f.write(f"{base + yf}\tYEAR_ERROR: {e}\n")
        return []

for yf in ONE_LVL:
    urls += safe(scrape_year, yf)
for yf in TWO_LVL:
    urls += safe(scrape_year_two_level, yf, suffix_filter='_edges.zip')

# Dedup, preserve order, drop blanks.
seen, uniq = set(), []
for u in urls:
    if u and u not in seen:
        seen.add(u); uniq.append(u)

with open('urls.txt', 'w') as f:
    f.write('\n'.join(uniq) + ('\n' if uniq else ''))

print(f"\nTotal: {len(uniq)} unique URLs written to urls.txt")
print(f"Check failed_urls.txt for any permanent failures")
