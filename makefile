.RECIPEPREFIX := >

PYTHON := C:/Users/noah.weidig/miniconda3/envs/ee/python.exe
SHAPEFILE_DIR := D:/roads_shapefiles

.PHONY: all urls check download retry convert upload upload-dry-run clean clean-logs

all: urls check download convert upload

urls:
>python 01_generate_urls.py

check:
>python 02_check_urls.py

download:
>python 03_download_zips.py

retry:
>python 03_download_zips.py --retry-fails

convert:
>python 04_convert_zips.py

upload:
>"$(PYTHON)" 05_upload_to_gee.py --shapefile-dir "$(SHAPEFILE_DIR)"

upload-dry-run:
>"$(PYTHON)" 05_upload_to_gee.py --shapefile-dir "$(SHAPEFILE_DIR)" --dry-run

clean:
>rm -f urls.txt failed_urls.txt aria2_session.txt incomplete_urls.txt conversion_failures.txt conversion_report.json tiger_inventory.json tiger_inventory_errors.txt
>rm -rf logs/

clean-logs:
>rm -rf logs/