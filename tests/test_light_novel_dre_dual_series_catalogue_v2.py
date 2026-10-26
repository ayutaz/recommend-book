"""Exercise candidate discovery, persistence and replay, not just the matcher."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import pytest

from recommend_book.ingestion.core import RawArtifact
from recommend_book.ingestion.ndl import NDLAdapter
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue, normalize_search
from recommend_book.light_novel_ndl_series_v1 import publisher_assertions
from test_light_novel_catalogue_v1 import setup_catalogue, edition_bundle, membership_bundle
from test_light_novel_dre_memberships_v1 import dre_membership
from test_light_novel_dre_dual_series_v2 import fixture as source_fixture, mutate, dump

DCT = 'http://purl.org/dc/terms/'
DC = 'http://purl.org/dc/elements/1.1/'
N = 'http://ndl.go.jp/dcndl/terms/'
RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'


def make_edition(cat, root, native_id, complete, official, volume, isbn, *, catalogued=None, alternatives=(), structured=True, extra_primary=None):
    with cat._connect() as db:
        template = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', ('lne:ndl:R100000002-I1',)).fetchone()[0])
    old_url = template['source_url']; new_url = 'https://ndlsearch.ndl.go.jp/books/' + native_id
    xml = Path(template['provenance']['raw_path']).read_text('utf8').replace(old_url, new_url)
    tree = ET.fromstring(xml); own, = list(tree.iter('{' + N + '}BibResource'))
    for node in list(own):
        if node.tag == '{' + DCT + '}title': node.text = complete
        elif node.tag == '{' + DC + '}title':
            values = list(node.iter('{' + RDF + '}value'))
            if values: values[0].text = complete
            else: node.text = complete
        elif node.tag == '{' + N + '}volume': own.remove(node)
        elif node.tag == '{' + DCT + '}identifier': own.remove(node)
    if catalogued is not None:
        for child in list(own):
            if child.tag == '{' + DC + '}title': own.remove(child)
        node = ET.SubElement(own, '{' + DC + '}title')
        if structured:
            desc = ET.SubElement(node, '{' + RDF + '}Description')
            ET.SubElement(desc, '{' + RDF + '}value').text = catalogued
        else: node.text = catalogued
    for alternative in alternatives:
        node = ET.SubElement(own, '{' + N + '}alternative'); desc = ET.SubElement(node, '{' + RDF + '}Description')
        ET.SubElement(desc, '{' + RDF + '}value').text = alternative
    if extra_primary: ET.SubElement(own, '{' + DCT + '}title').text = extra_primary
    ET.SubElement(own, '{' + DC + '}creator').text = '著者 著'
    if volume is not None: ET.SubElement(own, '{' + N + '}volume').text = volume
    identifier = ET.SubElement(own, '{' + DCT + '}identifier', {'{' + RDF + '}datatype': 'ISBN'}); identifier.text = isbn
    raw = root / (native_id + '.xml'); raw.write_bytes(ET.tostring(tree, encoding='utf-8'))
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = NDLAdapter(None)._normalize(tree, RawArtifact(digest, str(raw), len(raw.read_bytes()),
        '2026-10-26T00:00:00Z', 'https://ndlsearch.ndl.go.jp/api/sru'), 'iss-ndl-opac', locator='records[1]/recordData').model_dump(mode='json')
    record = deepcopy(template)
    record.update(id=source['source_id'], source_url=source['source_url'], source_record=source,
        title=source['titles'][0]['value'], volume=source['volume'], isbn=[isbn], label=source['series'],
        publisher=source['publisher'], authors=[a['name'] for a in source['contributors'] if a['role'] == 'author'])
    record['provenance'].update(raw_path=str(raw), raw_sha256=digest, url=new_url)
    assert all(normalize_search(t['value']) != normalize_search(official) for t in source['titles'])
    imported = cat.import_editions([record]); assert imported['failed'] == 0, json.dumps(imported, ensure_ascii=True)
    return 'lne:ndl:' + native_id


@pytest.mark.parametrize('scenario', ['upper_lower', 'unnumbered_subtitle'])
def test_v2_ISBN_discovery_creates_and_replays_members_without_bare_canonical_title(dre_membership, tmp_path, scenario):
    cat, old_manifest, old_pin, _, _ = dre_membership
    old_id = 'lne:ndl:R100000002-I1'
    assert cat.resolve_dre_edition_memberships(old_manifest, old_pin, edition_ids=[old_id])['linked'] == 1
    old_evidence = cat.work_detail(old_id)['series_membership_evidence']
    with cat._connect() as db:
        original_entries = {r['entry_id']: tuple(r) for r in db.execute('SELECT * FROM entries')}
        original_binding = tuple(db.execute('SELECT * FROM edition_series_memberships WHERE edition_id=?', (old_id,)).fetchone())
    source_root = tmp_path / 'dual-source'; source_root.mkdir()
    member = '合成シリーズ〈上〉' if scenario == 'upper_lower' else '合成シリーズ ～完全副題～'
    frame = source_fixture(source_root, member=member)
    for kind in ('product', 'series'):
        mutate(source_root, frame, kind, '架空著者', '著者')
    if scenario == 'upper_lower':
        for kind in ('product', 'series'):
            text = (source_root / (kind + '.html')).read_text('utf8')
            card = re.search(r'<li class="bookColumn__item">.*?</li>', text, re.S)[0]
            lower = card.replace('〈上〉', '〈下〉').replace('/product/157', '/product/158').replace('978-4-434-36579-9', '978-4-434-37277-3')
            mutate(source_root, frame, kind, '</ul>', lower + '</ul>')
        ids = [make_edition(cat, tmp_path, 'R100000002-I2', '合成シリーズ. 上', '合成シリーズ', '上', '9784434365799'),
               make_edition(cat, tmp_path, 'R100000002-I3', '合成シリーズ. 下', '合成シリーズ', '下', '9784434372773')]
    else:
        mutate(source_root, frame, 'series', '>合成シリーズ</h1>', '>合成シリーズ ～完全副題～</h1>')
        ids = [make_edition(cat, tmp_path, 'R100000002-I2', '合成シリーズ : 完全副題', member, None, '9784434365799')]
    path = source_root / 'frames.json'; pin = dump(path, [frame])
    assertions = publisher_assertions({'frames_path': str(path), 'frames_sha256': pin, 'source_root': str(source_root)}, cat._artifact, {})
    ref, = assertions
    result = cat.import_ndl_series_from_publishers(path, pin, source_root=source_root, reference_ids=[ref])
    assert result['inserted'] == 1 and result['new_edition_memberships'] == len(ids), json.dumps(result, ensure_ascii=True)
    before = {}
    for identity in ids:
        row = cat.work_detail(identity)
        assert row['series_membership_evidence']['rule_version'] == 'dre-explicit-dual-source-ndl-series-membership-v2'
        assert row['series_membership_evidence']['volume_number'] is None
        assert row['series_membership_evidence']['volume'] in ('上', '下', None)
        before[identity] = row['series_membership_evidence']
    again = cat.import_ndl_series_from_publishers(path, pin, source_root=source_root, reference_ids=[ref], refresh_memberships=True)
    assert again['new_edition_memberships'] == 0 and again['unchanged_edition_memberships'] == len(ids)
    assert {identity: cat.work_detail(identity)['series_membership_evidence'] for identity in ids} == before
    assert cat.work_detail(old_id)['series_membership_evidence'] == old_evidence
    with cat._connect() as db:
        assert all(tuple(db.execute('SELECT * FROM entries WHERE entry_id=?', (identity,)).fetchone()) == value for identity, value in original_entries.items())
        assert tuple(db.execute('SELECT * FROM edition_series_memberships WHERE edition_id=?', (old_id,)).fetchone()) == original_binding
    # Saved series evidence expires; neither replay nor refresh may silently keep it valid.
    (source_root / 'series.html').write_text('expired', encoding='utf8')
    with pytest.raises(ValueError): cat.work_detail(ids[0])


def grammar_source(root, *, numeric=False, subtitle='完全副題'):
    root.mkdir()
    first = '合成シリーズ ～' + subtitle + '～'
    frame = source_fixture(root, member=first)
    for kind in ('product', 'series'): mutate(root, frame, kind, '架空著者', '著者')
    mutate(root, frame, 'series', '>合成シリーズ</h1>', '>' + first + '</h1>')
    if numeric:
        for kind in ('product', 'series'):
            text = (root / (kind + '.html')).read_text('utf8')
            card = re.search(r'<li class="bookColumn__item">.*?</li>', text, re.S)[0]
            second = card.replace('合成シリーズ ～', '合成シリーズ2 ～').replace('/product/157', '/product/158').replace('978-4-434-36579-9', '978-4-434-37277-3')
            mutate(root, frame, kind, '</ul>', second + '</ul>')
    path = root / 'frames.json'; pin = dump(path, [frame])
    assertions = publisher_assertions({'frames_path': str(path), 'frames_sha256': pin, 'source_root': str(root)},
        LightNovelCatalogue.__new__(LightNovelCatalogue)._artifact, {})
    return path, pin, next(iter(assertions))


@pytest.mark.parametrize('grammar', ['terminal_numeric', 'parallel_alternatives'])
def test_full_catalogue_v3_own_grammar_positive_replay_preserves_existing_v2(dre_membership, tmp_path, grammar):
    cat, old_manifest, old_pin, _, _ = dre_membership
    assert cat.resolve_dre_edition_memberships(old_manifest, old_pin, edition_ids=['lne:ndl:R100000002-I1'])['linked'] == 1
    root = tmp_path / 'grammar'; numeric = grammar == 'terminal_numeric'
    path, pin, ref = grammar_source(root, numeric=numeric)
    if numeric:
        anchor = make_edition(cat, tmp_path, 'R100000002-I2', '合成シリーズ : 完全副題', '合成シリーズ ～完全副題～', None, '9784434365799')
        first = cat.import_ndl_series_from_publishers(path, pin, source_root=root, reference_ids=[ref])
        assert first['new_edition_memberships'] == 1
        v2_before = cat.work_detail(anchor)['series_membership_evidence']
        assert v2_before['rule_version'].endswith('-v2')
        with cat._connect() as db:
            v2_tuple = tuple(db.execute('SELECT * FROM edition_series_memberships WHERE edition_id=?', (anchor,)).fetchone())
        target = make_edition(cat, tmp_path, 'R100000002-I3', '合成シリーズ : 完全副題 2', '合成シリーズ2 ～完全副題～', '2',
            '9784434372773', catalogued='合成シリーズ : 完全副題')
        result = cat.import_ndl_series_from_publishers(path, pin, source_root=root, reference_ids=[ref], refresh_memberships=True)
        assert result['new_edition_memberships'] == 1 and result['unchanged_edition_memberships'] == 1
        assert cat.work_detail(anchor)['series_membership_evidence'] == v2_before
        with cat._connect() as db:
            assert tuple(db.execute('SELECT * FROM edition_series_memberships WHERE edition_id=?', (anchor,)).fetchone()) == v2_tuple
    else:
        parallel = "Complete literal, with punctuation"
        target = make_edition(cat, tmp_path, 'R100000002-I2', '合成シリーズ = ' + parallel + '. : 完全副題', '合成シリーズ ～完全副題～', None,
            '9784434365799', catalogued='合成シリーズ : 完全副題', alternatives=[parallel, parallel])
        result = cat.import_ndl_series_from_publishers(path, pin, source_root=root, reference_ids=[ref])
        assert result['inserted'] == 1 and result['new_edition_memberships'] == 1
    row = cat.work_detail(target); evidence = row['series_membership_evidence']
    assert evidence['rule_version'] == 'dre-explicit-dual-source-ndl-series-membership-v3'
    match = evidence['explicit_member_match']; assert match['rule_version'].endswith('-v3')
    assert len(match['own_title_grammar']['official_title_fields']) >= 4
    if numeric: assert evidence['volume'] == '2' and evidence['volume_number'] == 2
    else:
        assert evidence['volume_number'] is None
        assert match['own_title_grammar']['parallel_literal'] == parallel
        assert len(match['own_title_grammar']['parallel_alternatives']) == 2
    assert cat.work_detail('lne:ndl:R100000002-I1')['series_membership_evidence']['rule_version'] == 'dre-publisher-wikidata-edition-membership-v1'
    (root / 'series.html').write_text('expired', encoding='utf8')
    with pytest.raises(ValueError): cat.work_detail(target)


@pytest.mark.parametrize('change', ['numeric_missing_dc', 'numeric_unstructured_dc', 'numeric_wrong_volume', 'numeric_wrong_subtitle',
    'parallel_one', 'parallel_conflict', 'parallel_wrong_composite', 'parallel_unstructured_dc', 'parallel_extra_primary', 'official_Japanese_conflict'])
def test_full_catalogue_own_fields_reject_conflicts_and_accept_exact_v4_layouts(dre_membership, tmp_path, change):
    cat, _, _, _, _ = dre_membership; numeric = change.startswith('numeric')
    root = tmp_path / 'grammar'; path, pin, ref = grammar_source(root, numeric=numeric,
        subtitle='別の副題' if change == 'official_Japanese_conflict' else '完全副題')
    parallel = 'Complete literal, with punctuation'
    options = {'catalogued': '合成シリーズ : 完全副題'}
    if numeric:
        primary = '合成シリーズ : 完全副題 2'; volume = '2'; isbn = '9784434372773'; official = '合成シリーズ2 ～完全副題～'
        if change == 'numeric_missing_dc': options['catalogued'] = None
        if change == 'numeric_unstructured_dc': options['structured'] = False
        if change == 'numeric_wrong_volume': volume = '3'
        if change == 'numeric_wrong_subtitle': options['catalogued'] = '合成シリーズ : 別の副題'
    else:
        primary = '合成シリーズ = ' + parallel + '. : 完全副題'; volume = None; isbn = '9784434365799'; official = '合成シリーズ ～完全副題～'
        options['alternatives'] = [parallel, parallel]
        if change == 'parallel_one': options['alternatives'] = [parallel]
        if change == 'parallel_conflict': options['alternatives'] = [parallel, 'Different literal']
        if change == 'parallel_wrong_composite': primary = primary.replace('. : ', ' : ')
        if change == 'parallel_unstructured_dc': options['structured'] = False
        if change == 'parallel_extra_primary': options['extra_primary'] = '矛盾する別書名'
    make_edition(cat, tmp_path, 'R100000002-I2', primary, official, volume, isbn, **options)
    with cat._connect() as db:
        before = [tuple(r) for r in db.execute('SELECT * FROM entries ORDER BY entry_id')]
        bindings = [tuple(r) for r in db.execute('SELECT * FROM edition_series_memberships ORDER BY edition_id')]
    result = cat.import_ndl_series_from_publishers(path, pin, source_root=root, reference_ids=[ref])
    if change in {'parallel_one', 'parallel_wrong_composite'}:
        # These exact, independently printed layouts are now supported by v4.
        assert result['inserted'] == result['new_edition_memberships'] == 1
        evidence = cat.work_detail('lne:ndl:R100000002-I2')['series_membership_evidence']
        assert evidence['rule_version'] == 'dre-explicit-dual-source-ndl-series-membership-v4'
        assert evidence['explicit_member_match']['rule_version'] == 'dre-dual-source-explicit-ndl-member-match-v4'
        assert evidence['volume_number'] is None
        return
    assert result['inserted'] == result['new_edition_memberships'] == 0
    assert result['held_reasons'] == {'no_qualified_ndl_anchor': 1}
    with cat._connect() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM entries ORDER BY entry_id')] == before
        assert [tuple(r) for r in db.execute('SELECT * FROM edition_series_memberships ORDER BY edition_id')] == bindings
