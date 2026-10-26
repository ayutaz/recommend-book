import hashlib
import json

import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue


def _write_json(path, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def recommendation_catalogue(tmp_path):
    license_path = tmp_path / 'license.html'
    license_path.write_text('Wikidata structured data CC0', encoding='utf-8')
    conditions_path = tmp_path / 'conditions.json'
    conditions_sha = _write_json(conditions_path, {
        'version': 'light-novel-source-conditions-v1',
        'snapshots': [{
            'url': 'https://www.wikidata.org/wiki/Wikidata:Licensing',
            'raw_path': str(license_path),
            'raw_sha256': hashlib.sha256(license_path.read_bytes()).hexdigest(),
        }],
        'wikidata': {
            'scope': 'structured metadata only', 'license': 'CC0',
            'prose_excluded': True,
            'usages': {'local_storage': True, 'metadata_display': True,
                       'metadata_recommendation': True, 'llm_input': False},
        },
    })

    rows = [
        ('Q1', 'liked', 'series', 'Q100', []),
        ('Q2', 'unrelated', 'series', 'Q200', []),
        ('Q3', 'candidate', 'series', 'Q100', []),
        ('Q4', 'candidate work', 'work', 'Q200', ['Q3']),
        ('Q5', 'read leaf', 'work', 'Q200', []),
        ('Q6', 'nested candidate series', 'series', 'Q200', ['Q3']),
        ('Q7', 'nested candidate work', 'work', 'Q200', ['Q6']),
        ('Q8', 'membership leaf', 'work', 'Q200', []),
        ('Q9', 'membership second leaf', 'work', 'Q200', []),
    ]
    records = []
    for number, (qid, title, kind, genre, parents) in enumerate(rows, 1):
        entity = {
            'id': qid,
            'lastrevid': number,
            'labels': {'ja': {'value': title}},
            'aliases': {'ja': []},
            'claims': {
                'P31': [{'mainsnak': {'snaktype': 'value', 'datavalue': {'value': {'id':
                    'Q104213567' if kind == 'series' else 'Q747381'}}}}],
                'P407': [{'mainsnak': {'snaktype': 'value', 'datavalue': {'value': {'id': 'Q5287'}}}}],
                'P136': [{'mainsnak': {'snaktype': 'value', 'datavalue': {'value': {'id': genre}}}}],
            },
            'sitelinks': {},
        }
        if parents:
            entity['claims']['P179'] = [
                {'mainsnak': {'snaktype': 'value', 'datavalue': {'value': {'id': parent}}}}
                for parent in parents
            ]
        raw_path = tmp_path / f'{qid}.json'
        raw_sha = _write_json(raw_path, {
            'entities': {qid: entity,
                         genre: {'id': genre, 'labels': {'ja': {'value': genre}}}},
        })
        records.append({
            'schema_version': 'light-novel-metadata-v1', 'id': qid,
            'source': 'wikidata', 'kind': kind, 'title': title,
            'aliases': [title], 'authors': [],
            'original_languages': ['Q5287'],
            'genres': [{'id': genre, 'label': genre}], 'subjects': [],
            'publishers': [], 'publication_dates': [], 'sitelinks': {},
            'relations': {prop: ([*parents] if prop == 'P179' else [])
                          for prop in ('P179', 'P527', 'P629', 'P155', 'P156', 'P144')},
            'provenance': {
                'url': f'https://www.wikidata.org/wiki/{qid}',
                'entity_revision': number, 'raw_path': str(raw_path),
                'raw_sha256': raw_sha, 'conditions_path': str(conditions_path),
                'conditions_sha256': conditions_sha,
            },
            'admission': {'accepted': True, 'reasons': [], 'evidence': {}},
        })
    catalogue = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    assert catalogue.ingest(records)['inserted'] == len(records)
    catalogue.set_read_status('lnw:wd:Q5', 'read')
    return catalogue, records


def test_genre_prefilter_preserves_results_and_projects_family_read_closure(
        recommendation_catalogue, monkeypatch):
    catalogue, _ = recommendation_catalogue
    original = catalogue._project
    calls = []

    def observed(record, cache):
        calls.append(record['id'])
        return original(record, cache)

    monkeypatch.setattr(catalogue, '_project', observed)
    proof = json.dumps({}, separators=(',', ':'))
    proof_sha = hashlib.sha256(proof.encode()).hexdigest()
    with catalogue._connect() as db:
        db.executemany(
            'INSERT INTO edition_series_memberships VALUES(?,?,?,?)',
            [('lnw:wd:Q8', 'lns:wd:Q6', proof, proof_sha),
             ('lnw:wd:Q9', 'lnw:wd:Q8', proof, proof_sha)],
        )
    fast = catalogue.recommend_preferences(
        liked_entry_ids=['lns:wd:Q1'], genre='Q100', limit=10)
    assert 'Q2' not in calls
    assert {'Q1', 'Q3', 'Q4', 'Q5', 'Q6', 'Q7', 'Q8', 'Q9'} <= set(calls)
    assert [row['entry_id'] for row in fast['items']] == ['lns:wd:Q3']

    monkeypatch.setattr(catalogue, '_recommendation_prefilter_ids',
                        lambda records, stored, genre, liked, memberships: set(records))
    baseline = catalogue.recommend_preferences(
        liked_entry_ids=['lns:wd:Q1'], genre='Q100', limit=10)
    assert fast['items'] == baseline['items']


def test_genre_prefilter_still_rejects_mutated_selected_source(
        recommendation_catalogue, monkeypatch):
    catalogue, records = recommendation_catalogue
    target = next(row for row in records if row['id'] == 'Q3')
    raw_path = target['provenance']['raw_path']
    with open(raw_path, 'w', encoding='utf-8') as handle:
        handle.write('{}')
    with pytest.raises(ValueError, match='SHA'):
        catalogue.recommend_preferences(
            liked_entry_ids=['lns:wd:Q1'], genre='Q100', limit=10)


def test_unfiltered_recommendation_keeps_existing_full_projection(
        recommendation_catalogue, monkeypatch):
    catalogue, _ = recommendation_catalogue
    original = catalogue._project
    calls = []

    def observed(record, cache):
        calls.append(record['id'])
        return original(record, cache)

    monkeypatch.setattr(catalogue, '_project', observed)
    catalogue.recommend_preferences(liked_entry_ids=['lns:wd:Q1'], limit=10)
    assert {'Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6', 'Q7', 'Q8', 'Q9'} <= set(calls)


def test_source_genre_filter_does_not_promote_content_only_features(
        recommendation_catalogue, monkeypatch):
    catalogue, _ = recommendation_catalogue
    feature = {
        'entry_id': 'lns:wd:Q2', 'scope': 'series',
        'labels': [{'label_id': 'genre.romance', 'axis': 'genre',
                    'decision': 'present', 'scope': 'series'}],
    }
    monkeypatch.setattr(
        catalogue, '_feature_rows',
        lambda ids=None, **kwargs: {'lns:wd:Q2': feature}
        if ids is None or 'lns:wd:Q2' in ids else {},
    )
    result = catalogue.recommend_preferences(
        liked_entry_ids=['lns:wd:Q1'], genre='Q100', limit=10)
    assert 'lns:wd:Q2' not in {row['entry_id'] for row in result['items']}


def test_genre_prefilter_checks_sha_for_omitted_source_rows(recommendation_catalogue):
    catalogue, _ = recommendation_catalogue
    with catalogue._connect() as db:
        db.execute("UPDATE entries SET record_json='{}' WHERE entry_id='lns:wd:Q2'")
    with pytest.raises(ValueError, match='SHA'):
        catalogue.recommend_preferences(
            liked_entry_ids=['lns:wd:Q1'], genre='Q100', limit=10)


def test_prefilter_keeps_unknown_typed_contracts_and_excludes_known_mismatch():
    unknown = 'lns:future:Q10'
    known_mismatch = 'lns:wd:Q11'
    invalid_ndl_source = 'lns:future:Q12'
    valid_ndl_without_kind = 'lns:ndl:R100000002-I13'
    records = {
        unknown: {'schema_version': 'future-v1', 'kind': 'edition', 'id': 'Q10',
                  'relations': {}},
        known_mismatch: {'schema_version': 'light-novel-metadata-v1',
                         'source': 'wikidata', 'kind': 'series', 'id': 'Q11',
                         'genres': [{'id': 'Q200'}], 'relations': {}},
        invalid_ndl_source: {'schema_version': 'light-novel-ndl-series-v1',
                             'source': 'future-source', 'kind': 'series',
                             'id': 'Q12', 'genres': [], 'relations': {}},
        valid_ndl_without_kind: {'schema_version': 'light-novel-ndl-series-v1',
                                 'source': 'ndl', 'id': 'R100000002-I13',
                                 'genres': [], 'relations': {}},
    }
    stored = [
        {'entry_id': unknown, 'kind': 'series', 'status': 'unknown'},
        {'entry_id': known_mismatch, 'kind': 'series', 'status': 'unknown'},
        {'entry_id': invalid_ndl_source, 'kind': 'series', 'status': 'unknown'},
        {'entry_id': valid_ndl_without_kind, 'kind': 'series', 'status': 'unknown'},
    ]
    selected = LightNovelCatalogue._recommendation_prefilter_ids(
        records, stored, 'Q100', [], ())
    assert unknown in selected
    assert invalid_ndl_source in selected
    assert known_mismatch not in selected
    assert valid_ndl_without_kind not in selected
