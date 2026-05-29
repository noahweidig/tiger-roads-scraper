.PHONY: all urls check download convert clean

all: urls check download convert

urls:
	python 01_generate_urls.py

check:
	python 02_check_urls.py

download:
	python 03_download_zips.py

convert:
	python 04_convert_zips.py

clean:
	rm -f urls.txt failed_urls.txt aria2_session.txt conversion_failures.txt tiger_inventory.json tiger_inventory_errors.txt