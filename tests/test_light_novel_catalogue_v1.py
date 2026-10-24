import hashlib
import json

import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue, normalize_search


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def claim(qid):
    return {'mainsnak': {'snaktype': 'value', 'datavalue': {'value': {'id': qid}}}}


@pytest.fixture
def setup_catalogue(tmp_path):
    snapshot = tmp_path / 'license.html'
    snapshot.write_text('Wikidata structured data CC0', encoding='utf-8')
    conditions = tmp_path / 'conditions.json'
    chash = write(conditions, {'version': 'light-novel-source-conditions-v1',
        'snapshots': [{'url': 'https://www.wikidata.org/wiki/Wikidata:Licensing',
            'raw_path': str(snapshot), 'raw_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest()}],
        'wikidata': {'scope': 'structured metadata only', 'license': 'CC0', 'prose_excluded': True,
            'usages': {'local_storage': True, 'metadata_display': True, 'metadata_recommendation': True, 'llm_input': False}}})
    records = []
    for n, title, kind, parent in [(1, '魔法科高校の劣等生', 'series', None),
                                  (2, 'ソードアート・オンライン', 'series', None),
                                  (3, '魔法科高校の劣等生 第一巻', 'work', 'Q1')]:
        entity = {'id': f'Q{n}', 'lastrevid': n, 'labels': {'ja': {'value': title}},
                  'aliases': {'ja': [{'value': 'まほうか' if n == 1 else title}]},
                  'claims': {'P31': [claim('Q104213567' if kind == 'series' else 'Q747381')],
                             'P407': [claim('Q5287')], 'P136': [claim('Q100')]}, 'sitelinks': {}}
        if parent:
            entity['claims']['P179'] = [claim(parent)]
        raw = tmp_path / f'{n}.json'
        rhash = write(raw, {'entities': {f'Q{n}': entity, 'Q100': {'id': 'Q100', 'labels': {'ja': {'value': 'ファンタジー'}}}}})
        records.append({'schema_version': 'light-novel-metadata-v1', 'id': f'Q{n}', 'source': 'wikidata',
            'kind': kind, 'title': title, 'aliases': list(dict.fromkeys([title, 'まほうか' if n == 1 else title])),
            'authors': [], 'original_languages': ['Q5287'], 'genres': [{'id': 'Q100', 'label': 'ファンタジー'}],
            'subjects': [], 'publishers': [], 'publication_dates': [], 'sitelinks': {},
            'relations': {p: ([parent] if p == 'P179' and parent else []) for p in ('P179', 'P527', 'P629', 'P155', 'P156', 'P144')},
            'provenance': {'url': f'https://www.wikidata.org/wiki/Q{n}', 'entity_revision': n,
                'raw_path': str(raw), 'raw_sha256': rhash, 'conditions_path': str(conditions), 'conditions_sha256': chash},
            'admission': {'accepted': True, 'reasons': [], 'evidence': {}}})
    catalogue = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    return catalogue, records


def test_import_search_detail_and_no_private_paths(setup_catalogue):
    cat, records = setup_catalogue
    assert cat.ingest(records)['inserted'] == 3
    assert cat.search_catalogue('マホウカ')['items'][0]['entry_id'] == 'lns:wd:Q1'
    assert cat.search_catalogue('  魔法科　高校 ')['total'] == 2
    row = cat.work_detail('lns:wd:Q1')
    assert row['entry_kind'] == 'series' and row['read_status'] == 'unknown'
    assert row['members'][0]['entry_id'] == 'lnw:wd:Q3'
    assert 'raw_path' not in json.dumps(row)
    assert row['llm_features'] == []


def test_metadata_and_raw_forgery_rejected(setup_catalogue):
    cat, records = setup_catalogue
    records[0]['title'] = 'forged'
    assert cat.ingest(records)['failed'] == 1
    records[0]['title'] = '魔法科高校の劣等生'
    cat.ingest(records)
    raw = records[0]['provenance']['raw_path']
    from pathlib import Path
    Path(raw).write_text('{}')
    with pytest.raises(ValueError, match='SHA'):
        cat.work_detail('lns:wd:Q1')


def test_adaptation_and_unknown_language_stay_held(setup_catalogue):
    cat, records = setup_catalogue
    from pathlib import Path
    record = records[0]
    raw = Path(record['provenance']['raw_path'])
    payload = json.loads(raw.read_text(encoding='utf-8'))
    payload['entities']['Q1']['claims']['P31'].append(claim('Q5398426'))
    record['provenance']['raw_sha256'] = write(raw, payload)
    assert cat.ingest([record])['failed'] == 1
    payload['entities']['Q1']['claims']['P31'].pop()
    payload['entities']['Q1']['claims']['P407'] = []
    record['provenance']['raw_sha256'] = write(raw, payload)
    assert cat.ingest([record])['failed'] == 1


def test_preference_recommendation_excludes_family_and_read_series(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    result = cat.recommend_preferences(liked_entry_ids=['lns:wd:Q1'])
    assert [x['entry_id'] for x in result['items']] == ['lns:wd:Q2']
    assert result['items'][0]['reasons'][0]['kind'] == 'source_genre'
    cat.set_read_status('lnw:wd:Q3', 'read')
    assert cat.work_detail('lns:wd:Q1')['read_status'] == 'unknown'
    assert cat.similar_works('lns:wd:Q2')['items'] == []
    cat.set_read_status('lnw:wd:Q3', 'unknown')
    assert cat.search_catalogue(read_filter='unread')['total'] == 0
    assert cat.search_catalogue(read_filter='unknown')['total'] == 3


def test_qid_is_not_typed_entry_and_stats_separate_kinds(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    with pytest.raises(ValueError):
        cat.work_detail('Q1')
    stats = cat.catalogue_stats()
    assert stats['series'] == 2 and stats['individual_works'] == 1 and stats['editions'] == 0
    assert stats['content_feature_series'] == 0 and stats['source_genre_series'] == 2


def test_search_normalization():
    assert normalize_search(' ＳＡＯ　まほうか ') == normalize_search('saoマホウカ')


def test_changed_conditions_snapshot_fails_closed(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    from pathlib import Path
    conditions = json.loads(Path(records[0]['provenance']['conditions_path']).read_text())
    Path(conditions['snapshots'][0]['raw_path']).write_text('changed')
    with pytest.raises(ValueError, match='SHA'):
        cat.search_catalogue('魔法科')


def test_updated_metadata_preserves_independent_read_state(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    cat.set_read_status('lns:wd:Q1', 'reading')
    records[0]['admission']['evidence'] = {'review_note': 'new evidence without changed metadata'}
    assert cat.ingest(records)['updated'] == 1
    assert cat.work_detail('lns:wd:Q1')['read_status'] == 'reading'


def test_only_work_qids_are_removed_for_world_overlap(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    result = cat.search_catalogue(exclude_work_qids={'Q1', 'Q3'})
    assert {r['entry_id'] for r in result['items']} == {'lns:wd:Q1', 'lns:wd:Q2'}


def test_authenticated_series_features_never_become_volume_features(setup_catalogue, monkeypatch, tmp_path):
    cat, records = setup_catalogue
    cat.ingest(records)
    feature = {'entry_id': 'lns:wd:Q1', 'scope': 'series', 'content_features_adopted': True,
               'labels': [{'label_id': 'genre.fantasy', 'label': 'ファンタジー', 'axis': 'genre',
                           'decision': 'present', 'scope': 'series', 'relevance': .8,
                           'raw_path': '/private/source', 'evidence': [{'evidence_id': 'e1', 'locator': 'paragraph:1', 'raw_path': '/private/source'}]}],
               'source_attributions': [{'evidence_id': 'e1', 'kind': 'synopsis',
                   'source_url': 'https://ja.wikipedia.org/w/index.php?oldid=1', 'license': 'CC-BY-SA-4.0',
                   'raw_path': '/private/source', 'attribution': {'revision_id': 1, 'raw_path': '/private/source'}}]}
    import recommend_book.light_novel_classification_v1 as verifier
    calls = []
    def verify(path, sha):
        calls.append((path, sha))
        return {'rows': [feature]}
    monkeypatch.setattr(verifier, 'verify_feature_export', verify)
    assert cat.import_features(tmp_path / 'manifest.json', 'a' * 64)['imported'] == 1
    assert cat.work_detail('lns:wd:Q1')['llm_features'][0]['evidence_basis'] == 'content'
    assert cat.work_detail('lnw:wd:Q3')['llm_features'] == []
    assert 'raw_path' not in json.dumps(cat.work_detail('lns:wd:Q1'))
    assert cat.catalogue_stats()['content_feature_series'] == 1
    assert len(calls) >= 5  # Evidence is checked anew for separate operations.
    def invalid(path, sha):
        raise ValueError('changed pinned artifact')
    monkeypatch.setattr(verifier, 'verify_feature_export', invalid)
    assert cat.work_detail('lns:wd:Q1')['llm_features'] == []
    assert cat.catalogue_stats()['content_feature_series'] == 0
    assert cat.search_catalogue('魔法科')['total'] == 2


def test_world_genres_are_only_ranking_references(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    result = cat.recommend_preferences(reference_genres=[{'qid': 'Q100', 'label': 'ファンタジー',
        'source_url': 'https://www.wikidata.org/wiki/Q999', 'scope': 'work'}])
    assert len(result['items']) == 2
    assert result['items'][0]['llm_features'] == []
    assert result['items'][0]['reasons'][0]['reference_sources'][0]['scope'] == 'work'


@pytest.fixture
def edition_bundle(setup_catalogue, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.core import RawArtifact
    from recommend_book.ingestion.ndl import NDLAdapter
    cat, records = setup_catalogue
    conditions = Path(records[0]['provenance']['conditions_path'])
    document = json.loads(conditions.read_text())
    for name, url in [('guide', 'https://www.ndl.go.jp/data/data_service/quickguide'),
                      ('api', 'https://ndlsearch.ndl.go.jp/help/api')]:
        page = tmp_path / name
        page.write_text(name)
        document['snapshots'].append({'url': url, 'raw_path': str(page),
            'raw_sha256': hashlib.sha256(page.read_bytes()).hexdigest()})
    document['ndl'] = {'provider': 'iss-ndl-opac', 'usages': {
        'local_storage': True, 'metadata_display': True, 'metadata_recommendation': True}}
    chash = write(conditions, document)
    xml = '''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:d="http://purl.org/dc/terms/" xmlns:n="http://ndl.go.jp/dcndl/terms/">
        <n:BibResource rdf:about="https://ndlsearch.ndl.go.jp/books/R100000002-I1">
        <d:title>書誌候補 第一巻</d:title><d:creator>著者 著</d:creator>
        <d:publisher>出版社</d:publisher><d:issued>2020</d:issued>
        <d:language>jpn</d:language><n:seriesTitle>ラノベ文庫 ; 1</n:seriesTitle>
        <n:volume>1</n:volume></n:BibResource></rdf:RDF>'''
    raw = tmp_path / 'edition.xml'
    raw.write_text(xml, encoding='utf-8')
    rhash = hashlib.sha256(raw.read_bytes()).hexdigest()
    artifact = RawArtifact(rhash, str(raw), len(raw.read_bytes()), '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru')
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), artifact, 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    record = {'schema_version': 'light-novel-edition-v1', 'source': 'ndl', 'provider': 'iss-ndl-opac',
        'id': source['source_id'], 'title': source['titles'][0]['value'], 'volume': source['volume'],
        'authors': [c['name'] for c in source['contributors'] if c['role'] == 'author'],
        'publisher': source['publisher'], 'date': source['publication_year'], 'isbn': [],
        'languages': ['jpn'], 'label': source['series'], 'ndc': [], 'source_url': source['source_url'],
        'source_record': source, 'label_evidence': [{'query_term': 'ラノベ文庫'}], 'label_candidate': True,
        'admission': {'light_novel_confirmed': False}, 'provenance': {'url': source['source_url'],
            'raw_path': str(raw), 'raw_sha256': rhash, 'conditions_path': str(conditions), 'conditions_sha256': chash}}
    return cat, records, record


def test_ndl_edition_is_not_promoted_to_story_series_or_original_language(edition_bundle):
    cat, _, record = edition_bundle
    assert cat.import_editions([record])['inserted'] == 1
    row = cat.work_detail('lne:ndl:R100000002-I1')
    assert row['title'] == '書誌候補 第一巻' and row['entry_kind'] == 'edition'
    assert row['qid'] is None and row['original_language'] is None and row['language'] == 'ja'
    assert row['genres'] == [] and row['members'] == [] and not row['light_novel_confirmed']
    assert cat.catalogue_stats()['series'] == 0
    assert cat.search_catalogue(language='ja')['total'] == 1
    assert cat.recommend_preferences(preferred_features=['genre.fantasy'])['items'] == []
    record['title'] = '捏造'
    assert cat.import_editions([record])['failed'] == 1
    record['title'] = '書誌候補 第一巻'
    record['series_entry_id'] = 'lns:wd:Q1'
    assert cat.import_editions([record])['failed'] == 1


def test_real_feature_verifier_binding_and_native_mutation(setup_catalogue, tmp_path, monkeypatch):
    from pathlib import Path
    from test_light_novel_classification_v1 import build_verified_export_fixture
    cat, records = setup_catalogue
    cat.ingest(records)
    # The fixture executes deterministic child processes, with no model/network.
    result = build_verified_export_fixture(tmp_path, monkeypatch)
    assert cat.import_features(result['manifest_path'], result['manifest_sha256'])['imported'] == 1
    assert cat.catalogue_stats()['content_feature_series'] == 1
    row = cat.work_detail('lns:wd:Q1')
    assert row['llm_features'][0]['label'] and row['llm_features'][0]['scope'] == 'series'
    assert cat.work_detail('lnw:wd:Q3')['llm_features'] == []
    final = Path(result['manifest_path']).parent / 'classification/000/final.json'
    final.write_text('{}', encoding='utf-8')
    assert cat.work_detail('lns:wd:Q1')['llm_features'] == []
    assert cat.catalogue_stats()['content_feature_series'] == 0


def test_v2_explicit_japanese_literary_description_preserves_unknown_original_language(setup_catalogue):
    from pathlib import Path
    cat, records = setup_catalogue
    record = records[0]
    raw = Path(record['provenance']['raw_path'])
    payload = json.loads(raw.read_text(encoding='utf-8'))
    payload['entities']['Q1']['claims']['P407'] = []
    payload['entities']['Q1']['descriptions'] = {'en': {'value': 'Japanese light novel series'}}
    record['provenance']['raw_sha256'] = write(raw, payload)
    record['schema_version'] = 'light-novel-metadata-v2'
    record['original_languages'] = []
    record['original_language_resolution'] = {'method': 'wikidata_description_explicit_japanese_literary_original_v1',
        'language': 'Q5287', 'source_url': record['provenance']['url'], 'raw_path': str(raw),
        'raw_sha256': record['provenance']['raw_sha256'], 'entity_revision': 1,
        'locator': 'descriptions.en.value', 'text': 'Japanese light novel series'}
    assert cat.ingest([record])['inserted'] == 1
    row = cat.work_detail('lns:wd:Q1')
    assert row['original_language'] is None
    assert row['japanese_identity_basis'] == 'explicit_wikidata_description'
    assert cat.search_catalogue(language='ja')['total'] == 0
    record['original_language_resolution']['text'] = 'Japanese manga series'
    assert cat.ingest([record])['failed'] == 1
    record['original_language_resolution']['text'] = 'Japanese light novel series'
    record['schema_version'] = 'light-novel-metadata-v1'
    assert cat.ingest([record])['failed'] == 1


@pytest.fixture
def membership_bundle(edition_bundle, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, records, edition = edition_bundle
    series = records[0]
    series['provenance']['conditions_sha256'] = edition['provenance']['conditions_sha256']
    raw_series = Path(series['provenance']['raw_path'])
    payload = json.loads(raw_series.read_text(encoding='utf-8'))
    payload['entities']['Q1']['claims'].update(P50=[claim('Q500')], P123=[claim('Q600')])
    payload['entities']['Q500'] = {'id': 'Q500', 'labels': {'ja': {'value': '著者'}},
        'claims': {'P349': [{'mainsnak': {'snaktype': 'value', 'datavalue': {'value': '01234567'}}}]}}
    payload['entities']['Q600'] = {'id': 'Q600', 'labels': {'ja': {'value': 'ラノベ文庫'}}}
    series['provenance']['raw_sha256'] = write(raw_series, payload)
    series['authors'] = [{'id': 'Q500', 'label': '著者'}]
    series['publishers'] = [{'id': 'Q600', 'label': 'ラノベ文庫'}]
    raw_edition = Path(edition['provenance']['raw_path'])
    xml = raw_edition.read_text(encoding='utf-8').replace('書誌候補 第一巻', series['title'])
    xml = xml.replace('<d:creator>著者 著</d:creator>', '''<d:creator><rdf:Description rdf:about="http://id.ndl.go.jp/auth/entity/01234567">
        <foaf:name xmlns:foaf="http://xmlns.com/foaf/0.1/">著者</foaf:name></rdf:Description></d:creator>''')
    xml = xml.replace('</n:BibResource>', '<d:subject rdf:resource="http://id.ndl.go.jp/class/ndc9/913.6"/></n:BibResource>')
    raw_edition.write_text(xml, encoding='utf-8')
    rhash = hashlib.sha256(raw_edition.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(rhash, str(raw_edition), len(raw_edition.read_bytes()),
        '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    edition.update(title=source['titles'][0]['value'], source_record=source, authors=['著者'], ndc=['913.6'])
    edition['provenance']['raw_sha256'] = rhash
    assert cat.ingest([series])['inserted'] == 1
    assert cat.import_editions([edition])['inserted'] == 1
    manifest = tmp_path / 'author-authorities.json'
    msha = write(manifest, {'version': 'light-novel-author-authorities-v1', 'authors': [{
        'author_qid': 'Q500', 'raw_path': str(raw_series), 'raw_sha256': series['provenance']['raw_sha256']}]})
    return cat, series, edition, manifest, msha


def test_edition_series_membership_requires_all_fields_and_never_creates_original_work(membership_bundle):
    cat, _, _, manifest, msha = membership_bundle
    result = cat.resolve_edition_series_memberships(manifest, msha)
    assert result['linked'] == 1
    row = cat.work_detail('lns:wd:Q1')
    assert row['members'][0]['entry_id'] == 'lne:ndl:R100000002-I1'
    assert row['members'][0]['entry_kind'] == 'edition'
    edition = cat.work_detail('lne:ndl:R100000002-I1')
    assert edition['series_entry_id'] == 'lns:wd:Q1'
    assert edition['relation_resolution'] == 'edition_series_membership_verified'
    assert edition['original_language'] is None
    assert cat.catalogue_stats()['individual_works'] == 0
    assert 'raw_path' not in json.dumps(row)
    assert cat.resolve_edition_series_memberships(manifest, msha)['unchanged'] == 1
    cat.set_read_status('lne:ndl:R100000002-I1', 'read')
    assert cat.work_detail('lns:wd:Q1')['read_status'] == 'unknown'


def test_membership_missing_authority_does_not_link_and_pin_mutation_expires(membership_bundle):
    from pathlib import Path
    cat, _, _, manifest, msha = membership_bundle
    cat.resolve_edition_series_memberships(manifest, msha)
    manifest.write_text('{}', encoding='utf-8')
    assert cat.work_detail('lns:wd:Q1')['members'] == []
    assert cat.work_detail('lne:ndl:R100000002-I1')['relation_resolution'] == 'original_work_and_story_series_unresolved'
    document = {'version': 'light-novel-author-authorities-v1', 'authors': []}
    msha = write(manifest, document)
    result = cat.resolve_edition_series_memberships(manifest, msha)
    assert result['linked'] == 0


def test_membership_is_not_title_author_or_isbn_only(membership_bundle):
    from copy import deepcopy
    cat, _, edition, _, _ = membership_bundle
    series = cat.work_detail('lns:wd:Q1')
    source = edition['source_record']
    authorities = {'Q500': {'01234567'}}
    assert cat._membership_match(series, source, authorities)
    for field, replacement in [('volume', None), ('series', '別ラノベ文庫 ; 1'),
                               ('language', 'eng'), ('titles', [{'value': '魔法科高校の劣等生 コミック'}]),
                               ('contributors', [{'role': 'author', 'name': '著者', 'identifiers': []}]),
                               ('extra', {'ndc_codes': ['726.1']})]:
        wrong = deepcopy(source)
        wrong[field] = replacement
        assert cat._membership_match(series, wrong, authorities) is None
    parallel = deepcopy(source)
    parallel['series'] = 'ラノベ文庫 = LIGHT NOVEL LIBRARY ; 1'
    assert cat._membership_match(series, parallel, authorities)


def test_new_ambiguous_series_invalidates_previously_unique_edition_link(membership_bundle, tmp_path):
    from copy import deepcopy
    from pathlib import Path
    cat, series, _, manifest, msha = membership_bundle
    assert cat.resolve_edition_series_memberships(manifest, msha)['linked'] == 1
    duplicate = deepcopy(series)
    duplicate['id'] = 'Q4'
    raw = Path(series['provenance']['raw_path'])
    payload = json.loads(raw.read_text(encoding='utf-8'))
    payload['entities']['Q4'] = deepcopy(payload['entities']['Q1'])
    payload['entities']['Q4']['id'] = 'Q4'
    newraw = tmp_path / 'duplicate-series.json'
    duplicate['provenance'].update(raw_path=str(newraw), raw_sha256=write(newraw, payload), url='https://www.wikidata.org/wiki/Q4')
    assert cat.ingest([duplicate])['inserted'] == 1
    assert cat.work_detail('lns:wd:Q1')['members'] == []
    assert cat.resolve_edition_series_memberships(manifest, msha)['linked'] == 0


def test_series_title_search_keeps_series_on_first_page_with_32_editions(setup_catalogue, monkeypatch):
    cat, records = setup_catalogue
    cat.ingest(records)
    series = cat.work_detail('lns:wd:Q1')
    editions = [{**series, 'entry_id': f'lne:ndl:R100000002-I{n}', 'entry_kind': 'edition',
                 'title': series['title']} for n in range(1, 33)]
    monkeypatch.setattr(cat, '_rows', lambda **kwargs: [*editions, series])
    result = cat.search_catalogue(series['title'], page_size=30)
    assert result['total'] == 33 and result['items'][0]['entry_id'] == 'lns:wd:Q1'
    assert cat.search_catalogue('魔法科')['items'][0]['entry_kind'] == 'series'
    assert cat.search_catalogue('マホウカ')['items'][0]['entry_kind'] == 'series'


@pytest.fixture
def publisher_ndl_bundle(membership_bundle, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.core import RawArtifact
    from recommend_book.ingestion.ndl import NDLAdapter
    _, _, edition, _, _ = membership_bundle
    raw = Path(edition['provenance']['raw_path'])
    xml = raw.read_text(encoding='utf-8').replace('出版社', 'ホビージャパン').replace('ラノベ文庫', 'HJ文庫')
    raw.write_text(xml, encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), len(raw.read_bytes()),
        '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    edition.update(source_record=source, publisher=source['publisher'], label=source['series'], label_evidence=[{'query_term': 'HJ文庫'}])
    edition['provenance']['raw_sha256'] = digest
    cat = LightNovelCatalogue(tmp_path / 'ndl-only.sqlite')
    assert cat.import_editions([edition])['inserted'] == 1
    page = tmp_path / 'publisher.html'
    page.write_text('''<li class="seriesList_item"><a class="seriesList_itemTitle" href="https://firecross.jp/hjbunko/series/1">魔法科高校の劣等生</a>
        <ul class="book-author-list"><li class="book-author-item"><span class="book-author-type">著者</span>
        <a class="book-author-name">著者</a></li></ul></li>''', encoding='utf-8')
    frames = tmp_path / 'publisher-frames.json'
    fsha = write(frames, [{'id': 'hj-bunko-series', 'publisher': 'ホビージャパン', 'label': 'HJ文庫',
        'pages': [{'source_url': 'https://firecross.jp/hjbunko/series', 'http_status': 200,
            'raw_path': page.name, 'sha256': hashlib.sha256(page.read_bytes()).hexdigest(), 'content_type': 'text/html;charset=utf-8'}]}])
    return cat, edition, frames, fsha, tmp_path, page


def test_publisher_confirmed_ndl_series_is_typed_without_fabricated_work(publisher_ndl_bundle):
    cat, edition, frames, fsha, root, _ = publisher_ndl_bundle
    result = cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)
    assert result['inserted'] == 1
    row = cat.work_detail('lns:ndl:R100000002-I1')
    assert row['qid'] is None and row['original_language'] is None and row['entry_kind'] == 'series'
    assert row['title'] == edition['source_record']['titles'][0]['value']
    assert row['source_url'] == edition['source_url']
    assert row['series_assertion_source_url'] == 'https://firecross.jp/hjbunko/series/1'
    assert row['members'][0]['entry_id'] == 'lne:ndl:R100000002-I1'
    assert cat.catalogue_stats()['individual_works'] == 0
    assert cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)['unchanged'] == 1
    assert 'raw_path' not in json.dumps(row)


def test_publisher_ndl_series_holds_missing_author_and_changed_raw(publisher_ndl_bundle):
    cat, _, frames, fsha, root, page = publisher_ndl_bundle
    text = page.read_text(encoding='utf-8')
    page.write_text(text.replace('book-author-name">著者', 'book-author-name">別人'), encoding='utf-8')
    document = json.loads(frames.read_text(encoding='utf-8'))
    document[0]['pages'][0]['sha256'] = hashlib.sha256(page.read_bytes()).hexdigest()
    fsha = write(frames, document)
    assert cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)['inserted'] == 0
    assert cat.catalogue_stats()['series'] == 0
    page.write_text(text, encoding='utf-8')
    document[0]['pages'][0]['sha256'] = hashlib.sha256(page.read_bytes()).hexdigest()
    fsha = write(frames, document)
    assert cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)['inserted'] == 1
    page.write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='SHA'):
        cat.work_detail('lns:ndl:R100000002-I1')


def test_ndl_series_does_not_duplicate_uncertain_wikidata_title(publisher_ndl_bundle, membership_bundle):
    cat, _, frames, fsha, root, _ = publisher_ndl_bundle
    _, series, _, _, _ = membership_bundle
    assert cat.ingest([series])['inserted'] == 1
    result = cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)
    assert result['inserted'] == 0
    assert result['held_reasons']['potential_wikidata_series_overlap_requires_authority_review'] == 1
    assert cat.catalogue_stats()['series'] == 1
    assert cat.work_detail('lns:ndl:R100000002-I1') is None


def test_official_series_can_have_one_unnumbered_ndl_book(publisher_ndl_bundle):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.core import RawArtifact
    from recommend_book.ingestion.ndl import NDLAdapter
    cat, edition, frames, fsha, root, _ = publisher_ndl_bundle
    raw = Path(edition['provenance']['raw_path'])
    raw.write_text(raw.read_text(encoding='utf-8').replace('<n:volume>1</n:volume>', ''), encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(raw.read_bytes()), RawArtifact(digest, str(raw), len(raw.read_bytes()),
        '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    edition.update(source_record=source, volume=None)
    edition['provenance']['raw_sha256'] = digest
    assert cat.ingest([edition])['updated'] == 1
    assert cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)['inserted'] == 1
    row = cat.work_detail('lns:ndl:R100000002-I1')
    assert len(row['members']) == 1 and row['members'][0]['volume'] is None
    assert row['original_language'] is None and row['year'] is None
