import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from recommend_book.models import Identifier, SourceRecord, Title
from recommend_book.scale_catalogue_v1 import ScaleCatalogue


def save(path, payload):
    path.write_text(json.dumps(payload), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native_manifest(path, *, rows=(), artifacts=(), version='scale-native-classification-export-v1'):
    preparation = path.parent / f'{path.stem}-preparation'
    run_manifest = preparation / 'runtime/data/autonomous-next-v3/classification/fixture/run/manifest.json'
    run_manifest.parent.mkdir(parents=True, exist_ok=True)
    run_sha = save(run_manifest, {'run_id': 'fixture-run'})
    terminal = preparation / 'terminal-fixture'
    terminal.mkdir(exist_ok=True)
    coordinator = preparation / 'coordinator.py'
    coordinator.write_text('# fixture bytes only; never executed\n', encoding='utf-8')
    coordinator_sha = hashlib.sha256(coordinator.read_bytes()).hexdigest()
    request_sha = save(terminal / 'request.json', {'version': 'native-snapshot-terminal-request-v1',
                                                'coordinator_path': str(coordinator), 'coordinator_sha256': coordinator_sha})
    exit_sha = save(terminal / 'process-exit.json', {'version': 'native-snapshot-terminal-exit-v1',
                                                 'process_pid': 1234, 'actual_exit_code': 0, 'process_exit_observed': True})
    proof_path = terminal / 'terminal-proof.json'
    proof_sha = save(proof_path, {'schema_version': 'classification-terminal-proof-v1', 'run_id': 'fixture-run',
                                 'manifest_sha256': run_sha, 'exit_code': 0, 'process_exit_observed': True,
                                 'process_pid': 1234, 'parent_request_sha256': request_sha,
                                 'parent_exit_observation_sha256': exit_sha})
    return save(path, {'version': version,
                       'runtime_preparation': str(preparation), 'run_manifest': str(run_manifest),
                       'coordinator_sha256': coordinator_sha,
                       'artifact_sha256': {**{item['path']: item['sha256'] for item in artifacts},
                                           str(run_manifest): run_sha, str(proof_path): proof_sha},
                       'verified_projection': {'rows': list(rows)}})


def native_pins(manifest):
    return [{'path': path, 'sha256': sha} for path, sha in json.loads(manifest.read_bytes())['artifact_sha256'].items()]


def claim(qid, rank='normal'):
    return {'rank': rank, 'mainsnak': {'datavalue': {'value': {'id': qid}}}}


@pytest.fixture
def catalogue(tmp_path):
    policy = tmp_path / 'license.html'
    policy.write_text('Wikidata structured main namespace data CC0', encoding='utf-8')
    conditions = tmp_path / 'conditions.json'
    sha = save(conditions, {
        'version': 'scale-source-conditions-v1', 'source': 'wikidata', 'policy_id': 'wikidata-cc0',
        'metadataonly_scope': True, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'policy_snapshot': {'path': str(policy), 'sha256': hashlib.sha256(policy.read_bytes()).hexdigest()},
        'allowed_domains': ['www.wikidata.org', 'query.wikidata.org'],
        'usages': dict.fromkeys(['acquire', 'local_storage', 'display', 'external_llm', 'redistribution', 'commercial'], 'allowed'),
    })
    return ScaleCatalogue(tmp_path / 'catalogue.sqlite', source_conditions_path=conditions,
                          source_conditions_sha256=sha)


def record(tmp_path, qid='Q100', *, genre='Q200', extra_claims=None, title=None):
    claims = {'P31': [claim('Q8261')], 'P407': [claim('Q1860')]}
    if genre:
        claims['P136'] = [claim(genre)]
    claims.update(extra_claims or {})
    title = title or f'Novel {qid}'
    entities = {qid: {'id': qid, 'labels': {'en': {'value': title, 'language': 'en'}}, 'claims': claims}}
    if genre:
        entities[genre] = {'id': genre, 'labels': {'en': {'value': 'Fantasy', 'language': 'en'}}}
    raw = tmp_path / f'{qid}.json'
    sha = save(raw, {'entities': entities})
    return SourceRecord(source='wikidata', source_id=qid, provider_id='wikidata', record_kind='work',
                        source_url=f'https://www.wikidata.org/wiki/{qid}', retrieved_at=datetime.now(timezone.utc).isoformat(),
                        titles=[Title(value=title, language='en')], raw_path=str(raw), raw_sha256=sha,
                        rights_policy_id='wikidata-cc0')


def test_ingest_resume_query_and_native_genre_are_separate(catalogue, tmp_path):
    rows = [record(tmp_path, f'Q{i}') for i in range(100, 105)]
    result = catalogue.ingest(rows + [rows[0]], chunk_size=2)
    assert result['inserted'] == 5 and result['unchanged'] == 1 and result['failed'] == 0
    assert catalogue.ingest(rows)['unchanged'] == 5
    stats = catalogue.catalogue_stats()
    assert stats['works'] == stats['feature_bearing_works'] == 5
    assert stats['strict72_works'] == stats['llm_works'] == 0
    page = catalogue.search_catalogue(query='Q10', genre='Q200', language='en', page_size=2)
    assert page['total'] == 5 and len(page['items']) == 2 and page['pages'] == 3
    detail = catalogue.work_detail('wd:Q100')
    assert detail['genres'] == [{'qid': 'Q200', 'label': 'Fantasy'}]
    assert detail['strict72_features'] == detail['llm_features'] == []
    assert detail['provenance']['raw_sha256'] == rows[0].raw_sha256
    assert detail['source_record_json']['source_id'] == 'Q100'


@pytest.mark.parametrize('description,held', [
    ('novel series by Victor Dixen', True),
    ('série de romans japonaise', True),
    ('dylogie de roman de Victor Dixen', True),
    ('fourth novel in the Inhibitor Cycle, the main novel series within the Revelation Space universe', False),
    ("3rd volume of the Japanese light novel series I'm in Love with the Villainess", False),
])
def test_collective_descriptions_do_not_masquerade_as_individual_novels(catalogue, tmp_path, description, held):
    source = record(tmp_path)
    raw = Path(source.raw_path)
    payload = json.loads(raw.read_bytes())
    payload['entities']['Q100']['descriptions'] = {'en': {'language': 'en', 'value': description}}
    source.raw_sha256 = save(raw, payload)
    result = catalogue.ingest([source])
    assert result['failed'] == int(held)
    assert catalogue.catalogue_stats()['searchable_works'] == int(not held)


def test_failure_isolation_scope_and_raw_tamper(catalogue, tmp_path):
    edition = record(tmp_path, 'Q110', extra_claims={'P629': [claim('Q100')]})
    conflict = record(tmp_path, 'Q111', extra_claims={'P7937': [claim('Q49084')]})
    deprecated = record(tmp_path, 'Q112', extra_claims={'P31': [claim('Q8261', 'deprecated')]})
    forged = record(tmp_path, 'Q113').model_copy(update={'titles': [Title(value='Forged title')]})
    good = record(tmp_path, 'Q114')
    result = catalogue.ingest([edition, conflict, deprecated, forged, good])
    assert result['failed'] == 4 and result['inserted'] == 1
    assert catalogue.work_detail('Q110') is None
    (tmp_path / 'Q114.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='hash changed'):
        catalogue.work_detail('Q114')


def test_pinned_rights_hold_and_linked_label_proof(catalogue, tmp_path):
    row = record(tmp_path)
    raw_path = tmp_path / 'Q100.json'
    data = json.loads(raw_path.read_bytes())
    del data['entities']['Q200']
    sha = save(raw_path, data)
    row = row.model_copy(update={'raw_sha256': sha, 'extra': {'linked_label_provenance': {
        'Q200': {'label': 'Forged fantasy', 'raw_path': str(raw_path), 'raw_sha256': sha}}}})
    assert catalogue.ingest([row])['failed'] == 1
    assert catalogue.ingest([record(tmp_path, 'Q101')])['inserted'] == 1
    conditions = tmp_path / 'conditions.json'
    data = json.loads(conditions.read_bytes())
    data['usages']['display'] = 'hold'
    save(conditions, data)
    with pytest.raises(ValueError, match='conditions hash changed'):
        catalogue.search_catalogue()


def test_read_exclusion_reasons_and_no_personal_claim(catalogue, tmp_path):
    catalogue.ingest([record(tmp_path, f'Q{i}') for i in range(100, 104)] + [record(tmp_path, 'Q105', genre=None)])
    catalogue.set_read_status('Q101', 'read')
    assert catalogue.search_catalogue(read_filter='read')['total'] == 1
    assert catalogue.search_catalogue(read_filter='exclude_read')['total'] == 4
    result = catalogue.similar_works('Q100')
    assert result['mode'] == 'source_similarity' and result['personalized'] is False
    assert {item['qid'] for item in result['items']} == {'Q102', 'Q103'}
    assert result['items'][0]['reasons'][0]['kind'] == 'source_genre'
    assert catalogue.similar_works('Q105')['mode'] == 'insufficient_evidence'
    reopened = ScaleCatalogue(catalogue.db_path)
    assert reopened.work_detail('Q101')['read_status'] == 'read'


def test_display_hold_stores_but_does_not_serve(tmp_path, catalogue):
    conditions = tmp_path / 'conditions.json'
    data = json.loads(conditions.read_bytes())
    data['usages']['display'] = 'hold'
    sha = save(conditions, data)
    held = ScaleCatalogue(tmp_path / 'held.sqlite', source_conditions_path=conditions, source_conditions_sha256=sha)
    assert held.ingest([record(tmp_path)])['inserted'] == 1
    assert held.catalogue_stats()['held_works'] == 1
    assert held.search_catalogue()['items'] == []
    assert held.work_detail('Q100') is None


def test_source_declared_count_includes_novels_without_genre(catalogue, tmp_path):
    catalogue.ingest([record(tmp_path, 'Q100'), record(tmp_path, 'Q101', genre=None)])
    stats = catalogue.catalogue_stats()
    assert stats['source_declared_works'] == 2
    assert stats['source_genre_works'] == stats['recommendable_works'] == 1


@pytest.mark.parametrize('tamper', ['labels', 'confidence_type', 'qid', 'stripped_provenance'])
def test_native_sql_tamper_cannot_change_authenticated_labels_or_drop_required_pins(catalogue, tmp_path, tamper):
    catalogue.ingest([record(tmp_path), record(tmp_path, 'Q101')])
    dependency = tmp_path / 'required-proof.bin'
    dependency.write_bytes(b'required')
    pin = {'path': str(dependency), 'sha256': hashlib.sha256(dependency.read_bytes()).hexdigest()}
    labels = [{'label_id': 'genre.fantasy', 'axis': 'genre', 'decision': 'present', 'scope': 'work', 'raw_confidence': 1.0}]
    manifest = tmp_path / 'authentic-export.json'
    sha = native_manifest(manifest, rows=[{'qid': 'Q100', 'work_id': 'wd:Q100', 'admitted_labels': labels}], artifacts=[pin])
    proof = {'artifacts': native_pins(manifest)}
    with sqlite3.connect(catalogue.db_path) as db:
        db.execute('INSERT INTO native_classifications VALUES(?,?,?,?,?)',
                   ('Q100', str(manifest), sha, json.dumps(labels), json.dumps(proof)))
    assert catalogue.work_detail('Q100')['llm_features'] == labels
    assert catalogue.catalogue_stats()['llm_works'] == 1
    with sqlite3.connect(catalogue.db_path) as db:
        if tamper == 'labels':
            forged = [{'label_id': 'theme.revenge', 'axis': 'theme', 'decision': 'present', 'scope': 'work'}]
            db.execute('UPDATE native_classifications SET labels_json=?', (json.dumps(forged),))
        elif tamper == 'confidence_type':
            forged = [dict(labels[0], raw_confidence=True)]
            db.execute('UPDATE native_classifications SET labels_json=?', (json.dumps(forged),))
        elif tamper == 'qid':
            db.execute("UPDATE native_classifications SET qid='Q101'")
        else:
            db.execute('UPDATE native_classifications SET provenance_json=?',
                       (json.dumps({'artifacts': [{'path': str(manifest), 'sha256': sha}]}),))
    with pytest.raises(ValueError, match='native'):
        catalogue.work_detail('Q101' if tamper == 'qid' else 'Q100')
    with pytest.raises(ValueError, match='native'):
        catalogue.catalogue_stats()


def test_bulk_writer_keeps_committed_catalogue_readable(catalogue, tmp_path):
    catalogue.ingest([record(tmp_path, 'Q100')])
    writer = sqlite3.connect(catalogue.db_path)
    reader = sqlite3.connect(catalogue.db_path, timeout=0)
    try:
        writer.execute('BEGIN EXCLUSIVE')
        writer.execute("UPDATE works SET title='Pending' WHERE qid='Q100'")
        assert reader.execute("SELECT title FROM works WHERE qid='Q100'").fetchone()[0] == 'Novel Q100'
    finally:
        writer.rollback()
        reader.close()
        writer.close()


def test_same_titles_keep_distinct_qids_and_immutable_versions(catalogue, tmp_path):
    first = record(tmp_path, 'Q100', title='Same title')
    other = record(tmp_path, 'Q101', title='Same title')
    assert catalogue.ingest([first, other])['inserted'] == 2
    assert catalogue.search_catalogue(query='Same title')['total'] == 2
    catalogue.set_read_status('Q100', 'read')
    new_raw = tmp_path / 'new_raw'
    new_raw.mkdir()
    new = record(new_raw, 'Q100', title='New title')
    assert catalogue.ingest([new])['updated'] == 1
    assert catalogue.work_detail('Q100')['read_status'] == 'read'
    with sqlite3.connect(catalogue.db_path) as db:
        assert db.execute('SELECT count(*) FROM source_versions WHERE qid=?', ('Q100',)).fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute("UPDATE source_versions SET raw_sha256='forged'")


def test_linked_genre_raw_is_rechecked_on_display(catalogue, tmp_path):
    row = record(tmp_path)
    raw = json.loads((tmp_path / 'Q100.json').read_bytes())
    linked = tmp_path / 'genre.json'
    linked_sha = save(linked, {'entities': {'Q200': raw['entities'].pop('Q200')}})
    raw_sha = save(tmp_path / 'Q100.json', raw)
    row = row.model_copy(update={'raw_sha256': raw_sha, 'extra': {'linked_label_provenance': {
        'Q200': {'label': 'Fantasy', 'raw_path': str(linked), 'raw_sha256': linked_sha}}}})
    assert catalogue.ingest([row])['inserted'] == 1
    linked.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='raw hash changed'):
        catalogue.search_catalogue()


def test_snapshot_path_cannot_be_reused_for_changed_bytes(catalogue, tmp_path):
    first = record(tmp_path)
    catalogue.ingest([first])
    replacement = record(tmp_path, title='New title overwrote old snapshot')
    result = catalogue.ingest([replacement])
    assert result['failed'] == 1 and 'snapshot path reused' in result['failures'][0]['error']


def test_interrupted_stream_resumes_only_after_committed_chunk(catalogue, tmp_path):
    rows = [record(tmp_path, f'Q{i}') for i in range(100, 104)]

    def interrupted():
        yield from rows[:3]
        raise RuntimeError('process interrupted')

    with pytest.raises(RuntimeError, match='interrupted'):
        catalogue.ingest(interrupted(), chunk_size=2)
    assert catalogue.catalogue_stats()['works'] == 2
    result = catalogue.ingest(rows, chunk_size=2)
    assert result['inserted'] == result['unchanged'] == 2
    assert catalogue.catalogue_stats()['works'] == 4


def test_authenticated_native_import_keeps_raw_and_admitted_counts_separate(catalogue, tmp_path, monkeypatch):
    catalogue.ingest([record(tmp_path, 'Q100'), record(tmp_path, 'Q101')])
    manifest = tmp_path / 'manifest.json'
    label = {'label_id': 'genre.fantasy', 'axis': 'genre', 'decision': 'present', 'scope': 'work',
             'confidence': 0.73, 'relevance': None, 'evidence': []}
    manifest_sha = native_manifest(manifest, rows=[{'qid': 'Q100', 'work_id': 'wd:Q100', 'admitted_labels': [label]},
                                                 {'qid': 'Q101', 'work_id': 'wd:Q101', 'admitted_labels': []}])
    proof = {'artifacts': native_pins(manifest)}
    from recommend_book import scale_classification_v1
    monkeypatch.setattr(scale_classification_v1, 'verify_catalogue_classification', lambda *args: [
        {'qid': 'Q100', 'work_id': 'wd:Q100', 'input_work_id': 'old_work_100', 'labels': [label], 'provenance': proof},
        {'qid': 'Q101', 'work_id': 'wd:Q101', 'input_work_id': 'old_work_101', 'labels': [], 'provenance': proof}])
    result = catalogue.import_classifications(manifest, manifest_sha)
    assert result['raw_classified'] == 2 and result['adopted'] == 1
    assert catalogue.catalogue_stats()['llm_works'] == 1
    assert catalogue.catalogue_stats()['raw_classified_works'] == 2
    assert catalogue.catalogue_stats()['eligible_classified_works'] == 2
    assert catalogue.catalogue_stats()['feature_bearing_works'] == 2
    assert catalogue.work_detail('old_work_100')['llm_features'] == [label]
    assert catalogue.work_detail('Q100')['llm_features'][0]['relevance'] is None
    with catalogue._connect() as db:
        db.execute("UPDATE works SET display_allowed=0 WHERE qid='Q101'")
    assert catalogue.catalogue_stats()['raw_classified_works'] == 2
    assert catalogue.catalogue_stats()['eligible_classified_works'] == 1
    save(manifest, {'tampered': True})
    with pytest.raises(ValueError, match='hash changed'):
        catalogue.work_detail('Q100')


def test_ambiguous_romance_hold_preserves_native_proof_and_other_features(catalogue, tmp_path):
    catalogue.ingest([record(tmp_path, f'Q{i}', genre=None) for i in range(100, 103)])
    ambiguous = {'label_id': 'genre.romance', 'axis': 'genre', 'evidence': [{'quote': 'romantic fiction'}]}
    clear = dict(ambiguous, evidence=[{'quote': 'romantic fiction'}, {'quote': 'a romance novel'}])
    fantasy = {'label_id': 'genre.fantasy', 'axis': 'genre', 'evidence': [{'quote': 'fantasy novel'}]}
    original = [[ambiguous], [ambiguous, fantasy], [clear]]
    manifest = tmp_path / 'romance.json'
    sha = native_manifest(manifest, rows=[{'qid': f'Q{i}', 'work_id': f'wd:Q{i}', 'admitted_labels': labels}
                                         for i, labels in zip(range(100, 103), original)])
    with catalogue._connect() as db:
        db.executemany('INSERT INTO native_classifications VALUES(?,?,?,?,?)',
                       [(f'Q{i}', str(manifest), sha, json.dumps(labels), json.dumps({'artifacts': native_pins(manifest)}))
                        for i, labels in zip(range(100, 103), original)])
    assert catalogue.work_detail('Q100')['llm_features'] == []
    assert catalogue.work_detail('Q100')['llm_held_features'][0]['reason'] == 'ambiguous_romantic_fiction_citation'
    assert catalogue.work_detail('Q101')['llm_features'] == [fantasy]
    assert catalogue.work_detail('Q102')['llm_features'] == [clear]
    stats = catalogue.catalogue_stats()
    assert stats['raw_classified_works'] == 3
    assert stats['llm_works'] == stats['feature_bearing_works'] == 2
    assert stats['semantic_held_labels'] == 2
    with catalogue._connect() as db:
        assert json.loads(db.execute("SELECT labels_json FROM native_classifications WHERE qid='Q100'").fetchone()[0]) == [ambiguous]
        db.execute("UPDATE native_classifications SET labels_json='[]' WHERE qid='Q100'")
    with pytest.raises(ValueError, match='native catalogue identity or labels differ'):
        catalogue.work_detail('Q100')


def test_legacy_crosswalk_uses_authority_not_same_title(catalogue, tmp_path):
    from recommend_book.database import DatasetDB
    from recommend_book.scale_catalogue_v1 import export_legacy_crosswalk
    row = record(tmp_path, 'Q100', title='A source title')
    row.identifiers = [Identifier(source='wikidata', value='Q100', scope='work')]
    source_path = tmp_path / 'legacy.sqlite'
    source = DatasetDB(source_path)
    source.ingest([row])
    source.close()
    with sqlite3.connect(source_path) as db:
        old_id = db.execute("SELECT entity_id FROM external_ids WHERE source='wikidata'").fetchone()[0]
    delivery = tmp_path / 'delivery.json'
    save(delivery, {'ranking': [{'work_id': old_id, 'title': 'Unrelated display title is ignored'}]})
    snapshot = tmp_path / 'crosswalk.json'
    receipt = export_legacy_crosswalk(source_path, delivery, snapshot)
    assert receipt['mapped'] == 1
    catalogue.ingest([row])
    assert catalogue.import_crosswalk(snapshot, receipt['sha256'])['imported'] == 1
    assert catalogue.work_detail(old_id)['qid'] == 'Q100'
    assert catalogue.work_detail(old_id)['llm_features'] == []


def test_ingest_failures_retain_bounded_qid_for_models_and_json_dicts(catalogue, tmp_path):
    model = record(tmp_path, 'Q100').model_copy(update={'titles': [Title(value='Forged title')]})
    dictionary = record(tmp_path, 'Q101').model_dump(mode='json')
    dictionary['titles'] = [{'value': 'Forged title', 'language': 'en', 'kind': 'preferred'}]
    malformed = [{'source_id': 'Q' + '1' * 1000}, {'source_id': ['Q102']}, None]
    result = catalogue.ingest([model, dictionary, *malformed])
    assert result['failed'] == 5
    assert [failure['qid'] for failure in result['failures']] == ['Q100', 'Q101', None, None, None]
    with sqlite3.connect(catalogue.db_path) as db:
        assert [row[0] for row in db.execute('SELECT qid FROM ingestion_failures ORDER BY id')] == [
            'Q100', 'Q101', None, None, None]


@pytest.mark.parametrize('version,module_name', [
    ('scale-native-classification-export-v1', 'recommend_book.scale_classification_v1'),
    ('scale-content-native-classification-export-v1', 'recommend_book.scale_content_classification_v1'),
])
def test_verifier_admitted_theme_and_structure_are_stored_unchanged(catalogue, tmp_path, monkeypatch, version, module_name):
    import importlib
    import sys
    from types import ModuleType
    if version == 'scale-content-native-classification-export-v1':
        verifier = ModuleType(module_name)
        monkeypatch.setitem(sys.modules, module_name, verifier)
    else:
        verifier = importlib.import_module(module_name)
    from recommend_book.classification.validation import load_taxonomy
    catalogue.ingest([record(tmp_path)])
    manifest = tmp_path / 'reviewed-manifest.json'
    labels = [{'label_id': next(row['label_id'] for row in load_taxonomy()['labels'] if row['axis'] == axis),
               'axis': axis, 'decision': 'present', 'scope': 'work', 'raw_confidence': 0.71,
               'relevance': None, 'evidence': []} for axis in ('theme', 'structure')]
    projection = {'qid': 'Q100', 'work_id': 'wd:Q100', 'admitted_labels': labels}
    if version == 'scale-content-native-classification-export-v1':
        projection['source_attributions'] = [{'source_url': 'https://en.wikipedia.org/w/index.php?oldid=123',
                                             'revision': 123, 'display_allowed': True}]
    manifest_sha = native_manifest(manifest, version=version, rows=[projection])
    admitted = {'qid': 'Q100', 'work_id': 'wd:Q100', 'labels': labels,
                'provenance': {'artifacts': native_pins(manifest)}}
    calls = []
    def verify(path, digest):
        calls.append((path, digest))
        return [admitted]
    monkeypatch.setattr(verifier, 'verify_catalogue_classification', verify, raising=False)
    assert catalogue.import_classifications(manifest, manifest_sha)['adopted'] == 1
    assert calls == [(str(manifest.resolve()), manifest_sha)]
    assert catalogue.work_detail('Q100')['llm_features'] == labels
    if 'source_attributions' in projection:
        with catalogue._connect() as db:
            forged = dict(admitted['provenance'], source_attributions=[{'source_url': 'FORGED-SQL-SOURCE'}])
            db.execute('UPDATE native_classifications SET provenance_json=?', (json.dumps(forged),))
        assert catalogue.work_detail('Q100')['content_sources'] == projection['source_attributions']
    labels[0]['axis'] = 'genre'
    with pytest.raises(ValueError, match='unadmitted labels'):
        catalogue.import_classifications(manifest, manifest_sha)
    labels[0].update(axis='theme', label_id='theme.nonexistent')
    with pytest.raises(ValueError, match='unadmitted labels'):
        catalogue.import_classifications(manifest, manifest_sha)


def test_unknown_native_export_version_does_not_fall_back_to_metadata_verifier(catalogue, tmp_path, monkeypatch):
    from recommend_book import scale_classification_v1
    def forbidden(*args):
        raise AssertionError('unknown version must be rejected before invoking a verifier')
    monkeypatch.setattr(scale_classification_v1, 'verify_catalogue_classification', forbidden)
    manifest = tmp_path / 'unknown-version.json'
    digest = native_manifest(manifest, version='unsupported-native-export')
    with pytest.raises(ValueError, match='Unknown classification export version'):
        catalogue.import_classifications(manifest, digest)


def test_native_proof_pins_stream_twice_per_request_without_retaining_bytes(catalogue, tmp_path, monkeypatch):
    manifest = tmp_path / 'native-manifest.json'
    pins = []
    for index in range(110):
        path = tmp_path / f'pin-{index}.bin'
        path.write_bytes(f'proof {index}'.encode())
        pins.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest_sha = native_manifest(manifest, artifacts=pins)
    native = {'manifest_path': str(manifest), 'manifest_sha256': manifest_sha,
              'provenance_json': json.dumps({'artifacts': native_pins(manifest)})}
    hashes = []
    file_digest = hashlib.file_digest

    def counted_digest(stream, algorithm):
        hashes.append(stream.name)
        return file_digest(stream, algorithm)

    read_bytes = Path.read_bytes

    def forbid_proof_bytes(path):
        if path.name.startswith('pin-'):
            raise AssertionError('native proof verification must stream bytes, not retain them')
        return read_bytes(path)

    monkeypatch.setattr(hashlib, 'file_digest', counted_digest)
    monkeypatch.setattr(Path, 'read_bytes', forbid_proof_bytes)
    catalogue._native_proofs(native)
    catalogue._native_proofs(native)
    assert len(hashes) == 4 * len(set(hashes))
    assert {pin['path'] for pin in pins} <= set(hashes)
    assert {key[0] for key in catalogue._raw_cache} == {str(manifest)}


def test_native_proof_detects_same_size_tamper_with_restored_mtime(catalogue, tmp_path):
    path = tmp_path / 'mutable-proof.bin'
    path.write_bytes(b'first')
    expected_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    catalogue._native_proof_artifact(path, expected_sha)
    original = path.stat()
    path.write_bytes(b'other')
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert path.stat().st_mtime_ns == original.st_mtime_ns
    assert path.stat().st_size == original.st_size
    with pytest.raises(ValueError, match='hash changed'):
        catalogue._native_proof_artifact(path, expected_sha)


def test_native_proof_rechecks_changed_bytes_on_next_call_even_with_identical_metadata(catalogue, tmp_path, monkeypatch):
    from recommend_book import scale_catalogue_v1
    path = tmp_path / 'coarse-metadata.bin'
    path.write_bytes(b'first')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    signature = scale_catalogue_v1._native_proof_signature(path)
    monkeypatch.setattr(scale_catalogue_v1, '_native_proof_signature', lambda _: signature)
    catalogue._native_proof_artifact(path, sha)
    original = path.stat()
    path.write_bytes(b'other')
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    with pytest.raises(ValueError, match='hash changed'):
        catalogue._native_proof_artifact(path, sha)


def test_native_proof_rejects_change_during_streamed_hash(catalogue, tmp_path, monkeypatch):
    from recommend_book import scale_catalogue_v1
    path = tmp_path / 'changing.bin'
    path.write_bytes(b'first')
    expected_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    # Reproduce the Windows runner's timestamp collision deterministically;
    # the changed bytes must be rejected even when all metadata is identical.
    signature = scale_catalogue_v1._native_proof_signature(path)
    monkeypatch.setattr(scale_catalogue_v1, '_native_proof_signature', lambda _: signature)
    digest = hashlib.file_digest

    def change_after_hash(stream, algorithm):
        result = digest(stream, algorithm)
        path.write_bytes(b'other')
        return result

    monkeypatch.setattr(hashlib, 'file_digest', change_after_hash)
    with pytest.raises(ValueError, match='changed during hash verification'):
        catalogue._native_proof_artifact(path, expected_sha)


@pytest.mark.parametrize('operation', ['stats', 'search', 'similar'])
def test_native_response_checks_every_unique_pin_once_and_resets_next_call(catalogue, tmp_path, monkeypatch, operation):
    catalogue.ingest([record(tmp_path, f'Q{index}') for index in range(100, 200)])
    manifest = tmp_path / 'shared-manifest.json'
    pins = []
    for index in range(426):
        path = tmp_path / f'shared-pin-{index}.bin'
        path.write_bytes(str(index).encode())
        pins.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    labels = [{'label_id': 'genre.fantasy'}]
    manifest_sha = native_manifest(manifest, artifacts=pins, rows=[
        {'qid': f'Q{index}', 'work_id': f'wd:Q{index}', 'admitted_labels': labels} for index in range(100, 200)])
    pins = native_pins(manifest)
    extra = tmp_path / 'last-row-extra.bin'
    extra.write_bytes(b'first')
    extra_pin = {'path': str(extra), 'sha256': hashlib.sha256(extra.read_bytes()).hexdigest()}
    rows = []
    for index in range(100, 200):
        proof = {'native_result': {'work_id': f'wd:Q{index}'},
                 'artifacts': pins + ([extra_pin] if index == 199 else [])}
        rows.append((f'Q{index}', str(manifest), manifest_sha, '[{"label_id":"genre.fantasy"}]', json.dumps(proof)))
    with sqlite3.connect(catalogue.db_path) as db:
        db.executemany('INSERT INTO native_classifications VALUES(?,?,?,?,?)', rows)
    original = catalogue._native_proof_artifact
    checked = []

    def counted(path, sha):
        checked.append((str(path), sha))
        return original(path, sha)

    monkeypatch.setattr(catalogue, '_native_proof_artifact', counted)

    def invoke():
        if operation == 'stats':
            return catalogue.catalogue_stats()
        if operation == 'search':
            return catalogue.search_catalogue(page_size=100)
        return catalogue.similar_works('Q100', limit=100)

    from recommend_book.scale_coordinator_proof_v1 import coordinator_chain_pins
    expected_checks = len({(item['path'], item['sha256']) for item in
                          [*pins, extra_pin, {'path': str(manifest), 'sha256': manifest_sha},
                           *coordinator_chain_pins(json.loads(manifest.read_bytes()))]})

    invoke()
    assert len(checked) == len(set(checked)) == expected_checks
    assert (str(extra), extra_pin['sha256']) in checked
    checked.clear()
    invoke()
    assert len(checked) == len(set(checked)) == expected_checks
    # A row sharing the manifest can still introduce a different expected SHA.
    conflicting = json.loads(rows[-1][-1])
    conflicting['artifacts'][1]['sha256'] = '0' * 64
    with sqlite3.connect(catalogue.db_path) as db:
        db.execute('UPDATE native_classifications SET provenance_json=? WHERE qid=?',
                   (json.dumps(conflicting), 'Q199'))
    with pytest.raises(ValueError, match='native provenance omits pinned export dependencies'):
        invoke()
    with sqlite3.connect(catalogue.db_path) as db:
        db.execute('UPDATE native_classifications SET provenance_json=? WHERE qid=?', (rows[-1][-1], 'Q199'))
    before = extra.stat()
    extra.write_bytes(b'other')
    os.utime(extra, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match='hash changed'):
        invoke()
