import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from recommend_book.database import DatasetDB
from recommend_book.ingestion.core import write_jsonl
from recommend_book.models import Contributor, Identifier, SourceRecord, Title


def module():
    spec = importlib.util.spec_from_file_location('enrich_openlibrary_dataset', Path(__file__).parents[1] / 'scripts/enrich_openlibrary_dataset.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def save_raw(tmp_path, name, payload, *, ol=False, kind='work'):
    if ol:
        type_name = '/type/author' if kind == 'agent' else '/type/work'
        payload = {**payload, 'type': {'key': type_name}, 'revision': 1}
        raw = (type_name+'\t'+payload['key']+'\t1\t2026-10-13\t'+json.dumps(payload)+'\n').encode()
    else:
        raw = json.dumps(payload).encode()
    path = tmp_path / name
    path.write_bytes(raw)
    return str(path), hashlib.sha256(raw).hexdigest()


def fixture(tmp_path, *, wd_ids=True, conflict=False, volume=None, kind='novel', coauthor=False):
    author_payload = {'id': 'Q2', 'claims': {'P648': [{'mainsnak': {'datavalue': {'value': 'OL2A'}}}]}, 'labels': {'en': {'value': 'Author'}}}
    ap, ash = save_raw(tmp_path, 'wd-author.json', {'entities': {'Q2': author_payload}})
    wp, wsh = save_raw(tmp_path, 'wd.json', {'entities': {'Q1': {'id': 'Q1', 'claims': {'P648': [{'mainsnak': {'datavalue': {'value': 'OL1W'}}}], 'P50': [{'mainsnak': {'datavalue': {'value': {'id': 'Q2'}}}}]}}}})
    author = Contributor(name='Author', identifiers=[Identifier(source='wikidata', value='Q2', scope='agent'), Identifier(source='openlibrary', value='/authors/OL2A', scope='agent')] if wd_ids else [])
    wd = SourceRecord(source='wikidata', source_id='Q1', source_url='https://example.test/Q1', retrieved_at='2026-10-13', provider_id='wikidata', record_kind='work', titles=[Title(value='Novel', language='en')], contributors=[author] + ([Contributor(name='Second', identifiers=[Identifier(source='wikidata', value='Q3', scope='agent')])] if coauthor else []), identifiers=[Identifier(source='wikidata', value='Q1', scope='work'), Identifier(source='openlibrary', value='/works/OL1W', scope='work')], original_language='en', work_kind=kind, volume=volume, raw_path=wp, raw_sha256=wsh, rights_policy_id='wikidata-cc0', extra={'linked_label_provenance': {'Q2': {'raw_path': ap, 'raw_sha256': ash}}})
    op, osh = save_raw(tmp_path, 'ol-work.bin', {'key': '/works/OL1W', 'title': 'Novel', 'authors': [{'author': {'key': '/authors/OL2A'}}]}, ol=True)
    ol = SourceRecord(source='openlibrary', source_id='/works/OL1W', source_url='https://example.test/OL1W', retrieved_at='2026-10-13', provider_id='openlibrary', record_kind='work', titles=[Title(value='Novel')], contributors=[Contributor(name='Author', identifiers=[Identifier(source='openlibrary', value='/authors/OL2A', scope='agent')])], identifiers=[Identifier(source='openlibrary', value='/works/OL1W', scope='work')], raw_path=op, raw_sha256=osh, rights_policy_id='openlibrary-metadata')
    aid = 'Q999' if conflict else 'Q2'
    op, osh = save_raw(tmp_path, 'ol-author.bin', {'key': '/authors/OL2A', 'name': 'Author', 'remote_ids': {'wikidata': aid}}, ol=True, kind='agent')
    agent = SourceRecord(source='openlibrary', source_id='/authors/OL2A', source_url='https://example.test/OL2A', retrieved_at='2026-10-13', provider_id='openlibrary', record_kind='agent', titles=[Title(value='Author')], identifiers=[Identifier(source='openlibrary', value='/authors/OL2A', scope='agent'), Identifier(source='wikidata', value=aid, scope='agent')], raw_path=op, raw_sha256=osh, rights_policy_id='openlibrary-metadata')
    base = tmp_path / 'base.sqlite'
    with DatasetDB(base) as db:
        db.ingest([wd])
    candidates = tmp_path / 'candidates.jsonl'
    write_jsonl([ol, agent], candidates)
    return base, candidates, wd, ol, agent


def test_exact_authorities_enrich_copy_preserve_originals_and_report_crosswalk_groups(tmp_path):
    base, candidates, wd, ol, agent = fixture(tmp_path)
    old = base.read_bytes()
    frozen = tmp_path / 'frozen-results.jsonl'; frozen.write_bytes(b'fixed classification\n')
    result = module().enrich(base, candidates, tmp_path / 'new', protected_paths=[frozen])
    assert result['accepted_works'] == result['accepted_agents'] == 1
    assert result['canonical_works_before'] == result['canonical_works_after'] == 1
    assert result['classification_rebound'] is False and result['web_requests'] == 0
    assert base.read_bytes() == old and frozen.read_bytes() == b'fixed classification\n'
    assert result['audit']['integrity_check'] == 'ok' and result['audit']['foreign_key_violations'] == 0
    crosswalk = json.loads((tmp_path / 'new/canonical-crosswalk.json').read_text())
    assert len(crosswalk) == 1 and len(crosswalk[0]['new_canonical_ids']) == 1
    groups = json.loads((tmp_path / 'new/group-impact.json').read_text())
    assert groups['connected_partition_unchanged']
    with sqlite3.connect(tmp_path / 'new/dataset.sqlite') as db:
        assert db.execute('select count(*) from source_records').fetchone()[0] == 3
        assert db.execute('select count(*) from source_records where raw_sha256=?', (wd.raw_sha256,)).fetchone()[0] == 1


@pytest.mark.parametrize('options,reason', [({'conflict': True}, 'conflicting author authority namespace'), ({'wd_ids': False}, 'author authority'), ({'coauthor': True}, 'complete author set')])
def test_unsafe_author_sets_are_held_even_when_name_or_ol_work_id_matches(tmp_path, options, reason):
    base, candidates, *_ = fixture(tmp_path, **options)
    result = module().enrich(base, candidates, tmp_path / 'new')
    assert result['accepted_works'] == 0 and result['held_works'] == 1
    held = json.loads((tmp_path / 'new/selection-decisions.json').read_text())
    assert reason in held[0]['reason']
    assert result['canonical_works_after'] == result['canonical_works_before']


@pytest.mark.parametrize('updates,reason', [({'volume': '2'}, 'volume'), ({'work_kind': 'anthology'}, 'work_kind')])
def test_volume_and_unit_conflicts_are_held(tmp_path, updates, reason):
    base, candidates, wd, ol, agent = fixture(tmp_path, volume='1')
    write_jsonl([ol.model_copy(update=updates), agent], candidates)
    result = module().enrich(base, candidates, tmp_path / 'new')
    assert result['accepted_works'] == 0
    assert reason in json.loads((tmp_path / 'new/selection-decisions.json').read_text())[0]['reason']


def test_raw_tampering_fails_without_creating_output(tmp_path):
    base, candidates, wd, ol, agent = fixture(tmp_path)
    Path(ol.raw_path).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='SHA'):
        module().enrich(base, candidates, tmp_path / 'new')
    assert not (tmp_path / 'new').exists()


