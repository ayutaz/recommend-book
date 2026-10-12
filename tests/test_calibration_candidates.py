import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book.classification.runner import build_run_config
from recommend_book.classification.validation import ROOT, input_hash, target_language
from recommend_book.evaluation.splits import connected_groups
from recommend_book.jobs import canonical_json, job_key
from recommend_book.models import WorkInput


def module():
    path = Path(__file__).parents[1] / 'scripts/diagnose_calibration_candidates.py'
    spec = importlib.util.spec_from_file_location('diagnose_calibration_candidates', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path, *, mixed_target=False, unknown_language=False):
    frame = []
    for wid in ['d', 'c1', 'c2', 'h', 'p1', 'p2']:
        evidence = [{'evidence_id': wid+':b', 'kind': 'bibliographic', 'text': 'Fixture only', 'source_url': 'https://example.test', 'rights_policy_id': 'fixture', 'llm_input_allowed': True, 'language': 'en'}]
        if wid == 'c1':
            evidence.append({'evidence_id': wid+':note', 'kind': 'note', 'text': 'Not model-visible', 'source_url': 'https://example.test', 'rights_policy_id': 'fixture', 'llm_input_allowed': False})
        if mixed_target and wid == 'c2': evidence[0]['language'] = 'fr'
        if unknown_language: evidence[0]['language'] = None
        frame.append(WorkInput(work_id=wid, title=wid, authors=['Author '+wid], original_language=None if unknown_language else 'en', evidence=evidence).model_dump(mode='json'))
    identities = {r['work_id']: ['fixture:'+r['work_id']] for r in frame}
    split = {'sizes': [1, 2, 1], 'partitions': {'dev': ['d'], 'calibration': ['c1', 'c2'], 'holdout': ['h']}, 'unselected_work_ids': ['p1', 'p2'], 'group_identities': identities, 'group_ids': connected_groups(frame, identities)}
    split['input_sha256'] = hashlib.sha256(canonical_json({'inputs': sorted(frame, key=lambda r: r['work_id']), 'group_identities': identities}).encode()).hexdigest()
    split['group_identities_sha256'] = hashlib.sha256(canonical_json(identities).encode()).hexdigest()
    split['manifest_sha256'] = hashlib.sha256(json.dumps(split, ensure_ascii=False, indent=2).encode()).hexdigest()
    axes = ['genre', 'theme', 'structure', 'protagonist', 'style', 'atmosphere']
    taxonomy = json.loads((ROOT/'config/taxonomy.json').read_bytes())
    config = build_run_config('fixture-model', 'fixture-cli', taxonomy, 1)
    extra = WorkInput(work_id='extra-classic', title='Extra', authors=['Extra Author'], original_language='en', evidence=frame[0]['evidence']).model_dump(mode='json')
    selected = frame + [extra]
    predictions, jobs = [], {}
    for r in selected:
        work = WorkInput.model_validate(r)
        digest = input_hash(work)
        key = job_key(digest, config)
        jobs[key] = {'key': key, 'status': 'completed', 'result_path': 'results/'+key+'.json', 'work_id': r['work_id'], 'expression_id': r['expression_id'], 'input_hash': digest, 'config': config}
        evidence = work.evidence[0]
        predictions.append({'work_id': r['work_id'], 'expression_id': r['expression_id'], 'input_hash': digest, 'model': config['model'], 'prompt_version': config['prompt_version'], 'taxonomy_version': config['taxonomy_version'], 'cli_version': config['cli_version'], 'target_language': target_language(work), 'labels': [{'label_id': 'genre.mystery', 'axis': 'genre', 'decision': 'present', 'scope': 'work', 'expression_id': None, 'relevance': None, 'raw_confidence': .49 if r['work_id'] == 'c1' else .5 if r['work_id'] in ('c2', 'p1') else 1.0, 'calibrated_confidence': None, 'calibration_status': 'uncalibrated', 'calibration_version': None, 'evidence': [{'evidence_id': evidence.evidence_id, 'locator': evidence.locator, 'quote': evidence.text, 'summary': None, 'directness': 'direct'}], 'missing_reason': None, 'spoiler': 'none'}], 'missing_axes': [{'axis': a, 'decision': 'unknown', 'reason': 'Fixture insufficient'} for a in axes if a != 'genre'], 'conflicts': [], 'needs_human_review': True})
    controls = {'batch_size': 1, 'concurrency': 2, 'timeout': 900, 'max_run_seconds': None, 'max_observed_tokens': None, 'stop_on_rate_limit': False}
    manifest = {'run_id': 'fixture-run', 'config': config, 'selected': 7, 'completed': 7, 'failed': 0, 'pending': 0, 'stopped': False, 'stop_reasons': [], 'jobs': jobs, 'initial_concurrency': 2, 'limits': {k: v for k, v in controls.items() if k not in ('batch_size', 'concurrency')}}
    paths = {key: tmp_path/(key+'.json') for key in ['frame', 'frame_manifest', 'identities', 'split', 'review_inputs', 'taxonomy', 'run_inputs', 'run_manifest', 'results', 'execution_context_manifest', 'migration_manifest']}
    for key, value in [('frame', frame), ('identities', identities), ('split', split), ('review_inputs', [r for r in frame if r['work_id'] in ['d', 'c1', 'c2', 'h']]), ('taxonomy', taxonomy), ('run_inputs', selected), ('run_manifest', manifest), ('results', predictions)]:
        paths[key].write_text(json.dumps(value), 'utf-8')
    paths['frame_manifest'].write_text(json.dumps({'work_count': 6, 'work_inputs_sha256': sha(paths['frame'])}), 'utf-8')
    paths['taxonomy'].write_bytes((ROOT/'config/taxonomy.json').read_bytes())
    artifacts = ('config/sources.json', 'config/taxonomy.json', 'schemas/classification.schema.json', 'src/recommend_book/classification/prompt.py', 'src/recommend_book/classification/validation.py', 'src/recommend_book/classification/runner.py', 'src/recommend_book/models.py', 'src/recommend_book/jobs.py', 'pyproject.toml', 'uv.lock')
    paths['execution_context_manifest'].write_text(json.dumps({'version': 'classification-execution-freeze-v1', 'input_provenance': {'input_sha256': sha(paths['run_inputs']), 'work_expression_count': 7}, 'execution_context': {'run_config': config, 'controls': controls, 'artifact_count': 10, 'artifact_sha256': {n: sha(ROOT/n) for n in artifacts}, 'code_root': str(ROOT.resolve())}}), 'utf-8')
    paths['migration_manifest'].write_text(json.dumps({'fixture': 'selected input migration'}), 'utf-8')
    paths['cli_profile'] = paths['execution_context_manifest']
    (tmp_path/'results').mkdir()
    for result in predictions:
        key = job_key(result['input_hash'], config)
        (tmp_path/'results'/(key+'.json')).write_text(json.dumps(result), 'utf-8')
    proof_path = tmp_path/'terminal-proof.json'
    paths['terminal_proof'] = proof_path
    refresh_proof(paths)
    return paths


