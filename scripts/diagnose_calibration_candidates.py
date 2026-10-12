"""Offline prediction capacity diagnostic, never human calibration or accuracy."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from numbers import Real
from pathlib import Path, PureWindowsPath

from recommend_book.classification.runner import build_run_config
from recommend_book.classification.validation import ROOT, input_hash, target_language, validate_result
from recommend_book.evaluation.splits import connected_groups
from recommend_book.jobs import canonical_json, job_key
from recommend_book.models import WorkInput

FIELDS = ('model', 'prompt_version', 'taxonomy_version', 'cli_version', 'target_language')
ARTIFACTS = ('frame', 'frame_manifest', 'identities', 'split', 'review_inputs', 'taxonomy',
             'run_inputs', 'run_manifest', 'results', 'execution_context_manifest',
             'migration_manifest', 'cli_profile')
MIN_SCOPE, MIN_BIN = 40, 20
EXECUTION_ARTIFACTS = ('config/sources.json', 'config/taxonomy.json', 'schemas/classification.schema.json',
                       'src/recommend_book/classification/prompt.py', 'src/recommend_book/classification/validation.py',
                       'src/recommend_book/classification/runner.py', 'src/recommend_book/models.py',
                       'src/recommend_book/jobs.py', 'pyproject.toml', 'uv.lock')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def strict_json(payload):
    """Reject duplicate keys and every non-finite numeric spelling, including overflow."""
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f'duplicate JSON key: {key}')
            result[key] = value
        return result

    def floating(value):
        number = float(value)
        require(math.isfinite(number), 'non-finite JSON number')
        return number

    def constant(value):
        raise ValueError(f'non-finite JSON constant: {value}')

    return json.loads(payload, object_pairs_hook=pairs, parse_float=floating, parse_constant=constant)


def rows(payload):
    text = payload.decode('utf-8-sig').strip()
    values = strict_json(text) if text.startswith('[') else [strict_json(line) for line in text.splitlines() if line.strip()]
    require(isinstance(values, list) and all(isinstance(v, dict) for v in values), 'expected object rows')
    return values


def unique(values, name):
    result = {}
    for row in values:
        key = row.get('work_id')
        require(isinstance(key, str) and key and key not in result, f'duplicate/invalid {name} work identity')
        result[key] = row
    return result


def durable_path(root, value, key):
    require(isinstance(value, str) and value, 'completed job result path missing')
    windows = PureWindowsPath(value)
    parts = value.replace('\\', '/').split('/')
    require(not windows.drive and not windows.root and all(p not in ('', '.', '..') for p in parts), 'unsafe job result path')
    root = root.resolve()
    path = root.joinpath(*parts).resolve()
    expected = (root/'results'/(key+'.json')).resolve()
    require(path.is_relative_to(root) and path == expected and parts == ['results', key+'.json'], 'job path refers to different result')
    return path


def kind(work, level):
    evidence = [e for e in work.get('evidence', []) if level == 'work' or e.get('llm_input_allowed') is True]
    return '+'.join(sorted({e['kind'] for e in evidence})) or 'unknown'


def empty_counts():
    return {'metadata_upper_n': 0, 'returned_result_n': 0, 'missing_result_n': 0,
            'known_prediction_n': 0, 'known_prediction_with_raw_confidence_n': 0,
            'known_prediction_with_valid_five_field_context_n': 0,
            'known_prediction_missing_confidence_n': 0, 'unknown_prediction_n': 0,
            'omitted_label_n': 0, 'bins': {'low': 0, 'high': 0},
            'missing_result_status_counts': {}, 'contexts': []}


def prediction_counts(ids, label_id, predictions, states):
    out = empty_counts()
    contexts, missing = {}, Counter()
    out['metadata_upper_n'] = len(ids)
    for wid in ids:
        prediction = predictions.get(wid)
        if prediction is None:
            out['missing_result_n'] += 1
            missing[states[wid]] += 1
            continue
        out['returned_result_n'] += 1
        label = next((l for l in prediction['labels'] if l['label_id'] == label_id), None)
        if label is None:
            out['omitted_label_n'] += 1
            continue
        if label['decision'] == 'unknown':
            out['unknown_prediction_n'] += 1
            continue
        out['known_prediction_n'] += 1
        confidence = label['raw_confidence']
        if confidence is None:
            out['known_prediction_missing_confidence_n'] += 1
            continue
        out['known_prediction_with_raw_confidence_n'] += 1
        band = 'low' if confidence < .5 else 'high'
        out['bins'][band] += 1
        context = tuple(prediction[f] for f in FIELDS)
        valid_context = all(isinstance(v, str) and v.strip() for v in context)
        if valid_context:
            out['known_prediction_with_valid_five_field_context_n'] += 1
        item = contexts.setdefault(context, {'context': list(context), 'valid_five_field_context': valid_context, 'n': 0, 'bins': {'low': 0, 'high': 0}})
        item['n'] += 1
        item['bins'][band] += 1
    out['contexts'] = [contexts[c] for c in sorted(contexts, key=lambda c: canonical_json(list(c)))]
    out['all_scored_prediction_contexts_consistent'] = len(contexts) == 1 and all(c['valid_five_field_context'] for c in contexts.values())
    out['missing_result_status_counts'] = dict(sorted(missing.items()))
    require(out['metadata_upper_n'] == out['returned_result_n'] + out['missing_result_n'], 'scope denominator mismatch')
    return out


def capacity(calibration, pool, scope_known):
    contexts = {tuple(c['context']) for side in (calibration, pool) for c in side['contexts']}
    scenarios = []
    for context in sorted(contexts, key=lambda c: canonical_json(list(c))):
        existing = next((c for c in calibration['contexts'] if tuple(c['context']) == context), {'n': 0, 'bins': {'low': 0, 'high': 0}})
        available = next((c for c in pool['contexts'] if tuple(c['context']) == context), {'n': 0, 'bins': {'low': 0, 'high': 0}})
        combined = existing['n'] + available['n']
        bins = {b: existing['bins'][b] + available['bins'][b] for b in ('low', 'high')}
        valid_context = all(isinstance(v, str) and v.strip() for v in context)
        compatible = not calibration['contexts'] or (calibration['all_scored_prediction_contexts_consistent'] and calibration['contexts'][0]['context'] == list(context))
        scenarios.append({'context': list(context), 'valid_five_field_context': valid_context,
                          'compatible_with_all_scored_existing_calibration_contexts': compatible,
                          'calibration_known_scored_n': existing['n'],
                          'fresh_pool_known_scored_n': available['n'], 'combined_known_scored_upper_n': combined,
                          'combined_bins_upper_n': bins,
                          'scope40_prediction_capacity_only': scope_known and valid_context and combined >= MIN_SCOPE,
                          'one_or_more_bin20_prediction_capacity_only': scope_known and valid_context and combined >= MIN_SCOPE and any(n >= MIN_BIN for n in bins.values()),
                          'both_bin20_prediction_capacity_only': scope_known and valid_context and combined >= MIN_SCOPE and all(n >= MIN_BIN for n in bins.values()),
                          'existing_calibration_plus_pool_one_bin_capacity_only': scope_known and valid_context and compatible and combined >= MIN_SCOPE and any(n >= MIN_BIN for n in bins.values()),
                          'minimum_additional_known_scored_n_to40': max(0, MIN_SCOPE-existing['n']),
                          'additional_low_bin_n_to20': max(0, MIN_BIN-existing['bins']['low']),
                          'additional_high_bin_n_to20': max(0, MIN_BIN-existing['bins']['high']),
                          'candidate_partition_assignment': None})
    return {'minimum_scope_n': MIN_SCOPE, 'minimum_bin_n': MIN_BIN, 'human_truth_not_measured': True,
            'scenarios_by_five_field_context': scenarios,
            'existing_calibration_all_scored_contexts_consistent': calibration['all_scored_prediction_contexts_consistent'],
            'mixed_contexts_not_pooled': True}


def diagnose(paths, *, expected_frame_n=2000, expected_run_n=2020, expected_sizes=(150, 100, 150)):
    """Read only after an external process-exit attestation; retain failed denominator."""
    require(set(paths) == set(ARTIFACTS) | {'terminal_proof'}, 'missing/extra diagnostic artifact paths')
    paths = {k: Path(v) for k, v in paths.items()}
    proof_bytes = paths['terminal_proof'].read_bytes()
    proof = strict_json(proof_bytes)
    require(isinstance(proof, dict), 'terminal proof must be an object')
    require(proof.get('schema_version') == 'classification-terminal-proof-v1', 'terminal proof schema mismatch')
    require(proof.get('process_exit_observed') is True, 'process exit was not observed')
    require(type(proof.get('exit_code')) is int, 'terminal exit_code must be a non-bool int')
    require(isinstance(proof.get('session_id'), (str, int)) and not isinstance(proof.get('session_id'), bool) and str(proof['session_id']).strip(), 'terminal session_id missing')
    try:
        observed = datetime.fromisoformat(proof['observed_at'])
        require(observed.utcoffset() is not None and observed.utcoffset().total_seconds() == 0, 'terminal observed_at needs UTC')
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('invalid terminal observed_at') from exc
    hashes = proof.get('artifact_sha256')
    require(isinstance(hashes, dict) and all(k in hashes for k in ARTIFACTS), 'terminal artifact SHA mapping incomplete')
    # Check pending/stopped before reading prediction bytes. A manifest emitted
    # in finally does not by itself establish that the process has terminated.
    manifest_bytes = paths['run_manifest'].read_bytes()
    require(sha(manifest_bytes) == hashes['run_manifest'] == proof.get('manifest_sha256'), 'run manifest SHA mismatch')
    manifest = strict_json(manifest_bytes)
    require(isinstance(manifest, dict), 'run manifest must be an object')
    require(manifest.get('run_id') == proof.get('run_id') and isinstance(manifest.get('run_id'), str) and manifest['run_id'], 'terminal run identity mismatch')
    require(manifest.get('stopped') is False and not manifest.get('stop_reasons'), 'stopped run rejected')
    require(manifest.get('pending') == 0 and type(manifest.get('pending')) is int, 'partial live run rejected')
    for field in ('selected', 'completed', 'failed'):
        require(type(manifest.get(field)) is int and manifest[field] >= 0, f'invalid manifest {field}')
    require(manifest['selected'] == expected_run_n and manifest['completed'] + manifest['failed'] == manifest['selected'], 'terminal selected/completed/failed denominator mismatch')
    payloads = {'run_manifest': manifest_bytes}
    for key in ARTIFACTS:
        if key not in payloads:
            payloads[key] = paths[key].read_bytes()
        require(sha(payloads[key]) == hashes[key], f'{key} SHA mismatch')
    for artifact, field in [('results', 'results_sha256'), ('run_inputs', 'ordered_inputs_sha256'),
                            ('execution_context_manifest', 'execution_context_manifest_sha256'),
                            ('migration_manifest', 'migration_manifest_sha256')]:
        require(hashes[artifact] == proof.get(field), f'{field} attestation mismatch')
    require(hashes['cli_profile'] == hashes['execution_context_manifest'] == proof.get('cli_profile_sha256_before') == proof.get('cli_profile_sha256_after'), 'prepared CLI profile/context before-after SHA mismatch')
    frame = unique(rows(payloads['frame']), 'frame')
    run = unique(rows(payloads['run_inputs']), 'selected run')
    review = unique(rows(payloads['review_inputs']), 'review')
    require(len(frame) == expected_frame_n and len(run) == expected_run_n, 'frame/selected cardinality mismatch')
    require(set(frame) <= set(run) and all(frame[w] == run[w] for w in frame), 'main frozen inputs differ from selected run inputs')
    frame_manifest = strict_json(payloads['frame_manifest'])
    require(isinstance(frame_manifest, dict), 'frozen frame manifest must be object')
    require(type(frame_manifest.get('work_count')) is int and frame_manifest.get('work_count') == expected_frame_n and frame_manifest.get('work_inputs_sha256') == hashes['frame'], 'frozen frame manifest identity/hash mismatch')
    identities = strict_json(payloads['identities'])
    require(isinstance(identities, dict) and set(identities) == set(frame), 'frozen authority identity coverage mismatch')
    split = strict_json(payloads['split'])
    require(isinstance(split, dict), 'split must be object')
    without_sha = {k: v for k, v in split.items() if k != 'manifest_sha256'}
    require(sha(json.dumps(without_sha, ensure_ascii=False, indent=2).encode()) == split.get('manifest_sha256'), 'split manifest self SHA mismatch')
    require(split.get('sizes') == list(expected_sizes) and all(type(n) is int for n in split['sizes']) and set(split.get('partitions', {})) == {'dev', 'calibration', 'holdout'}, 'frozen partition sizes/names mismatch')
    partitions = {}
    for name, expected_size in zip(('dev', 'calibration', 'holdout'), expected_sizes):
        members = split['partitions'][name]
        require(len(members) == expected_size, 'frozen partition cardinality mismatch')
        for wid in members:
            require(wid in frame and wid not in partitions, 'frozen partition duplicate/unknown identity')
            partitions[wid] = name
    require(set(review) == set(partitions) and all(review[w] == frame[w] for w in review), 'review/frozen split input mismatch')
    require(set(split.get('unselected_work_ids', [])) == set(frame) - set(partitions) and len(split['unselected_work_ids']) == len(frame)-len(partitions), 'unselected frame identity mismatch')
    require(split.get('group_identities') == identities and split.get('group_identities_sha256') == sha(canonical_json(identities).encode()), 'split authority identity SHA mismatch')
    expected_input = {'inputs': [frame[w] for w in sorted(frame)], 'group_identities': identities}
    require(split.get('input_sha256') == sha(canonical_json(expected_input).encode()), 'split full input/identity SHA mismatch')
    groups = connected_groups(list(frame.values()), identities)
    require(split.get('group_ids') == groups, 'connected group union mismatch')
    group_partitions = defaultdict(set)
    for wid, partition in partitions.items():
        group_partitions[groups[wid]].add(partition)
    require(all(len(v) <= 1 for v in group_partitions.values()), 'author/series/authority group partition leakage')
    used_groups = set(group_partitions)
    fresh = sorted(w for w in frame if w not in partitions and groups[w] not in used_groups)
    fresh_groups = defaultdict(list)
    for wid in fresh:
        fresh_groups[groups[wid]].append(wid)
    taxonomy = strict_json(payloads['taxonomy'])
    require(isinstance(taxonomy, dict) and isinstance(taxonomy.get('labels'), list), 'taxonomy must be object with labels')
    labels = [l['label_id'] for l in taxonomy['labels']]
    require(labels and len(labels) == len(set(labels)), 'taxonomy label identity invalid')
    config = manifest['config']
    require(isinstance(config, dict), 'run config must be object')
    require(config.get('taxonomy_version') == taxonomy.get('version') and config.get('taxonomy_hash') == sha(canonical_json(taxonomy).encode()), 'run taxonomy context/hash mismatch')
    require(all(isinstance(config.get(f), str) and config[f].strip() for f in FIELDS[:-1]), 'four-field run context incomplete')
    local_artifacts = {'validation_hash': ROOT/'src/recommend_book/classification/validation.py',
                       'prompt_hash': ROOT/'src/recommend_book/classification/prompt.py',
                       'schema_hash': ROOT/'schemas/classification.schema.json'}
    local_hashes = {field: sha(path.read_bytes()) for field, path in local_artifacts.items()}
    for field, digest in local_hashes.items():
        require(config.get(field) == digest, f'local execution artifact {field} mismatch')
    prepared = strict_json(payloads['execution_context_manifest'])
    require(isinstance(prepared, dict) and prepared.get('version') == 'classification-execution-freeze-v1', 'prepared context format mismatch')
    scope = prepared.get('execution_context')
    provenance = prepared.get('input_provenance')
    require(isinstance(scope, dict) and isinstance(provenance, dict), 'prepared context/provenance missing')
    require(isinstance(scope.get('run_config'), dict) and canonical_json(scope['run_config']) == canonical_json(config), 'prepared run config mismatch')
    require(type(config.get('batch_size')) is int and 1 <= config['batch_size'] <= 10, 'run batch size invalid')
    require(config == build_run_config(config['model'], config['cli_version'], taxonomy, config['batch_size']), 'current code run config mismatch')
    require(type(scope.get('artifact_count')) is int and scope['artifact_count'] == len(EXECUTION_ARTIFACTS), 'prepared artifact count mismatch')
    require(isinstance(scope.get('artifact_sha256'), dict) and set(scope['artifact_sha256']) == set(EXECUTION_ARTIFACTS), 'prepared artifact inventory mismatch')
    require(isinstance(scope.get('code_root'), str) and Path(scope['code_root']).resolve() == ROOT.resolve(), 'prepared code root mismatch')
    prepared_hashes = {name: sha((ROOT/name).read_bytes()) for name in EXECUTION_ARTIFACTS}
    require(scope['artifact_sha256'] == prepared_hashes, 'prepared/current execution artifact SHA mismatch')
    require(prepared_hashes['config/taxonomy.json'] == hashes['taxonomy'], 'prepared taxonomy raw SHA mismatch')
    require(provenance.get('input_sha256') == hashes['run_inputs'] and type(provenance.get('work_expression_count')) is int and provenance['work_expression_count'] == len(run), 'prepared input provenance mismatch')
    controls = scope.get('controls')
    expected_controls = {'batch_size': config['batch_size'], 'concurrency': manifest.get('initial_concurrency'),
                         **{k: manifest.get('limits', {}).get(k) for k in ('timeout', 'max_run_seconds', 'max_observed_tokens', 'stop_on_rate_limit')}}
    require(isinstance(controls, dict) and set(controls) == set(expected_controls), 'prepared/runtime controls missing')
    for settings in (controls, expected_controls):
        for field in ('batch_size', 'concurrency'):
            require(type(settings[field]) is int and settings[field] > 0, 'control integer must not be bool')
        require(type(settings['stop_on_rate_limit']) is bool, 'control rate-limit flag must be bool')
        for field in ('timeout', 'max_run_seconds'):
            value = settings[field]
            require((field != 'timeout' and value is None) or (isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) and (value > 0 if field == 'timeout' else value >= 0)), 'control time invalid')
        value = settings['max_observed_tokens']
        require(value is None or type(value) is int and value >= 0, 'control token budget invalid')
    require(controls == expected_controls, 'prepared/runtime controls mismatch')
    require(isinstance(strict_json(payloads['migration_manifest']), dict), 'migration manifest must be object')
    require(strict_json(payloads['cli_profile']) == prepared, 'prepared CLI profile layout mismatch')
    jobs = manifest.get('jobs')
    require(isinstance(jobs, dict) and len(jobs) == len(run), 'selected job coverage mismatch')
    states, expected_keys, durable = {}, set(), {}
    for wid, record in run.items():
        work = WorkInput.model_validate(record)
        digest = input_hash(work)
        key = job_key(digest, config)
        expected_keys.add(key)
        job = jobs.get(key, {})
        require(job.get('status') in ('completed', 'held', 'invalid_cached_result'), 'job is not terminal')
        require(job.get('work_id') == wid and job.get('expression_id') == work.expression_id and job.get('input_hash') == digest and job.get('config') == config, 'job input identity/context mismatch')
        if 'key' in job:
            require(job['key'] == key, 'job key mismatch')
        states[wid] = job['status']
        if job['status'] == 'completed':
            durable[wid] = durable_path(paths['run_manifest'].parent, job.get('result_path'), key)
    require(set(jobs) == expected_keys, 'selected job-key set mismatch')
    counts = Counter(states.values())
    require(counts['completed'] == manifest['completed'] and counts['held'] + counts['invalid_cached_result'] == manifest['failed'], 'terminal job status counts mismatch')
    if 'held' in manifest:
        require(type(manifest['held']) is int and manifest['held'] == counts['held'], 'declared held count mismatch')
    predictions = unique(rows(payloads['results']), 'result')
    require(set(predictions) == {w for w, status in states.items() if status == 'completed'}, 'completed/result identity coverage mismatch; held must have no result')
    for wid, prediction in predictions.items():
        work = WorkInput.model_validate(run[wid])
        require(prediction.get('expression_id') == work.expression_id and prediction.get('input_hash') == input_hash(work), 'result input/expression identity mismatch')
        require(all(prediction.get(f) == config[f] for f in FIELDS[:-1]), 'result execution context mismatch')
        expected_language = target_language(work)
        require((expected_language is None or isinstance(expected_language, str) and expected_language.strip()) and
                'target_language' in prediction and prediction['target_language'] == expected_language, 'result target-language context missing/mismatch')
        result_labels = prediction.get('labels')
        require(isinstance(result_labels, list), 'result labels missing')
        seen = set()
        for label in result_labels:
            lid = label.get('label_id')
            require(lid in labels and lid not in seen, 'result duplicate/unknown label identity')
            seen.add(lid)
            require(label.get('decision') in ('present', 'absent', 'unknown'), 'result invalid decision')
            require('raw_confidence' in label, 'raw confidence missing; legacy confidence is not substituted')
            value = label['raw_confidence']
            require(value is None or (isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1), 'invalid raw confidence')
        validate_result(prediction, work, taxonomy)
        artifact = 'durable_result:'+job_key(input_hash(work), config)
        paths[artifact] = durable[wid]
        payloads[artifact] = durable[wid].read_bytes()
        stored = strict_json(payloads[artifact])
        require(isinstance(stored, dict) and stored == prediction, 'durable job result/bundle mismatch')
    scopes = []
    for level in ('evidence', 'work'):
        cells = defaultdict(lambda: {'calibration': [], 'fresh_pool': []})
        for side, members in [('calibration', split['partitions']['calibration']), ('fresh_pool', fresh)]:
            for wid in members:
                cell = (frame[wid].get('original_language') or 'unknown', kind(frame[wid], level))
                cells[cell][side].append(wid)
        for (language, evidence_kind), members in sorted(cells.items()):
            for lid in sorted(labels):
                cal = prediction_counts(members['calibration'], lid, predictions, states)
                pool = prediction_counts(members['fresh_pool'], lid, predictions, states)
                scopes.append({'truth_level': level, 'label_id': lid, 'original_language': language,
                               'evidence_kind': evidence_kind, 'calibration': cal, 'fresh_pool': pool,
                               'fresh_pool_connected_groups': len({groups[w] for w in members['fresh_pool']}),
                               'combined_prediction_capacity': capacity(cal, pool, language != 'unknown' and evidence_kind != 'unknown')})
    source_hashes = {k: sha(b) for k, b in payloads.items()}
    source_hashes['terminal_proof'] = sha(proof_bytes)
    require(all(sha(paths[k].read_bytes()) == digest for k, digest in source_hashes.items()), 'source SHA changed during diagnostic; possible live mutation')
    require(local_hashes == {field: sha(path.read_bytes()) for field, path in local_artifacts.items()}, 'local validation artifact SHA changed during diagnostic')
    require(prepared_hashes == {name: sha((ROOT/name).read_bytes()) for name in EXECUTION_ARTIFACTS}, 'prepared/current artifacts changed during diagnostic')
    return {'schema_version': 'calibration-candidate-diagnostic-v1', 'run_id': manifest['run_id'],
            'diagnostic_frame_works': len(frame), 'validated_run_selected': len(run),
            'validated_run_completed': manifest['completed'], 'validated_run_failed': manifest['failed'],
            'validated_run_status_counts': dict(counts), 'excluded_extra_run_works': len(run)-len(frame),
            'diagnostic_scope': 'main frozen frame only; extra canonical classics validated but not counted',
            'fixed_partition_sizes': dict(Counter(partitions.values())),
            'fresh_pool_works': len(fresh), 'fresh_pool_connected_groups': len(fresh_groups),
            'unselected_excluded_due_to_fixed400_group': len(frame)-len(partitions)-len(fresh),
            'source_sha256': source_hashes, 'local_execution_artifact_sha256': local_hashes,
            'prepared_current_artifact_sha256': prepared_hashes,
            'terminal_proof_observed_at': proof['observed_at'],
            'thresholds': {'minimum_scope_n': MIN_SCOPE, 'minimum_bin_n': MIN_BIN, 'low': '[0,.5)', 'high': '[.5,1]'},
            'human_truth_used': False, 'human_accuracy_estimated': False, 'calibration_fitted': False,
            'candidate_partition_assignments': 0, 'annotations_generated': 0,
            'scopes': scopes,
            'group_pool': [{'group_id': g, 'member_work_ids': sorted(members), 'candidate_partition_assignment': None,
                            'metadata_works': len(members), 'missing_result_n': sum(w not in predictions for w in members)} for g, members in sorted(fresh_groups.items())],
            'limitations': ['Known predictions and confidence occupancy are upper bounds conditional on future human truth being decidable.',
                            'No label accuracy, precision, calibration success, bin probabilities or human gold are inferred.',
                            'Context scenarios are never pooled; actual calibrator may skip a mixed context scope.',
                            'Whole groups must be retained in any future design; scope counts do not assign candidates.',
                            'External terminal proof records an observed process exit; this tool cannot independently witness that historical event.',
                            'Migration is byte-bound; prepared run config/controls/current artifacts are checked, but no identity adjudication is inferred.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (*ARTIFACTS, 'terminal_proof'):
        parser.add_argument('--'+name.replace('_', '-'), type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = {name: getattr(args, name) for name in (*ARTIFACTS, 'terminal_proof')}
    require(not args.output.exists() or not any(args.output.iterdir()), 'output must be new or empty')
    report = diagnose(paths)
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output/'diagnostic.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    (args.output/'manifest.json').write_text(json.dumps({'schema_version': report['schema_version'], 'run_id': report['run_id'], 'source_sha256': report['source_sha256'], 'diagnostic_sha256': sha(output.read_bytes()), 'script_sha256': sha(Path(__file__).read_bytes()), 'human_truth_used': False, 'calibration_fitted': False, 'candidate_partition_assignments': 0}, indent=2) + '\n', 'utf-8')
    print(json.dumps({'run_id': report['run_id'], 'selected': report['validated_run_selected'], 'completed': report['validated_run_completed'], 'failed': report['validated_run_failed'], 'main_frame': report['diagnostic_frame_works'], 'fresh_pool': report['fresh_pool_works'], 'scope_rows': len(report['scopes']), 'calibration_fitted': False}))


if __name__ == '__main__':
    main()
