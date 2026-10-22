import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from recommend_book.scale_catalogue_v1 import ScaleCatalogue
from recommend_book.scale_preferences_v1 import _read_native_rows
from test_scale_catalogue_v1 import claim, native_manifest, native_pins, record, save


@pytest.fixture
def preference_catalogue(tmp_path):
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


def add_native(catalogue, tmp_path, rows):
    manifest = tmp_path / 'native-export.json'
    sha = native_manifest(manifest, rows=rows)
    proof = json.dumps({'artifacts': native_pins(manifest)})
    with sqlite3.connect(catalogue.db_path) as db:
        db.executemany('INSERT INTO native_classifications VALUES(?,?,?,?,?)', [
            (row['qid'], str(manifest), sha, json.dumps(row['admitted_labels']), proof) for row in rows])
    return manifest


def test_read_native_rows_projects_only_raw_labels_and_chunks_over_500(preference_catalogue, monkeypatch):
    qids = [f'Q{10000 + index}' for index in range(501)]
    labels = [json.dumps([{'label_id': 'theme.revenge', 'axis': 'theme'}]) for _ in qids]
    with sqlite3.connect(preference_catalogue.db_path) as db:
        db.executemany(
            'INSERT INTO native_classifications VALUES(?,?,?,?,?)',
            [(qid, 'manifest.json', 'manifest-sha', value, 'large provenance payload')
             for qid, value in zip(qids, labels)],
        )

    reads = []
    statements = []
    original_connect = preference_catalogue._connect

    @contextmanager
    def traced_connect():
        with original_connect() as db:
            db.set_authorizer(
                lambda action, arg1, arg2, database, source: (
                    reads.append((action, arg1, arg2)), sqlite3.SQLITE_OK
                )[1]
            )
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(preference_catalogue, '_connect', traced_connect)
    result = _read_native_rows(preference_catalogue, qids)

    assert set(result) == set(qids)
    assert result[qids[0]]['labels_json'] == labels[0]
    assert sum(statement.startswith('SELECT qid,labels_json FROM native_classifications')
               for statement in statements) == 2
    assert {column for action, table, column in reads
            if action == sqlite3.SQLITE_READ and table == 'native_classifications'} == {
                'qid', 'labels_json'}


def label(label_id, axis, *, quote='fixture evidence'):
    return {'label_id': label_id, 'axis': axis, 'decision': 'present', 'scope': 'work',
            'raw_confidence': 0.9, 'relevance': 1,
            'evidence': [{'locator': 'article:fixture', 'quote': quote}]}


def explicit_series_record(tmp_path, qid, series_qid, author_qid):
    source = record(tmp_path, qid, genre='Q200')
    raw = json.loads((tmp_path / f'{qid}.json').read_bytes())
    raw['entities'][qid]['claims']['P179'] = [claim(series_qid)]
    raw['entities'][qid]['claims']['P50'] = [claim(author_qid)]
    raw['entities'][series_qid] = {'id': series_qid, 'labels': {'en': {'value': series_qid, 'language': 'en'}}, 'claims': {}}
    raw['entities'][author_qid] = {'id': author_qid, 'labels': {'en': {'value': author_qid, 'language': 'en'}}, 'claims': {}}
    sha = save(tmp_path / f'{qid}.json', raw)
    return source.model_copy(update={'raw_sha256': sha})


def test_multiple_likes_exclusion_and_non_genre_overlap(preference_catalogue, tmp_path):
    preference_catalogue.ingest([
        record(tmp_path, 'Q100', genre='Q200'),
        record(tmp_path, 'Q101', genre='Q201'),
        record(tmp_path, 'Q102', genre='Q202'),
        record(tmp_path, 'Q103', genre='Q201'),
    ])
    add_native(preference_catalogue, tmp_path, [
        {'qid': 'Q100', 'work_id': 'wd:Q100', 'admitted_labels': [label('theme.revenge', 'theme')]},
        {'qid': 'Q101', 'work_id': 'wd:Q101', 'admitted_labels': [label('theme.revenge', 'theme')],
         'source_attributions': [{'source_url': 'https://example.org/revision/1',
                                  'license': 'CC-BY-SA-4.0', 'display_allowed': True}]},
        {'qid': 'Q102', 'work_id': 'wd:Q102', 'admitted_labels': [label('theme.family', 'theme')]},
    ])
    result = preference_catalogue.recommend_preferences(
        liked_qids=['Q100'], preferred_features=['theme.revenge'], limit=3)
    assert result['mode'] == 'preference_similarity'
    assert result['personalized'] is True
    assert result['items'][0]['qid'] == 'Q101'
    assert 'Q100' not in {item['qid'] for item in result['items']}
    assert result['items'][0]['reasons'][0]['kind'] == 'native_feature_match'
    assert result['items'][0]['reasons'][0]['model_confidence'] == 0.9
    assert result['items'][0]['score_kind'] == 'uncalibrated_preference_similarity'
    assert result['items'][0]['recommendation_provenance'][0]['status'] == 'verified'
    assert result['items'][0]['recommendation_provenance'][0]['license'] == 'CC-BY-SA-4.0'


