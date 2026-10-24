"""Preserve a failed continuation gate and withdraw only its imported SQL rows.

The failed model inputs and all original proofs remain untouched. The CLI uses
independently fixed production pins; tests inject small offline authorities.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from recommend_book.scale20k_native_v1 import BASE, DATABASE, writer_lock

FIELDS = ('qid', 'manifest_path', 'manifest_sha256', 'labels_json', 'provenance_json')
CONTROL = BASE / 'window-controls/scale20k-content-6801-6900-v1.json'
CONTROL_SHA = '073dfb95d9c0fdb70c7f58c7638779ad940696b9ccfcab3a45ef1e542c4cec6a'
BASELINE_SHA = '5734eda48467012f4c257a57027e655cb829ba309730a5dc2cce2a01c47ddf36'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, allow_nan=False)
        stream.write('\n')


def verify_failed(control, expected):
    """Authenticate the failed window without granting continuation permission."""
    from recommend_book.scale_content_control_v4 import actual_gate, display_counts, verify_control_receipt
    from audit_scale20k_catalogue_v1 import verify_public_manifest

    control = Path(control).resolve(strict=True)
    if sha(control) != expected:
        raise ValueError('Failed control SHA differs')
    value = json.loads(control.read_bytes())
    if value.get('version') != 'scale-content-window-control-v4' or value['gate']['passed'] is not False:
        raise ValueError('An actual failed content control is required')
    # The unchanged normal verifier must still reject this gate. It authenticates
    # all process/artifact/display/import bindings before reaching this rejection.
    try:
        verify_control_receipt(control, expected)
    except ValueError as exc:
        if str(exc) != 'Actual content continuation gate failed or differs':
            raise
    else:
        raise ValueError('Failed gate unexpectedly authorized continuation')
    pins = value['control_artifacts']
    public = json.loads(Path(pins['export']['path']).read_bytes())
    run = json.loads(Path(pins['run']['path']).read_bytes())
    counts = display_counts(public['verified_projection']['rows'])
    actual = actual_gate(run['selected'], len(public['verified_projection']['rows']),
                        counts['actual_display_feature_works'],
                        counts['actual_content_non_genre_feature_works'],
                        stopped=bool(run.get('stopped') or run.get('stop_reasons')))
    if actual != value['gate'] or actual['passed'] is not False:
        raise ValueError('Saved failed gate differs from actual unchanged gate')
    verified = verify_public_manifest(pins['export']['path'], pins['export']['sha256'])
    rows = [dict(qid=r['qid'], manifest_path=pins['export']['path'],
                 manifest_sha256=pins['export']['sha256'],
                 labels_json=json.dumps(r['labels'], ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                 provenance_json=json.dumps(r['provenance'], ensure_ascii=False, sort_keys=True, separators=(',', ':')))
            for r in verified]
    if sha(control) != expected or sha(pins['export']['path']) != pins['export']['sha256']:
        raise ValueError('Failed-window authority changed during verification')
    return {'control_path': str(control), 'control_sha256': expected,
            'failed_gate': value['gate'], 'export_path': pins['export']['path'],
            'export_sha256': pins['export']['sha256'], 'rows': rows}


def _same_row(actual, expected):
    return (all(actual[k] == expected[k] for k in FIELDS[:3])
            and all(json.loads(actual[k]) == json.loads(expected[k]) for k in FIELDS[3:]))


def quarantine(*, database, control, control_sha, baseline, baseline_sha, output,
               baseline_count=10000, verify=None):
    database, baseline, output = Path(database).resolve(strict=True), Path(baseline), Path(output)
    if output.exists():
        raise FileExistsError('Quarantine is create-only; inspect existing evidence instead of replaying')
    if sha(baseline) != baseline_sha:
        raise ValueError('Frozen baseline SHA differs')
    original = [json.loads(line) for line in baseline.read_bytes().splitlines() if line.strip()]
    baseline_qids = {r['qid'] for r in original}
    if len(original) != baseline_count or len(baseline_qids) != baseline_count:
        raise ValueError('Exact distinct baseline authority set required')
    authority = (verify or verify_failed)(control, control_sha)
    rows = authority['rows']
    qids = {r['qid'] for r in rows}
    if (authority['failed_gate'].get('passed') is not False or not 1 <= len(rows) <= 100
            or len(qids) != len(rows) or qids & baseline_qids
            or any(r['manifest_sha256'] != authority['export_sha256'] for r in rows)):
        raise ValueError('Only distinct nonbaseline failed-window rows may be withdrawn')
    with writer_lock(purpose='quarantine_failed_v20_window_without_model_retry'):
        if sha(baseline) != baseline_sha or (verify is None and (
                sha(control) != control_sha or sha(authority['export_path']) != authority['export_sha256'])):
            raise ValueError('Pinned authority changed before withdrawal')
        with sqlite3.connect(database.as_uri() + '?mode=rw', uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='native_classifications'").fetchone():
                raise ValueError('Native deletion trigger prevents bounded quarantine')
            actual = [dict(r) for r in db.execute('SELECT ' + ','.join(FIELDS)
                      + ' FROM native_classifications WHERE manifest_sha256=? ORDER BY qid',
                      (authority['export_sha256'],))]
            expected = {r['qid']: r for r in rows}
            if ({r['qid'] for r in actual} != qids
                    or any(not _same_row(r, expected[r['qid']]) for r in actual)):
                raise ValueError('Actual SQL rows differ from authenticated failed export')
            placeholders = ','.join('?' for _ in baseline_qids)
            if db.execute('SELECT count(*) FROM native_classifications WHERE qid IN (' + placeholders + ')',
                          sorted(baseline_qids)).fetchone()[0] != baseline_count:
                raise ValueError('Original baseline membership differs')
            before = db.execute('SELECT count(*) FROM native_classifications').fetchone()[0]
            output.mkdir(parents=True, exist_ok=False)
            save(output / 'rows-before.json', {**authority, 'rows': actual,
                 'database': str(database), 'baseline_path': str(baseline.resolve()),
                 'baseline_sha256': baseline_sha, 'baseline_qids_disjoint': True,
                 'full_baseline_row_audit': 'deferred_to_final_catalogue_audit',
                 'restore_requires_explicit_independent_review': True})
            for qid in sorted(qids):
                if db.execute('DELETE FROM native_classifications WHERE qid=? AND manifest_sha256=?',
                              (qid, authority['export_sha256'])).rowcount != 1:
                    raise ValueError('Exact SQL withdrawal differs; transaction rolled back')
            remaining = db.execute('SELECT count(*) FROM native_classifications').fetchone()[0]
            if remaining != before - len(qids):
                raise ValueError('Unexpected catalogue change; transaction rolled back')
            db.commit()
        result = {'version': 'scale20k-failed-window-quarantine-v1', 'status': 'quarantined',
                  'removed': len(qids), 'remaining': remaining, 'baseline_qids_untouched': True,
                  'rows_before_path': str((output / 'rows-before.json').resolve()),
                  'rows_before_sha256': sha(output / 'rows-before.json'),
                  'failed_gate': authority['failed_gate'], 'failed_inputs_retried': 0,
                  'model_calls': 0, 'original_proofs_modified': False,
                  'observed_at': datetime.now(timezone.utc).isoformat()}
        save(output / 'manifest.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(quarantine(database=DATABASE, control=CONTROL, control_sha=CONTROL_SHA,
          baseline=BASE / 'baseline-native-10000.jsonl', baseline_sha=BASELINE_SHA,
          output=args.output), ensure_ascii=True))


if __name__ == '__main__':
    main()
