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
    assert len(calls) >= 4  # Relevant evidence is checked anew for separate operations.
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


def test_detail_and_search_do_not_verify_unrelated_native_manifests(setup_catalogue, tmp_path, monkeypatch):
    import recommend_book.light_novel_classification_v1 as native
    cat, records = setup_catalogue
    cat.ingest(records)
    calls = []
    def verify(path, sha):
        calls.append((str(path), sha))
        return {'rows': [{'entry_id': 'lns:wd:Q1', 'scope': 'series', 'labels': [], 'admitted_labels': []}]}
    monkeypatch.setattr(native, 'verify_feature_export', verify)
    cat.import_features(tmp_path / 'manifest.json', 'a' * 64)
    calls.clear()
    assert cat.work_detail('lns:wd:Q2')['entry_id'] == 'lns:wd:Q2'
    assert cat.search_catalogue('ソードアート')['total'] == 1
    assert calls == []
    cat.work_detail('lns:wd:Q1')
    assert len(calls) == 1
    cat.search_catalogue('魔法科')
    assert len(calls) == 2


def test_ndl_batch_normalizes_only_records_used_in_this_request(edition_bundle, monkeypatch):
    from pathlib import Path
    from recommend_book.ingestion.ndl import NDLAdapter
    cat, _, record = edition_bundle
    raw = Path(record['provenance']['raw_path'])
    first = raw.read_text(encoding='utf-8')
    other = first.replace('R100000002-I1', 'R100000002-I2').replace('書誌候補 第一巻', '無関係な別の書誌')
    raw.write_text('<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/">'
        '<srw:records><srw:record><srw:recordData>' + first + '</srw:recordData></srw:record>'
        '<srw:record><srw:recordData>' + other + '</srw:recordData></srw:record></srw:records></srw:searchRetrieveResponse>', encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    record['provenance']['raw_sha256'] = record['source_record']['raw_sha256'] = digest
    calls = []
    original = NDLAdapter._normalize
    def normalize(self, *args, **kwargs):
        calls.append(kwargs.get('locator'))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(NDLAdapter, '_normalize', normalize)
    assert cat.ingest([record])['inserted'] == 1
    assert calls == ['records[1]/recordData']


def test_sbcr_series_membership_uses_bookloop_and_ndl_author_role(publisher_ndl_bundle, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, edition, frames, _, root, page = publisher_ndl_bundle
    raw = Path(edition['provenance']['raw_path'])
    xml = raw.read_text(encoding='utf-8').replace('ホビージャパン', 'SBクリエイティブ').replace('HJ文庫', 'GA文庫')
    raw.write_text(xml, encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), len(raw.read_bytes()),
        '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    edition.update(source_record=source, publisher=source['publisher'], label=source['series'], label_evidence=[{'query_term': 'GA文庫'}])
    edition['provenance']['raw_sha256'] = digest
    assert cat.ingest([edition])['updated'] == 1
    series_url = 'https://ebook.sbcr.jp/lightnovel/series/id/1'
    page.write_text('<a href="/lightnovel/series/id/1">「魔法科高校の劣等生」シリーズ (1)</a>', encoding='utf-8')
    fsha = write(frames, [{'id': 'ga-ebook-series', 'publisher': 'SBクリエイティブ', 'label': 'GA文庫 / GAノベル / ツギクルブックス',
        'pages': [{'source_url': 'https://ebook.sbcr.jp/lightnovel/series', 'http_status': 200,
            'raw_path': page.name, 'sha256': hashlib.sha256(page.read_bytes()).hexdigest(), 'content_type': 'text/html;charset=utf-8'}]}])
    series_page = tmp_path / 'series-page.html'
    series_page.write_text('''<a href="/product/RANKING">無関係なランキング</a>
        <!-- ↓↓↓↓ partial.comic.bookloop ↓↓↓↓ --><a href="/product/BOOK1">魔法科高校の劣等生</a>
        <!-- ↑↑↑↑ partial.comic.bookloop↑↑↑↑ -->''', encoding='utf-8')
    product_page = tmp_path / 'product-page.html'
    product_page.write_text('''<ul class="author clear"><li class="mgnBottom10">著者 / 絵担当</li>
        <li>レーベル：<a>GA文庫</a></li></ul>''', encoding='utf-8')
    def receipt(path, url):
        return {'source_url': url, 'http_status': 200, 'raw_path': path.name,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'content_type': 'text/html;charset=utf-8'}
    book = {'book_url': 'https://ebook.sbcr.jp/product/BOOK1', 'title': '魔法科高校の劣等生'}
    supplemental = tmp_path / 'sbcr-handoff.json'
    handoff = [{'reference_id': 'publisher-series:' + hashlib.sha256(series_url.encode()).hexdigest()[:24],
        'source_url': series_url, 'title': '魔法科高校の劣等生', 'series_source_snapshot': receipt(series_page, series_url),
        'explicit_book_memberships': [book], 'representative_products': [{**book,
            'source_snapshot': receipt(product_page, book['book_url']),
            'fields': {'著者表示': ['著者 / 絵担当'], 'レーベル': 'GA文庫'}}]}]
    ssha = write(supplemental, handoff)
    result = cat.import_ndl_series_from_publishers(frames, fsha, source_root=root,
                                                  sbcr_series_path=supplemental, sbcr_series_sha256=ssha)
    assert result['inserted'] == 1
    row = cat.work_detail('lns:ndl:R100000002-I1')
    assert [a['name'] for a in row['authors']] == ['著者']
    assert row['original_language'] is None and row['isbn'] == []
    handoff[0]['representative_products'][0]['book_url'] = 'https://ebook.sbcr.jp/product/RANKING'
    ssha = write(supplemental, handoff)
    with pytest.raises(ValueError, match='not an explicit member'):
        cat.import_ndl_series_from_publishers(frames, fsha, source_root=root,
                                             sbcr_series_path=supplemental, sbcr_series_sha256=ssha)


def test_gzip_publisher_html_is_decoded_after_raw_hash_binding():
    import gzip
    from recommend_book.light_novel_ndl_series_v1 import _decode
    assert _decode(gzip.compress('<p>公式シリーズ</p>'.encode('utf-8')), 'text/html;charset=utf-8') == '<p>公式シリーズ</p>'


@pytest.mark.parametrize(('raw_title', 'admitted'), [
    ('魔法科高校の劣等生 : 外部書誌の副題', True),
    ('魔法科高校の劣等生 = English Parallel Title : 外部書誌の副題', True),
    ('魔法科高校の劣等生を語る別の小説', False),
    ('魔法科高校の劣等生:独立した題名', False),
])
def test_main_title_is_explicit_rdf_isbd_part_not_fuzzy_prefix(publisher_ndl_bundle, raw_title, admitted):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, edition, frames, fsha, root, _ = publisher_ndl_bundle
    raw = Path(edition['provenance']['raw_path'])
    xml = raw.read_text(encoding='utf-8').replace('魔法科高校の劣等生', raw_title)
    raw.write_text(xml, encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), len(raw.read_bytes()),
        '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    edition.update(source_record=source, title=source['titles'][0]['value'])
    edition['provenance']['raw_sha256'] = digest
    assert cat.ingest([edition])['updated'] == 1
    result = cat.import_ndl_series_from_publishers(frames, fsha, source_root=root)
    assert result['inserted'] == int(admitted)
    if admitted:
        row = cat.work_detail('lns:ndl:R100000002-I1')
        assert row['title'] == '魔法科高校の劣等生'
        assert row['title_origin']['kind'] == 'rdf_title_isbd_main'
        assert row['members'][0]['entry_id'] == 'lne:ndl:R100000002-I1'


def test_typed_read_states_survive_new_python_process(membership_bundle, setup_catalogue):
    import subprocess
    import sys
    cat, series, _, manifest, msha = membership_bundle
    _, records = setup_catalogue
    records[2]['provenance']['conditions_sha256'] = series['provenance']['conditions_sha256']
    assert cat.ingest([records[2]])['inserted'] == 1
    cat.resolve_edition_series_memberships(manifest, msha)
    cat.set_read_status('lns:wd:Q1', 'reading')
    cat.set_read_status('lne:ndl:R100000002-I1', 'read')
    command = [sys.executable, '-c', '''import json, sys
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
c = LightNovelCatalogue(sys.argv[1])
print(json.dumps([c.work_detail('lns:wd:Q1')['read_status'],
                  c.work_detail('lne:ndl:R100000002-I1')['read_status'],
                  c.work_detail('lnw:wd:Q3')['read_status']]))
''', str(cat.db_path)]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    assert json.loads(result.stdout) == ['reading', 'read', 'unknown']


def test_hj_label_alias_requires_two_exact_edition_membership_witnesses(publisher_ndl_bundle, tmp_path):
    from pathlib import Path
    from recommend_book.light_novel_ndl_series_v1 import publisher_assertions
    cat, edition, frames, _, root, _ = publisher_ndl_bundle
    base = Path(edition['provenance']['raw_path']).read_text(encoding='utf-8')
    cards, witnesses = [], []
    def receipt(path, url):
        return {'source_url': url, 'http_status': 200, 'raw_path': path.name,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'content_type': 'text/html;charset=utf-8'}
    for n, title, isbn in [(1, '魔法科高校の劣等生', '9784798613161'), (2, '別の小説シリーズ', '9784798613178')]:
        url = f'https://firecross.jp/hjnovels/series/{n}'
        book_url = f'https://firecross.jp/hjnovels/product/{n}'
        ref = 'publisher-series:' + hashlib.sha256(url.encode()).hexdigest()[:24]
        cards.append(f'''<li class="seriesList_item"><a class="seriesList_itemTitle" href="{url}">{title}</a>
            <ul class="book-author-list"><li class="book-author-item"><span class="book-author-type">著者</span>
            <a class="book-author-name">著者</a></li></ul></li>''')
        ndl = tmp_path / f'alias-ndl-{n}.xml'
        xml = base.replace('魔法科高校の劣等生', title).replace('HJ文庫', 'HJ NOVELS').replace('R100000002-I1', f'R100000002-I{n}')
        xml = xml.replace('</n:BibResource>', f'<d:identifier rdf:datatype="ISBN">{isbn}</d:identifier></n:BibResource>')
        ndl.write_text(xml, encoding='utf-8')
        series = tmp_path / f'alias-series-{n}.html'
        series.write_text('<script type="application/ld+json">' + json.dumps({'@type': 'BookSeries',
            'name': title, 'url': url, 'hasPart': [{'@type': 'Book', 'url': book_url, 'isbn': isbn}]}) + '</script>', encoding='utf-8')
        book = tmp_path / f'alias-book-{n}.html'
        book.write_text('<script type="application/ld+json">' + json.dumps({'@type': 'Book',
            'url': book_url, 'isbn': isbn, 'isPartOf': {'url': url}}) + '</script>', encoding='utf-8')
        witnesses.append({'ndl_record_id': f'R100000002-I{n}', 'ndl_source_url': f'https://ndlsearch.ndl.go.jp/books/R100000002-I{n}',
            'ndl_raw_path': str(ndl), 'ndl_raw_sha256': hashlib.sha256(ndl.read_bytes()).hexdigest(), 'ndl_imprint': 'HJ NOVELS ; 1',
            'publisher_reference': {'reference_id': ref}, 'series_source_snapshot': receipt(series, url),
            'publisher_product_checks': [{'source_snapshot': receipt(book, book_url)}]})
    page = tmp_path / 'alias-index.html'
    page.write_text(''.join(cards), encoding='utf-8')
    fsha = write(frames, [{'id': 'hj-novels-series', 'publisher': 'ホビージャパン', 'label': 'HJノベルス',
                          'pages': [receipt(page, 'https://firecross.jp/hjnovels/series')]}])
    alias = tmp_path / 'alias.json'
    asha = write(alias, witnesses)
    proof = {'frames_path': str(frames), 'frames_sha256': fsha, 'source_root': str(root),
             'label_aliases_path': str(alias), 'label_aliases_sha256': asha}
    rows = publisher_assertions(proof, cat._artifact, {})
    assert len(rows) == 2 and all(row['label_aliases'] == ['HJ NOVELS'] for row in rows.values())
    # Same ISBN without Book.isPartOf proving this series cannot establish the alias.
    book = tmp_path / 'alias-book-2.html'
    book.write_text(book.read_text(encoding='utf-8').replace('/series/2', '/series/999'), encoding='utf-8')
    witnesses[1]['publisher_product_checks'][0]['source_snapshot']['sha256'] = hashlib.sha256(book.read_bytes()).hexdigest()
    proof['label_aliases_sha256'] = write(alias, witnesses)
    with pytest.raises(ValueError, match='exact edition ISBN'):
        publisher_assertions(proof, cat._artifact, {})


def test_official_book_confirms_only_ndl_edition_and_expires_on_raw_change(publisher_ndl_bundle, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, edition, _, _, _, _ = publisher_ndl_bundle
    raw = Path(edition['provenance']['raw_path'])
    xml = raw.read_text(encoding='utf-8').replace('HJ文庫', '電撃文庫').replace('ホビージャパン', '角川書店')
    xml = xml.replace('</n:BibResource>', '<d:identifier rdf:datatype="ISBN">9784798613161</d:identifier></n:BibResource>')
    raw.write_text(xml, encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), len(raw.read_bytes()),
        '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    edition.update(source_record=source, publisher=source['publisher'], label=source['series'], label_evidence=[{'query_term': '電撃文庫'}], isbn=['9784798613161'])
    edition['provenance']['raw_sha256'] = digest
    assert cat.ingest([edition])['updated'] == 1
    publisher_raw = tmp_path / 'official-books.json'
    item = {'itemCode': '123456789012', 'title': '魔法科高校の劣等生', 'isbn': '9784798613161',
            'label_id': 330, 'disp_author_name': '<li>著者 著者</li><li>イラスト 絵担当</li>', 'catchphrase': 'not public'}
    rawsha = write(publisher_raw, {'count_raw': {'book': 1}, 'result': {'book': [item]}})
    manifest = tmp_path / 'official-books-manifest.json'
    body = {'schema_version': 'kadokawa-publisher-books-v1', 'rights_scope': 'reference_only_bibliography',
        'pages': [{'source_url': 'https://www.kadokawa.co.jp/product/search/', 'final_url': 'https://www.kadokawa.co.jp/product/search/',
            'http_status': 200, 'http_method': 'POST', 'request_data': {'label': '330', 'releaseDate': '1', 'size': '20',
                'pageno': '1', 'pageno_book': '1', 'pageno_media': '1'}, 'raw_path': publisher_raw.name, 'sha256': rawsha}]}
    sha = write(manifest, body)
    result = cat.confirm_publisher_editions('kadokawa', manifest, sha)
    assert result['confirmed'] == 1
    row = cat.work_detail('lne:ndl:R100000002-I1')
    assert row['edition_bibliography_confirmed'] and row['light_novel_confirmed']
    assert row['entry_kind'] == 'edition' and row['original_language'] is None
    assert row['publishers'][0]['label'] == '角川書店'
    assert row['source_url'] == source['source_url']
    assert row['confirmation_source_url'] == 'https://www.kadokawa.co.jp/product/123456789012/'
    assert row['members'] == [] and row['llm_features'] == []
    assert cat.catalogue_stats()['series'] == 0 and cat.catalogue_stats()['individual_works'] == 0
    assert cat.confirm_publisher_editions('kadokawa', manifest, sha)['unchanged'] == 1
    assert 'raw_path' not in json.dumps(row) and 'not public' not in json.dumps(row)
    publisher_raw.write_text('{}', encoding='utf-8')
    row = cat.work_detail('lne:ndl:R100000002-I1')
    assert not row['light_novel_confirmed'] and 'edition_bibliography_confirmed' not in row


@pytest.mark.parametrize('label', ['ヤングジャンプコミックス', 'ジャンプコミックスDIGITAL',
    'ヤングジャンプコミックスDIGITAL', 'ダッシュエックス文庫コミックス'])
def test_dx_comics_are_denied_even_when_other_edition_evidence_matches(setup_catalogue, monkeypatch, label):
    cat, _ = setup_catalogue
    source = {'language': 'jpn', 'publisher': '集英社', 'series': label,
        'extra': {'ndc_codes': ['913.6']}, 'identifiers': [{'source': 'isbn', 'value': '9784798613161'}],
        'titles': [{'value': '小説と同名の漫画'}], 'source_url': 'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'contributors': [{'name': '著者', 'role': 'author', 'identifiers': [
            {'source': 'ndl-authority', 'value': 'http://id.ndl.go.jp/auth/entity/01234567'}]}]}
    monkeypatch.setattr(cat, '_source_with_title_parts', lambda record, cache: source)
    book = {'publisher_book_id': '123', 'source_url': 'https://dash.shueisha.co.jp/bookDetail/index/9784798613161',
        'isbn': '9784798613161', 'title': '小説と同名の漫画', 'credits': [{'role': '著者', 'name': '著者'}],
        'publisher': '集英社', 'label': label}
    assert cat._publisher_edition_match('shueisha-dx', book, {}, {}) is None