def test_source_fallback_and_read_exclusion(preference_catalogue, tmp_path):
    preference_catalogue.ingest([
        record(tmp_path, 'Q100', genre='Q200'),
        record(tmp_path, 'Q101', genre='Q200'),
        record(tmp_path, 'Q102', genre='Q201'),
    ])
    result = preference_catalogue.recommend_preferences(liked_qids=['Q100'], limit=2)
    assert result['items'][0]['qid'] == 'Q101'
    assert any(reason['kind'] == 'source_genre' for reason in result['items'][0]['reasons'])
    assert result['items'][0]['recommendation_provenance'][0]['status'] == 'not_checked'
    assert result['items'][0]['feature_insufficiency']
    preference_catalogue.set_read_status('Q101', 'read')
    result = preference_catalogue.recommend_preferences(liked_qids=['Q100'], limit=2)
    assert 'Q101' not in {item['qid'] for item in result['items']}


def test_explicit_series_authority_is_used_for_diversity_cap(preference_catalogue, tmp_path):
    preference_catalogue.ingest([
        record(tmp_path, 'Q100', genre='Q200'),
        explicit_series_record(tmp_path, 'Q101', 'Q900', 'Q501'),
        explicit_series_record(tmp_path, 'Q102', 'Q900', 'Q502'),
        explicit_series_record(tmp_path, 'Q103', 'Q901', 'Q503'),
    ])
    result = preference_catalogue.recommend_preferences(liked_qids=['Q100'], limit=2)
    assert [item['qid'] for item in result['items']] == ['Q101', 'Q103']


def test_unknown_feature_is_rejected_but_valid_unavailable_feature_is_not_negative(preference_catalogue, tmp_path):
    preference_catalogue.ingest([record(tmp_path, 'Q100', genre='Q200'), record(tmp_path, 'Q101', genre='Q201')])
    result = preference_catalogue.recommend_preferences(
        preferred_features=['theme.revenge'], avoided_features=['theme.family'], limit=2)
    assert result['items']
    assert all(reason['kind'] != 'avoided_feature' for item in result['items'] for reason in item['reasons'])
    with pytest.raises(ValueError, match='unknown feature'):
        preference_catalogue.recommend_preferences(preferred_features=['theme.not_in_taxonomy'])


def test_tampered_native_row_falls_back_without_hidden_reason(preference_catalogue, tmp_path):
    preference_catalogue.ingest([record(tmp_path, 'Q100', genre='Q200'), record(tmp_path, 'Q101', genre='Q201')])
    add_native(preference_catalogue, tmp_path, [
        {'qid': 'Q101', 'work_id': 'wd:Q101', 'admitted_labels': [label('theme.revenge', 'theme')]},
    ])
    with sqlite3.connect(preference_catalogue.db_path) as db:
        db.execute("UPDATE native_classifications SET labels_json=? WHERE qid='Q101'", (json.dumps([
            label('theme.revenge', 'theme'), label('theme.family', 'theme')]),))
    result = preference_catalogue.recommend_preferences(preferred_features=['theme.revenge'], limit=2)
    item = next(item for item in result['items'] if item['qid'] == 'Q101')
    assert all(reason['kind'] != 'native_feature_match' for reason in item['reasons'])
    assert item['evidence_status'] == 'source_metadata'
    assert item['recommendation_provenance'][0]['status'] == 'proof_unavailable'
    assert item['avoided_feature_status']['unknown'] == []


def test_invalid_avoided_proof_is_not_a_negative_upper_bound(preference_catalogue, tmp_path):
    preference_catalogue.ingest([record(tmp_path, f'Q{index}', genre='Q200') for index in range(101, 161)])
    manifest = add_native(preference_catalogue, tmp_path, [
        {'qid': 'Q101', 'work_id': 'wd:Q101', 'admitted_labels': [label('theme.family', 'theme')]},
    ])
    manifest.write_text(manifest.read_text(encoding='utf-8') + ' ', encoding='utf-8')
    result = preference_catalogue.recommend_preferences(avoided_features=['theme.family'], limit=1)
    assert result['items'][0]['qid'] == 'Q101'
    assert result['items'][0]['avoided_feature_status']['matched'] == []
    assert result['items'][0]['avoided_feature_status']['unknown'] == ['theme.family']
    assert result['items'][0]['recommendation_provenance'][0]['status'] == 'proof_unavailable'


def test_preference_facets_do_not_expose_non_taxonomy_rows(preference_catalogue, tmp_path):
    preference_catalogue.ingest([record(tmp_path, 'Q100')])
    facets = preference_catalogue.preference_facets()
    assert facets['native_features']
    assert all(item['label_id'].split('.', 1)[0] in {
        'genre', 'theme', 'structure', 'protagonist', 'style', 'atmosphere'}
               for item in facets['native_features'])
    assert all(item['availability'] == 'verified_on_demand' for item in facets['native_features'])