def refresh_proof(paths):
    manifest = json.loads(paths['run_manifest'].read_text('utf-8'))
    proof = {'schema_version': 'classification-terminal-proof-v1', 'run_id': manifest['run_id'], 'process_exit_observed': True, 'session_id': 'fixture-handle', 'exit_code': 0, 'observed_at': '2026-10-12T12:00:00+00:00', 'manifest_sha256': sha(paths['run_manifest']), 'results_sha256': sha(paths['results']), 'ordered_inputs_sha256': sha(paths['run_inputs']), 'execution_context_manifest_sha256': sha(paths['execution_context_manifest']), 'migration_manifest_sha256': sha(paths['migration_manifest']), 'cli_profile_sha256_before': sha(paths['cli_profile']), 'cli_profile_sha256_after': sha(paths['cli_profile']), 'artifact_sha256': {k: sha(p) for k, p in paths.items() if k != 'terminal_proof'}}
    paths['terminal_proof'].write_text(json.dumps(proof), 'utf-8')


def run(paths):
    return module().diagnose(paths, expected_frame_n=6, expected_run_n=7, expected_sizes=(1, 2, 1))


def mutate(paths, key, change, refresh=True):
    value = json.loads(paths[key].read_text('utf-8'))
    change(value)
    paths[key].write_text(json.dumps(value), 'utf-8')
    if key == 'results':
        config = json.loads(paths['run_manifest'].read_bytes())['config']
        for result in value:
            if isinstance(result.get('input_hash'), str):
                durable = paths['run_manifest'].parent/'results'/(job_key(result['input_hash'], config)+'.json')
                durable.write_text(json.dumps(result), 'utf-8')
    if refresh: refresh_proof(paths)


