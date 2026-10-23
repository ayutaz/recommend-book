"""Offline regression checks for removing only an explicitly failed window."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/quarantine_scale20k_failed_window_v1.py'


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def fixture(tmp_path):
    spec = importlib.util.spec_from_file_location('quarantine20k_test', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    database = tmp_path / 'catalogue.sqlite'
    fields = ('qid', 'manifest_path', 'manifest_sha256', 'labels_json', 'provenance_json')
    rows = [dict(zip(fields, (qid, 'export.json', 'b' * 64, '[]', '{}')))
            for qid in ('Q1', 'Q2', 'Q3')]
    rows[0]['manifest_sha256'] = 'a' * 64
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE native_classifications(qid TEXT PRIMARY KEY,manifest_path TEXT, '
                   'manifest_sha256 TEXT,labels_json TEXT,provenance_json TEXT)')
        db.executemany('INSERT INTO native_classifications VALUES(?,?,?,?,?)',
                       [tuple(r.values()) for r in rows])
    baseline = tmp_path / 'baseline.jsonl'
    baseline.write_bytes(encoded({'qid': 'Q1', 'row_sha256': 'unused'}) + b'\n')
    report = {'failed_gate': {'passed': False}, 'export_sha256': 'b' * 64,
              'rows': rows[1:], 'control_path': 'control.json', 'control_sha256': 'c' * 64}
    def verify(*args):
        return report
    def lock(**kwargs):
        from contextlib import nullcontext
        return nullcontext()
    mod.writer_lock = lock
    kwargs = dict(database=database, control=tmp_path / 'control.json', control_sha='c' * 64,
                  baseline=baseline, baseline_sha=hashlib.sha256(baseline.read_bytes()).hexdigest(),
                  baseline_count=1, output=tmp_path / 'quarantine', verify=verify)
    return mod, kwargs, rows, report


def saved(database):
    with sqlite3.connect(database) as db:
        return db.execute('SELECT * FROM native_classifications ORDER BY qid').fetchall()


def test_quarantines_exact_failed_rows_with_reversible_backup(tmp_path):
    mod, kwargs, rows, report = fixture(tmp_path)
    result = mod.quarantine(**kwargs)
    assert saved(kwargs['database']) == [tuple(rows[0].values())]
    backup = json.loads((kwargs['output'] / 'rows-before.json').read_bytes())
    assert backup['rows'] == rows[1:]
    assert backup['failed_gate']['passed'] is False
    assert result['removed'] == 2 and result['remaining'] == 1
    assert result['baseline_qids_untouched'] is True


@pytest.mark.parametrize('failure', ['passed', 'baseline_overlap', 'changed_row', 'extra_export_row', 'baseline_pin'])
def test_refuses_changed_or_unbounded_targets_before_deletion(tmp_path, failure):
    mod, kwargs, rows, report = fixture(tmp_path)
    before = saved(kwargs['database'])
    if failure == 'passed':
        report['failed_gate']['passed'] = True
    elif failure == 'baseline_overlap':
        report['rows'] = [rows[0]]
    elif failure == 'changed_row':
        report['rows'][0] = {**report['rows'][0], 'labels_json': '["changed"]'}
    elif failure == 'extra_export_row':
        report['rows'] = report['rows'][:1]
    else:
        kwargs['baseline_sha'] = '0' * 64
    with pytest.raises(ValueError):
        mod.quarantine(**kwargs)
    assert saved(kwargs['database']) == before


def test_refuses_delete_triggers_with_other_row_side_effects(tmp_path):
    mod, kwargs, _, _ = fixture(tmp_path)
    with sqlite3.connect(kwargs['database']) as db:
        db.execute("CREATE TRIGGER collateral AFTER DELETE ON native_classifications "
                   "BEGIN DELETE FROM native_classifications WHERE qid='Q1'; END")
    before = saved(kwargs['database'])
    with pytest.raises(ValueError, match='trigger'):
        mod.quarantine(**kwargs)
    assert saved(kwargs['database']) == before


def test_quarantine_is_create_only_and_never_replays(tmp_path):
    mod, kwargs, _, _ = fixture(tmp_path)
    mod.quarantine(**kwargs)
    raw = (kwargs['output'] / 'rows-before.json').read_bytes()
    with pytest.raises(FileExistsError):
        mod.quarantine(**kwargs)
    assert (kwargs['output'] / 'rows-before.json').read_bytes() == raw
