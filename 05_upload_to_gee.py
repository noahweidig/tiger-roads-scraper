"""Batch-upload zipped TIGER/Line shapefiles into Google Earth Engine.

Reads every `*.zip` under TIGER_SHAPEFILE_DIR (step 4's output — one merged
state shapefile per zip), stages each zip in a GCS bucket, then submits an
Earth Engine table-ingestion task per zip. One EE asset per zip, named after
the zip's stem (e.g. `TIGER1992_01`), under a single destination folder.

Prerequisites:
- `pip install earthengine-api`
- `gcloud auth login` / `earthengine authenticate` already run
- `gsutil` on PATH, with write access to the staging bucket
- An Earth Engine project you can write assets to

Usage:
    python 05_upload_to_gee.py --bucket my-staging-bucket \
        --ee-project my-ee-project --asset-folder tiger_roads

Safe to rerun: assets that already exist are skipped (use --overwrite to
replace them); zips already staged in GCS at the expected size are not
re-uploaded.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SHAPEFILE_DIR = Path(os.environ.get("TIGER_SHAPEFILE_DIR", "D:/roads_shapefiles"))
LOG_DIR = Path("logs")

FAIL_LOG = LOG_DIR / "gee_upload_failures.txt"
OK_LOG = LOG_DIR / "gee_upload_ok.txt"
SUMMARY_JSON = LOG_DIR / "gee_upload_summary.json"
PROGRESS_LOG = LOG_DIR / "gee_upload_progress.log"

DEFAULT_WORKERS = 4
POLL_INTERVAL = 15
_print_lock = threading.Lock()


def gsutil_uri_exists(uri: str, expected_size: int) -> bool:
    r = subprocess.run(["gsutil", "stat", uri], capture_output=True, text=True)
    if r.returncode != 0:
        return False
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("Content-Length:"):
            return int(line.split(":", 1)[1].strip()) == expected_size
    return False


def stage_to_gcs(zip_path: Path, uri: str) -> None:
    if gsutil_uri_exists(uri, zip_path.stat().st_size):
        return
    r = subprocess.run(["gsutil", "-q", "cp", str(zip_path), uri],
                        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gsutil cp failed: {r.stderr.strip()}")


def asset_exists(asset_id: str) -> bool:
    import ee
    try:
        ee.data.getAsset(asset_id)
        return True
    except ee.EEException:
        return False


def submit_ingestion(asset_id: str, gcs_uri: str, overwrite: bool) -> str:
    import ee
    manifest = {
        "name": asset_id,
        "sources": [{"uris": [gcs_uri]}],
    }
    task_id = ee.data.newTaskId()[0]
    ee.data.startTableIngestion(task_id, manifest, allow_overwrite=overwrite)
    return task_id


def process_one(zip_path: Path, args, folder_prefix: str) -> tuple[str, str, str]:
    """Returns (status, zip_name, info). status in {ok, skip, fail}."""
    import ee

    stem = zip_path.stem
    asset_id = f"{folder_prefix}/{stem}"
    try:
        if not args.overwrite and asset_exists(asset_id):
            return ("skip", stem, f"asset exists: {asset_id}")

        gcs_uri = f"gs://{args.bucket}/{args.gcs_prefix}/{zip_path.name}"
        stage_to_gcs(zip_path, gcs_uri)
        task_id = submit_ingestion(asset_id, gcs_uri, args.overwrite)

        if args.no_wait:
            return ("ok", stem, f"submitted task={task_id} asset={asset_id}")

        while True:
            status = ee.data.getTaskStatus(task_id)[0]
            state = status.get("state")
            if state in ("COMPLETED",):
                return ("ok", stem, f"asset={asset_id}")
            if state in ("FAILED", "CANCELLED"):
                return ("fail", stem, f"task {state}: {status.get('error_message')}")
            time.sleep(POLL_INTERVAL)
    except Exception as e:
        return ("fail", stem, f"{type(e).__name__}: {e}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shapefile-dir", type=Path, default=SHAPEFILE_DIR,
                        help="Root dir to search for *.zip (default TIGER_SHAPEFILE_DIR env or D:/roads_shapefiles)")
    parser.add_argument("--bucket", required=True, help="GCS bucket used to stage zips for EE ingestion")
    parser.add_argument("--gcs-prefix", default="tiger_roads_staging", help="Path prefix within the bucket")
    parser.add_argument("--ee-project", required=True, help="Earth Engine cloud project ID")
    parser.add_argument("--asset-folder", required=True,
                        help="Destination EE folder, relative to the project's assets root (e.g. tiger_roads)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--overwrite", action="store_true", help="Re-ingest and replace existing assets")
    parser.add_argument("--no-wait", action="store_true",
                        help="Submit ingestion tasks without waiting for them to complete")
    parser.add_argument("--dry-run", action="store_true", help="List what would be uploaded and exit")
    args = parser.parse_args()

    zips = sorted(args.shapefile_dir.rglob("*.zip"))
    if not zips:
        print(f"No zips found under {args.shapefile_dir.resolve()}", file=sys.stderr)
        return 1

    print(f"Found {len(zips)} zipped shapefiles under {args.shapefile_dir.resolve()}")

    if args.dry_run:
        for z in zips:
            print(f"  {z}")
        return 0

    import ee
    ee.Initialize(project=args.ee_project)

    project_root = f"projects/{args.ee_project}/assets"
    folder_id = f"{project_root}/{args.asset_folder}"
    try:
        ee.data.getAsset(folder_id)
    except ee.EEException:
        ee.data.createAsset({"type": "FOLDER"}, folder_id)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    progress_fh = PROGRESS_LOG.open("w", buffering=1)
    results: dict[str, list[tuple[str, str]]] = {"ok": [], "skip": [], "fail": []}
    start = time.time()
    done = 0

    def worker(z: Path):
        return process_one(z, args, folder_id)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(worker, z): z for z in zips}
            for fut in concurrent.futures.as_completed(futures):
                status, name, info = fut.result()
                results[status].append((name, info))
                done += 1
                line = f"  {status.upper():5s} {name} :: {info}"
                with _print_lock:
                    print(line, flush=True)
                    progress_fh.write(line + "\n")
                if done % 20 == 0 or done == len(zips):
                    with _print_lock:
                        print(f"[{done}/{len(zips)}] ok={len(results['ok'])} "
                              f"skip={len(results['skip'])} fail={len(results['fail'])}",
                              flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted — writing partial logs...", flush=True)
    finally:
        progress_fh.close()

    FAIL_LOG.write_text(
        "\n".join(f"{n}\t{i}" for n, i in results["fail"]) + ("\n" if results["fail"] else "")
    )
    OK_LOG.write_text(
        "\n".join(n for n, _ in results["ok"] + results["skip"]) + "\n"
    )
    summary = {
        "total": len(zips),
        "ok": len(results["ok"]),
        "skip": len(results["skip"]),
        "fail": len(results["fail"]),
        "elapsed_seconds": round(time.time() - start, 1),
        "asset_folder": folder_id,
        "bucket": args.bucket,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2))

    print("\n" + "=" * 60)
    print("GEE UPLOAD SUMMARY")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:15s} {v}")

    return 2 if results["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
