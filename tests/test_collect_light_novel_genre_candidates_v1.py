from __future__ import annotations
import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('ln_genre_candidates', Path(__file__).parents[1] / 'scripts/collect_light_novel_genre_candidates_v1.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def test_discovery_covers_explicit_genre_without_language_or_type_bias():
    query = M.discovery_query(100, 200)
    assert 'wdt:P136 wd:Q747381' in query
    assert 'P407' not in query and 'P31' not in query
    assert 'LIMIT 100 OFFSET 200' in query


def test_generic_literary_work_with_explicit_japanese_genre_stays_candidate():
    def st(prop, qid):
        return {'rank': 'normal', 'mainsnak': {'property': prop, 'snaktype': 'value', 'datavalue': {'value': {'id': qid}}}}
    entity = {'id': 'Q106126401', 'labels': {'ja': {'value': 'Fixture novel'}}, 'claims': {'P31': [st('P31', 'Q7725634')], 'P407': [st('P407', 'Q5287')], 'P136': [st('P136', 'Q747381')]}}
    row = M.project_candidate(entity, {}, {}, '')
    assert row['unit_kind'] == 'unknown'
    assert row['japanese_candidate'] is True
    assert row['admission']['catalogue_admission_allowed'] is False
    assert row['candidate_basis']['light_novel_genre_asserted'] is True


def test_existing_entities_are_reused_as_bytes_not_refetched(tmp_path):
    parent = tmp_path / 'parent'
    parent.mkdir()
    raw = parent / 'raw.json'
    payload = {'entities': {'Q11': {'id': 'Q11', 'lastrevid': 123, 'claims': {}}}}
    raw.write_text(json.dumps(payload))
    M.H.save(parent / 'entities-index.json', {'Q11': {'raw_path': str(raw), 'raw_sha256': M.H.sha(raw.read_bytes()), 'entity_revision': 123}})
    index = M.prior_entities(parent)
    assert index['Q11']['raw_path'] == str(raw)
    assert M.read_entity('Q11', index['Q11'])['lastrevid'] == 123
