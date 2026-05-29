import random
import time

import requests
from bs4 import BeautifulSoup

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


def get_links(url, retries=5):
    for attempt in range(retries):
        try:
            time.sleep(random.uniform(0.5, 1.5))
            r = requests.get(url, headers=HEADERS, timeout=60)
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
