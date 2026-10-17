import hashlib
import json
from pathlib import Path

import pytest

from recommend_book.jobs import canonical_json
from recommend_book.models import SourceRecord
from recommend_book.resolution import compare_records
from recommend_book.authority_identity_correction import project_identity_correction, verify_identity_correction


def digest(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def case(tmp_path):
    def claim(prop, value):
        return {'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'property': prop, 'datavalue': {'value': value}}}
    claims = {'P31': [claim('P31', {'id': 'Q8261'})], 'P648': [claim('P648', 'OL471932W'), claim('P648', 'OL471547W')]}
    raw = tmp_path / 'wd.json'
    raw.write_text(json.dumps({'entities': {'Q224078': {'id': 'Q224078', 'claims': claims}}}), encoding='utf-8')
    original = SourceRecord(source='wikidata', source_id='Q224078', source_url='https://www.wikidata.org/wiki/Q224078',
        retrieved_at='2026-10-17T00:00:00Z', provider_id='wikidata', record_kind='work', rights_policy_id='wikidata-cc0',
        raw_path=str(raw.resolve()), raw_sha256=digest(raw.read_bytes()), extra={'claims': claims},
        identifiers=[{'source': 'wikidata', 'value': 'Q224078', 'scope': 'work'},
                     *[{'source': 'openlibrary', 'value': '/works/' + v, 'scope': 'work'} for v in ('OL471932W', 'OL471547W')]])
    ol = tmp_path / 'ol.json'
    ol.write_text(json.dumps({'key': '/works/OL471547W', 'type': {'key': '/type/work'}, 'title': 'Agatha Christie, tome 2',
        'subtitle': 'Les Années 1926-1930', 'description': {'value': 'Contains:\n[Murder of Roger Ackroyd](https://openlibrary.org/works/OL472086W/The_Murder_of_Roger_Ackroyd)\nLes quatre --\nLe train bleu --\nLes sept cadrans --\nLe crime est notre affaire --\nL\'affaire Protheroe.'}}), encoding='utf-8')
    packet = {'schema': 'authority-identity-correction-v1', 'original_record': original.model_dump(mode='json'),
        'original_record_sha256': digest(canonical_json(original.model_dump(mode='json')).encode()),
        'wikidata_raw': {'path': str(raw.resolve()), 'sha256': original.raw_sha256, 'entity_id': 'Q224078',
                         'claim_locator': '/entities/Q224078/claims/P648/1/mainsnak/datavalue/value'},
        'quarantined_identifier': '/works/OL471547W', 'aggregate_proof': {'official_url': 'https://openlibrary.org/works/OL471547W.json',
            'raw_path': str(ol.resolve()), 'raw_sha256': digest(ol.read_bytes())}}
    path = tmp_path / 'correction.json'
    def save():
        path.write_text(canonical_json(packet), encoding='utf-8')
        return digest(path.read_bytes())
    return original, packet, path, save, ol


def test_projection_preserves_original_and_guard(case):
    original, packet, path, save, _ = case
    before = original.model_dump(mode='json')
    assert compare_records(original, original).status == 'uncertain'
    projected = project_identity_correction(original, path, save())
    assert original.model_dump(mode='json') == before == packet['original_record']
    assert [i.value for i in projected.identifiers] == ['Q224078', '/works/OL471932W']
    assert projected.extra['claims'] == original.extra['claims']
    assert projected.raw_path == original.raw_path and projected.raw_sha256 == original.raw_sha256
    verify_identity_correction(projected)
    from recommend_book.usage_guard import UsageGuard
    UsageGuard._raw_identity(None, projected, path.parent.joinpath('wd.json').read_bytes())


@pytest.mark.parametrize('mutation', ['locator', 'identifier', 'raw', 'collection', 'url', 'original_hash'])
def test_invalid_packet_rejected(case, mutation):
    original, packet, path, save, ol = case
    if mutation == 'locator':
        packet['wikidata_raw']['claim_locator'] = '/entities/Q224078/claims/P648/0/mainsnak/datavalue/value'
    elif mutation == 'identifier':
        packet['quarantined_identifier'] = '/works/OL471932W'
    elif mutation == 'raw':
        packet['wikidata_raw']['sha256'] = '0' * 64
    elif mutation == 'collection':
        payload = json.loads(ol.read_bytes()); payload['description']['value'] = 'A novel.'
        ol.write_text(json.dumps(payload), encoding='utf-8')
        packet['aggregate_proof']['raw_sha256'] = digest(ol.read_bytes())
    elif mutation == 'url':
        packet['aggregate_proof']['official_url'] = 'https://example.com/works/OL471547W.json'
    else:
        packet['original_record_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        project_identity_correction(original, path, save())


def test_tampering_rejected(case):
    original, packet, path, save, ol = case
    projected = project_identity_correction(original, path, save())
    injected = projected.model_copy(deep=True)
    injected.subjects.append('unbound injection')
    with pytest.raises(ValueError):
        verify_identity_correction(injected)
    packet['quarantined_identifier'] = '/works/OL471932W'; save()
    with pytest.raises(ValueError):
        verify_identity_correction(projected)


def test_recipe_and_aggregate_raw_tampering_rejected(case):
    original, _, path, save, ol = case
    projected = project_identity_correction(original, path, save())
    injected = projected.model_copy(deep=True)
    injected.extra['identity_correction']['recipe']['sha256'] = '0' * 64
    with pytest.raises(ValueError):
        verify_identity_correction(injected)
    ol.write_bytes(b'{}')
    with pytest.raises(ValueError):
        verify_identity_correction(projected)


@pytest.mark.parametrize('mutation', ['provider', 'edition', 'not_novel', 'deprecated', 'claims', 'missing_title'])
def test_scope_and_raw_semantics_fail_closed(case, mutation):
    original, packet, path, save, ol = case
    raw = Path(original.raw_path)
    entity = json.loads(raw.read_bytes())['entities']['Q224078']
    if mutation == 'provider':
        original.provider_id = 'other'
    elif mutation == 'edition':
        entity['claims']['P629'] = [entity['claims']['P31'][0]]
    elif mutation == 'not_novel':
        entity['claims']['P31'][0]['mainsnak']['datavalue']['value']['id'] = 'Q3331189'
    elif mutation == 'deprecated':
        entity['claims']['P648'][1]['rank'] = 'deprecated'
    elif mutation == 'claims':
        original.extra['claims'] = {}
    else:
        aggregate = json.loads(ol.read_bytes())
        aggregate['description']['value'] = aggregate['description']['value'].replace('Le train bleu --\n', '')
        ol.write_text(json.dumps(aggregate), encoding='utf-8')
        packet['aggregate_proof']['raw_sha256'] = digest(ol.read_bytes())
    if mutation in ('edition', 'not_novel', 'deprecated'):
        raw.write_text(json.dumps({'entities': {'Q224078': entity}}), encoding='utf-8')
        original.raw_sha256 = digest(raw.read_bytes())
        original.extra['claims'] = entity['claims']
        packet['wikidata_raw']['sha256'] = original.raw_sha256
    packet['original_record'] = original.model_dump(mode='json')
    packet['original_record_sha256'] = digest(canonical_json(packet['original_record']).encode())
    with pytest.raises(ValueError):
        project_identity_correction(original, path, save())


def test_existing_wikidata_raw_identity_check_unchanged(case):
    original, _, _, _, _ = case
    from recommend_book.usage_guard import UsageGuard
    UsageGuard._raw_identity(None, original, Path(original.raw_path).read_bytes())


@pytest.mark.parametrize('conflict', [None, 'Q3331189', 'Q53731850', 'Q49084', 'Q277759', 'Q1667921', 'Q1279564', 'Q105420'])
def test_novel_form_p7937_and_conflicting_units(case, conflict):
    original, packet, path, save, _ = case
    claims = original.extra['claims']
    claims['P7937'] = [claims['P31'][0]]
    claims['P31'] = [{'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'property': 'P31',
                         'datavalue': {'value': {'id': 'Q7725634'}}}}]
    if conflict:
        claims['P31'].append({'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'property': 'P31',
                                  'datavalue': {'value': {'id': conflict}}}})
    raw = Path(original.raw_path)
    raw.write_text(json.dumps({'entities': {'Q224078': {'id': 'Q224078', 'claims': claims}}}), encoding='utf-8')
    original.raw_sha256 = digest(raw.read_bytes())
    packet['wikidata_raw']['sha256'] = original.raw_sha256
    packet['original_record'] = original.model_dump(mode='json')
    packet['original_record_sha256'] = digest(canonical_json(packet['original_record']).encode())
    sha = save()
    if conflict:
        with pytest.raises(ValueError):
            project_identity_correction(original, path, sha)
    else:
        verify_identity_correction(project_identity_correction(original, path, sha))
