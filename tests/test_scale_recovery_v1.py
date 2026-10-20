"""Fresh nonambiguous cohort and unconditional genuine review, no model calls."""
import ast
import importlib.util
from pathlib import Path
import sqlite3

import pytest

from test_scale_classification_v1 import record, write_records

ROOT = Path(__file__).resolve().parents[1]


def helper():
    path = ROOT / 'scripts/prepare_scale_recovery_v1.py'
    spec = importlib.util.spec_from_file_location('recovery_preparation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def genres(value):
    return {'rank': 'normal', 'mainsnak': {'datavalue': {'value': {'id': value}}}}


def test_ambiguous_romance_is_excluded_even_if_other_genre_is_safe(tmp_path):
    module = helper()
    source = record(tmp_path)
    assert module.source_genres(source) == {'fantasy'}
    source.extra['claims']['P136'].append(genres('Q19765983'))
    assert module.source_genres(source) == set()


def test_only_new_catalogue_admitted_scoped_sources_enter_candidate_snapshot(tmp_path):
    module = helper()
    sources = [record(tmp_path, qid) for qid in ('Q101', 'Q102', 'Q103', 'Q104')]
    sources[2].extra['claims']['P136'].append(genres('Q19765983'))
    path = tmp_path / 'sources.jsonl'
    write_records(path, sources)
    database = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE works(qid TEXT,display_allowed INT,recommendable INT)')
        db.executemany('INSERT INTO works VALUES(?,?,?)', [('Q101', 1, 1), ('Q102', 1, 1), ('Q103', 1, 1)])
    result, counts = module.select_candidate_records(path, database, {'Q101'}, limit=1,
                                                     preparer=module.original_preparer())
    assert [r.source_id for r in result] == ['Q102']
    with pytest.raises(ValueError, match='Insufficient fresh'):
        module.select_candidate_records(path, database, {'Q101', 'Q102'}, limit=1,
                                        preparer=module.original_preparer())


def test_original_source_preparer_is_versioned_only_for_fresh_namespace():
    module = helper()
    original = ROOT / 'scripts/prepare_scale_classification_v1.py'
    before = original.read_bytes()
    loaded = module.original_preparer()
    assert loaded.VERSION == 'scale-classification-source-v1'
    assert 'scale-recovery-' in loaded.prepare_sources.__code__.co_consts
    assert original.read_bytes() == before


def test_all_future_recovery_windows_have_unconditional_genuine_review():
    path = ROOT / 'scripts/run_scale_classification_v4.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    execute = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'execute_window')
    direct_calls = [node.value for node in execute.body if isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)]
    assert sum(call.func.id == 'review_initial_window' for call in direct_calls) == 1
