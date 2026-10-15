"""Offline, synthetic update demonstration using existing production adapters.

This module never reads a real corpus or starts classification. Empty-feature
rankings exercise candidate membership and metadata, not recommendation quality.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from recommend_book.database import DatasetDB
from recommend_book.jobs import canonical_json
from recommend_book.models import Identifier, SourceRecord, Title
from recommend_book.next_database import build_next_database, get_next_inputs, load_next_world_database
from recommend_book.recommendation.engine import rank_candidates
from recommend_book.rights import RightsRegistry
from recommend_book.usage_guard import UsageGuard, build_input_entry, model_hash, source_binding


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + '\n', encoding='utf-8')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _record(root, qid, title, revision):
    # Q1/Q2 and licensing pages below are explicitly synthetic fixtures.
    raw = root / 'raw' / (qid + '-' + revision + '.json')
    _save(raw, {'synthetic_fixture': True, 'entities': {qid: {
        'id': qid, 'labels': {'en': {'language': 'en', 'value': title}},
        'aliases': {}, 'claims': {},
    }}})
    return SourceRecord(source='wikidata', source_id=qid,
        source_url='https://www.wikidata.org/wiki/' + qid,
        retrieved_at='2026-10-15T00:00:00Z', provider_id='wikidata',
        record_kind='work', titles=[Title(value=title, language='en')],
        identifiers=[Identifier(source='wikidata', value=qid, scope='work')],
        work_kind='novel', raw_path=str(raw), raw_sha256=_sha(raw),
        rights_policy_id='wikidata-cc0')


def _versions(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute('SELECT version_id,record_json FROM source_records ORDER BY version_id').fetchall()


def _ledger(snapshot, official):
    database = snapshot / 'dataset.sqlite'
    registry = RightsRegistry.default()
    policy = registry.policies['wikidata-cc0']
    entries = {}
    for work in get_next_inputs(database)['bibliographic']:
        bindings = {ref: source_binding(database, ref) for ref in work.source_record_ids}
        entries[model_hash(work)] = build_input_entry(work, bindings)
    derivations = snapshot / 'derivations.jsonl'
    artifacts = {}
    for index, raw in enumerate(derivations.read_text(encoding='utf-8').splitlines(), 1):
        packet = json.loads(raw)
        artifacts[packet['value_sha256']] = {
            'kind': packet['kind'], 'raw_path': str(derivations),
            'raw_sha256': _sha(derivations), 'format': 'jsonl', 'line_number': index,
            'locator': '/payload', 'value_sha256': packet['value_sha256'],
            'originating_input_hashes': packet['originating_input_hashes'],
            'recipe': packet['recipe'],
        }
    value = {
        'version': 'usage-ledger-v3', 'synthetic_fixture': True,
        'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
        'official_conditions': {'wikidata-cc0': [{
            'raw_path': str(official), 'raw_sha256': _sha(official),
            'source_url': policy.evidence_urls[0],
            'verified_at': datetime.now(timezone.utc).isoformat(),
        }]},
        'inputs': entries, 'derived_artifacts': artifacts,
        'revoked_input_hashes': [], 'private_origins': {},
    }
    path = snapshot / 'usage-ledger.fixture.json'
    _save(path, value)
    return path, value


def _recommend(snapshot, guard, output):
    loaded = load_next_world_database(snapshot / 'dataset.sqlite', guard=guard, usage='local_storage')
    result = rank_candidates(loaded['rows'], [], limit=10)
    _save(output / 'recommendations.json', result)
    _save(output / 'world-rows.json', loaded)
    return {
        'database_path': str(snapshot / 'dataset.sqlite'),
        'candidate_count': len(loaded['rows']),
        'candidate_ids': sorted(row['work_id'] for row in loaded['rows']),
        'ranked_ids': sorted(row['work_id'] for row in result['ranking']),
        'titles': sorted(row['title'] for row in loaded['rows']),
        'input_hashes': sorted(key for row in loaded['rows'] for key in row['bibliographic_originating_input_hashes']),
        'purpose_held_count': loaded['audit']['purpose_held_count'],
        'supported_count': result['audit']['supported_count'],
    }


def verify_dataset_updates(output_dir):
    """Write a bounded fixture receipt into a new namespace; no network/model.

    Withdrawal here means revoking current use permission. It is deliberately
    distinct from undoing an ingestion, which may restore an earlier version.
    """
    root = Path(output_dir).resolve()
    if root.exists():
        raise ValueError('Verification output must be a new directory')
    root.mkdir(parents=True)
    official = root / 'fixture-cc0.html'
    official.write_text('Synthetic official-condition fixture: structured data CC0.', encoding='utf-8')
    source = root / 'source' / 'dataset.sqlite'
    alpha = _record(root, 'Q1', 'Alpha', 'v1')
    beta = _record(root, 'Q2', 'Beta', 'v1')
    with DatasetDB(source) as db:
        db.ingest([alpha, beta])

    phases = {}
    protected = {}

    def snapshot(name):
        destination = root / name / 'snapshot'
        build_next_database(source_database=source, output_dir=destination)
        path, value = _ledger(destination, official)
        guard = UsageGuard(path)
        phases[name] = _recommend(destination, guard, root / name)
        for artifact in destination.iterdir():
            if artifact.is_file() and artifact != path:
                protected[str(artifact)] = _sha(artifact)
        return destination, guard, path, value

    baseline, _, _, _ = snapshot('baseline')
    initial = next(w for w in get_next_inputs(baseline / 'dataset.sqlite')['bibliographic'] if w.title == 'Alpha')
    corrected = _record(root, 'Q1', 'Alpha corrected', 'v2')
    with DatasetDB(source) as db:
        update = db.ingest([corrected])
        if update['updated'] != 1:
            raise AssertionError('Fixture correction was not a source update')
        before_replay = db.stats()['source_records']
        replay = db.ingest([corrected])
        replay_counts = {key: replay[key] for key in ('inserted', 'updated', 'unchanged')}
    with DatasetDB(source) as db:
        reopened = next(w for w in db.work_inputs() if w.work_id == initial.work_id)
        reopened_count = db.stats()['source_records']
        before_failed_ingest = [tuple(row) for row in db.connection.execute('SELECT * FROM ingestion_events ORDER BY event_id')]
        try:
            db.ingest([corrected.model_copy(update={'titles': [Title(value='Uncommitted title')]}), {}])
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed second record must fail the ingestion transaction')
        rolled_back_events = [tuple(row) for row in db.connection.execute('SELECT * FROM ingestion_events ORDER BY event_id')]
    with DatasetDB(source) as db:
        rollback_preserved = (
            db.stats()['source_records'] == reopened_count
            and next(w for w in db.work_inputs() if w.work_id == initial.work_id).title == reopened.title
            and rolled_back_events == before_failed_ingest
        )
    corrected_snapshot, _, _, _ = snapshot('corrected')
    corrected_input = next(w for w in get_next_inputs(corrected_snapshot / 'dataset.sqlite')['bibliographic']
                           if w.work_id == initial.work_id)

    versions = _versions(source)
    with DatasetDB(source) as db:
        ids = [w.work_id for w in db.work_inputs()]
        decision = db.merge_work(ids[0], ids[1], reason='Intentional erroneous synthetic merge to test undo',
                                 reviewer='synthetic-fixture-only')
    snapshot('merged')
    with DatasetDB(source) as db:
        db.undo(decision)
    current, guard, ledger_path, ledger = snapshot('merge_undone')
    current_input = next(w for w in get_next_inputs(current / 'dataset.sqlite')['bibliographic']
                         if w.work_id == initial.work_id)
    revoked = model_hash(current_input)
    raw_and_db = {str(path): _sha(path) for path in [source, current / 'dataset.sqlite', *sorted((root / 'raw').iterdir())]}
    before_allowed = guard.check_input(current_input, 'local_storage')
    if not before_allowed.allowed:
        raise AssertionError('Fixture must be admitted before revocation: ' + repr(before_allowed.reasons))
    ledger['revoked_input_hashes'] = [revoked]
    _save(ledger_path, ledger)
    after_denied = guard.check_input(current_input, 'local_storage')
    if after_denied.allowed:
        raise AssertionError('Same guard must observe current-ledger revocation')
    phases['permission_withdrawn'] = _recommend(current, guard, root / 'permission_withdrawn')

    report = {
        'version': 'dataset-update-verification-v1', 'synthetic_fixture': True,
        'human_gold': False, 'human_quality_verified': False,
        'external_requests': 0, 'model_started': False,
        'phases': phases,
        'correction': {'initial_input_hash': model_hash(initial), 'corrected_input_hash': model_hash(corrected_input),
            'replay_counts': replay_counts, 'source_versions_before_replay': before_replay,
            'source_versions_after_reopen': reopened_count, 'reopened_title': reopened.title},
        'failed_ingest': {'exception_rollback_preserved_versions_heads_and_events': rollback_preserved,
            'scenario': 'valid first update followed by invalid second record in one ingestion transaction'},
        'merge_undo': {'decision_id': decision, 'immutable_source_versions_preserved': _versions(source) == versions},
        'withdrawal': {'meaning': 'current usage permission revoked; immutable history retained',
            'same_database_and_guard_reused': True, 'revoked_input_hash': revoked,
            'guard_reasons': after_denied.reasons,
            'raw_and_database_unchanged': all(_sha(path) == digest for path, digest in raw_and_db.items())},
        'protected_snapshots_unchanged': all(_sha(path) == digest for path, digest in protected.items()),
        'limitations': [
            'All inputs and official-condition artifacts are synthetic fixtures, not acquired real works or rights evidence.',
            'Empty-feature rankings verify candidate membership and metadata; scores are unsupported.',
            'No content score recalculation or invalidation of old content classifications is demonstrated.',
            'Close/reopen verifies persistence, not crash recovery or interrupted transaction restart.',
            'Permission withdrawal does not physically withdraw a corrected source or delete immutable history.',
            'Existing snapshots remain historical; updates require newly built derived snapshots.',
        ],
    }
    _save(root / 'verification.json', report)
    return report
