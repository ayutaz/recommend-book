import argparse
import json
from pathlib import Path
from recommend_book.downloads import download_file

parser = argparse.ArgumentParser()
parser.add_argument("url")
parser.add_argument("target", type=Path)
arguments = parser.parse_args()
print(json.dumps(download_file(arguments.url, arguments.target), ensure_ascii=False, indent=2))
