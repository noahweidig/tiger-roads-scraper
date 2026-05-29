# generate_urls.py
import time

from tiger_utils import clean_folders, get_links

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
            if suffix_filter and f.endswith(suffix_filter):
                urls.append(url + f)
            elif not suffix_filter and f.endswith('.zip'):
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
            if suffix_filter:
                if f.endswith(suffix_filter):
                    urls.append(base + year_folder + sf + f)
            else:
                if f.endswith('.zip'):
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
                if suffix_filter:
                    if f.endswith(suffix_filter):
                        urls.append(base + year_folder + sf + cf + f)
                else:
                    if f.endswith('.zip'):
                        urls.append(base + year_folder + sf + cf + f)
    urls += retry_failed(failed, suffix_filter=suffix_filter)
    return urls

# clear failed log
open('failed_urls.txt', 'w').close()

urls = []

for year_folder in ['TIGER1992/', 'TIGER1999/', 'TIGER2002/', 'TIGER2003/']:
    urls += scrape_year(year_folder)

urls += scrape_year('rd_2ktiger/')

for year_folder in ['TIGER2007FE/', 'TIGER2008/']:
    urls += scrape_year_two_level(year_folder, suffix_filter='_edges.zip')

with open('urls.txt', 'w') as f:
    for url in urls:
        f.write(url + '\n')

print(f"\nTotal: {len(urls)} URLs written to urls.txt")
print(f"Check failed_urls.txt for any permanent failures")
