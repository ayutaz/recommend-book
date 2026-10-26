"""Closed v4 title recipes use actual normalized NDL XML and pinned HTML."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import pytest

from test_light_novel_catalogue_v1 import setup_catalogue, edition_bundle, membership_bundle
from test_light_novel_dre_memberships_v1 import dre_membership
from test_light_novel_dre_dual_series_v2 import fixture as source_fixture, mutate, dump, verify
from test_light_novel_dre_dual_series_catalogue_v2 import make_edition, grammar_source
from recommend_book import light_novel_dre_dual_series_v2 as dre
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.ingestion.core import RawArtifact
from recommend_book.ingestion.ndl import NDLAdapter

ISBN = {None: '9784434365799', '2': '9784434372773', '3': '9784434350917'}


def make_parallel_edition(cat, root, complete, volume, isbn, catalogued, alternatives, extra_primary=None):
    """Generate actual own XML; unlike the historical discovery fixture, DC's
    Japanese title may equal the official title. Never manufacture parsed parts."""
    with cat._connect() as db:
        template = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', ('lne:ndl:R100000002-I1',)).fetchone()[0])
    url = 'https://ndlsearch.ndl.go.jp/books/R100000002-I2'
    tree = ET.fromstring(Path(template['provenance']['raw_path']).read_text('utf8').replace(template['source_url'], url))
    own, = list(tree.iter('{http://ndl.go.jp/dcndl/terms/}BibResource'))
    for child in list(own):
        if child.tag in ('{http://purl.org/dc/terms/}title', '{http://purl.org/dc/elements/1.1/}title',
                         '{http://ndl.go.jp/dcndl/terms/}volume', '{http://purl.org/dc/terms/}identifier'):
            own.remove(child)
    ET.SubElement(own, '{http://purl.org/dc/terms/}title').text = complete
    if extra_primary: ET.SubElement(own, '{http://purl.org/dc/terms/}title').text = extra_primary
    for tag, value in [('{http://purl.org/dc/elements/1.1/}title', catalogued),
                       *[('{http://ndl.go.jp/dcndl/terms/}alternative', a) for a in alternatives]]:
        node = ET.SubElement(own, tag); desc = ET.SubElement(node, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')
        ET.SubElement(desc, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}value').text = value
    ET.SubElement(own, '{http://purl.org/dc/elements/1.1/}creator').text = '著者 著'
    if volume is not None:
        node = ET.SubElement(own, '{http://ndl.go.jp/dcndl/terms/}volume')
        description = ET.SubElement(node, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')
        ET.SubElement(description, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}value').text = volume
    ET.SubElement(own, '{http://purl.org/dc/terms/}identifier', {'{http://www.w3.org/1999/02/22-rdf-syntax-ns#}datatype': 'ISBN'}).text = isbn
    raw = root / 'own-v4.xml'; raw.write_bytes(ET.tostring(tree, encoding='utf-8')); pin = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(tree, RawArtifact(pin,str(raw),len(raw.read_bytes()),'2026-10-26T00:00:00Z',url),'iss-ndl-opac',locator='records[1]/recordData').model_dump(mode='json')
    record = deepcopy(template)
    record.update(id=source['source_id'],source_url=url,source_record=source,title=source['titles'][0]['value'],volume=source['volume'],isbn=[isbn],label=source['series'],publisher=source['publisher'],authors=[a['name'] for a in source['contributors'] if a['role']=='author'])
    record['provenance'].update(raw_path=str(raw),raw_sha256=pin,url=url)
    result = cat.import_editions([record])
    assert result['failed']==0, json.dumps(result,ensure_ascii=True)
    return 'lne:ndl:'+record['id']


@pytest.fixture
def v4_source(dre_membership, tmp_path):
    cat = dre_membership[0]
    def build(*, volume=None, subtitle=None, alternatives=1, layout='plain', representative_second=True,
              bad_primary=None, bad_parallel=None, structured=True):
        root = tmp_path / 'v4-source'; root.mkdir()
        tail = ' ～' + subtitle + '～' if subtitle else ''
        first = '合成シリーズ' + tail
        frame = source_fixture(root, member=first)
        for kind in ('product', 'series'):
            mutate(root, frame, kind, '架空著者', '著者')
            text = (root / (kind + '.html')).read_text('utf8')
            card = re.search(r'<li class="bookColumn__item">.*?</li>', text, re.S)[0]
            for number in ('2', '3'):
                extra = card.replace(first, '合成シリーズ' + number + tail).replace('/product/157', '/product/' + ('158' if number == '2' else '159')).replace('978-4-434-36579-9', ISBN[number])
                mutate(root, frame, kind, '</ul>', extra + '</ul>')
        if subtitle:
            mutate(root, frame, 'series', '>合成シリーズ</h1>', '>' + first + '</h1>')
        if representative_second:
            mutate(root, frame, 'product', '<h1 class="lineupColumn__title">' + first,
                   '<h1 class="lineupColumn__title">合成シリーズ2' + tail)
            mutate(root, frame, 'product', 'lineupColumn__infoValue">9784434365799',
                   'lineupColumn__infoValue">' + ISBN['2'])
            mutate(root, frame, 'product', '<link rel="canonical" href="https://drecom-media.jp/drenovels/product/157',
                   '<link rel="canonical" href="https://drecom-media.jp/drenovels/product/158')
            pin = frame['pairs'][0]['product']; path = root / pin['receipt_path']; rec = json.loads(path.read_bytes())
            rec['source_url'] = rec['final_url'] = 'https://drecom-media.jp/drenovels/product/158'
            pin['receipt_sha256'] = dump(path, rec)
        parallel = 'Complete literal, with punctuation'
        complete = '合成シリーズ = ' + parallel
        if subtitle:
            complete += ('. : ' if layout == 'period_subtitle' else ' : ') + subtitle
            if volume: complete += '. ' + volume
        elif volume:
            complete += (' ' if layout == 'space_volume' else '. ') + volume
        target = make_parallel_edition(cat, tmp_path, complete, volume, ISBN[volume],
            '合成シリーズ' + (' : ' + subtitle if subtitle else ''),
            [parallel] * alternatives if bad_parallel is None else bad_parallel, bad_primary)
        with cat._connect() as db:
            record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (target,)).fetchone()[0])
        cache = {}; parts = cat._edition_primary_bibliographic_parts(record, cache)
        own = cat._authenticated_own_primary_resource(record, parts, cache)
        assertion, = verify(root, frame).values()
        return cat, root, frame, target, record, parts, own, assertion
    return build


# Fifteen observed edition shapes, with private source titles replaced by fixtures.
CASES = [
    ('akutoku1', None, None, 1, 'plain'), ('akutoku2', '2', None, 1, 'plain'), ('akutoku3', '3', None, 1, 'plain'),
    ('loop1', None, None, 1, 'plain'), ('loop2', '2', None, 1, 'space_volume'), ('loop3', '3', None, 1, 'space_volume'),
    ('yame1', None, None, 1, 'plain'), ('yame2', '2', None, 1, 'space_volume'),
    ('koisuru1', None, '完全副題', 2, 'period_subtitle'), ('koisuru2', '2', '完全副題', 2, 'period_subtitle'),
    ('wizard1', None, '完全副題', 2, 'plain'), ('wizard2', '2', '完全副題', 2, 'plain'),
    ('asra1', None, '完全副題', 2, 'plain'), ('asra2', '2', '完全副題', 2, 'plain'), ('asra3', '3', '完全副題', 2, 'plain'),
]


@pytest.mark.parametrize('name,volume,subtitle,alternatives,layout', CASES)
def test_all_fifteen_observed_shapes_replay_exact_own_fields(v4_source, name, volume, subtitle, alternatives, layout):
    cat, root, frame, identity, record, parts, own, assertion = v4_source(volume=volume, subtitle=subtitle, alternatives=alternatives, layout=layout)
    assert dre.match_member_v3(assertion, parts, own, cat._artifact, {}) is None
    matched = dre.match_member_v4(assertion, parts, own, cat._artifact, {})
    assert matched['rule_version'].endswith('-v4')
    assert matched['volume'] == volume
    assert matched['volume_number'] == (int(volume) if volume else None)
    grammar = matched['own_title_grammar']
    assert len(grammar['parallel_alternatives']) == alternatives
    assert all(a['raw_literal'] == 'Complete literal, with punctuation' for a in grammar['parallel_alternatives'])
    assert grammar['source_value'] == next(r['value'] for r in parts['titles'] if '/{http://purl.org/dc/terms/}title[' in r['locator'])
    fields = grammar['official_title_fields']
    assert all(f['raw_sha256'] and f['locator'] for f in fields)
    product = next(f for f in fields if 'source_product_is_selected_member' in f)
    assert product['isbn'] == ISBN['2']
    assert product['source_product_is_selected_member'] is (volume == '2')


@pytest.mark.parametrize('change', ['parallel', 'extra_primary', 'Japanese_main', 'Japanese_subtitle', 'volume', 'missing_alternative', 'third_alternative'])
def test_no_supplied_own_field_may_be_discarded(v4_source, change):
    _, _, _, _, _, parts, own, _ = v4_source(volume='2', subtitle='完全副題', alternatives=2)
    changed = deepcopy(own)
    if change == 'parallel':
        next(ch for ch in changed if ch.tag.endswith('}alternative'))[0][0].text += ' changed'
    elif change in ('missing_alternative', 'third_alternative'):
        alternatives = [ch for ch in changed if ch.tag.endswith('}alternative')]
        if change == 'missing_alternative':
            for ch in alternatives: changed.remove(ch)
        else: changed.append(deepcopy(alternatives[0]))
    elif change == 'extra_primary':
        import xml.etree.ElementTree as ET
        ET.SubElement(changed, '{http://purl.org/dc/terms/}title').text = 'contradictory primary'
    else:
        node = next(ch for ch in changed if ch.tag.endswith('}title') and len(ch) == 0)
        node.text = node.text.replace('合成シリーズ', '別シリーズ') if change == 'Japanese_main' else node.text.replace('完全副題', '別副題') if change == 'Japanese_subtitle' else node.text.replace('. 2', '. 3')
    assert dre._own_title_grammar_v4(parts, changed) is None


@pytest.mark.parametrize('case', ['own_h1', 'own_isbn', 'canonical', 'card_title', 'raw_sha', 'receipt_missing'])
def test_all_provided_primary_official_fields_remain_mandatory(v4_source, case):
    cat, root, frame, _, _, parts, own, assertion = v4_source(volume='2', subtitle='完全副題')
    if case == 'raw_sha':
        (root / 'product.html').write_text('changed after admission', encoding='utf8')
    elif case == 'receipt_missing':
        (root / 'product.receipt.json').unlink()
    else:
        old, new = {
            'own_h1': ('lineupColumn__title">合成シリーズ2', 'lineupColumn__title">合成シリーズ3'),
            'own_isbn': ('lineupColumn__infoValue">' + ISBN['2'], 'lineupColumn__infoValue">' + ISBN['3']),
            'canonical': ('<link rel="canonical" href="https://drecom-media.jp/', '<link rel="canonical" href="https://evil.test/'),
            'card_title': ('bookColumn__title">合成シリーズ2', 'bookColumn__title">別シリーズ2'),
        }[case]
        mutate(root, frame, 'product', old, new)
        recpin = frame['pairs'][0]['product']; recpath = root / recpin['receipt_path']; rec = json.loads(recpath.read_bytes())
        assertion['primary_source_snapshots']['product'].update(raw_sha256=rec['sha256'], receipt_sha256=recpin['receipt_sha256'])
    assert dre.match_member_v4(assertion, parts, own, cat._artifact, {}) is None


@pytest.mark.parametrize('kind', ['v2', 'v3'])
def test_successful_old_proof_dictionary_is_bit_identical(dre_membership, tmp_path, kind):
    cat = dre_membership[0];root = tmp_path / 'old-source'
    path, pin, _ = grammar_source(root)
    if kind == 'v2':
        primary='合成シリーズ : 完全副題'; alternatives=[]
    else:
        primary='合成シリーズ = Complete literal. : 完全副題'; alternatives=['Complete literal'] * 2
    identity=make_edition(cat,tmp_path,'R100000002-I2',primary,'合成シリーズ ～完全副題～',None,ISBN[None],catalogued='合成シリーズ : 完全副題',alternatives=alternatives)
    with cat._connect() as db:record=json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?',(identity,)).fetchone()[0])
    cache={};parts=cat._edition_primary_bibliographic_parts(record,cache);own=cat._authenticated_own_primary_resource(record,parts,cache)
    frame=json.loads(path.read_bytes())[0];assertion,=verify(root,frame).values()
    before=dre.match_member_v3(assertion,parts,own,cat._artifact,{})
    assert before is not None and before['rule_version'].endswith('-'+kind)
    after=dre.match_member_v4(assertion,parts,own,cat._artifact,{})
    assert json.dumps(after,sort_keys=True,separators=(',',':'))==json.dumps(before,sort_keys=True,separators=(',',':'))


@pytest.mark.parametrize('name,volume,subtitle,alternatives,layout', CASES + [
    ('roman2_own', '2', '完全副題', 2, 'period_subtitle'),
    ('roman3_own', '3', '完全副題', 2, 'period_subtitle'),
])
@pytest.mark.parametrize('change', ['alternative', 'contradictory_primary', 'Japanese_projection'])
def test_fifty_one_observed_own_field_contradictions_fail_closed(v4_source, name, volume, subtitle, alternatives, layout, change):
    cat, _, _, _, _, parts, own, assertion = v4_source(volume=volume, subtitle=subtitle, alternatives=alternatives, layout=layout)
    grammar = dre._own_title_grammar_v4(parts, own)
    assert grammar is not None
    if change == 'Japanese_projection':
        grammar = deepcopy(grammar); grammar['Japanese_main'] += ' changed'
        assert dre._official_title_grammar_witness_v4(assertion, assertion['explicit_book_memberships'][0 if volume is None else int(volume)-1], grammar, cat._artifact, {}) is None
    else:
        own = deepcopy(own)
        if change == 'alternative':
            next(ch for ch in own if ch.tag.endswith('}alternative'))[0][0].text += ' changed'
        else:
            ET.SubElement(own, '{http://purl.org/dc/terms/}title').text = 'contradictory primary'
        assert dre._own_title_grammar_v4(parts, own) is None


@pytest.mark.parametrize('subtitle,volume', [(None, '2'), ('完全副題', None)])
def test_actual_catalogue_v4_discovery_persistence_replay_and_source_expiry(v4_source, dre_membership, subtitle, volume):
    cat, root, frame, identity, record, parts, own, assertion = v4_source(volume=volume, subtitle=subtitle, alternatives=1)
    old_id = 'lne:ndl:R100000002-I1'
    assert cat.resolve_dre_edition_memberships(dre_membership[1], dre_membership[2], edition_ids=[old_id])['linked'] == 1
    with cat._connect() as db:
        old = tuple(db.execute('SELECT * FROM edition_series_memberships WHERE edition_id=?', (old_id,)).fetchone())
    path = root / 'frames.json'; pin = dump(path, [frame])
    result = cat.import_ndl_series_from_publishers(path, pin, source_root=root, reference_ids=[assertion['reference_id']])
    assert result['inserted'] == 1 and result['new_edition_memberships'] == 1, result
    detail = cat.work_detail(identity); evidence = detail['series_membership_evidence']
    assert evidence['rule_version'] == 'dre-explicit-dual-source-ndl-series-membership-v4'
    assert evidence['explicit_member_match']['rule_version'] == 'dre-dual-source-explicit-ndl-member-match-v4'
    assert evidence['volume_number'] == (int(volume) if volume else None)
    again = cat.import_ndl_series_from_publishers(path, pin, source_root=root, reference_ids=[assertion['reference_id']], refresh_memberships=True)
    assert again['new_edition_memberships'] == 0 and again['unchanged_edition_memberships'] == 1
    assert cat.work_detail(identity)['series_membership_evidence'] == evidence
    with cat._connect() as db:
        assert tuple(db.execute('SELECT * FROM edition_series_memberships WHERE edition_id=?', (old_id,)).fetchone()) == old
    (root / 'series.html').write_text('expired source', encoding='utf8')
    with pytest.raises(ValueError): cat.work_detail(identity)


@pytest.mark.parametrize('kind,volume', [('roman', '2'), ('roman', '3'), ('bracket', None), ('bracket', '2')])
def test_four_roman_and_bracket_differences_remain_held(v4_source, tmp_path, kind, volume):
    subtitle = '完全副題' if kind == 'roman' else '本文〈演技〉'
    cat, root, frame, identity, _, parts, own, assertion = v4_source(volume=volume, subtitle=subtitle, alternatives=2)
    if kind == 'roman':
        for source in ('product', 'series'):
            mutate(root, frame, source, '合成シリーズ' + volume + ' ～', '合成シリーズ' + ('Ⅱ' if volume == '2' else 'Ⅲ') + ' ～')
    else:
        for source in ('product', 'series'): mutate(root, frame, source, '〈演技〉', '（演技）')
        identity = make_parallel_edition(cat, tmp_path,
            '合成シリーズ : ' + subtitle + ('. ' + volume if volume else ''), volume, ISBN[volume],
            '合成シリーズ : ' + subtitle, [])
        with cat._connect() as db: record=json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?',(identity,)).fetchone()[0])
        cache={};parts=cat._edition_primary_bibliographic_parts(record,cache);own=cat._authenticated_own_primary_resource(record,parts,cache)
    assertion, = verify(root, frame).values()
    assert dre.match_member_v4(assertion, parts, own, cat._artifact, {}) is None