def test_output_must_be_new_and_cannot_contain_protected_files(tmp_path):
    base, candidates, *_ = fixture(tmp_path)
    with pytest.raises(ValueError, match='new'):
        module().enrich(base, candidates, tmp_path)


def test_unrepresented_raw_series_position_is_held(tmp_path):
    base, candidates, wd, ol, agent = fixture(tmp_path)
    payload = {'key': ol.source_id, 'title': 'Novel', 'authors': [{'author': {'key': agent.source_id}}], 'series': [{'series': {'key': '/series/OL7L'}, 'position': '2'}]}
    path, sha = save_raw(tmp_path, 'series.bin', payload, ol=True)
    write_jsonl([ol.model_copy(update={'raw_path': path, 'raw_sha256': sha}), agent], candidates)
    result = module().enrich(base, candidates, tmp_path / 'new')
    assert result['accepted_works'] == 0
    assert 'unrepresented series' in json.loads((tmp_path / 'new/selection-decisions.json').read_text())[0]['reason']


def test_shared_ol_work_id_with_two_wikidata_works_is_held(tmp_path):
    base, candidates, wd, *_ = fixture(tmp_path)
    raw = json.loads(Path(wd.raw_path).read_text())['entities']['Q1']; raw['id'] = 'Q4'
    path, digest = save_raw(tmp_path, 'wd-other.json', {'entities': {'Q4': raw}})
    other = wd.model_copy(update={'source_id': 'Q4', 'raw_path': path, 'raw_sha256': digest,
                                'identifiers': [Identifier(source='wikidata', value='Q4', scope='work'), Identifier(source='openlibrary', value='/works/OL1W', scope='work')]})
    with DatasetDB(base) as db:
        db.ingest([other])
    report = module().enrich(base, candidates, tmp_path / 'new')
    assert report['accepted_works'] == 0 and report['canonical_works_after'] == 2
    assert json.loads((tmp_path / 'new/selection-decisions.json').read_text())[0]['reason'] == 'ambiguous explicit work identity'


