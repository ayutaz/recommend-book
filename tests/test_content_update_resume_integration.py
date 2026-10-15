"""Synthetic offline integration; model responses are explicitly mocked."""
import json
import sqlite3

import pytest


@pytest.fixture(scope='module')
def verified(tmp_path_factory):
    from recommend_book.content_update_verification import verify_content_updates
    root = tmp_path_factory.mktemp('content-update-parent') / 'receipt'
    return root, verify_content_updates(root)


def test_changed_evidence_invalidates_old_content_and_requires_fresh_run(verified):
    root, report = verified
    change = report['content_update']
    assert change['title_unchanged'] and change['work_id_unchanged']
    assert change['evidence_text_changed']
    assert change['old_input_hash'] != change['new_input_hash']
    assert change['old_job_key'] != change['new_job_key']
    assert change['stale_result_rejected'] and change['stale_cache_rejected']
    assert change['old_namespace_rejected']
    assert change['old_features'] == {'genre.mystery': 1.0}
    assert change['rebuilt_unclassified_features'] == {}
    assert change['new_features'] == {}
    assert report['protected_artifacts_unchanged']
    assert json.loads((root / 'verification.json').read_text(encoding='utf-8')) == report


def test_real_kill_then_resume_reuses_durable_adopted_shard_without_duplicates(verified):
    root, report = verified
    resume = report['interruption_resume']
    assert resume['child_process_terminated'] and resume['terminated_exit_code'] != 0
    assert resume['boundary'] == 'after_atomic_shard_adoption_before_next_shard'
    assert resume['completed_before_kill'] == 1 and resume['pending_shard_unstarted_before_kill']
    assert resume['completed_after_resume'] == 2 and resume['reused_shards'] == 1
    assert resume['executed_shards_after_resume'] == 1
    assert resume['new_cli_response_invocations_after_resume'] == 1
    assert resume['first_shard_artifacts_unchanged']
    assert resume['unique_result_count'] == resume['result_count'] == 2
    assert resume['attempt_counts'] == [1, 1]
    assert len(set(resume['display_ids'])) == 2
    assert resume['duplicate_adoptions'] == 0
    assert resume['current_permission_rechecked_on_resume']
    assert resume['mixed_run_importer_repaired'] is False
    for index in range(2):
        assert (root / 'resume' / ('shard-' + str(index)) / 'adopted/dataset.sqlite').exists()


def test_current_permission_withdrawal_removes_content_from_display_and_ranking(verified):
    root, report = verified
    revoke = report['permission_withdrawal']
    assert revoke['same_database_and_guard_reused']
    assert revoke['before_display_count'] == 2 and revoke['after_display_count'] == 0
    assert revoke['before_ranking_count'] == 2 and revoke['after_ranking_count'] == 0
    assert revoke['unrelated_rows_held_by_frozen_ledger_change'] == 1
    assert revoke['revoked_work_id'] not in revoke['after_display_ids']
    assert revoke['revoked_work_id'] not in revoke['after_ranking_ids']
    assert revoke['database_raw_and_results_unchanged']
    assert revoke['cache_rejected_before_discovery']
    assert json.loads((root / 'after-withdrawal/display.json').read_text(encoding='utf-8'))['rows'] == []
    assert json.loads((root / 'after-withdrawal/recommendations.json').read_text(encoding='utf-8'))['ranking'] == []
    assert json.loads((root / 'resume/display.json').read_text(encoding='utf-8'))['rows'] == []
    with sqlite3.connect(root / 'baseline' / 'adopted' / 'dataset.sqlite') as db:
        assert db.execute('SELECT count(*) FROM next_classification_inputs').fetchone()[0] == 2
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_worker_finished_before_adoption_restarts_only_unadopted_attempt(verified):
    root, report = verified
    recovery = report['unadopted_attempt_recovery']
    assert recovery['boundary'] == 'worker_finished_before_shard_adoption'
    assert recovery['child_process_terminated'] and recovery['terminated_exit_code'] != 0
    assert recovery['worker_exit_observed_before_kill']
    assert recovery['worker_exit_code_before_kill'] == 0
    assert recovery['unadopted_completed_results_before_kill'] == 1
    assert recovery['orphan_attempt_preserved']
    assert recovery['first_shard_artifacts_unchanged']
    assert recovery['second_shard_attempt_count'] == 2
    assert recovery['reused_shards'] == 1 and recovery['executed_shards_after_resume'] == 1
    assert recovery['new_cli_response_invocations_after_resume'] == 1
    assert recovery['attempt_run_ids_are_distinct']
    assert recovery['second_shard_attempt_run_ids'][0] != recovery['adopted_run_ids'][1]
    assert recovery['second_shard_attempt_run_ids'][1] == recovery['adopted_run_ids'][1]
    assert recovery['total_cli_response_invocations'] == 3
    assert recovery['adopted_original_run_ids_preserved']
    assert recovery['result_count'] == recovery['unique_result_count'] == 2
    assert recovery['duplicate_adoptions'] == 0
    assert len(set(recovery['display_ids'])) == 2
    assert (root / 'resume-before-adoption/shard-1/attempts/attempt-0001/run/manifest.json').exists()
    assert (root / 'resume-before-adoption/shard-1/attempts/attempt-0002/run/manifest.json').exists()


def test_withdrawal_rebuilds_production_html_without_stale_cards(verified):
    root, report = verified
    before = (root / 'before-withdrawal/recommendations.html').read_text(encoding='utf-8')
    after = (root / 'after-withdrawal/recommendations.html').read_text(encoding='utf-8')
    assert before.count('<article ') == 2
    assert '<article ' not in after
    assert 'Synthetic book Q101' in before and 'Synthetic book Q101' not in after
    assert '<article ' not in (root / 'resume/recommendations.html').read_text(encoding='utf-8')
    assert report['display_renderer']['production_renderer_used']
    assert len(report['display_renderer']['sha256']) == 64


def test_fixed_shard_loader_rejects_duplicate_adopted_works(verified):
    from recommend_book.content_update_verification import load_fixed_shards
    root, _ = verified
    with pytest.raises(ValueError, match='Duplicate adopted work'):
        load_fixed_shards([root / 'updated', root / 'updated'])


def test_fixed_shard_reuse_rejects_changed_input_before_any_execution(verified):
    from recommend_book.content_update_verification import load_fixed_shards
    root, _ = verified
    inputs = root / 'updated/inputs/ordered-inputs.jsonl'
    original = inputs.read_bytes()
    try:
        inputs.write_bytes(original + b'\n')
        with pytest.raises(ValueError, match='Fixed shard input/source/context changed'):
            load_fixed_shards([root / 'updated'])
    finally:
        inputs.write_bytes(original)


def test_verifier_never_overwrites_existing_namespace(verified):
    from recommend_book.content_update_verification import verify_content_updates
    root, _ = verified
    before = (root / 'verification.json').read_bytes()
    with pytest.raises(ValueError, match='new directory'):
        verify_content_updates(root)
    assert (root / 'verification.json').read_bytes() == before
