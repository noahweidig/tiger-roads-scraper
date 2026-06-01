import subprocess
from pathlib import Path

URLS_FILE = Path("urls.txt")
DOWNLOAD_DIR = Path("D:/roads/tiger_downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

lines = URLS_FILE.read_text().splitlines()
filtered = [l for l in lines if "OtherFiles.zip" not in l]
URLS_FILE.write_text("\n".join(filtered) + "\n")
print(f"Removed {len(lines) - len(filtered)} OtherFiles.zip entries.")

def download_all():
    if not URLS_FILE.exists():
        print(f"ERROR: {URLS_FILE} not found.")
        raise SystemExit(1)

    print("Starting aria2c download...")
    cmd = [
        "aria2c",
        "-i", str(URLS_FILE),
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
        session_path = Path("aria2_session.txt")
        incomplete_path = Path("incomplete_urls.txt")
        if session_path.exists():
            session_path.replace(incomplete_path)
            print(
                f"ERROR: aria2c exited with code {e.returncode}. "
                f"Incomplete URLs written to {incomplete_path} — rerun to resume."
            )
        else:
            print(
                f"ERROR: aria2c exited with code {e.returncode}. "
                "No session file found; rerun to resume from partial downloads."
            )
        raise SystemExit(int(e.returncode))

if __name__ == "__main__":
    download_all()
