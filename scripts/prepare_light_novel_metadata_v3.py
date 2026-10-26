"""Prepare a finite pinned v3 selection; no discovery, database writes or models."""
import argparse
import hashlib
import json
from pathlib import Path

from recommend_book.light_novel_admission_v3 import prepare_metadata, check_duplicate_titles


def prepare(selection_path, selection_sha256, existing_path, existing_sha256, output):
    def read(path, pin):
        raw = Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pin:
            raise ValueError('v3 preparation input SHA differs')
        return json.loads(raw)
    selection = read(selection_path, selection_sha256)
    existing = read(existing_path, existing_sha256)
    if selection.get('version') != 'light-novel-v3-finite-selection-v1':
        raise ValueError('v3 finite selection version differs')
    rows = selection['series']
    if not 1 <= len(rows) <= 50 or len({r['qid'] for r in rows}) != len(rows):
        raise ValueError('v3 finite selection absent or duplicated')
    if not isinstance(existing, list) or len(existing) > 100000:
        raise ValueError('v3 finite existing-title snapshot differs')
    records = []
    for row in rows:
        record = prepare_metadata(row['qid'], row['provenance'], row['type_authorities'])
        check_duplicate_titles(record, [*existing, *[
            {'entry_id': 'lns:wd:' + r['id'], 'entry_kind': 'series',
             'title': r['title'], 'aliases': r['aliases']} for r in records]])
        records.append(record)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    target = output / 'metadata-v3.jsonl'
    with target.open('x', encoding='utf8') as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    report = {'version': 'light-novel-v3-preparation-v1', 'series_ids': [r['id'] for r in records],
        'metadata_path': str(target), 'metadata_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'selection_path': str(Path(selection_path).resolve()), 'selection_sha256': selection_sha256,
        'existing_title_snapshot_path': str(Path(existing_path).resolve()),
        'existing_title_snapshot_sha256': existing_sha256, 'insert_time_duplicate_recheck_required': True,
        'network_calls': 0, 'model_calls': 0, 'catalogue_writes': 0}
    with (output / 'manifest.json').open('x', encoding='utf8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', required=True)
    parser.add_argument('--selection-sha256', required=True)
    parser.add_argument('--existing-titles', required=True)
    parser.add_argument('--existing-titles-sha256', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.selection, args.selection_sha256, args.existing_titles,
        args.existing_titles_sha256, args.output), ensure_ascii=True))
