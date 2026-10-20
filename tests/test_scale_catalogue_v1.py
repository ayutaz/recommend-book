import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from recommend_book.models import Identifier, SourceRecord, Title
from recommend_book.scale_catalogue_v1 import ScaleCatalogue


def save(path, payload):
    path.write_text(json.dumps(payload), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    manifest_sha = save(manifest, {'fixture': 'production uses frozen native verifier'})
    label = {'label_id': 'genre.fantasy', 'axis': 'genre', 'decision': 'present', 'scope': 'work',
             'confidence': 0.73, 'relevance': None, 'evidence': []}
    proof = {'artifacts': [{'path': str(manifest), 'sha256': manifest_sha}]}
    from recommend_book import scale_classification_v1
    monkeypatch.setattr(scale_classification_v1, 'verify_catalogue_classification', lambda *args: [
        {'qid': 'Q100', 'work_id': 'wd:Q100', 'input_work_id': 'old_work_100', 'labels': [label], 'provenance': proof},
        {'qid': 'Q101', 'work_id': 'wd:Q101', 'input_work_id': 'old_work_101', 'labels': [], 'provenance': proof}])
    result = catalogue.import_classifications(manifest, manifest_sha)
    assert result['raw_classified'] == 2 and result['adopted'] == 1
    assert catalogue.catalogue_stats()['llm_works'] == 1
    assert catalogue.catalogue_stats()['raw_classified_works'] == 2
    assert catalogue.catalogue_stats()['feature_bearing_works'] == 2
    assert catalogue.work_detail('old_work_100')['llm_features'] == [label]
    assert catalogue.work_detail('Q100')['llm_features'][0]['relevance'] is None
    save(manifest, {'tampered': True})
    with pytest.raises(ValueError, match='hash changed'):
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


def test_verifier_admitted_theme_and_structure_are_stored_unchanged(catalogue, tmp_path, monkeypatch):
    from recommend_book import scale_classification_v1
    from recommend_book.classification.validation import load_taxonomy
    catalogue.ingest([record(tmp_path)])
    manifest = tmp_path / 'reviewed-manifest.json'
    manifest_sha = save(manifest, {'fixture': 'authentic verifier contract, not an actual native review'})
    labels = [{'label_id': next(row['label_id'] for row in load_taxonomy()['labels'] if row['axis'] == axis),
               'axis': axis, 'decision': 'present', 'scope': 'work', 'raw_confidence': 0.71,
               'relevance': None, 'evidence': []} for axis in ('theme', 'structure')]
    admitted = {'qid': 'Q100', 'work_id': 'wd:Q100', 'labels': labels,
                'provenance': {'artifacts': [{'path': str(manifest), 'sha256': manifest_sha}]}}
    monkeypatch.setattr(scale_classification_v1, 'verify_catalogue_classification', lambda *args: [admitted])
    assert catalogue.import_classifications(manifest, manifest_sha)['adopted'] == 1
    assert catalogue.work_detail('Q100')['llm_features'] == labels
    labels[0]['axis'] = 'genre'
    with pytest.raises(ValueError, match='unadmitted labels'):
        catalogue.import_classifications(manifest, manifest_sha)
    labels[0].update(axis='theme', label_id='theme.nonexistent')
    with pytest.raises(ValueError, match='unadmitted labels'):
        catalogue.import_classifications(manifest, manifest_sha)
