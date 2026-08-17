"""Batch-upload zipped TIGER/Line shapefiles into Google Earth Engine."""
from __future__ import annotations
import argparse
import concurrent.futures
import json
import os
import sys
import threading
import time
from pathlib import Path
import google.oauth2.credentials

SHAPEFILE_DIR = Path(os.environ.get("TIGER_SHAPEFILE_DIR", "D:/roads_shapefiles"))
LOG_DIR = Path("logs")
FAIL_LOG = LOG_DIR / "gee_upload_failures.txt"
OK_LOG = LOG_DIR / "gee_upload_ok.txt"
SUMMARY_JSON = LOG_DIR / "gee_upload_summary.json"
PROGRESS_LOG = LOG_DIR / "gee_upload_progress.log"
DEFAULT_WORKERS = 4
POLL_INTERVAL = 30
EE_PROJECT = "tiger-roads"
EE_ASSET_FOLDER = "states"
GCS_BUCKET = "tiger-roads-staging"
GCS_PREFIX = "tiger_roads_staging"
_print_lock = threading.Lock()


def stage_to_gcs(zip_path: Path, bucket_name: str, blob_name: str) -> str:
    from google.cloud import storage
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_name)
    expected = zip_path.stat().st_size
    if blob.exists(client=client):
        blob.reload(client=client)
        if blob.size == expected:
            return f"gs://{bucket_name}/{blob_name}"
    blob.upload_from_filename(str(zip_path))
    return f"gs://{bucket_name}/{blob_name}"


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
    import ee
    stem = zip_path.stem
    asset_id = f"{folder_prefix}/{stem}"
    try:
        if not args.overwrite and asset_exists(asset_id):
            return ("skip", stem, f"asset exists: {asset_id}")
        blob_name = f"{args.gcs_prefix}/{zip_path.name}"
        gcs_uri = stage_to_gcs(zip_path, args.bucket, blob_name)
        task_id = submit_ingestion(asset_id, gcs_uri, args.overwrite)
        if args.no_wait:
            return ("ok", stem, f"submitted task={task_id} asset={asset_id}")
        while True:
            status = ee.data.getTaskStatus(task_id)[0]
            state = status.get("state")
            if state == "COMPLETED":
                return ("ok", stem, f"asset={asset_id}")
            if state in ("FAILED", "CANCELLED"):
                return ("fail", stem, f"task {state}: {status.get('error_message')}")
            time.sleep(POLL_INTERVAL)
    except Exception as e:
        return ("fail", stem, f"{type(e).__name__}: {e}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shapefile-dir", type=Path, default=SHAPEFILE_DIR)
    parser.add_argument("--bucket", default=GCS_BUCKET)
    parser.add_argument("--gcs-prefix", default=GCS_PREFIX)
    parser.add_argument("--ee-project", default=EE_PROJECT)
    parser.add_argument("--asset-folder", default=EE_ASSET_FOLDER)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
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
    from google.auth.transport.requests import Request

    creds_path = os.path.join(os.path.expanduser("~"), ".config", "earthengine", "credentials")
    with open(creds_path) as f:
      creds_data = json.load(f)

    credentials = google.oauth2.credentials.Credentials(
        token=None,
        refresh_token=creds_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"],
    )
    credentials.refresh(Request())
    ee.Initialize(credentials=credentials, project=args.ee_project)

    folder_id = f"projects/{args.ee_project}/assets/{args.asset_folder}"
    try:
        ee.data.getAsset(folder_id)
    except ee.EEException:
        ee.data.createAsset({"type": "FOLDER"}, folder_id)
        print(f"Created EE folder: {folder_id}")

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
                        print(
                            f"[{done}/{len(zips)}] ok={len(results['ok'])} "
                            f"skip={len(results['skip'])} fail={len(results['fail'])}",
                            flush=True,
                        )
    except KeyboardInterrupt:
        print("\nInterrupted — writing partial logs...", flush=True)
    finally:
        progress_fh.close()

    FAIL_LOG.write_text(
        "\n".join(f"{n}\t{i}" for n, i in results["fail"])
        + ("\n" if results["fail"] else "")
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