@pytest.fixture
def publisher_edition_v2(edition_bundle):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from xml.sax.saxutils import escape
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, _, template = edition_bundle

    def build(*, title='主書名 : 副書名', volume='3', official_title='主書名３ 副書名',
              imprint='角川スニーカー文庫', label='角川スニーカー文庫', label_id=339,
              publisher='KADOKAWA', authority=True, literal='著者 著', extra='', ndc='913.6', publisher_agent=False):
        record = json.loads(json.dumps(template))
        raw = Path(record['provenance']['raw_path'])
        url = record['source_url']
        creator = ('<d:creator><rdf:Description rdf:about="http://id.ndl.go.jp/auth/entity/01234567">'
                   '<f:name>著者</f:name></rdf:Description></d:creator>') if authority else ''
        publisher_xml = ('<d:publisher><f:Agent><f:name>' + escape(publisher) + '</f:name></f:Agent></d:publisher>') if publisher_agent else '<d:publisher>' + escape(publisher) + '</d:publisher>'
        xml = f'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
          xmlns:d="http://purl.org/dc/terms/" xmlns:c="http://purl.org/dc/elements/1.1/"
          xmlns:n="http://ndl.go.jp/dcndl/terms/" xmlns:f="http://xmlns.com/foaf/0.1/">
          <n:BibResource rdf:about="{url}#material"><d:title>{escape(title + ('. ' + volume if volume else ''))}</d:title>
          <c:title><rdf:Description><rdf:value>{escape(title)}</rdf:value></rdf:Description></c:title>
          {('<n:volume>' + escape(volume) + '</n:volume>') if volume else ''}{creator}
          {('<c:creator>' + escape(literal) + '</c:creator>') if literal else ''}
          {publisher_xml}<d:language>jpn</d:language>
          <n:seriesTitle>{escape(imprint)}</n:seriesTitle><d:identifier rdf:datatype="ISBN">9784798613161</d:identifier>
          <d:subject rdf:resource="http://id.ndl.go.jp/class/ndc10/{ndc}"/>{extra}</n:BibResource></rdf:RDF>'''
        raw.write_text(xml, encoding='utf-8')
        digest = hashlib.sha256(raw.read_bytes()).hexdigest()
        source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), len(raw.read_bytes()),
            '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
        record.update(source_record=source, title=source['titles'][0]['value'], volume=source['volume'],
            authors=[a['name'] for a in source['contributors'] if a['role'] == 'author'],
            publisher=source['publisher'], date=source['publication_year'],
            isbn=[i['value'] for i in source['identifiers'] if i['source'] == 'isbn'],
            label=source['series'], ndc=[ndc], label_evidence=[{'query_term': imprint}])
        record['provenance']['raw_sha256'] = digest
        book = {'publisher_book_id': 'kadokawa:123456789012', 'source_url': 'https://www.kadokawa.co.jp/product/123456789012/',
            'isbn': '9784798613161', 'title': official_title, 'credits': [{'role': '著者', 'name': '著者'}],
            'publisher': 'KADOKAWA', 'label': label, 'official_label_id': label_id, 'product_form_id': 3}
        return cat, record, book
    return build


def test_publisher_edition_v1_proof_is_unchanged(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='主書名', volume=None, official_title='主書名')
    assert cat._publisher_edition_match('kadokawa', book, record, {}) == {
        'rule_version': 'publisher-ndl-edition-confirmation-v1', 'scope': 'edition_only',
        'isbn13': '9784798613161', 'matched_ndl_title': '主書名',
        'authors': [{'official_credit': '著者', 'ndl_authorities': ['http://id.ndl.go.jp/auth/entity/01234567']}],
        'ndl_imprint': '角川スニーカー文庫', 'official_label': '角川スニーカー文庫',
        'ndl_publisher': 'KADOKAWA', 'original_work_identity_asserted': False,
        'story_series_identity_asserted': False, 'sources': [record['source_url'], book['source_url']]}


@pytest.mark.parametrize('official_title,version', [('主書名３ 副書名', 'v2'), ('主書名 副書名３', 'v1')])
def test_publisher_edition_v2_composes_all_title_parts_with_exact_volume(publisher_edition_v2, official_title, version):
    cat, record, book = publisher_edition_v2(official_title=official_title)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-' + version
    if version == 'v2':
        assert proof['title_match']['volume'] == '3' and proof['title_match']['subtitle'] == '副書名'
        assert proof['title_match']['locators'] and not proof['story_series_identity_asserted']
    book['title'] = official_title.replace('３', '２')
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_publisher_edition_v2_wave_decoration_keeps_complete_subtitle(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='17歳と、18以上 : 後輩ギャル', volume=None,
        official_title='17歳と、18以上 ～後輩ギャル～')
    assert cat._publisher_edition_match('kadokawa', book, record, {})['title_match']['subtitle'] == '後輩ギャル'
    book['title'] = '17歳と、18以上 ～別の後輩ギャル～'
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_publisher_edition_v2_never_uses_contents_title(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='本の正式題', volume=None, official_title='別作品 ～副題～',
        extra='<d:tableOfContents><rdf:Description><d:title>別作品 : 副題</d:title></rdf:Description></d:tableOfContents>')
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_publisher_edition_v2_never_uses_contents_isbn_or_electronic_format(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(extra='<d:tableOfContents><rdf:Description>'
        '<d:identifier rdf:datatype="ISBN">9784041173701</d:identifier></rdf:Description></d:tableOfContents>')
    book['isbn'] = '9784041173701'
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None
    book['isbn'] = '9784798613161'
    book['product_form_id'] = 13
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


@pytest.mark.parametrize('change,accepted', [({}, True), ({'label_id': 330}, False),
    ({'publisher': '別出版社'}, False), ({'imprint': '富士見ファンタジア文庫コミックス'}, False)])
def test_publisher_edition_v2_fantasia_pair_is_edition_scoped(publisher_edition_v2, change, accepted):
    options = dict(title='主書名', volume=None, official_title='主書名', imprint='富士見ファンタジア文庫',
        label='ファンタジア文庫', label_id=344)
    options.update(change)
    cat, record, book = publisher_edition_v2(**options)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert bool(proof) is accepted
    if proof:
        assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-v2'
        assert proof['imprint_match']['official_label_id'] == 344
        assert proof['scope'] == 'edition_only'


@pytest.mark.parametrize('literal,accepted', [('著者 著', True), ('著者', False), ('著者 イラスト', False)])
def test_publisher_edition_v2_missing_authority_requires_explicit_writer_literal(publisher_edition_v2, literal, accepted):
    cat, record, book = publisher_edition_v2(title='主書名', volume=None, official_title='主書名', authority=False, literal=literal)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert bool(proof) is accepted
    if proof:
        assert proof['authors'][0]['authority_status'] == 'unknown'
        assert proof['authors'][0]['ndl_authorities'] == []
        assert proof['authors'][0]['ndl_literal_credit'] == '著者 著'


def test_publisher_edition_v2_unknown_authority_cannot_replace_publisher_identity(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='主書名', volume=None, official_title='主書名',
        authority=False, publisher='別出版社')
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_existing_v2_proof_shape_values_and_selection_remain_stable(publisher_edition_v2):
    cat, record, book = publisher_edition_v2()
    base = 'records[1]/recordData/dcndl:BibResource[@rdf:about=' + record['source_url'] + '#material]'
    title_locator = base + '/{http://purl.org/dc/terms/}title[1]'
    volume_locator = base + '/{http://ndl.go.jp/dcndl/terms/}volume[3]'
    assert cat._publisher_edition_match('kadokawa', book, record, {}) == {
        'rule_version': 'publisher-ndl-edition-confirmation-v2', 'scope': 'edition_only',
        'isbn13': '9784798613161', 'matched_ndl_title': '主書名 : 副書名. 3',
        'title_match': {'rule': 'primary_isbd_title_and_explicit_volume', 'source_value': '主書名 : 副書名. 3',
            'main': '主書名', 'subtitle': '副書名', 'volume': '3', 'locators': [title_locator, volume_locator]},
        'authors': [{'official_credit': '著者', 'ndl_authorities': ['http://id.ndl.go.jp/auth/entity/01234567'],
            'authority_status': 'known', 'ndl_literal_credit': '著者 著',
            'locator': base + '/{http://purl.org/dc/elements/1.1/}creator[5]'}],
        'ndl_imprint': '角川スニーカー文庫', 'official_label': '角川スニーカー文庫',
        'imprint_match': {'rule': 'exact_label', 'ndl_imprint': '角川スニーカー文庫', 'official_label': '角川スニーカー文庫'},
        'ndl_publisher': 'KADOKAWA', 'official_product_form_id': 3,
        'source_raw_sha256': record['provenance']['raw_sha256'],
        'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
        'sources': [record['source_url'], book['source_url']]}


@pytest.mark.parametrize('title', ['副書名 主書名３', '副書名３ 主書名'])
def test_publisher_edition_v3_reverse_preserves_every_title_part_and_volume(publisher_edition_v2, title):
    cat, record, book = publisher_edition_v2(official_title=title)
    proof = cat._publisher_edition_match('kadokawa', book, record, {})
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-v3'
    assert proof['title_match']['rule'] == 'primary_isbd_reverse_title_and_explicit_volume_v1'
    assert proof['title_match']['main'] == '主書名' and proof['title_match']['subtitle'] == '副書名'
    assert proof['title_match']['volume'] == '3' and len(proof['title_match']['locators']) == 2
    for false_title in (title.replace('３', '２'), title.replace('副書名', ''), title.replace('主書名', '別書名')):
        book['title'] = false_title
        assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


def test_publisher_edition_v3_contents_and_unapproved_writer_roles_remain_held(publisher_edition_v2):
    cat, record, book = publisher_edition_v2(title='正式主書名 : 正式副書名', official_title='副書名 主書名３',
        extra='<d:tableOfContents><rdf:Description><d:title>主書名 : 副書名. 3</d:title></rdf:Description></d:tableOfContents>')
    assert cat._publisher_edition_match('kadokawa', book, record, {}) is None
    cat, record, book = publisher_edition_v2(official_title='副書名 主書名３')
    for role in ('Author', '編者', 'イラスト'):
        book['credits'][0]['role'] = role
        assert cat._publisher_edition_match('kadokawa', book, record, {}) is None


@pytest.fixture
def publisher_confirmation_pair(publisher_edition_v2, tmp_path):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter, SRU
    from recommend_book.ingestion.core import RawArtifact

    def build(*, shared_raw=False, second_isbn='9784041173701'):
        cat, first, book = publisher_edition_v2(title='主書名', volume=None, official_title='主書名')
        first_xml = Path(first['provenance']['raw_path']).read_text(encoding='utf-8')
        second_xml = first_xml.replace('R100000002-I1', 'R100000002-I2').replace('9784798613161', second_isbn)
        if shared_raw:
            combined = ET.Element(f'{{{SRU}}}searchRetrieveResponse')
            for xml in (first_xml, second_xml):
                data = ET.SubElement(combined, f'{{{SRU}}}recordData')
                data.append(ET.fromstring(xml))
            shared_path = tmp_path / 'two-editions.xml'
            shared_path.write_bytes(ET.tostring(combined, encoding='utf-8'))
        records, paths = [], []
        for index, xml in enumerate((first_xml, second_xml), 1):
            record = json.loads(json.dumps(first))
            raw = shared_path if shared_raw else tmp_path / f'one-edition-{index}.xml'
            if not shared_raw:
                raw.write_text(xml, encoding='utf-8')
            digest = hashlib.sha256(raw.read_bytes()).hexdigest()
            source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(digest, str(raw), len(raw.read_bytes()),
                '2026-10-24T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac',
                locator=f'records[{index if shared_raw else 1}]/recordData').model_dump(mode='json')
            record.update(id=source['source_id'], source_url=source['source_url'], source_record=source,
                isbn=[i['value'] for i in source['identifiers'] if i['source'] == 'isbn'])
            record['provenance'].update(url=source['source_url'], raw_path=str(raw), raw_sha256=digest)
            records.append(record)
            paths.append(raw)
        assert cat.import_editions(records)['inserted'] == 2
        return cat, records, book, paths
    return build


def test_book_confirmation_opens_only_requested_isbn_candidates(publisher_confirmation_pair, monkeypatch, tmp_path):
    cat, _, book, paths = publisher_confirmation_pair()
    paths[1].write_bytes(b'unrelated raw is unavailable')
    monkeypatch.setattr(cat, '_publisher_books', lambda *args: [book])
    result = cat.confirm_publisher_editions('kadokawa', tmp_path / 'fixed.json', 'f' * 64)
    assert result['confirmed'] == 1
    with cat._connect() as db:
        assert db.execute('SELECT edition_id FROM edition_book_confirmations').fetchone()[0] == 'lne:ndl:R100000002-I1'


@pytest.mark.parametrize('tamper', ['sha', 'typed_id', 'rehashed_projection'])
def test_book_confirmation_rejects_target_projection_tampering(publisher_confirmation_pair, monkeypatch, tmp_path, tamper):
    cat, records, book, _ = publisher_confirmation_pair()
    monkeypatch.setattr(cat, '_publisher_books', lambda *args: [book])
    with cat._connect() as db:
        if tamper == 'sha':
            db.execute('UPDATE entries SET record_sha256=? WHERE entry_id=?', ('f' * 64, 'lne:ndl:R100000002-I1'))
        elif tamper == 'typed_id':
            db.execute('UPDATE entries SET entry_id=? WHERE entry_id=?', ('lne:ndl:R100000002-Ifake', 'lne:ndl:R100000002-I1'))
        else:
            records[0]['publisher'] = '偽出版社'
            encoded = json.dumps(records[0], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
                (encoded, hashlib.sha256(encoded.encode()).hexdigest(), 'lne:ndl:R100000002-I1'))
    with pytest.raises(ValueError):
        cat.confirm_publisher_editions('kadokawa', tmp_path / 'fixed.json', 'f' * 64)


def test_book_confirmation_revalidates_second_record_even_when_raw_is_cached(publisher_confirmation_pair, monkeypatch, tmp_path):
    cat, records, book, _ = publisher_confirmation_pair(shared_raw=True, second_isbn='9784798613161')
    monkeypatch.setattr(cat, '_publisher_books', lambda *args: [book])
    records[1]['publisher'] = '偽出版社'
    encoded = json.dumps(records[1], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with cat._connect() as db:
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), 'lne:ndl:R100000002-I2'))
    with pytest.raises(ValueError, match='fields differ'):
        cat.confirm_publisher_editions('kadokawa', tmp_path / 'fixed.json', 'f' * 64)
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM edition_book_confirmations').fetchone()[0] == 0


@pytest.fixture
def iris_neo_edition(publisher_edition_v2):
    def build(**changes):
        options = dict(title='四竜帝の大陸', volume=None, official_title='四竜帝の大陸',
            imprint='IRIS NEO', publisher='一迅社', label='アイリスNEO')
        options.update(changes)
        cat, record, book = publisher_edition_v2(**options)
        book['publisher'] = '一迅社'
        return cat, record, book
    return build


def test_iris_neo_bridge_is_same_isbn_edition_only_with_known_authority(iris_neo_edition):
    cat, record, book = iris_neo_edition()
    proof = cat._publisher_edition_match('ichijinsha-iris', book, record, {})
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-iris-neo-v1'
    assert proof['scope'] == 'edition_only' and proof['isbn13'] == '9784798613161'
    assert proof['imprint_match'] == {'rule': 'same_edition_ichijinsha_iris_neo_pair',
        'official_label': 'アイリスNEO', 'ndl_imprint': 'IRIS NEO', 'scope': 'this_isbn_edition_only'}
    assert proof['authors'][0]['ndl_authorities'] == ['http://id.ndl.go.jp/auth/entity/01234567']
    assert proof['primary_title_locator'] and not proof['original_work_identity_asserted']
    assert not proof['story_series_identity_asserted']


@pytest.mark.parametrize('change', [{'publisher': '別出版社'}, {'label': '一迅社文庫アイリス'},
    {'imprint': 'IRIS NEOアンソロジー'}, {'authority': False}, {'ndc': '726.5'},
    {'title': '四竜帝の大陸 : 省略不可の副題'},
    {'title': '別の正式題', 'extra': '<d:tableOfContents><rdf:Description><d:title>四竜帝の大陸</d:title></rdf:Description></d:tableOfContents>'}])
def test_iris_neo_bridge_rejects_different_scope_or_incomplete_evidence(iris_neo_edition, change):
    cat, record, book = iris_neo_edition(**change)
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None


def test_iris_neo_bridge_rejects_wrong_isbn_provider_or_editor(iris_neo_edition):
    cat, record, book = iris_neo_edition()
    book['isbn'] = '9784041173701'
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None
    book['isbn'] = '9784798613161'
    assert cat._publisher_edition_match('shueisha-dx', book, record, {}) is None
    book['credits'][0]['role'] = '編者'
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None


def test_iris_neo_original_bridge_proof_remains_unchanged(iris_neo_edition):
    cat, record, book = iris_neo_edition()
    locator = 'records[1]/recordData/dcndl:BibResource[@rdf:about=' + record['source_url'] + '#material]/{http://purl.org/dc/terms/}title[1]'
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) == {
        'rule_version': 'publisher-ndl-edition-confirmation-iris-neo-v1', 'scope': 'edition_only',
        'isbn13': '9784798613161', 'matched_ndl_title': '四竜帝の大陸', 'primary_title_locator': locator,
        'source_raw_sha256': record['provenance']['raw_sha256'],
        'authors': [{'official_credit': '著者', 'ndl_authorities': ['http://id.ndl.go.jp/auth/entity/01234567']}],
        'ndl_imprint': 'IRIS NEO', 'official_label': 'アイリスNEO', 'ndl_publisher': '一迅社',
        'imprint_match': {'rule': 'same_edition_ichijinsha_iris_neo_pair', 'official_label': 'アイリスNEO',
            'ndl_imprint': 'IRIS NEO', 'scope': 'this_isbn_edition_only'},
        'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
        'sources': [record['source_url'], book['source_url']]}


def test_iris_neo_explicit_distribution_role_preserves_original_publisher_values(iris_neo_edition):
    extra = '<d:publisher><f:Agent><f:name>講談社</f:name><d:description>頒布</d:description></f:Agent></d:publisher>'
    cat, record, book = iris_neo_edition(publisher_agent=True, extra=extra)
    proof = cat._publisher_edition_match('ichijinsha-iris', book, record, {})
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-iris-neo-v2'
    assert proof['ndl_publisher'] == record['source_record']['publisher'] == '一迅社; 講談社'
    assert proof['publisher_match']['rule'] == 'unique_publisher_and_explicit_distribution_agents'
    assert proof['publisher_match']['distribution_agents'][0]['name'] == '講談社'
    assert proof['publisher_match']['distribution_agents'][0]['description'] == '頒布'
    assert proof['publisher_match']['distribution_agents'][0]['description_locator']


@pytest.mark.parametrize('extra', [
    '<d:publisher><f:Agent><f:name>講談社</f:name></f:Agent></d:publisher>',
    '<d:publisher><f:Agent><f:name>講談社</f:name><d:description>出版</d:description></f:Agent></d:publisher>',
    '<d:publisher>講談社</d:publisher>',
    '<d:publisher><f:Agent><f:name>一迅社</f:name></f:Agent></d:publisher><d:publisher><f:Agent><f:name>講談社</f:name><d:description>頒布</d:description></f:Agent></d:publisher>'])
def test_iris_neo_unknown_or_multiple_actual_publishers_remain_held(iris_neo_edition, extra):
    cat, record, book = iris_neo_edition(publisher_agent=True, extra=extra)
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None


@pytest.mark.parametrize('official_title,volume', [('主書名３ 副書名', '3'), ('主書名 副書名３', '3'),
    ('主書名～副書名～', None)])
def test_iris_neo_complete_title_variants_keep_every_part(iris_neo_edition, official_title, volume):
    cat, record, book = iris_neo_edition(title='主書名 : 副書名', official_title=official_title, volume=volume)
    proof = cat._publisher_edition_match('ichijinsha-iris', book, record, {})
    if volume == '3' and official_title == '主書名 副書名３':
        assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-iris-neo-v1'
    else:
        assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-iris-neo-v2'
        assert proof['title_match']['main'] == '主書名' and proof['title_match']['subtitle'] == '副書名'
        assert proof['title_match']['volume'] == volume
    for false_title in ('主書名', '副書名', '主書名２ 副書名'):
        book['title'] = false_title
        assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None


def test_iris_neo_explicit_volume_subtitle_and_unknown_publisher_are_not_dropped(iris_neo_edition):
    cat, record, book = iris_neo_edition(title='主書名', volume='[2] (巻副題)', official_title='主書名2')
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None
    book['title'] = '主書名 巻副題'
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None
    cat, record, book = iris_neo_edition(title='主書名 : 副書名', volume='3', official_title='主書名３ 副書名',
        publisher_agent=True, extra='<d:publisher><f:Agent/></d:publisher>')
    assert cat._publisher_edition_match('ichijinsha-iris', book, record, {}) is None


def test_iris_neo_distribution_and_complete_title_extensions_work_together(iris_neo_edition):
    cat, record, book = iris_neo_edition(title='主書名 : 副書名', volume='3', official_title='主書名３～副書名～',
        publisher_agent=True, extra='<d:publisher><f:Agent><f:name>講談社</f:name><d:description>頒布</d:description></f:Agent></d:publisher>')
    proof = cat._publisher_edition_match('ichijinsha-iris', book, record, {})
    assert proof['rule_version'] == 'publisher-ndl-edition-confirmation-iris-neo-v2'
    assert proof['publisher_match']['distribution_agents'] and proof['title_match']['volume'] == '3'


def test_series_delta_scope_avoids_unrelated_raw_and_reuses_same_cache(publisher_ndl_bundle, tmp_path, monkeypatch):
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter
    from recommend_book.ingestion.core import RawArtifact
    cat, edition, frames, sha, root, _ = publisher_ndl_bundle
    other = json.loads(json.dumps(edition))
    raw = tmp_path / 'unrelated-edition.xml'
    raw.write_text(Path(edition['provenance']['raw_path']).read_text(encoding='utf-8')
        .replace('R100000002-I1', 'R100000002-I2').replace(edition['title'], '無関係な別書名'), encoding='utf-8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(raw.read_bytes()), RawArtifact(digest, str(raw), raw.stat().st_size,
        edition['source_record']['retrieved_at'], 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    other.update(id=source['source_id'], source_url=source['source_url'], title=source['titles'][0]['value'], source_record=source)
    other['provenance'].update(url=source['source_url'], raw_path=str(raw), raw_sha256=digest)
    assert cat.import_editions([other])['inserted'] == 1
    raw.write_bytes(b'unrelated source is unavailable')
    target_raw = Path(edition['provenance']['raw_path'])
    real_read = Path.read_bytes
    reads = []
    def observed_read(path):
        if path == target_raw:
            reads.append(path)
        return real_read(path)
    monkeypatch.setattr(Path, 'read_bytes', observed_read)
    monkeypatch.setattr(cat, '_rows', lambda **kwargs: pytest.fail('full rows must not run inside series import'))
    monkeypatch.setattr(cat, '_publisher_books', lambda *args: pytest.fail('unneeded book confirmation replay'))
    reference = 'publisher-series:' + hashlib.sha256(b'https://firecross.jp/hjbunko/series/1').hexdigest()[:24]
    result = cat.import_ndl_series_from_publishers(frames, sha, source_root=root, reference_ids={reference})
    assert result['inserted'] == 1 and result['verified_edition_memberships'] == 1
    assert len(reads) == 1


@pytest.mark.parametrize('tamper', ['sha', 'rehashed_projection', 'second_cached_record_identity'])
def test_series_delta_revalidates_each_target_projection(publisher_ndl_bundle, tamper):
    cat, edition, frames, sha, root, _ = publisher_ndl_bundle
    with cat._connect() as db:
        if tamper == 'sha':
            db.execute('UPDATE entries SET record_sha256=?', ('f' * 64,))
        elif tamper == 'rehashed_projection':
            edition['publisher'] = '偽出版社'
            encoded = json.dumps(edition, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            db.execute('UPDATE entries SET record_json=?,record_sha256=?', (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
        else:
            row = db.execute('SELECT * FROM entries').fetchone()
            db.execute('INSERT INTO entries VALUES(?,?,?,?)',
                ('lne:ndl:R100000002-I2', 'edition', row['record_json'], row['record_sha256']))
    reference = 'publisher-series:' + hashlib.sha256(b'https://firecross.jp/hjbunko/series/1').hexdigest()[:24]
    with pytest.raises(ValueError):
        cat.import_ndl_series_from_publishers(frames, sha, source_root=root, reference_ids={reference})
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM entries WHERE kind=?', ('series',)).fetchone()[0] == 0


def test_series_delta_preserves_completed_records_without_reingesting(publisher_ndl_bundle, monkeypatch):
    cat, _, frames, sha, root, _ = publisher_ndl_bundle
    # The existing unscoped API remains compatible.
    assert cat.import_ndl_series_from_publishers(frames, sha, source_root=root)['inserted'] == 1
    with cat._connect() as db:
        before = [tuple(row) for row in db.execute('SELECT * FROM entries WHERE kind=?', ('series',))]
        memberships = [tuple(row) for row in db.execute('SELECT * FROM edition_series_memberships')]
    monkeypatch.setattr(cat, 'ingest', lambda *args, **kwargs: pytest.fail('completed series must not be reingested'))
    reference = 'publisher-series:' + hashlib.sha256(b'https://firecross.jp/hjbunko/series/1').hexdigest()[:24]
    result = cat.import_ndl_series_from_publishers(frames, sha, source_root=root, reference_ids={reference})
    assert result['unchanged'] == 1 and result['inserted'] == 0
    with cat._connect() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM entries WHERE kind=?', ('series',))] == before
        assert [tuple(row) for row in db.execute('SELECT * FROM edition_series_memberships')] == memberships


@pytest.fixture
def bulk_bundle(edition_bundle, tmp_path):
    from pathlib import Path
    cat, _, edition = edition_bundle
    conditions = Path(edition['provenance']['conditions_path'])
    policy = json.loads(conditions.read_text())
    policy['version'] = 'light-novel-source-conditions-bulk-v2'
    policy['ndl'].update(provider='iss-ndl-opac-national', source_provider_evidence_url='https://ndlsearch.ndl.go.jp/bib/help')
    policy['ndl']['usages']['llm_input'] = False
    for i, url in enumerate(('https://ndlsearch.ndl.go.jp/bib/help', 'https://www.ndl.go.jp/data/data_service/jnb')):
        raw = tmp_path / f'national-help-{i}'
        raw.write_text('official conditions')
        policy['snapshots'].append({'url': url, 'raw_path': str(raw), 'raw_sha256': hashlib.sha256(raw.read_bytes()).hexdigest()})
    condition_path = tmp_path / 'bulk-conditions.json'
    condition_sha = write(condition_path, policy)
    from test_light_novel_ndl_bulk_v1 import document
    raw = tmp_path / 'bulk.xml'
    raw.write_text(document(), encoding='utf-8')
    receipt = {'version': 'ndl-official-bulk-ui-probe-v2', 'source_provider': 'iss-ndl-opac-national',
        'source_url': 'https://ndlsearch.ndl.go.jp/bib?cs=marc&keyword=test',
        'official_help': 'https://ndlsearch.ndl.go.jp/bib/help', 'retrieved_at': '2026-10-24T00:00:00Z',
        'ui_total': 1, 'documents': 1, 'admin_records': 1, 'bibliographic_records': 1, 'unique_ids': 1,
        'bytes': raw.stat().st_size, 'raw_path': str(raw), 'raw_sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
        'repository_ids': ['R100000002'], 'all_count_match': True, 'maximum_selected': 5000,
        'conditions_path': str(condition_path), 'conditions_sha256': condition_sha}
    path = tmp_path / 'bulk-receipt.json'
    return cat, edition, path, write(path, receipt), raw


def test_bulk_own_bibliography_roundtrip_and_raw_revalidation(bulk_bundle):
    cat, _, path, sha, raw = bulk_bundle
    assert cat.import_ndl_bulk(path, sha)['inserted'] == 1
    row = cat.work_detail('lne:ndl:R100000002-I1')
    assert row['title'] == '日本語小説'
    assert row['publishers'] == [{'qid': None, 'label': '出版社'}]
    assert row['source_attributions'][0]['provider'] == 'iss-ndl-opac-national'
    assert not row['light_novel_confirmed'] and row['original_language'] is None
    assert cat.import_ndl_bulk(path, sha)['existing_identities_preserved'] == 1
    assert '他館' not in json.dumps(row, ensure_ascii=False)
    raw.write_bytes(b'changed')
    with pytest.raises(ValueError):
        cat.work_detail('lne:ndl:R100000002-I1')


def test_bulk_preserves_existing_sru_identity_and_history(bulk_bundle):
    cat, edition, path, sha, _ = bulk_bundle
    cat.ingest([edition])
    cat.set_read_status('lne:ndl:R100000002-I1', 'reading')
    result = cat.import_ndl_bulk(path, sha)
    assert result['inserted'] == 0 and result['existing_identities_preserved'] == 1
    row = cat.work_detail('lne:ndl:R100000002-I1')
    assert row['title'] == edition['title'] and row['read_status'] == 'reading'
    assert row['source_attributions'][0]['provider'] == 'iss-ndl-opac'


def test_narrow_search_and_detail_authenticate_only_candidates_and_relatives(setup_catalogue, monkeypatch):
    from pathlib import Path
    cat, records = setup_catalogue
    cat.ingest(records)
    original = cat._project
    calls = []
    def project(record, cache):
        calls.append(record['id'])
        return original(record, cache)
    monkeypatch.setattr(cat, '_project', project)
    Path(records[1]['provenance']['raw_path']).write_text('{}')
    assert cat.search_catalogue('マホウカ')['total'] == 1
    assert set(calls) == {'Q1', 'Q3'}
    calls.clear()
    assert cat.work_detail('lns:wd:Q1')['members'][0]['entry_id'] == 'lnw:wd:Q3'
    assert set(calls) == {'Q1', 'Q3'}
    with pytest.raises(ValueError, match='SHA'):
        cat.catalogue_stats()


def test_narrow_search_does_not_publish_forged_saved_projection(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    record = records[1]
    record['title'] = '偽装題名'
    encoded = json.dumps(record, ensure_ascii=False)
    with cat._connect() as db:
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), 'lns:wd:Q2'))
    with pytest.raises(ValueError, match='title'):
        cat.search_catalogue('偽装題名')


def test_narrow_search_checks_all_saved_record_digests(setup_catalogue):
    cat, records = setup_catalogue
    cat.ingest(records)
    with cat._connect() as db:
        db.execute("UPDATE entries SET record_json='{}' WHERE entry_id='lns:wd:Q2'")
    with pytest.raises(ValueError, match='SHA'):
        cat.search_catalogue('魔法科')


@pytest.mark.parametrize('provider,expected', [('iss-ndl-opac-national', True), ('aozora', False)])
def test_publisher_series_matching_accepts_authenticated_national_provider_only(publisher_ndl_bundle, provider, expected):
    from recommend_book.light_novel_ndl_series_v1 import publisher_assertions, match_bibliography
    cat, edition, frames, sha, root, _ = publisher_ndl_bundle
    proof = {'frames_path': str(frames), 'frames_sha256': sha, 'source_root': str(root)}
    assertion = next(iter(publisher_assertions(proof, cat._artifact, {}).values()))
    source = {**edition['source_record'], 'provider_id': provider}
    assert bool(match_bibliography(assertion, source, normalize_search)) is expected


def test_source_only_recommendation_verifies_liked_and_selected_features_only(setup_catalogue, monkeypatch):
    cat, records = setup_catalogue
    cat.ingest(records)
    calls = []
    def verify(entry_ids=None):
        calls.append(entry_ids)
        assert entry_ids is not None  # An irrelevant native window must not be opened.
        return {}
    monkeypatch.setattr(cat, '_feature_rows', verify)
    result = cat.recommend_preferences(liked_entry_ids=['lns:wd:Q1'])
    assert [r['entry_id'] for r in result['items']] == ['lns:wd:Q2']
    assert result['items'][0]['reasons'][0]['kind'] == 'source_genre'
    assert calls == [{'lns:wd:Q1'}, {'lns:wd:Q2'}]


def test_explicit_subseries_p179_deduplicates_recommendations_without_copying_features(setup_catalogue):
    from pathlib import Path
    cat, records = setup_catalogue
    child = records[2]
    child['kind'] = 'series'
    raw = Path(child['provenance']['raw_path'])
    payload = json.loads(raw.read_text(encoding='utf8'))
    payload['entities']['Q3']['claims']['P31'] = [claim('Q104213567')]
    child['provenance']['raw_sha256'] = write(raw, payload)
    cat.ingest(records)
    result = cat.recommend_preferences(liked_entry_ids=['lns:wd:Q2'])
    assert len(result['items']) == 1
    assert cat.work_detail('lns:wd:Q1')['members'][0]['entry_id'] == 'lns:wd:Q3'
    assert cat.work_detail('lns:wd:Q3')['llm_features'] == []
    cat.set_read_status('lns:wd:Q3', 'read')
    assert cat.work_detail('lns:wd:Q1')['read_status'] == 'unknown'
    assert cat.similar_works('lns:wd:Q2')['items'] == []


def test_failed_feature_manifest_is_verified_once_per_operation(setup_catalogue, monkeypatch, tmp_path):
    from recommend_book import light_novel_classification_v1 as verifier
    cat, records = setup_catalogue
    cat.ingest(records)
    path, sha = str(tmp_path / 'changed-native.json'), 'f' * 64
    with cat._connect() as db:
        db.executemany('INSERT INTO features VALUES(?,?,?)',
            [(identity, path, sha) for identity in ('lns:wd:Q1', 'lns:wd:Q2')])
    calls = []
    def reject(path, sha):
        calls.append((path, sha))
        raise ValueError('changed protected native artifact')
    monkeypatch.setattr(verifier, 'verify_feature_export', reject)
    assert cat._feature_rows() == {}
    assert calls == [(path, sha)]
    assert cat._feature_rows() == {}
    assert calls == [(path, sha), (path, sha)]  # A new operation retries verification.


def test_failed_book_manifest_is_verified_once_per_operation(edition_bundle, monkeypatch, tmp_path):
    from copy import deepcopy
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.core import RawArtifact
    from recommend_book.ingestion.ndl import NDLAdapter
    cat, _, edition = edition_bundle
    second = deepcopy(edition)
    raw = tmp_path / 'second-edition.xml'
    raw.write_text(Path(edition['provenance']['raw_path']).read_text(encoding='utf8')
        .replace('R100000002-I1', 'R100000002-I2'), encoding='utf8')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(ET.fromstring(raw.read_bytes()), RawArtifact(digest,
        str(raw), raw.stat().st_size, edition['source_record']['retrieved_at'],
        'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    second.update(id=source['source_id'], source_url=source['source_url'], source_record=source)
    second['provenance'].update(url=source['source_url'], raw_path=str(raw), raw_sha256=digest)
    assert cat.ingest([edition, second])['inserted'] == 2
    rows = cat._rows(include_features=False)
    with cat._connect() as db:
        stored = db.execute('SELECT * FROM entries').fetchall()
        for entry in stored:
            proof = {'provider': 'kadokawa', 'manifest_path': str(tmp_path / 'changed-books.json'),
                'manifest_sha256': 'f' * 64, 'publisher_book_id': 'book1',
                'edition_record_sha256': entry['record_sha256'], 'match': {}}
            encoded = json.dumps(proof)
            db.execute('INSERT INTO edition_book_confirmations VALUES(?,?,?,?)',
                (entry['entry_id'], 'kadokawa', encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    calls = []
    def reject(*args):
        calls.append(args)
        raise ValueError('changed official book artifact')
    monkeypatch.setattr(cat, '_publisher_books', reject)
    cat._attach_book_confirmations(rows, stored, {})
    assert len(calls) == 1
    assert all(not row.get('edition_bibliography_confirmed') for row in rows)
    cat._attach_book_confirmations(rows, stored, {})
    assert len(calls) == 2  # No failed-result cache survives the operation.


def _avoidance_feature(label_id, axis):
    return {'entry_id': 'lns:wd:Q2', 'scope': 'series', 'content_features_adopted': True,
        'labels': [{'label_id': label_id, 'axis': axis, 'scope': 'series', 'decision': 'present',
            'relevance': 1, 'evidence': [{'evidence_id': 'prose1', 'summary': '内容の直接根拠'}]}],
        'source_attributions': [{'evidence_id': 'prose1', 'kind': 'note',
            'rights_policy_id': 'light-novel-series-wikipedia-screened-v1', 'license': 'CC-BY-SA-4.0'}]}


@pytest.mark.parametrize('label_id,axis', [(None, None), ('genre.fantasy', 'genre'), ('theme.family', 'theme')])
def test_unknown_avoided_feature_warns_without_claiming_absence(setup_catalogue, monkeypatch, label_id, axis):
    cat, records = setup_catalogue
    cat.ingest(records)
    features = {'lns:wd:Q2': _avoidance_feature(label_id, axis)} if label_id else {}
    monkeypatch.setattr(cat, '_feature_rows', lambda ids=None: {key: row for key, row in features.items() if ids is None or key in ids})
    result = cat.recommend_preferences(liked_entry_ids=['lns:wd:Q1'], avoided_features=['theme.revenge'])
    assert [row['entry_id'] for row in result['items']] == ['lns:wd:Q2']
    row = result['items'][0]
    assert row['avoided_feature_status'] == {'matched': [], 'unknown': ['theme.revenge']}
    assert row['feature_insufficiency'] == [{'axis': 'theme', 'status': 'unknown',
        'reason': 'requested_avoided_feature_absence_unproved'}]
    from recommend_book.recommendation.scale_site_v1 import public_work
    explanation = public_work(row)['feature_insufficiency']
    assert 'テーマ' in explanation and '判断できません' in explanation


def test_adopted_avoided_feature_still_excludes_candidate(setup_catalogue, monkeypatch):
    cat, records = setup_catalogue
    cat.ingest(records)
    features = {'lns:wd:Q2': _avoidance_feature('theme.revenge', 'theme')}
    monkeypatch.setattr(cat, '_feature_rows', lambda ids=None: {key: row for key, row in features.items() if ids is None or key in ids})
    assert cat.recommend_preferences(liked_entry_ids=['lns:wd:Q1'], avoided_features=['theme.revenge'])['items'] == []


@pytest.mark.parametrize('relevance,score,unknown', [(None, 0, True), (.4, .186667, False)])
def test_nullable_relevance_preserves_match_without_inventing_score(setup_catalogue, monkeypatch, relevance, score, unknown):
    cat, records = setup_catalogue
    cat.ingest(records)
    feature = _avoidance_feature('theme.family', 'theme')
    feature['labels'][0]['relevance'] = relevance
    monkeypatch.setattr(cat, '_feature_rows', lambda ids=None: {'lns:wd:Q2': feature} if ids is None or 'lns:wd:Q2' in ids else {})
    result = cat.recommend_preferences(preferred_features=['theme.family'], axis_weights={'theme': .7})
    assert [row['entry_id'] for row in result['items']] == ['lns:wd:Q2']
    row = result['items'][0]
    assert row['score'] == score
    assert row['reasons'][0]['kind'] == 'series_content_feature'
    assert row['reasons'][0]['label_id'] == 'theme.family'
    assert row['reasons'][0].get('relevance_unknown', False) is unknown
    assert row['llm_features'][0]['relevance'] == relevance


def test_nullable_relevance_still_excludes_adopted_avoided_label(setup_catalogue, monkeypatch):
    cat, records = setup_catalogue
    cat.ingest(records)
    feature = _avoidance_feature('theme.revenge', 'theme')
    feature['labels'][0]['relevance'] = None
    monkeypatch.setattr(cat, '_feature_rows', lambda ids=None: {'lns:wd:Q2': feature} if ids is None or 'lns:wd:Q2' in ids else {})
    assert cat.recommend_preferences(liked_entry_ids=['lns:wd:Q1'], avoided_features=['theme.revenge'])['items'] == []
