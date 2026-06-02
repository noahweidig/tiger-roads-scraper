"""Bulletproof TIGER/Line downloader.

Reads urls.txt, downloads every file with size verification, resume,
exponential backoff, concurrent workers, and exhaustive failure logging.

Safe to rerun: already-complete files are skipped via Content-Length check.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

URLS_FILE = Path("urls.txt")
DOWNLOAD_DIR = Path(os.environ.get("TIGER_DOWNLOAD_DIR", "D:/roads"))
LOG_DIR = Path("logs")

FAIL_LOG = LOG_DIR / "download_failures.txt"
MISS_LOG = LOG_DIR / "download_missing.txt"
OK_LOG = LOG_DIR / "download_ok.txt"
SUMMARY_JSON = LOG_DIR / "download_summary.json"
PROGRESS_LOG = LOG_DIR / "download_progress.log"

DEFAULT_WORKERS = 16
CHUNK = 1 << 16
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 180
MAX_RETRIES = 6
HEADERS = {"User-Agent": "Mozilla/5.0 tiger-line-downloader/1.0"}

ALLOWED_HOSTS = frozenset({"www2.census.gov", "www.census.gov", "census.gov"})
ALLOWED_SCHEMES = frozenset({"https"})


def validate_url(url: str) -> None:
    """Raise ValueError unless url targets a known-good Census host over HTTPS."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"scheme {parsed.scheme!r} not allowed")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"host {parsed.hostname!r} not in allowlist")
    if parsed.port not in (None, 443):
        raise ValueError(f"port {parsed.port} not allowed")


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=0,  # we do our own retries to control timing/logging
        connect=0,
        read=0,
        status=0,
    )
    adapter = HTTPAdapter(pool_connections=64, pool_maxsize=64, max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update(HEADERS)
    return s


def url_to_path(url: str) -> Path:
    p = urlparse(url).path.lstrip("/")
    parts = [seg for seg in p.split("/") if seg]
    if any(seg in ("..", ".") or "/" in seg or "\\" in seg or seg.startswith(("/", "~"))
           for seg in parts):
        raise ValueError(f"unsafe URL path components: {url}")
    if parts[:2] == ["geo", "tiger"]:
        parts = parts[2:]
    if not parts:
        raise ValueError(f"empty URL path: {url}")
    # Lowercase the year folder (e.g. TIGER1992 → tiger1992) so each year
    # gets its own predictably-named directory and same-FIPS files from
    # different years don't collide.
    parts[0] = parts[0].lower()
    root = DOWNLOAD_DIR.resolve()
    dest = root.joinpath(*parts).resolve()
    if not dest.is_relative_to(root):
        raise ValueError(f"URL escapes download dir: {url}")
    return dest


def head_size(session: requests.Session, url: str) -> tuple[int | None, int | None]:
    """Return (size, status_code). size is None if unknown."""
    try:
        r = session.head(
            url,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            allow_redirects=True,
        )
        cl = r.headers.get("Content-Length")
        size = int(cl) if cl and cl.isdigit() else None
        return size, r.status_code
    except Exception:
        return None, None


def download_one(url: str, session: requests.Session) -> tuple[str, str, str]:
    """Returns (status, url, info). status ∈ {ok, skip, missing, fail}."""
    try:
        validate_url(url)
        dest = url_to_path(url)
    except ValueError as e:
        return ("fail", url, f"unsafe url: {e}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    expected: int | None = None
    head_code: int | None = None
    for hattempt in range(3):
        expected, head_code = head_size(session, url)
        if head_code is not None:
            break
        time.sleep(2 ** hattempt)

    if head_code == 404:
        return ("missing", url, "HTTP 404")
    if head_code is not None and head_code >= 400 and head_code != 405:
        # 405 = Method Not Allowed for HEAD; fall through to GET
        if head_code == 403:
            return ("missing", url, "HTTP 403")
        # other 4xx/5xx: try GET anyway

    if dest.exists() and expected is not None and dest.stat().st_size == expected:
        return ("skip", url, "verified")
    if dest.exists() and expected is None and dest.stat().st_size > 0:
        # Can't verify; assume good but flag in info
        return ("skip", url, "no-size-header")

    last_err = "unknown"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resume_from = tmp.stat().st_size if tmp.exists() else 0
            req_headers = {}
            if resume_from and expected and resume_from < expected:
                req_headers["Range"] = f"bytes={resume_from}-"
            elif resume_from and expected and resume_from >= expected:
                tmp.unlink(missing_ok=True)
                resume_from = 0

            with session.get(
                url,
                headers=req_headers,
                stream=True,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            ) as r:
                if r.status_code == 404:
                    return ("missing", url, "HTTP 404")
                if r.status_code == 416:
                    if tmp.exists():
                        tmp.replace(dest)
                        return ("ok", url, str(dest.stat().st_size))
                    return ("fail", url, "HTTP 416 with no partial file")
                if r.status_code not in (200, 206):
                    raise IOError(f"HTTP {r.status_code}")

                # If server ignored Range and returned 200, restart from scratch
                mode = "ab" if (resume_from and r.status_code == 206) else "wb"
                if mode == "wb" and tmp.exists():
                    tmp.unlink()

                # If we didn't get a HEAD size, use Content-Length from GET
                if expected is None:
                    cl = r.headers.get("Content-Length")
                    if cl and cl.isdigit():
                        get_size = int(cl)
                        expected = (get_size + resume_from) if r.status_code == 206 else get_size

                with open(tmp, mode) as f:
                    for chunk in r.iter_content(CHUNK):
                        if chunk:
                            f.write(chunk)

            got = tmp.stat().st_size
            if expected is not None and got != expected:
                last_err = f"size mismatch got={got} expected={expected}"
                tmp.unlink(missing_ok=True)  # don't trust a partial
                if attempt < MAX_RETRIES:
                    time.sleep(min(60, 2 ** attempt))
                    continue
                return ("fail", url, last_err)

            tmp.replace(dest)
            return ("ok", url, str(got))
        except requests.exceptions.Timeout as e:
            last_err = f"timeout: {e}"
        except requests.exceptions.ConnectionError as e:
            last_err = f"conn: {e}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        if attempt < MAX_RETRIES:
            time.sleep(min(60, 2 ** attempt))

    return ("fail", url, last_err)


_print_lock = threading.Lock()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--urls", type=Path, default=URLS_FILE)
    parser.add_argument("--retry-fails", action="store_true",
                        help="Only retry URLs in logs/download_failures.txt")
    args = parser.parse_args()

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.retry_fails:
        if not FAIL_LOG.exists():
            print(f"No prior fail log at {FAIL_LOG}; nothing to retry.")
            return 0
        urls = [l.split("\t", 1)[0].strip()
                for l in FAIL_LOG.read_text().splitlines() if l.strip()]
    else:
        if not args.urls.exists():
            print(f"ERROR: {args.urls} not found. Run step 1 first.", file=sys.stderr)
            return 1
        urls = [u.strip() for u in args.urls.read_text().splitlines() if u.strip()]

    urls = [u for u in urls if "OtherFiles.zip" not in u]
    seen: set[str] = set()
    urls = [u for u in urls if not (u in seen or seen.add(u))]

    total = len(urls)
    print(f"Downloading {total} files → {DOWNLOAD_DIR.resolve()}")
    print(f"Workers: {args.workers}  Retries/file: {MAX_RETRIES}")
    print(f"Logs:    {LOG_DIR.resolve()}")
    print("-" * 60, flush=True)

    results: dict[str, list[tuple[str, str]]] = {
        "ok": [], "skip": [], "missing": [], "fail": []
    }
    start = time.time()
    progress_fh = PROGRESS_LOG.open("w", buffering=1)

    session = make_session()

    def worker(u: str) -> tuple[str, str, str]:
        return download_one(u, session)

    done = 0
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(worker, u): u for u in urls}
            for fut in concurrent.futures.as_completed(futures):
                status, url, info = fut.result()
                results[status].append((url, info))
                done += 1

                if status in ("fail", "missing"):
                    with _print_lock:
                        line = f"  {status.upper():7s} {url} :: {info}"
                        print(line, flush=True)
                        progress_fh.write(line + "\n")

                if done % 100 == 0 or done == total:
                    el = time.time() - start
                    rate = done / el if el else 0
                    eta = (total - done) / rate if rate else 0
                    with _print_lock:
                        print(
                            f"[{done}/{total}] "
                            f"ok={len(results['ok'])} skip={len(results['skip'])} "
                            f"miss={len(results['missing'])} fail={len(results['fail'])} "
                            f"| {rate:.1f}/s ETA={eta/60:.1f}m",
                            flush=True,
                        )
    except KeyboardInterrupt:
        print("\nInterrupted — writing partial logs...", flush=True)
    finally:
        progress_fh.close()

    FAIL_LOG.write_text(
        "\n".join(f"{u}\t{i}" for u, i in results["fail"]) + ("\n" if results["fail"] else "")
    )
    MISS_LOG.write_text(
        "\n".join(f"{u}\t{i}" for u, i in results["missing"]) + ("\n" if results["missing"] else "")
    )
    OK_LOG.write_text(
        "\n".join(u for u, _ in results["ok"] + results["skip"]) + "\n"
    )

    summary = {
        "total": total,
        "ok": len(results["ok"]),
        "skip": len(results["skip"]),
        "missing": len(results["missing"]),
        "fail": len(results["fail"]),
        "elapsed_seconds": round(time.time() - start, 1),
        "download_dir": str(DOWNLOAD_DIR.resolve()),
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2))

    print("\n" + "=" * 60)
    print("DOWNLOAD SUMMARY")
    print("=" * 60)
    for k in ("total", "ok", "skip", "missing", "fail", "elapsed_seconds"):
        print(f"  {k:18s} {summary[k]}")
    print(f"\n  ok+skip            {summary['ok'] + summary['skip']}")
    print(f"  failures log       {FAIL_LOG}")
    print(f"  missing log        {MISS_LOG}")
    print(f"  summary            {SUMMARY_JSON}")

    if results["fail"]:
        print(f"\n{len(results['fail'])} failures — rerun with --retry-fails to retry just those.")
        return 2
    if results["missing"]:
        print(f"\n{len(results['missing'])} URLs returned 404/403 (server-side missing).")
    print("\nAll downloads accounted for.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
