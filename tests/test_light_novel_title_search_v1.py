"""Search-only title variants must not weaken bibliographic identity checks."""
from copy import deepcopy
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from recommend_book.ingestion.core import RawArtifact
from recommend_book.ingestion.ndl import NDLAdapter
from recommend_book.light_novel_catalogue_v1 import normalize_search
from test_light_novel_catalogue_v1 import edition_bundle, setup_catalogue


@pytest.fixture
def title_variants(edition_bundle, tmp_path):
    cat, _, template = edition_bundle
    original = Path(template['provenance']['raw_path']).read_text(encoding='utf-8')
    records = []
    for index, title in enumerate([
        '架空のかんけい. 4', '架空のかんけい4',
        'Re:架空から始める異世界生活. 短編集14', '別作品4',
        '架空THE MUSIC NOVEL : 副題. 3',
        '架空THE MUSIC NOVEL : 副題. 13',
        '架空THE MUSIC NOVEL : 副題. 03',
        '架空のかんけい. 40', '架空のかんけい. 41',
    ], 1):
        xml = original.replace('書誌候補 第一巻', title).replace('R100000002-I1', f'R100000002-I{index}')
        if index == 1:
            xml = xml.replace(f'<d:title>{title}</d:title>',
                f'<n:title><rdf:Description><rdf:value>{title}</rdf:value>'
                '<n:transcription>2リノヨミ</n:transcription></rdf:Description></n:title>')
        raw = tmp_path / f'title-{index}.xml'
        raw.write_text(xml, encoding='utf-8')
        digest = hashlib.sha256(raw.read_bytes()).hexdigest()
        source = NDLAdapter(None)._normalize(ET.fromstring(xml), RawArtifact(
            digest, str(raw), raw.stat().st_size, '2026-10-25T00:00:00Z',
            'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac',
            locator='records[1]/recordData').model_dump(mode='json')
        record = deepcopy(template)
        record.update(id=source['source_id'], title=source['titles'][0]['value'],
                      source_url=source['source_url'], source_record=source)
        record['provenance'].update(url=source['source_url'], raw_path=str(raw), raw_sha256=digest)
        records.append(record)
    assert cat.import_editions(records)['inserted'] == len(records)
    return cat, records


@pytest.mark.parametrize('query,expected', [
    ('架空のかんけい４', {1, 2}),
    ('架空のかんけい. 4', {1, 2, 8, 9}),  # Preserve the existing literal substring search.
    ('Ｒｅ：架空から始める異世界生活　短編集１４', {3}),
    ('架空 THE MUSIC NOVEL 3 副題', {5, 7}),
])
def test_official_volume_title_finds_isbd_punctuation_without_merging(title_variants, query, expected):
    cat, _ = title_variants
    with cat._connect() as db:
        before = [tuple(row) for row in db.execute('SELECT * FROM entries ORDER BY entry_id')]
    result = cat.search_catalogue(query, entry_kind='edition', language='ja')
    assert {row['entry_id'] for row in result['items']} == {
        f'lne:ndl:R100000002-I{number}' for number in expected}
    assert all(row['entry_kind'] == 'edition' and not row['light_novel_confirmed']
               and row.get('series_entry_id') is None for row in result['items'])
    # Identity and membership still use the original punctuation-sensitive normalizer.
    assert normalize_search('架空のかんけい. 4') != normalize_search('架空のかんけい4')
    with cat._connect() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM entries ORDER BY entry_id')] == before
        assert db.execute('SELECT count(*) FROM edition_series_memberships').fetchone()[0] == 0


def test_punctuation_only_query_does_not_become_an_empty_match(title_variants):
    cat, _ = title_variants
    assert cat.search_catalogue('【】')['total'] == 0


def test_relaxed_volume_does_not_widen_to_other_numeric_sequence(title_variants):
    cat, _ = title_variants
    assert {row['entry_id'] for row in cat.search_catalogue('架空のかんけい4')['items']} == {
        'lne:ndl:R100000002-I1', 'lne:ndl:R100000002-I2'}


def test_title_and_reading_alias_do_not_concatenate_their_numbers(title_variants):
    cat, _ = title_variants
    rows = cat.search_catalogue('架空のかんけい4')['items']
    row = next(row for row in rows if row['entry_id'] == 'lne:ndl:R100000002-I1')
    assert row['title'] == '架空のかんけい. 4' and row['aliases'] == ['2リノヨミ']


def test_new_title_match_still_authenticates_candidate_raw(title_variants):
    cat, records = title_variants
    Path(records[2]['provenance']['raw_path']).write_text('<changed/>', encoding='utf-8')
    with pytest.raises(ValueError):
        cat.search_catalogue('Re:架空から始める異世界生活 短編集14')