def test_bounds_known_bins_and_scope_match_importer(tmp_path):
    report = run(fixture(tmp_path))
    row = next(r for r in report['scopes'] if r['truth_level'] == 'evidence' and r['label_id'] == 'genre.mystery')
    assert row['calibration']['metadata_upper_n'] == 2
    assert row['calibration']['known_prediction_with_raw_confidence_n'] == 2
    assert row['calibration']['bins'] == {'low': 1, 'high': 1}
    assert row['fresh_pool']['bins'] == {'low': 0, 'high': 2}
    assert row['combined_prediction_capacity']['minimum_scope_n'] == 40
    assert report['diagnostic_frame_works'] == 6
    assert report['validated_run_selected'] == 7
    assert report['human_truth_used'] is False
    assert sorted({r['evidence_kind'] for r in report['scopes'] if r['truth_level'] == 'work'}) == ['bibliographic', 'bibliographic+note']


def test_declared_held_is_missing_result_never_unknown_or_denominator_removed(tmp_path):
    paths = fixture(tmp_path)
    mutate(paths, 'results', lambda r: r.__setitem__(slice(None), [x for x in r if x['work_id'] != 'p2']))
    def held(m):
        m['completed'] = 6; m['failed'] = 1
        next(v for v in m['jobs'].values() if v['work_id'] == 'p2')['status'] = 'held'
    mutate(paths, 'run_manifest', held)
    row = next(r for r in run(paths)['scopes'] if r['truth_level'] == 'evidence' and r['label_id'] == 'genre.mystery')
    assert row['fresh_pool']['metadata_upper_n'] == 2
    assert row['fresh_pool']['missing_result_n'] == 1
    assert row['fresh_pool']['unknown_prediction_n'] == 0
    assert row['fresh_pool']['known_prediction_with_raw_confidence_n'] == 1


@pytest.mark.parametrize('key,change', [
    ('run_manifest', lambda m: m.update(pending=1)),
    ('run_manifest', lambda m: m.update(stopped=True)),
    ('run_manifest', lambda m: m.update(completed=6)),
    ('results', lambda r: r.pop()),
    ('results', lambda r: r.append(copy.deepcopy(r[0]))),
    ('results', lambda r: r[0].update(input_hash='0'*64)),
    ('results', lambda r: r[0].update(expression_id='wrong-expression')),
    ('results', lambda r: r[0].update(model='other-model')),
    ('results', lambda r: r[0].update(cli_version='other-cli')),
    ('results', lambda r: r[0].update(target_language='ja')),
    ('results', lambda r: r[0]['labels'][0].update(raw_confidence=True)),
    ('results', lambda r: r[0]['labels'][0].update(raw_confidence=1.1)),
    ('results', lambda r: r[0]['labels'].append(copy.deepcopy(r[0]['labels'][0]))),
    ('terminal_proof', lambda p: p.update(process_exit_observed=False)),
])
def test_partial_or_corrupt_identity_context_terminal_data_rejected(tmp_path, key, change):
    paths = fixture(tmp_path)
    if key == 'terminal_proof':
        mutate(paths, key, change, refresh=False)
    else:
        mutate(paths, key, change)
    with pytest.raises(ValueError): run(paths)


def test_changed_bytes_after_terminal_attestation_are_rejected(tmp_path):
    paths = fixture(tmp_path)
    mutate(paths, 'results', lambda r: r[0]['labels'][0].update(raw_confidence=.25), refresh=False)
    with pytest.raises(ValueError, match='SHA'): run(paths)


