import random
import time

import requests
from bs4 import BeautifulSoup

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


# The Census WAF answers a throttled request with HTTP 200 and a tiny
# "Request Rejected" page. Treated as a valid listing it looks like an empty
# directory, so a whole year or state silently contributes zero URLs — that is
# how rd_2ktiger (3219 files), TIGER2002/17_il + 31_NE, TIGER2003/05_AR and
# tiger2006se/DE + NC went missing from urls.txt with nothing in failed_urls.
REJECT_MARKERS = ('request rejected', 'requested url was rejected',
                  'access denied', 'support id is')


def get_links(url, retries=5):
    for attempt in range(retries):
        try:
            time.sleep(random.uniform(0.5, 1.5))
            r = requests.get(url, headers=HEADERS, timeout=60)
            r.raise_for_status()
            low = r.text.lower()
            if any(m in low for m in REJECT_MARKERS):
                raise OSError("blocked by WAF (HTTP 200 'Request Rejected')")
            soup = BeautifulSoup(r.text, 'html.parser')
            links = [a.get('href') for a in soup.find_all('a') if a.get('href')]
            # A real Census index always links back to its parent, so a listing
            # with no relative links at all is a bad response, not an empty dir.
            if not any(not h.startswith(('http', '/', '?', '#')) for h in links):
                raise OSError("listing has no directory entries")
            return links
        except Exception as e:
            wait = 2 ** (attempt + 1)
            print(f"  ATTEMPT {attempt+1} FAILED: {url} — {e} (retrying in {wait}s)")
            time.sleep(wait)
    print(f"  GIVING UP: {url}")
    return None


def matches_suffix(filename, suffix=None):
    """Case-insensitive suffix match. Some archive years (e.g. tiger2006se)
    name files uppercase TGR01001.ZIP while others use lowercase .zip, so a
    case-sensitive endswith('.zip') silently drops a whole year."""
    f = filename.lower()
    return f.endswith(suffix.lower()) if suffix else f.endswith('.zip')


def clean_folders(links):
    if links is None:
        return []
    return [l for l in links
            if l.endswith('/')
            and not l.startswith('/')
            and not l.startswith('http')
            and '?' not in l
            and l.count('/') == 1]