def test_unrepresented_subtitle_unit_is_held_and_duplicate_candidates_fail(tmp_path):
    base, candidates, wd, ol, agent = fixture(tmp_path)
    raw = json.loads(Path(ol.raw_path).read_bytes().rstrip(b'\r\n').split(b'\t', 4)[4]); raw['subtitle'] = 'Vol 2'
    path, digest = save_raw(tmp_path, 'volume.bin', raw, ol=True)
    write_jsonl([ol.model_copy(update={'raw_path': path, 'raw_sha256': digest}), agent], candidates)
    report = module().enrich(base, candidates, tmp_path / 'new')
    assert report['accepted_works'] == 0
    assert 'subtitle volume/book' in json.loads((tmp_path / 'new/selection-decisions.json').read_text())[0]['reason']
    write_jsonl([ol, ol, agent], candidates)
    with pytest.raises(ValueError, match='Duplicate'):
        module().enrich(base, candidates, tmp_path / 'duplicates')


def test_failed_validation_has_explicit_manifest_and_is_not_a_successful_snapshot(tmp_path):
    base, candidates, *_ = fixture(tmp_path)
    api = module()
    def invalid_group_tokens(*args):
        raise ValueError('simulated copy validation failure')
    api.group_tokens = invalid_group_tokens
    with pytest.raises(ValueError, match='simulated'):
        api.enrich(base, candidates, tmp_path / 'new')
    manifest = json.loads((tmp_path / 'new/manifest.json').read_text())
    assert manifest['status'] == 'pending_validation' and manifest['outputs_are_valid'] is False


def test_injected_work_refs_cannot_bypass_raw_identity_verification(tmp_path):
    base, candidates, wd, ol, agent = fixture(tmp_path)
    raw = json.loads(Path(wd.raw_path).read_text()); raw['entities']['Q1']['claims'].pop('P648')
    path, digest = save_raw(tmp_path, 'wd-without-ol.json', raw)
    wd = wd.model_copy(update={'raw_path': path, 'raw_sha256': digest, 'identifiers': [Identifier(source='wikidata',value='Q1',scope='work')]})
    alternate = tmp_path / 'alternate.sqlite'
    with DatasetDB(alternate) as db:
        db.ingest([wd])
    raw = {'key': '/works/OL999W', 'title': 'Novel', 'authors': [{'author': {'key': '/authors/OL2A'}}]}
    path, digest = save_raw(tmp_path, 'unrelated.bin', raw, ol=True)
    unrelated = ol.model_copy(update={'source_id': '/works/OL999W', 'raw_path': path, 'raw_sha256': digest,
        'identifiers': [Identifier(source='openlibrary',value='/works/OL999W',scope='work')],
        'work_refs': [Identifier(source='wikidata',value='Q1',scope='work')]})
    write_jsonl([unrelated,agent],candidates)
    with pytest.raises(ValueError, match='work_refs'):
        module().enrich(alternate,candidates,tmp_path / 'injected')
    assert not (tmp_path / 'injected').exists()


def test_multiple_author_qids_are_held_even_when_one_matches(tmp_path):
    base, candidates, wd, ol, agent = fixture(tmp_path)
    raw = {'key': agent.source_id, 'name': 'Author', 'identifiers': {'wikidata': ['Q2','Q999']}}
    path, digest = save_raw(tmp_path, 'ambiguous-author.bin', raw, ol=True, kind='agent')
    multi = agent.model_copy(update={'raw_path': path, 'raw_sha256': digest, 'identifiers': [*agent.identifiers, Identifier(source='wikidata',value='Q999',scope='agent')]})
    write_jsonl([ol,multi],candidates)
    report = module().enrich(base,candidates,tmp_path / 'new')
    assert report['accepted_works'] == 0
    assert 'ambiguous author authority namespace' in json.loads((tmp_path/'new/selection-decisions.json').read_text())[0]['reason']
