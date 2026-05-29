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
    subprocess.run(cmd, check=True)

if __name__ == "__main__":
    download_all()