def test_unknown_and_omitted_labels_not_known_capacity(tmp_path):
    paths = fixture(tmp_path)
    def change(rows):
        unknown = next(r for r in rows if r['work_id'] == 'c1')
        omitted = next(r for r in rows if r['work_id'] == 'c2')
        unknown['labels'][0].update(decision='unknown', raw_confidence=None, missing_reason='Fixture insufficient')
        omitted['labels'] = []
        for row in [unknown, omitted]: row['missing_axes'].append({'axis': 'genre', 'decision': 'unknown', 'reason': 'Fixture insufficient'})
    mutate(paths, 'results', change)
    row = next(r for r in run(paths)['scopes'] if r['truth_level'] == 'evidence' and r['label_id'] == 'genre.mystery')
    assert row['calibration']['unknown_prediction_n'] == 1
    assert row['calibration']['omitted_label_n'] == 1
    assert row['calibration']['known_prediction_with_raw_confidence_n'] == 0


def test_authority_bridge_across_partitions_rejected(tmp_path):
    paths = fixture(tmp_path)
    def change(split):
        split['group_identities']['c1'] = split['group_identities']['d']
        # Refresh the self checksum; mismatch with identity input still must fail.
        split.pop('manifest_sha256')
        split['manifest_sha256'] = hashlib.sha256(json.dumps(split, ensure_ascii=False, indent=2).encode()).hexdigest()
    mutate(paths, 'split', change)
    with pytest.raises(ValueError): run(paths)


def test_context_buckets_keep_scope_target_languages_separate(tmp_path):
    paths = fixture(tmp_path)
    report = run(paths)
    row = next(r for r in report['scopes'] if r['truth_level'] == 'evidence' and r['label_id'] == 'genre.mystery')
    assert row['calibration']['contexts'][0]['context'] == ['fixture-model', '1.0', '1.0', 'fixture-cli', 'en']
    assert row['calibration']['all_scored_prediction_contexts_consistent'] is True
    assert report['candidate_partition_assignments'] == 0


def test_mixed_valid_target_contexts_are_not_pooled(tmp_path):
    row = next(r for r in run(fixture(tmp_path, mixed_target=True))['scopes'] if r['truth_level'] == 'evidence' and r['label_id'] == 'genre.mystery')
    assert row['calibration']['known_prediction_with_raw_confidence_n'] == 2
    assert row['calibration']['all_scored_prediction_contexts_consistent'] is False
    assert {c['context'][-1] for c in row['calibration']['contexts']} == {'en', 'fr'}
    scenarios = row['combined_prediction_capacity']['scenarios_by_five_field_context']
    assert sorted(c['calibration_known_scored_n'] for c in scenarios) == [1, 1]


def test_unknown_original_language_and_null_target_kept_as_uncalibratable_metadata(tmp_path):
    row = next(r for r in run(fixture(tmp_path, unknown_language=True))['scopes'] if r['truth_level'] == 'evidence' and r['label_id'] == 'genre.mystery')
    assert row['original_language'] == 'unknown'
    assert row['calibration']['known_prediction_with_raw_confidence_n'] == 2
    assert row['calibration']['known_prediction_with_valid_five_field_context_n'] == 0
    assert row['calibration']['contexts'][0]['context'][-1] is None
    assert not any(c['scope40_prediction_capacity_only'] for c in row['combined_prediction_capacity']['scenarios_by_five_field_context'])


@pytest.mark.parametrize('text', [
    '{"a":1,"a":2}', '{"outer":{"a":1,"a":2}}',
    '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{"x":1e999}', '{"x":-1e999}',
])
def test_strict_json_rejects_duplicates_nonfinite_and_overflow(text):
    with pytest.raises(ValueError): module().strict_json(text)


@pytest.mark.parametrize('key', ['run_manifest', 'split', 'results', 'terminal_proof', 'execution_context_manifest', 'migration_manifest'])
def test_full_diagnostic_rejects_nonfinite_anywhere_in_artifact(tmp_path, key):
    paths = fixture(tmp_path)
    if key == 'results':
        text = paths[key].read_text('utf-8').replace('"raw_confidence": 0.49', '"raw_confidence": 1e999', 1)
    else:
        text = '{"irrelevant_diagnostic_field":1e999,' + paths[key].read_text('utf-8')[1:]
    paths[key].write_text(text, 'utf-8')
    if key != 'terminal_proof': refresh_proof(paths)
    with pytest.raises(ValueError, match='non-finite'): run(paths)


