"""Bounded update proofs through the current adapter, real guard and ranking."""
import json
from pathlib import Path

import pytest

from recommend_book.dataset_update_verification import verify_dataset_updates


def test_updates_reach_current_rows_and_ranking_without_rewriting_history(tmp_path):
    output = tmp_path / 'verification'
    report = verify_dataset_updates(output)
    phases = report['phases']
    assert {name: row['candidate_count'] for name, row in phases.items()} == {
        'baseline': 2, 'corrected': 2, 'merged': 1, 'merge_undone': 2,
        'permission_withdrawn': 1,
    }
    assert all(row['candidate_ids'] == row['ranked_ids'] for row in phases.values())
    assert report['correction']['initial_input_hash'] != report['correction']['corrected_input_hash']
    assert report['correction']['replay_counts'] == {'inserted': 0, 'updated': 0, 'unchanged': 1}
    assert report['correction']['source_versions_before_replay'] == report['correction']['source_versions_after_reopen'] == 3
    assert report['correction']['reopened_title'] == 'Alpha corrected'
    assert report['failed_ingest']['exception_rollback_preserved_versions_heads_and_events'] is True
    assert 'Alpha corrected' in phases['corrected']['titles']
    assert 'Alpha' in phases['baseline']['titles']
    assert all('Uncommitted title' not in phase['titles'] for phase in phases.values())
    assert report['merge_undo']['immutable_source_versions_preserved'] is True
    assert report['withdrawal']['same_database_and_guard_reused'] is True
    assert report['withdrawal']['guard_reasons'] == ['complete input revoked']
    assert report['withdrawal']['raw_and_database_unchanged'] is True
    assert report['withdrawal']['revoked_input_hash'] not in phases['permission_withdrawn']['input_hashes']
    assert phases['permission_withdrawn']['purpose_held_count'] == 1
    assert report['protected_snapshots_unchanged'] is True
    assert all(row['supported_count'] == 0 for row in phases.values())
    saved = json.loads((output / 'verification.json').read_text(encoding='utf-8'))
    assert saved == report
    for name, phase in phases.items():
        assert (output / name / 'recommendations.json').is_file()
        assert Path(phase['database_path']).is_file()


def test_verification_refuses_existing_namespace_without_overwriting(tmp_path):
    output = tmp_path / 'existing'
    output.mkdir()
    sentinel = output / 'keep.txt'
    sentinel.write_text('keep', encoding='utf-8')
    with pytest.raises(ValueError, match='new directory'):
        verify_dataset_updates(output)
    assert sentinel.read_text(encoding='utf-8') == 'keep'
