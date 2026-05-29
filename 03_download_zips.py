import subprocess
from pathlib import Path

URLS_FILE = "urls.txt"
DOWNLOAD_DIR = Path("D:/roads/tiger_downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

def download_all():
    print("Starting aria2c download...")
    cmd = [
        "aria2c",
        "-i", URLS_FILE,
        "-j", "4",
        "-x", "4",
        "--dir", str(DOWNLOAD_DIR),
        "--retry-wait=10",
        "--max-tries=5",
        "--auto-file-renaming=false",
        "--continue=true",
        "--save-session=aria2_session.txt",
        "--save-session-interval=30"
    ]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print("ERROR: aria2c not found on PATH. Install aria2 and retry.")
        raise SystemExit(1)
    except subprocess.CalledProcessError as e:
        # aria2c writes unfinished/failed URLs to the session file on exit.
        session_path = Path("aria2_session.txt")
        failed_path = Path("failed_urls.txt")
        if session_path.exists():
            session_path.replace(failed_path)
            print(
                f"ERROR: aria2c exited with code {e.returncode}. "
                f"Unfinished URLs written to {failed_path} — rerun to resume."
            )
        else:
            print(
                f"ERROR: aria2c exited with code {e.returncode}. "
                "No session file found; rerun to resume from partial downloads."
            )
        raise SystemExit(e.returncode)

if __name__ == "__main__":
    download_all()