@pytest.mark.parametrize('key,field', [
    ('run_manifest', 'selected'), ('run_manifest', 'completed'), ('run_manifest', 'failed'),
    ('run_manifest', 'pending'), ('terminal_proof', 'exit_code'), ('terminal_proof', 'session_id'),
])
def test_integer_fields_do_not_accept_boolean(tmp_path, key, field):
    paths = fixture(tmp_path)
    mutate(paths, key, lambda r: r.update({field: False}), refresh=key != 'terminal_proof')
    with pytest.raises(ValueError): run(paths)


def test_invalid_cached_result_is_declared_failed_missing_result(tmp_path):
    paths = fixture(tmp_path)
    mutate(paths, 'results', lambda r: r.__setitem__(slice(None), [x for x in r if x['work_id'] != 'p2']))
    def change(m):
        m['completed'] = 6; m['failed'] = 1
        next(j for j in m['jobs'].values() if j['work_id'] == 'p2')['status'] = 'invalid_cached_result'
    mutate(paths, 'run_manifest', change)
    row = next(r for r in run(paths)['scopes'] if r['truth_level'] == 'evidence' and r['label_id'] == 'genre.mystery')
    assert row['fresh_pool']['missing_result_status_counts'] == {'invalid_cached_result': 1}


def test_nonzero_exit_is_preserved_when_complete_terminal_manifest_is_consistent(tmp_path):
    paths = fixture(tmp_path)
    mutate(paths, 'terminal_proof', lambda p: p.update(exit_code=1), refresh=False)
    assert run(paths)['validated_run_selected'] == 7


def test_current_validator_rejects_invalid_evidence_and_keeps_known_null_relevance(tmp_path):
    paths = fixture(tmp_path)
    original = paths['results'].read_bytes()
    run(paths)
    assert paths['results'].read_bytes() == original
    assert json.loads(original)[0]['labels'][0]['relevance'] is None
    mutate(paths, 'results', lambda rows: rows[0]['labels'][0]['evidence'][0].update(quote='Not present in fixture'))
    with pytest.raises(ValueError, match='Quote'): run(paths)


def test_prepared_context_profile_before_after_mismatch_rejected(tmp_path):
    paths = fixture(tmp_path)
    mutate(paths, 'terminal_proof', lambda p: p.update(cli_profile_sha256_after='0'*64), refresh=False)
    with pytest.raises(ValueError, match='profile'): run(paths)


def test_source_mutation_during_diagnostic_rejected(tmp_path, monkeypatch):
    paths = fixture(tmp_path)
    mod = module()
    original = mod.validate_result
    changed = False
    def validator(*args):
        nonlocal changed
        result = original(*args)
        if not changed:
            changed = True
            paths['results'].write_bytes(paths['results'].read_bytes()+b'\n')
        return result
    monkeypatch.setattr(mod, 'validate_result', validator)
    with pytest.raises(ValueError, match='changed during diagnostic'):
        mod.diagnose(paths, expected_frame_n=6, expected_run_n=7, expected_sizes=(1, 2, 1))


@pytest.mark.parametrize('change', [
    lambda c: c['execution_context']['run_config'].update(model='wrong'),
    lambda c: c['execution_context'].update(artifact_count=True),
    lambda c: c['execution_context']['artifact_sha256'].update({'src/recommend_book/models.py': '0'*64}),
    lambda c: c['execution_context']['controls'].update(concurrency=3),
    lambda c: c['input_provenance'].update(input_sha256='0'*64),
    lambda c: c['input_provenance'].update(work_expression_count=True),
])
def test_prepared_profile_layout_and_run_config_are_semantically_bound(tmp_path, change):
    paths = fixture(tmp_path)
    mutate(paths, 'execution_context_manifest', change)
    with pytest.raises(ValueError): run(paths)


def test_terminal_observation_requires_utc(tmp_path):
    paths = fixture(tmp_path)
    mutate(paths, 'terminal_proof', lambda p: p.update(observed_at='2026-10-12T21:00:00+09:00'), refresh=False)
    with pytest.raises(ValueError): run(paths)


