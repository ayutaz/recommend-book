"""Bounded offline integration of content updates and immutable fixed shards.

All sources, licensing text and model output here are synthetic. Only the
worker/coordinator processes, production guards, runner, jobs and DB import are
real. This module is a verification harness, never a model execution entrypoint.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

from recommend_book.classification import native_attempt
from recommend_book.classification.runner import build_run_config, run_classification
from recommend_book.classification.validation import ROOT, input_hash, load_taxonomy, target_language, validate_result
from recommend_book.database import DatasetDB
from recommend_book.dataset_update_verification import _ledger, _save, _sha
from recommend_book.jobs import atomic_json, canonical_json, job_key
from recommend_book.models import Evidence, Identifier, SourceRecord, Title, WorkInput
from recommend_book import next_database
from recommend_book.next_database import build_next_database, get_next_inputs, import_next_classifications, load_next_world_database
from recommend_book.recommendation.engine import rank_candidates
from recommend_book.recommendation.pipeline import build_profile, render_recommendations
from recommend_book.usage_guard import UsageDenied, UsageGuard, build_input_entry, model_hash, source_binding

MODEL = 'gpt-6.1-sol'
VERSION = 'synthetic-cli-fixture-1'
SPEC = 'guarded-v3-baseline'


def _read(path):
    return json.loads(Path(path).read_bytes())


def _record(root, qid, description, revision):
    raw = root / 'raw' / (qid + '-' + revision + '.json')
    title = 'Synthetic book ' + qid
    url = 'https://www.wikidata.org/wiki/' + qid
    _save(raw, {'synthetic_fixture': True, 'entities': {qid: {'id': qid,
        'labels': {'en': {'value': title}}, 'descriptions': {'en': {'value': description}}, 'claims': {}}}})
    return SourceRecord(source='wikidata', source_id=qid, source_url=url,
        retrieved_at='2026-10-15T00:00:00Z', provider_id='wikidata', record_kind='work',
        titles=[Title(value=title, language='en')], work_kind='novel',
        identifiers=[Identifier(source='wikidata', value=qid, scope='work')],
        raw_path=str(raw), raw_sha256=_sha(raw), rights_policy_id='wikidata-cc0',
        evidence=[Evidence(evidence_id='wd:' + qid + ':description', kind='description', text=description,
            source_url=url, rights_policy_id='wikidata-cc0', llm_input_allowed=True, locator='descriptions.en')])


def _snapshot(root, source, official):
    snapshot = root / 'snapshot'
    build_next_database(source_database=source, output_dir=snapshot)
    ledger, value = _ledger(snapshot, official)
    works = get_next_inputs(snapshot / 'dataset.sqlite')['classification']
    for work in works:
        value['inputs'][model_hash(work)] = build_input_entry(work,
            {ref: source_binding(snapshot / 'dataset.sqlite', ref) for ref in work.source_record_ids})
    config = build_run_config(MODEL, VERSION, load_taxonomy(), 1, execution_spec=SPEC)
    verifier = Path(native_attempt.__file__).resolve()
    value['execution_recipes'] = {model_hash(config): {'verifier_path': str(verifier), 'verifier_sha256': _sha(verifier)}}
    recipe = Path(next_database.__file__).resolve()
    value['feature_recipes'] = {'work-present-relevance-max-v1': {'path': str(recipe), 'sha256': _sha(recipe)}}
    _save(ledger, value)
    return snapshot, ledger, works


def _prepare(group, snapshot, ledger, works):
    """Fix exact single/bounded input and code context before any child starts."""
    inputs = group / 'inputs' / 'ordered-inputs.jsonl'
    inputs.parent.mkdir(parents=True)
    inputs.write_text(''.join(canonical_json(w.model_dump(mode='json')) + '\n' for w in works), encoding='utf-8')
    _save(inputs.with_name('manifest.json'), {'ordered_inputs_sha256': _sha(inputs), 'synthetic_fixture': True})
    spec = importlib.util.spec_from_file_location('content_fixture_freeze', ROOT / 'scripts/freeze_classification_execution.py')
    freezer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(freezer)
    context_dir = group / 'context'
    context = freezer.freeze_execution(inputs, context_dir, model=MODEL, cli_version=VERSION, concurrency=1)
    # The legacy preparation helper has no execution_spec option. Complete the
    # supplied fixture context BEFORE execution using the actual guarded config;
    # the importer independently reproduces every value and frozen artifact.
    config = build_run_config(MODEL, VERSION, load_taxonomy(), 1, execution_spec=SPEC)
    context['execution_context']['run_config'] = config
    context['synthetic_fixture'] = True
    _save(context_dir / 'manifest.json', context)
    binary = group / 'synthetic-cli.exe'
    binary.write_bytes(b'inert fixture artifact; never executed')
    _save(group / 'migration' / 'manifest.json', {'version': 'synthetic-no-rebind', 'llm_started': False})
    fixed = [inputs, inputs.with_name('manifest.json'), snapshot / 'dataset.sqlite',
        context_dir / 'manifest.json', binary, group / 'migration/manifest.json']
    _save(group / 'prepared.json', {'snapshot': str(snapshot), 'ledger': str(ledger),
        'inputs': str(inputs), 'context': str(context_dir / 'manifest.json'), 'binary': str(binary),
        'config': config, 'fixed_sha256': {str(path): _sha(path) for path in fixed}})


def _check_prepared(group):
    prepared = _read(Path(group) / 'prepared.json')
    if any(_sha(path) != digest for path, digest in prepared['fixed_sha256'].items()):
        raise ValueError('Fixed shard input/source/context changed; use a new plan')
    return prepared


def _result(work):
    """Deterministic fixture response, deliberately not semantic model evidence."""
    evidence = work.evidence[0]
    present = 'detective' in evidence.text
    return {'work_id': work.work_id, 'expression_id': work.expression_id, 'target_language': target_language(work),
        'taxonomy_version': '1.0', 'prompt_version': '1.0', 'model': MODEL, 'cli_version': VERSION,
        'input_hash': input_hash(work), 'labels': [{'label_id': 'genre.mystery', 'axis': 'genre',
            'decision': 'present' if present else 'absent', 'scope': 'work', 'expression_id': None,
            'relevance': None, 'raw_confidence': 0.8, 'calibrated_confidence': None,
            'calibration_status': 'uncalibrated', 'calibration_version': None,
            'evidence': [{'evidence_id': evidence.evidence_id, 'locator': evidence.locator,
                'quote': evidence.text, 'summary': None, 'directness': 'direct'}],
            'missing_reason': None, 'spoiler': 'premise'}],
        'missing_axes': [{'axis': axis, 'reason': 'Synthetic fixture has no supporting evidence', 'decision': 'unknown'}
            for axis in ['theme', 'structure', 'protagonist', 'style', 'atmosphere']],
        'conflicts': [], 'needs_human_review': True}


def run_fixture_worker(group, *, run_directory=None):
    """A real worker process, with only the CLI subprocess response mocked."""
    group = Path(group)
    prepared = _check_prepared(group)
    run_directory = Path(run_directory).resolve() if run_directory else group / 'run'
    if not run_directory.resolve().is_relative_to(group.resolve()):
        raise ValueError('Fixture run must stay inside its prepared group')
    works = [WorkInput.model_validate(json.loads(line)) for line in Path(prepared['inputs']).read_text(encoding='utf-8').splitlines()]

    def response(argv, **kwargs):
        if '--version' in argv:
            return subprocess.CompletedProcess(argv, 0, (VERSION + '\n').encode(), b'')
        packet = json.loads(kwargs['input'].decode().split('BEGIN_LOCAL_DATA_JSON\n', 1)[1].rsplit('\nEND_LOCAL_DATA_JSON', 1)[0])
        work = next(w for w in works if w.work_id == packet['work']['work_id'])
        value = _result(work)
        Path(argv[argv.index('-o') + 1]).write_text(canonical_json(value), encoding='utf-8')
        stdout = canonical_json({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': canonical_json(value)}}) + '\n'
        stdout += canonical_json({'type': 'turn.completed', 'usage': {'input_tokens': 12}}) + '\n'
        return subprocess.CompletedProcess(argv, 0, stdout.encode(), b'synthetic response; no native model executed\n')

    with patch('recommend_book.classification.runner.subprocess.run', response):
        report = run_classification(works, run_directory, executable=prepared['binary'],
            usage_ledger=prepared['ledger'], concurrency=1, expected_run_config=prepared['config'])
    if report['completed'] != len(works):
        raise ValueError('Fixture classification held: ' + canonical_json(report))


def _child(mode, path, *, pause=False, pause_before_adoption=False):
    command = [sys.executable, str(ROOT / 'scripts/verify_content_updates.py'), mode, str(path)]
    if pause:
        command.append('--pause-after-first')
    if pause_before_adoption:
        command.extend(['--pause-before-adoption-of', '1'])
    return command


def _execute_and_adopt(group, *, attempt_dir=None, pause_before_adoption=None):
    """Observe real worker exit, then import its original unchanged native proof."""
    group = Path(group)
    attempt_dir = Path(attempt_dir) if attempt_dir else group
    prepared = _check_prepared(group)
    profile_before = _sha(prepared['context'])
    run = attempt_dir / 'run'
    if run.exists():
        raise ValueError('An unadopted attempt must never resume in its old run namespace')
    command = [*_child('--fixture-worker', group), '--run-directory', str(run)]
    process = subprocess.run(command, capture_output=True, timeout=45)
    (attempt_dir / 'worker.stdout').write_bytes(process.stdout)
    (attempt_dir / 'worker.stderr').write_bytes(process.stderr)
    if process.returncode:
        raise RuntimeError('Synthetic worker failed: ' + process.stderr.decode('utf-8', errors='replace'))
    _check_prepared(group)
    manifest = _read(run / 'manifest.json')
    context = Path(prepared['context'])
    migration = group / 'migration' / 'manifest.json'
    source = Path(prepared['snapshot']) / 'dataset.sqlite'
    proof = attempt_dir / 'proof' / 'terminal.json'
    _save(proof, {'schema_version': 'classification-terminal-proof-v1',
        'observed_at': datetime.now(timezone.utc).isoformat(), 'session_id': 'synthetic-child-' + manifest['run_id'],
        'exit_code': process.returncode, 'process_exit_observed': True, 'run_id': manifest['run_id'],
        'manifest_sha256': _sha(run / 'manifest.json'), 'results_sha256': _sha(run / 'results.jsonl'),
        'ordered_inputs_sha256': _sha(prepared['inputs']), 'execution_context_manifest_sha256': _sha(context),
        'migration_manifest_sha256': _sha(migration), 'cli_profile_sha256_before': profile_before,
        'cli_profile_sha256_after': _sha(context), 'artifact_sha256': {'database': _sha(source)},
        'model_execution': 'mocked synthetic CLI response inside real child'})
    if pause_before_adoption:
        atomic_json(pause_before_adoption, {'boundary': 'worker_finished_before_shard_adoption',
            'unadopted_shard': str(group), 'attempt_dir': str(attempt_dir),
            'worker_exit_code': process.returncode, 'worker_process_exit_observed': True,
            'run_id': manifest['run_id']})
        while True:
            time.sleep(0.1)  # The worker is already reaped; only coordinator remains.
    imported = import_next_classifications(source_database=source, ordered_inputs=prepared['inputs'], run_dir=run,
        execution_context_manifest=context, migration_manifest=migration, terminal_proof=proof,
        cli_profile=context, output_dir=group / 'adopted', guard=UsageGuard(prepared['ledger']))
    _save(group / 'completed.json', {'run_id': manifest['run_id'], 'attempt_dir': str(attempt_dir), 'imported': imported})


def _fresh_shard_attempt(group):
    attempts = Path(group) / 'attempts'
    attempts.mkdir(exist_ok=True)
    for number in range(1, 1001):
        candidate = attempts / ('attempt-' + str(number).zfill(4))
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise ValueError('Bounded shard attempt count exhausted')


def load_fixed_shards(groups, *, usage='display'):
    """Recheck original current permissions and reject duplicate adopted work IDs.

    Reuse is readonly. A changed source/ledger fails the production adapter; no
    historical descriptor is rewritten to a later run ID or ledger hash.
    """
    rows = []
    identities = set()
    for group in map(Path, groups):
        prepared = _check_prepared(group)
        loaded = load_next_world_database(group / 'adopted/dataset.sqlite',
            guard=UsageGuard(prepared['ledger']), usage=usage, content_classified=True)
        for row in loaded['rows']:
            if row['work_id'] in identities:
                raise ValueError('Duplicate adopted work in fixed shards')
            identities.add(row['work_id'])
            rows.append(row)
    return rows


def run_fixed_shards(root, *, pause_after_first=False, pause_before_adoption_of=None):
    """Resume fixed independent shards, executing only missing adoptions.

    This verification coordinator accepts only its prepared synthetic fixture
    namespaces; it cannot launch real CLI/model execution.
    """
    root = Path(root)
    plan = _read(root / 'plan.json')
    groups = [Path(value) for value in plan['groups']]
    executed, reused = [], []
    try:
        for index, group in enumerate(groups):
            _check_prepared(group)
            if (group / 'adopted').exists():
                # Reopen and recheck current guard before declaring durable reuse.
                if len(load_fixed_shards([group])) != 1:
                    raise ValueError('Previously adopted shard no longer usage eligible')
                reused.append(str(group))
            else:
                # An unadopted attempt may contain completed jobs bound to an
                # orphan run ID. Preserve it and start a fresh original run;
                # adopted shards are never rerun or rebound to another ID.
                attempt = _fresh_shard_attempt(group)
                marker = root / 'boundary.json' if index == pause_before_adoption_of else None
                _execute_and_adopt(group, attempt_dir=attempt, pause_before_adoption=marker)
                executed.append(str(group))
            if pause_after_first and index == 0:
                atomic_json(root / 'boundary.json', {'boundary': 'after_atomic_shard_adoption_before_next_shard',
                    'adopted_shard': str(group), 'run_id': _read(group / 'completed.json')['run_id']})
                while True:
                    time.sleep(0.1)  # Parent terminates this exact fixture coordinator.
    except (ValueError, OSError):
        # Refresh the current materialized UI artifacts fail-closed as well as
        # rejecting reuse; old files must not continue advertising withdrawn rows.
        _save(root / 'display.json', {'rows': [], 'state': 'held_current_inputs_or_permission', 'synthetic_fixture': True})
        _recommendation_artifacts(root, rank_candidates([], [], limit=10))
        raise
    rows = load_fixed_shards(groups)
    _save(root / 'display.json', {'rows': rows, 'synthetic_fixture': True})
    _recommendation_artifacts(root, rank_candidates(rows, [], limit=10))
    _save(root / 'coordinator.json', {'executed': executed, 'reused': reused,
        'display_ids': [row['work_id'] for row in rows],
        'duplicate_adoptions': len(rows) - len({row['work_id'] for row in rows})})


def _inventory(directory):
    return {str(path): _sha(path) for path in Path(directory).rglob('*') if path.is_file()}


def _recommendation_artifacts(destination, ranking):
    _save(destination / 'recommendations.json', ranking)
    (destination / 'recommendations.html').write_text(
        render_recommendations(ranking, build_profile([])), encoding='utf-8')


def _display(group, guard, destination):
    loaded = load_next_world_database(Path(group) / 'adopted/dataset.sqlite', guard=guard,
        usage='display', content_classified=True)
    ranking = rank_candidates(loaded['rows'], [], limit=10)
    _save(destination / 'display.json', loaded)
    _recommendation_artifacts(destination, ranking)
    return loaded, ranking


def _selected_run(group):
    return Path(_read(Path(group) / 'completed.json')['attempt_dir']) / 'run'


def _verify_shard_interruption(root, snapshot, ledger, works, *, before_adoption=False):
    groups = [root / ('shard-' + str(index)) for index in range(len(works))]
    for group, work in zip(groups, works):
        _prepare(group, snapshot, ledger, [work])
    _save(root / 'plan.json', {'synthetic_fixture': True, 'groups': list(map(str, groups))})
    stdout = (root / 'interrupted.stdout').open('wb')
    stderr = (root / 'interrupted.stderr').open('wb')
    command = _child('--fixture-coordinator', root, pause=not before_adoption, pause_before_adoption=before_adoption)
    process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
    try:
        deadline = time.monotonic() + 45
        while not (root / 'boundary.json').exists():
            if process.poll() is not None:
                raise RuntimeError('Coordinator exited before boundary: ' + (root / 'interrupted.stderr').read_text())
            if time.monotonic() > deadline:
                raise TimeoutError('Coordinator did not reach durable boundary')
            time.sleep(0.05)
        boundary = _read(root / 'boundary.json')
        first_inventory = _inventory(groups[0])
        first_run = _selected_run(groups[0])
        completed_before = len(list((first_run / 'jobs').glob('*.json')))
        second_unstarted = not (groups[1] / 'attempts').exists()
        orphan = Path(boundary['attempt_dir']) if before_adoption else None
        orphan_inventory = _inventory(orphan) if orphan else {}
        unadopted_results = len((orphan / 'run/results.jsonl').read_text().splitlines()) if orphan else 0
        _save(root / 'before-kill-sha256.json', {'adopted_shard': first_inventory, 'unadopted_attempt': orphan_inventory})
    finally:
        if process.poll() is None:
            process.terminate()
        exit_code = process.wait(timeout=10)
        stdout.close()
        stderr.close()
    resumed = subprocess.run(_child('--fixture-coordinator', root), capture_output=True, timeout=45)
    (root / 'resumed.stdout').write_bytes(resumed.stdout)
    (root / 'resumed.stderr').write_bytes(resumed.stderr)
    if resumed.returncode:
        raise RuntimeError('Resume failed: ' + resumed.stderr.decode('utf-8', errors='replace'))
    coordinator = _read(root / 'coordinator.json')
    selected_runs = [_selected_run(group) for group in groups]
    states = [_read(path) for run in selected_runs for path in (run / 'jobs').glob('*.json')]
    results = [json.loads(line) for run in selected_runs for line in (run / 'results.jsonl').read_text().splitlines()]
    second_attempts = sorted((groups[1] / 'attempts').iterdir())
    attempt_run_ids = [_read(attempt / 'run/manifest.json')['run_id'] for attempt in second_attempts]
    original_ids_preserved = True
    adopted_run_ids = []
    for group, run in zip(groups, selected_runs):
        run_id = _read(run / 'manifest.json')['run_id']
        adopted_run_ids.append(run_id)
        original_ids_preserved &= _read(group / 'adopted/manifest.json')['run_id'] == run_id
        original_ids_preserved &= all(_read(request)['run_id'] == run_id for request in (run / 'attempts').glob('*.request.json'))
    return {'boundary': boundary['boundary'], 'coordinator_pid': process.pid,
        'child_process_terminated': exit_code != 0, 'terminated_exit_code': exit_code,
        'completed_before_kill': completed_before, 'pending_shard_unstarted_before_kill': second_unstarted,
        'completed_after_resume': len(results), 'reused_shards': len(coordinator['reused']),
        'executed_shards_after_resume': len(coordinator['executed']),
        'new_cli_response_invocations_after_resume': sum(_read(_selected_run(Path(group)) / 'manifest.json')['cli_invocations']
            for group in coordinator['executed']),
        'first_shard_artifacts_unchanged': all(_sha(path) == digest for path, digest in first_inventory.items()),
        'result_count': len(results), 'unique_result_count': len({r['work_id'] for r in results}),
        'attempt_counts': sorted(state['attempts'] for state in states),
        'display_ids': coordinator['display_ids'], 'duplicate_adoptions': coordinator['duplicate_adoptions'],
        'worker_exit_observed_before_kill': boundary.get('worker_process_exit_observed', False),
        'worker_exit_code_before_kill': boundary.get('worker_exit_code'),
        'unadopted_completed_results_before_kill': unadopted_results,
        'orphan_attempt_preserved': all(_sha(path) == digest for path, digest in orphan_inventory.items()),
        'second_shard_attempt_count': len(second_attempts),
        'second_shard_attempt_run_ids': attempt_run_ids, 'adopted_run_ids': adopted_run_ids,
        'total_cli_response_invocations': sum(_read(run / 'manifest.json')['cli_invocations']
            for group in groups for run in (group / 'attempts').glob('attempt-*/run')),
        'attempt_run_ids_are_distinct': len(set(attempt_run_ids)) == len(attempt_run_ids),
        'adopted_original_run_ids_preserved': bool(original_ids_preserved),
        'mixed_run_importer_repaired': False}


def verify_content_updates(output_dir):
    root = Path(output_dir).resolve()
    if root.exists():
        raise ValueError('Verification output must be a new directory')
    root.mkdir(parents=True)
    official = root / 'synthetic-cc0.html'
    official.write_text('Synthetic official-condition fixture: CC0.', encoding='utf-8')
    source = root / 'source/dataset.sqlite'
    original = _record(root, 'Q101', 'A detective solves a murder.', 'v1')
    other = _record(root, 'Q102', 'A detective investigates a missing letter.', 'v1')
    with DatasetDB(source) as db:
        db.ingest([original, other])
    baseline = root / 'baseline'
    snapshot, ledger, old_works = _snapshot(baseline, source, official)
    _prepare(baseline, snapshot, ledger, old_works)
    _execute_and_adopt(baseline)
    guard = UsageGuard(ledger)
    old_loaded, old_ranking = _display(baseline, guard, root / 'before-withdrawal')
    protected = _inventory(baseline / 'adopted') | _inventory(baseline / 'run') | _inventory(baseline / 'context')
    old_work = next(w for w in old_works if w.title == original.titles[0].value)
    old_state = next(state for state in _read(baseline / 'run/manifest.json')['jobs'].values()
        if state['input_hash'] == input_hash(old_work))
    old_result = _read(baseline / 'run' / old_state['result_path'])

    corrected = _record(root, 'Q101', 'A romance with explicitly no mystery or investigation.', 'v2')
    with DatasetDB(source) as db:
        assert db.ingest([corrected])['updated'] == 1
    updated = root / 'updated'
    new_snapshot, new_ledger, new_works = _snapshot(updated, source, official)
    new_work = next(w for w in new_works if w.work_id == old_work.work_id)
    new_guard = UsageGuard(new_ledger)
    no_content = load_next_world_database(new_snapshot / 'dataset.sqlite', guard=new_guard, usage='display')
    stale_result_rejected = False
    try:
        validate_result(old_result, new_work)
    except ValueError:
        stale_result_rejected = True
    stale_cache_rejected = False
    try:
        new_guard.assert_cache_allowed(new_work, input_hash(old_work), usage='external_llm')
    except UsageDenied:
        stale_cache_rejected = True
    old_namespace_rejected = False
    try:
        run_classification(new_works, baseline / 'run', executable=str(baseline / 'synthetic-cli.exe'), usage_ledger=new_ledger)
    except ValueError as exc:
        old_namespace_rejected = 'Frozen usage ledger changed' in str(exc)
    _prepare(updated, new_snapshot, new_ledger, new_works)
    _execute_and_adopt(updated)
    new_loaded, _ = _display(updated, new_guard, root / 'after-update')

    # One interruption after adoption, another after the next worker exits but
    # before its adoption. No live model/worker grandchild at either boundary.
    resume = root / 'resume'
    recovery = _verify_shard_interruption(resume, snapshot, ledger, old_works)
    orphan_recovery = _verify_shard_interruption(root / 'resume-before-adoption', snapshot, ledger, old_works, before_adoption=True)

    # Current ledger withdrawal is independent from immutable ingestion history.
    frozen = _inventory(root / 'raw') | _inventory(baseline / 'adopted') | _inventory(baseline / 'run')
    ledger_value = _read(ledger)
    ledger_value['revoked_input_hashes'] = [input_hash(old_work)]
    _save(ledger, ledger_value)
    after_loaded, after_ranking = _display(baseline, guard, root / 'after-withdrawal')
    discovered = []
    def forbidden_discovery(*args, **kwargs):
        discovered.append(True)
        raise AssertionError('Revoked cached input reached executable discovery')
    cache_rejected = False
    with patch('recommend_book.classification.runner.resolve_executable', forbidden_discovery):
        try:
            run_classification(old_works, baseline / 'run', executable='never', usage_ledger=ledger)
        except UsageDenied:
            cache_rejected = True
    shard_reuse_denied = False
    try:
        run_fixed_shards(resume)
    except ValueError as exc:
        shard_reuse_denied = 'no longer usage eligible' in str(exc)

    old_row = next(row for row in old_loaded['rows'] if row['work_id'] == old_work.work_id)
    new_row = next(row for row in new_loaded['rows'] if row['work_id'] == new_work.work_id)
    bare_row = next(row for row in no_content['rows'] if row['work_id'] == new_work.work_id)
    report = {'version': 'content-update-verification-v1', 'synthetic_fixture': True,
        'external_requests': 0, 'model_started': False, 'model_output_mocked': True,
        'human_gold': False, 'human_quality_verified': False,
        'display_renderer': {'production_renderer_used': True,
            'path': str(ROOT / 'src/recommend_book/recommendation/pipeline.py'),
            'sha256': _sha(ROOT / 'src/recommend_book/recommendation/pipeline.py'),
            'human_browser_verified': False},
        'content_update': {'title_unchanged': old_work.title == new_work.title, 'work_id_unchanged': old_work.work_id == new_work.work_id,
            'evidence_text_changed': old_work.evidence[0].text != new_work.evidence[0].text,
            'old_input_hash': input_hash(old_work), 'new_input_hash': input_hash(new_work),
            'old_job_key': job_key(input_hash(old_work), _read(baseline / 'prepared.json')['config']),
            'new_job_key': job_key(input_hash(new_work), _read(updated / 'prepared.json')['config']),
            'stale_result_rejected': stale_result_rejected, 'stale_cache_rejected': stale_cache_rejected,
            'old_namespace_rejected': old_namespace_rejected, 'old_features': old_row['features'],
            'rebuilt_unclassified_features': bare_row['features'], 'new_features': new_row['features']},
        'interruption_resume': {**recovery, 'current_permission_rechecked_on_resume': shard_reuse_denied},
        'unadopted_attempt_recovery': orphan_recovery,
        'permission_withdrawal': {'same_database_and_guard_reused': True, 'revoked_work_id': old_work.work_id,
            'before_display_count': len(old_loaded['rows']), 'after_display_count': len(after_loaded['rows']),
            'before_ranking_count': len(old_ranking['ranking']), 'after_ranking_count': len(after_ranking['ranking']),
            'after_display_ids': [r['work_id'] for r in after_loaded['rows']],
            'after_ranking_ids': [r['work_id'] for r in after_ranking['ranking']],
            'database_raw_and_results_unchanged': all(_sha(path) == digest for path, digest in frozen.items()),
            'cache_rejected_before_discovery': cache_rejected and not discovered,
            'unrelated_rows_held_by_frozen_ledger_change': len(old_loaded['rows']) - len(after_loaded['rows']) - 1},
        'protected_artifacts_unchanged': all(_sha(path) == digest for path, digest in protected.items()),
        'limitations': [
            'Synthetic descriptions and licensing artifacts; no real acquired work, legal clearance or Sol/model execution.',
            'Feature decisions are deterministic mocked fixtures, not semantic entailment or recommendation quality evidence.',
            'Interrupted coordinator resumes adopted shards or recomputes unadopted attempts in fresh namespaces; no mid-model/process-tree crash or lease recovery claim.',
            'Unadopted completed attempts are retained but may be recomputed; no exactly-once model-invocation claim across arbitrary interruption.',
            'Fixed shards retain their original run IDs; the existing mixed-run resume importer remains fail-closed and is not repaired.',
            'Any current ledger change invalidates prior native descriptor ledger binding, so unrelated classifications are conservatively held too.',
            'Historical snapshots and display files remain historical; refreshed display/ranking artifacts are written explicitly.',
        ]}
    _save(root / 'verification.json', report)
    return report
