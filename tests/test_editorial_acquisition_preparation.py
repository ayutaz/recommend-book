import hashlib
import importlib
import json
from pathlib import Path

import pytest

from recommend_book.models import SourceRecord


@pytest.fixture
def preparation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    return importlib.import_module('prepare_editorial_acquisitions_v1')


@pytest.fixture
def authority(tmp_path):
    country = tmp_path / 'country.json'
    country.write_text(json.dumps({'entities': {'Q145': {'id': 'Q145',
        'labels': {'en': {'value': 'United Kingdom'}}}}}), encoding='utf-8')
    return SourceRecord(source='wikidata', source_id='Q1', provider_id='wikidata',
        record_kind='work', source_url='https://www.wikidata.org/wiki/Q1',
        retrieved_at='2026-10-17T00:00:00Z', raw_path=str(tmp_path / 'original.json'),
        raw_sha256='0'*64, rights_policy_id='wikidata-cc0', original_language='Q7979',
        region=['Q145'], extra={'original_language_qids': ['Q7979'],
            'claims': {'P407': [{'mainsnak': {'snaktype': 'value', 'datavalue': {'value': {'id': 'Q7979'}}}}]},
            'linked_label_provenance': {'Q145': {'raw_path': str(country),
                'raw_sha256': hashlib.sha256(country.read_bytes()).hexdigest(), 'label': 'United Kingdom'}}})


def test_unknown_language_and_original_claims_are_preserved(preparation, authority):
    before = authority.model_dump(mode='json')
    projected = preparation.normalize_supported_metadata(authority)
    assert projected.region == ['United Kingdom']
    assert projected.original_language is None
    assert projected.extra['original_language_qids'] == ['Q7979']
    assert projected.extra['claims'] == before['extra']['claims']
    assert authority.model_dump(mode='json') == before
    assert projected.raw_sha256 == authority.raw_sha256


@pytest.mark.parametrize('tamper', ['label', 'raw'])
def test_country_projection_requires_exact_official_label(preparation, authority, tamper):
    proof = authority.extra['linked_label_provenance']['Q145']
    if tamper == 'label':
        proof['label'] = 'France'
    else:
        Path(proof['raw_path']).write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='country'):
        preparation.normalize_supported_metadata(authority)


def test_unselected_acquisition_rejected_before_output_creation(preparation, tmp_path, monkeypatch):
    monkeypatch.setattr(preparation, 'BASE', tmp_path)
    frozen = tmp_path / 'frozen.json'
    frozen.write_text(json.dumps({'rows': [{'source_url': 'https://example.test/selected'}]}), encoding='utf-8')
    cases = tmp_path / 'cases.json'
    cases.write_text(json.dumps({'eligible_cases': [{'wikidata_qid': 'Q1', 'source_url': 'https://example.test/unselected'}],
        'frozen_selection_path': str(frozen), 'frozen_selection_sha256': preparation.sha(frozen)}), encoding='utf-8')
    records = tmp_path / 'records.jsonl'
    records.write_text('', encoding='utf-8')
    output = tmp_path / 'preparation/new'
    with pytest.raises(ValueError, match='pre-fetch selection'):
        preparation.prepare_acquisitions(cases, records, output)
    assert not output.exists()
