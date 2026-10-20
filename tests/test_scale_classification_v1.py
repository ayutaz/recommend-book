"""Future scale source/gate fixtures; no real model invocation or network."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book.database import DatasetDB
from recommend_book.models import Evidence, Identifier, SourceRecord, Title, WorkInput
from recommend_book.scale_classification_v1 import admit_native_labels, verify_catalogue_classification

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(tmp_path, qid='Q101', *, genre=True, claims=None):
    def assertion(ref):
        return {'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'datavalue': {'value': {'id': ref}}}}
    entity = {'id': qid, 'labels': {'en': {'value': 'Book ' + qid}},
              'descriptions': {'en': {'value': 'fantasy novel'}},
              'claims': {'P31': [assertion('Q8261')], **({'P136': [assertion('Q132311')]} if genre else {}), **(claims or {})}}
    payload = {'entities': {qid: entity, 'Q132311': {'id': 'Q132311', 'labels': {'en': {'value': 'fantasy'}}}}}
    path = tmp_path / (qid + '.json')
    path.write_text(json.dumps(payload), encoding='utf-8')
    url = 'https://www.wikidata.org/wiki/' + qid
    evidence = [Evidence(evidence_id=qid + ':desc', kind='description', text='fantasy novel',
        source_url=url, rights_policy_id='wikidata-cc0', llm_input_allowed=True, locator='descriptions.en')]
    if genre:
        evidence.append(Evidence(evidence_id=qid + ':subjects', kind='subject', text='fantasy',
            source_url=url, rights_policy_id='wikidata-cc0', llm_input_allowed=True, locator='claims.P136;claims.P921'))
    return SourceRecord(source='wikidata', source_id=qid, source_url=url,
        retrieved_at=datetime.now(timezone.utc).isoformat(), provider_id='wikidata', record_kind='work',
        titles=[Title(value='Book ' + qid, language='en')], identifiers=[Identifier(source='wikidata', value=qid, scope='work')],
        work_kind='novel', evidence=evidence, raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        raw_path=str(path), rights_policy_id='wikidata-cc0',
        extra={'claims': entity['claims'], 'linked_label_provenance': {'Q132311': {
            'label': 'fantasy', 'raw_path': str(path), 'raw_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}})


def write_records(path, records):
    path.write_text(''.join(row.model_dump_json() + '\n' for row in records), encoding='utf-8')


def test_selection_excludes_actual_old_authorities_and_prefers_genre_claims(tmp_path):
    prep = script('prepare_scale_classification_v1')
    old, plain, genre = record(tmp_path, 'Q101'), record(tmp_path, 'Q102', genre=False), record(tmp_path, 'Q103')
    database = tmp_path / 'old.sqlite'
    with DatasetDB(database) as db:
        db.ingest([old])
    original = database.read_bytes()
    qids, proofs = prep.historical_qids([database])
    assert qids == {'Q101'} and proofs[0]['full_database_hashed'] is False
    path = tmp_path / 'records.jsonl'
    write_records(path, [old, plain, genre])
    selected, excluded = prep.choose_records(path, qids, limit=2)
    assert [row[2].source_id for row in selected] == ['Q103', 'Q102']
    assert excluded[0]['reasons'] == ['historical_work_excluded']
    assert database.read_bytes() == original


def test_raw_and_scope_conflicts_hold_before_source_preparation(tmp_path):
    prep = script('prepare_scale_classification_v1')
    good = record(tmp_path)
    path = tmp_path / 'records.jsonl'
    write_records(path, [good])
    Path(good.raw_path).write_text('{}')
    with pytest.raises(ValueError, match='raw SHA'):
        prep.choose_records(path, set(), limit=1)
    good = record(tmp_path)
    entity = json.loads(Path(good.raw_path).read_bytes())['entities'][good.source_id]
    entity['claims']['P629'] = [{'mainsnak': {'datavalue': {'value': {'id': 'Q999'}}}}]
    assert 'explicit_edition_assertion' in prep.scope_issues(good, entity)


def test_real_source_preparation_admits_all_six_uses_and_saves_exact_next_inputs(tmp_path, monkeypatch):
    prep = script('prepare_scale_classification_v1')
    acquisition = tmp_path / 'acquisition'
    acquisition.mkdir()
    source = record(acquisition)
    records_path = acquisition / 'records.jsonl'
    write_records(records_path, [source])
    old_path = tmp_path / 'old.sqlite'
    with DatasetDB(old_path):
        pass
    official = tmp_path / 'official.html'
    official.write_text('Structured data CC0 licensing fixture', encoding='utf-8')
    conditions = tmp_path / 'native-conditions.json'
    conditions.write_text(json.dumps({'wikidata-cc0': [{'raw_path': str(official),
        'raw_sha256': hashlib.sha256(official.read_bytes()).hexdigest(),
        'source_url': 'https://www.wikidata.org/wiki/Wikidata:Licensing',
        'verified_at': datetime.now(timezone.utc).isoformat()}]}))
    monkeypatch.setattr(prep.subprocess, 'run', lambda *a, **k: pytest.fail('Preparation must not invoke any CLI'))
    result = prep.prepare_sources(records_path, tmp_path / 'source-prep', historical_databases=[old_path],
                                  official_conditions=conditions, limit=1)
    assert result['selected'] == 1 and result['all_six_purpose_admissions_allowed'] is True
    assert result['model_calls'] == result['web_requests'] == 0
    assert result['chunks'][0]['selected'] == 1
    receipts = json.loads((tmp_path / 'source-prep/admission-receipts.json').read_bytes())
    assert len(receipts) == 6 and all(row['allowed'] for values in receipts.values() for row in values)
    saved = json.loads((tmp_path / 'source-prep/work-mapping.json').read_bytes())[0]
    assert saved['wikidata_qid'] == 'Q101' and saved['catalogue_work_id'] == 'wd:Q101'


def label(label_id='genre.fantasy', axis='genre', evidence_id='subject', locator='claims.P136;claims.P921'):
    return {'label_id': label_id, 'axis': axis, 'decision': 'present', 'scope': 'work',
            'raw_confidence': .92, 'relevance': None, 'calibrated_confidence': None,
            'evidence': [{'evidence_id': evidence_id, 'locator': locator, 'quote': 'fantasy', 'summary': None}]}


def work_result():
    work = WorkInput(work_id='test', title='A magical title with no independent meaning',
        evidence=[Evidence(evidence_id='subject', kind='subject', text='fantasy; politics',
            source_url='https://www.wikidata.org/wiki/Q1', rights_policy_id='wikidata-cc0',
            llm_input_allowed=True, locator='claims.P136;claims.P921')])
    result = {'work_id': 'test', 'labels': [label()], 'conflicts': []}
    return work, result


def test_explicit_native_genre_admission_preserves_confidence_and_unknown_relevance():
    work, result = work_result()
    before = deepcopy(result)
    projection = admit_native_labels(work, result, source_genre_terms={'fantasy'})
    assert projection['admitted_labels'] == result['labels']
    assert projection['admitted_labels'][0]['relevance'] is None
    assert projection['admitted_labels'][0]['raw_confidence'] == .92
    assert result == before and projection['native_result_modified'] is False


def test_genuinely_separately_reviewed_non_genre_can_be_admitted_without_an_axis_ban():
    work, result = work_result()
    work.evidence[0].text = 'epistolary novel'
    result['labels'] = [label('structure.epistolary', 'structure')]
    result['labels'][0]['evidence'][0]['quote'] = 'epistolary novel'
    before = deepcopy(result)
    assert not admit_native_labels(work, result)['admitted_labels']
    admitted = admit_native_labels(work, result, review_cleared_label_ids={'structure.epistolary'})
    assert admitted['admitted_labels'] == result['labels']
    assert result == before and admitted['null_relevance_preserved']
    result['labels'][0]['decision'] = 'unknown'
    assert not admit_native_labels(work, result, review_cleared_label_ids={'structure.epistolary'})['admitted_labels']


@pytest.mark.parametrize('change', ['theme', 'literary', 'topic_only', 'wrong_citation', 'conflict'])
def test_metadata_does_not_invent_plot_labels_or_treat_topic_as_genre(change):
    work, result = work_result()
    genres = {'fantasy'}
    if change == 'theme':
        result['labels'] = [label('theme.power', 'theme')]
    elif change == 'literary':
        result['labels'] = [label('genre.literary')]
    elif change == 'topic_only':
        genres = {'political fiction'}
    elif change == 'conflict':
        result['conflicts'] = ['actual conflicting source scope']
    else:
        result['labels'][0]['evidence'][0]['locator'] = 'wrong'
        with pytest.raises(ValueError, match='citation'):
            admit_native_labels(work, result, source_genre_terms=genres)
        return
    projection = admit_native_labels(work, result, source_genre_terms=genres)
    assert not projection['admitted_labels'] and projection['held_labels']


def test_exact_stage_gate_requires_real_adoption_not_only_native_completion():
    run = script('run_scale_classification_v1')
    manifest = {'selected': 100, 'failed': 5, 'pending': 0, 'stopped': False, 'stop_reasons': []}
    projection = {'actual_feature_adopted_works': 70}
    assert run.check_gate(manifest, projection)['passed'] is True
    assert not run.check_gate({**manifest, 'failed': 6}, projection)['passed']
    assert not run.check_gate(manifest, {'actual_feature_adopted_works': 69})['passed']
    assert not run.check_gate({**manifest, 'stop_reasons': ['rate_limit']}, projection)['passed']
    assert not run.check_gate({**manifest, 'pending': 1}, projection)['passed']


def test_catalogue_export_rejects_unpinned_or_changed_manifest_before_subprocess(tmp_path, monkeypatch):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text('{}')
    monkeypatch.setattr('recommend_book.scale_classification_v1.subprocess.run', lambda *a, **k: pytest.fail('Must reject first'))
    with pytest.raises(ValueError, match='manifest SHA'):
        verify_catalogue_classification(manifest, '0' * 64)


def test_new_input_keeps_only_two_preferred_descriptions_and_subjects_without_touching_original():
    prep = script('prepare_scale_classification_v1')
    work, _ = work_result()
    for language in ('fr', 'de', 'ja', 'en'):
        work.evidence.append(Evidence(evidence_id='desc-' + language, kind='description', text='fantasy novel',
            source_url='https://www.wikidata.org/wiki/Q1', rights_policy_id='wikidata-cc0',
            llm_input_allowed=True, language=language, locator='descriptions.' + language))
    before = work.model_dump(mode='json')
    reduced = prep.scale_evidence_input(work)
    assert {e.evidence_id for e in reduced.evidence} == {'subject', 'desc-en', 'desc-ja'}
    assert work.model_dump(mode='json') == before


def test_separate_review_reuses_full_v2_protocol_and_only_relaxes_held_job_coverage(monkeypatch):
    review = script('review_scale_classification_v1')
    original = (ROOT / 'scripts/review_world_pilot_v1.py').read_bytes()
    generated = review.load_review()
    assert generated.execute.__kwdefaults__['instructions'] == generated.INSTRUCTIONS
    assert (ROOT / 'scripts/review_world_pilot_v1.py').read_bytes() == original
    captured = {}
    def fake_execute(args, **kwargs):
        captured.update(kwargs)
        return {'fixture_only': True}
    generated.execute = fake_execute
    monkeypatch.setattr(review, 'load_review', lambda: generated)
    assert review.execute(object()) == {'fixture_only': True}
    assert captured['protocol'] == 'complete-input-native-output-internal-review-v2'
    assert 'independent internal evidence reviewer' in captured['instructions']
    assert 'EVERY exact native label' in captured['instructions']
    assert Path(review.__file__) in captured['extra_source_paths']


@pytest.mark.parametrize('usage,elapsed,stop', [({'input_tokens': 2000000}, 1, []), ({}, 3600, []), ({}, 1, ['rate_limit'])])
def test_initial_separate_review_never_requests_more_model_after_combined_budget_or_stop(tmp_path, monkeypatch, usage, elapsed, stop):
    run = script('run_scale_classification_v1')
    (tmp_path / 'manifest.json').write_text(json.dumps({'usage': usage, 'elapsed_seconds': elapsed, 'stop_reasons': stop}))
    monkeypatch.setattr(run, 'frozen_call', lambda *a, **k: pytest.fail('No model call after budget or quota stop'))
    with pytest.raises(ValueError, match='exhausted'):
        run.review_initial_window(tmp_path, {'run_dir': str(tmp_path)})


def test_scale_cache_is_exact_byte_read_only_and_does_not_patch_global_json():
    from recommend_book.scale_guard_context_v1 import ExactJSONCache, guard_json_cache
    from recommend_book import usage_guard
    global_loads = json.loads
    original = usage_guard.json
    with guard_json_cache() as cache:
        assert json.loads is global_loads and usage_guard.json is not original
        first = usage_guard.json.loads(b'{"nested":{"values":[1]}}')
        assert usage_guard.json.loads(b'{"nested":{"values":[1]}}') is first
        with pytest.raises(TypeError, match='read-only'):
            first['nested']['values'].append(2)
        with pytest.raises(TypeError, match='read-only'):
            first['nested']['other'] = 2
        assert usage_guard.json.loads(b'{"nested":{"values":[2]}}')['nested']['values'] == [2]
        assert cache.hits == 1 and cache.misses == 2
    assert usage_guard.json is original


def test_scale_cache_never_reuses_usage_decision_after_revocation_or_raw_tamper(tmp_path):
    import os
    from test_usage_guard import fixture_ledger
    from recommend_book.scale_guard_context_v1 import guard_json_cache
    from recommend_book.usage_guard import UsageGuard, model_hash
    work, path, raw, _, payload = fixture_ledger(tmp_path)
    guard = UsageGuard(path)
    with guard_json_cache():
        assert guard.check_input(work, 'external_llm').allowed
        payload['revoked_input_hashes'] = [model_hash(work)]
        path.write_text(json.dumps(payload), encoding='utf-8')
        assert not guard.check_input(work, 'external_llm').allowed
        payload['revoked_input_hashes'] = []
        path.write_text(json.dumps(payload), encoding='utf-8')
        assert guard.check_input(work, 'external_llm').allowed
        stat = raw.stat()
        raw.write_bytes(raw.read_bytes().replace(b'Book', b'Hack'))
        os.utime(raw, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert not guard.check_input(work, 'external_llm').allowed


def test_resume_source_admission_reuses_pinned_database_without_reingest(tmp_path, monkeypatch):
    from recommend_book.usage_guard import UsageGuard
    prep = script('prepare_scale_classification_v1')
    acquisition = tmp_path / 'acquisition'; acquisition.mkdir()
    records_path = acquisition / 'records.jsonl'; write_records(records_path, [record(acquisition)])
    old = tmp_path / 'old.sqlite'
    with DatasetDB(old):
        pass
    official = tmp_path / 'official.html'; official.write_text('CC0 structured data')
    conditions = tmp_path / 'conditions.json'
    conditions.write_text(json.dumps({'wikidata-cc0': [{'raw_path': str(official),
        'raw_sha256': hashlib.sha256(official.read_bytes()).hexdigest(),
        'source_url': 'https://www.wikidata.org/wiki/Wikidata:Licensing',
        'verified_at': datetime.now(timezone.utc).isoformat()}]}))
    output = tmp_path / 'prepared'
    original_check = UsageGuard.check_input
    monkeypatch.setattr(UsageGuard, 'check_input', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('interrupted-before-admission')))
    with pytest.raises(RuntimeError, match='interrupted-before-admission'):
        prep.prepare_sources(records_path, output, historical_databases=[old], official_conditions=conditions, limit=1)
    files = [output / 'sources/source-bindings.sqlite', output / 'ordered-inputs.jsonl',
             output / 'source-registry.json', output / 'usage-ledger-inputs.json']
    pins = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (output / 'admission-interrupted-checkpoint.v1.json').write_text(json.dumps({
        'selected': 1, 'stopped_before_model_calls': True, 'files': pins}))
    monkeypatch.setattr(UsageGuard, 'check_input', original_check)
    monkeypatch.setattr(DatasetDB, 'ingest', lambda *a, **k: pytest.fail('Resume must not reingest source DB'))
    result = prep.prepare_sources(records_path, output, historical_databases=[old], official_conditions=conditions, limit=1, resume=True)
    assert result['selected'] == 1
    assert (output / 'admission-receipts-resumed.v1.json').exists()
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest for p, digest in pins.items())