@pytest.mark.parametrize('separator', ['/', '\\'])
def test_native_job_path_separators_are_supported(tmp_path, separator):
    paths = fixture(tmp_path)
    mutate(paths, 'run_manifest', lambda m: [j.update(result_path=j['result_path'].replace('/', separator)) for j in m['jobs'].values()])
    assert run(paths)['validated_run_completed'] == 7


@pytest.mark.parametrize('value', ['../results/wrong.json', 'results/../results/wrong.json', '/results/wrong.json', '\\results\\wrong.json', 'C:\\results\\wrong.json', 'results/wrong.json'])
def test_escaping_or_different_job_path_is_rejected(tmp_path, value):
    paths = fixture(tmp_path)
    mutate(paths, 'run_manifest', lambda m: next(iter(m['jobs'].values())).update(result_path=value))
    with pytest.raises(ValueError): run(paths)


def test_durable_result_disagrees_with_result_bundle_rejected(tmp_path):
    paths = fixture(tmp_path)
    manifest = json.loads(paths['run_manifest'].read_bytes())
    durable = paths['run_manifest'].parent/next(iter(manifest['jobs'].values()))['result_path']
    value = json.loads(durable.read_bytes())
    value['labels'][0]['raw_confidence'] = .25
    durable.write_text(json.dumps(value), 'utf-8')
    with pytest.raises(ValueError): run(paths)


def rebind_group_fixture(paths, left, right):
    identities = json.loads(paths['identities'].read_bytes())
    identities[left] = identities[right]
    paths['identities'].write_text(json.dumps(identities), 'utf-8')
    frame = json.loads(paths['frame'].read_bytes())
    split = json.loads(paths['split'].read_bytes())
    split.update(group_identities=identities, group_ids=connected_groups(frame, identities),
                 group_identities_sha256=hashlib.sha256(canonical_json(identities).encode()).hexdigest(),
                 input_sha256=hashlib.sha256(canonical_json({'inputs': sorted(frame, key=lambda r: r['work_id']), 'group_identities': identities}).encode()).hexdigest())
    split.pop('manifest_sha256')
    split['manifest_sha256'] = hashlib.sha256(json.dumps(split, ensure_ascii=False, indent=2).encode()).hexdigest()
    paths['split'].write_text(json.dumps(split), 'utf-8')
    refresh_proof(paths)


def test_valid_rehashed_authority_union_still_rejects_dev_calibration_leak(tmp_path):
    paths = fixture(tmp_path)
    rebind_group_fixture(paths, 'c1', 'd')
    with pytest.raises(ValueError, match='partition leakage'): run(paths)


def test_unselected_holdout_group_neighbor_excluded_from_fresh_pool(tmp_path):
    paths = fixture(tmp_path)
    rebind_group_fixture(paths, 'p1', 'h')
    report = run(paths)
    assert report['fresh_pool_works'] == 1
    assert report['unselected_excluded_due_to_fixed400_group'] == 1
    assert [w for g in report['group_pool'] for w in g['member_work_ids']] == ['p2']


def test_known_prediction_null_confidence_rejected_by_current_contract(tmp_path):
    paths = fixture(tmp_path)
    mutate(paths, 'results', lambda rows: next(r for r in rows if r['work_id'] == 'c1')['labels'][0].update(raw_confidence=None))
    with pytest.raises(ValueError, match='raw confidence'): run(paths)


def test_actual_jsonl_input_result_shape_supported(tmp_path):
    paths = fixture(tmp_path)
    for key in ('frame', 'run_inputs', 'review_inputs', 'results'):
        values = json.loads(paths[key].read_bytes())
        paths[key].write_text(''.join(canonical_json(v)+'\n' for v in values), 'utf-8')
    mutate(paths, 'frame_manifest', lambda m: m.update(work_inputs_sha256=sha(paths['frame'])))
    mutate(paths, 'execution_context_manifest', lambda m: m['input_provenance'].update(input_sha256=sha(paths['run_inputs'])))
    assert run(paths)['validated_run_selected'] == 7


def test_equivalent_integer_float_timeout_layout_supported(tmp_path):
    paths = fixture(tmp_path)
    mutate(paths, 'run_manifest', lambda m: m['limits'].update(timeout=900.0))
    assert run(paths)['validated_run_completed'] == 7
