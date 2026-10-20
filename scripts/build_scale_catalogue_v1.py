"""Build/resume the metadata-only SQLite catalogue from source-record JSONL."""
import argparse
import json
from pathlib import Path

from recommend_book.scale_catalogue_v1 import ScaleCatalogue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--source-conditions', type=Path, required=True)
    parser.add_argument('--source-conditions-sha256', required=True)
    parser.add_argument('--chunk-size', type=int, default=1000)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    catalogue = ScaleCatalogue(args.database, source_conditions_path=args.source_conditions,
                               source_conditions_sha256=args.source_conditions_sha256)

    def records():
        with args.records.open(encoding='utf-8-sig') as stream:
            for line in stream:
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        yield {'invalid_json_line': True}

    result = catalogue.ingest(records(), chunk_size=args.chunk_size)
    report = {'ingestion': result, 'catalogue': catalogue.catalogue_stats()}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
