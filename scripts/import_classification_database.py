"""Offline single-writer import into a new, sealed derivative of a frozen DB."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import shutil
import sqlite3
import uuid

from recommend_book.classification.runner import build_run_config
from recommend_book.classification.specs import get_spec
from recommend_book.classification.validation import ROOT, input_hash, validate_result
from recommend_book.database import DatasetDB
from recommend_book.jobs import canonical_json, job_key
from recommend_book.models import Evidence, WorkInput

ARTIFACTS = (
    'config/sources.json', 'config/taxonomy.json', 'schemas/classification.schema.json',
    'src/recommend_book/classification/prompt.py', 'src/recommend_book/classification/validation.py',
    'src/recommend_book/classification/runner.py', 'src/recommend_book/models.py',
    'src/recommend_book/jobs.py', 'pyproject.toml', 'uv.lock',
)

DDL = (
    '''CREATE TABLE classification_runs (
        run_id TEXT PRIMARY KEY, config_json TEXT NOT NULL, execution_context_json TEXT NOT NULL,
        migration_manifest_json TEXT NOT NULL, terminal_proof_json TEXT NOT NULL,
        provenance_json TEXT NOT NULL, selected INTEGER NOT NULL, completed INTEGER NOT NULL,
        failed INTEGER NOT NULL, held INTEGER NOT NULL, invalid_cached_result INTEGER NOT NULL,
        pending INTEGER NOT NULL CHECK(pending=0), human_verified INTEGER NOT NULL CHECK(human_verified=0),
        CHECK(completed+failed+pending=selected), CHECK(held+invalid_cached_result=failed))''',
    '''CREATE TABLE classification_inputs (
        job_key TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES classification_runs(run_id),
        selected_position INTEGER NOT NULL, work_id TEXT NOT NULL REFERENCES works(work_id),
        expression_id TEXT REFERENCES expressions(expression_id), input_hash TEXT NOT NULL,
        input_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('completed','held','invalid_cached_result')),
        job_state_json TEXT NOT NULL, result_json TEXT, result_sha256 TEXT, held_reason_json TEXT,
        CHECK((status='completed' AND result_json IS NOT NULL AND held_reason_json IS NULL) OR
              (status!='completed' AND result_json IS NULL AND held_reason_json IS NOT NULL)),
        UNIQUE(run_id,selected_position))''',
    '''CREATE TABLE classification_source_refs (
        job_key TEXT NOT NULL REFERENCES classification_inputs(job_key), position INTEGER NOT NULL,
        source_version_id TEXT NOT NULL REFERENCES source_records(version_id),
        source_scope TEXT NOT NULL CHECK(source_scope IN ('work','edition')), payload_sha256 TEXT NOT NULL,
        raw_sha256 TEXT NOT NULL, work_nodes_json TEXT NOT NULL, edition_ids_json TEXT NOT NULL,
        PRIMARY KEY(job_key,position))''',
    '''CREATE TABLE classification_input_evidence (
        job_key TEXT NOT NULL REFERENCES classification_inputs(job_key), evidence_id TEXT NOT NULL,
        evidence_json TEXT NOT NULL, source_evidence_keys_json TEXT NOT NULL,
        PRIMARY KEY(job_key,evidence_id))''',
    '''CREATE TABLE classification_labels (
        job_key TEXT NOT NULL REFERENCES classification_inputs(job_key), label_id TEXT NOT NULL,
        axis TEXT NOT NULL, decision TEXT NOT NULL, scope TEXT NOT NULL,
        expression_id TEXT REFERENCES expressions(expression_id), relevance REAL, raw_confidence REAL,
        calibrated_confidence REAL CHECK(calibrated_confidence IS NULL),
        calibration_status TEXT NOT NULL CHECK(calibration_status='uncalibrated'),
        calibration_version TEXT CHECK(calibration_version IS NULL),
        human_verified INTEGER NOT NULL CHECK(human_verified=0), label_json TEXT NOT NULL,
        PRIMARY KEY(job_key,label_id))''',
    '''CREATE TABLE classification_label_evidence (
        job_key TEXT NOT NULL, label_id TEXT NOT NULL, position INTEGER NOT NULL,
        evidence_id TEXT NOT NULL, locator TEXT, quote TEXT, summary TEXT, directness TEXT NOT NULL,
        reference_json TEXT NOT NULL,
        FOREIGN KEY(job_key,label_id) REFERENCES classification_labels(job_key,label_id),
        FOREIGN KEY(job_key,evidence_id) REFERENCES classification_input_evidence(job_key,evidence_id),
        PRIMARY KEY(job_key,label_id,position))''',
)


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate JSON property: ' + key)
            value[key] = item
        return value
    def nonfinite(value):
        raise ValueError('Non-finite JSON value: ' + value)
    def floating(value):
        number = float(value)
        return number if math.isfinite(number) else nonfinite(value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite, parse_float=floating)


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def value_sha(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def read_json(path):
    return strict_json(Path(path).read_bytes())


def contained(root, relative):
    if not isinstance(relative, str) or not relative or '\x00' in relative:
        raise ValueError('Artifact path must be relative')
    windows = PureWindowsPath(relative)
    normalized = relative.replace('\\', '/')
    if windows.drive or windows.root or Path(normalized).is_absolute() or '..' in normalized.split('/'):
        raise ValueError('Artifact path must be relative without drive/root/parent traversal')
    try:
        path = (root / normalized).resolve(strict=True)
    except OSError as exc:
        raise ValueError('Artifact path does not resolve to an existing source') from exc
    if not path.is_relative_to(root):
        raise ValueError('Artifact escapes source namespace')
    return path


def _inventory(root):
    paths = set()
    for path in root.rglob('*'):
        if path.is_file():
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError('Source symlink escapes namespace')
            paths.add(resolved)
    return paths


def artifact_names_for_config(config):
    """Return the source artifacts bound by a saved execution recipe.

    The ten historical artifacts are the default contract.  Future specs add
    only the files resolved by the local spec registry; the prepared manifest
    is deliberately not used as an authority for this set.
    """
    spec_name = config.get('execution_spec')
    if spec_name is None or spec_name == 'guarded-v3-baseline':
        return ARTIFACTS
    spec = get_spec(spec_name, root=ROOT)
    names = list(ARTIFACTS)
    for path in (spec.prompt_path, spec.schema_path,
                 ROOT / 'src/recommend_book/classification/specs.py'):
        name = path.resolve(strict=True).relative_to(ROOT).as_posix()
        if name not in names:
            names.append(name)
    return tuple(names)


def _protected(files, run_dir, context_manifest, artifact_names=None):
    artifact_names = ARTIFACTS if artifact_names is None else tuple(artifact_names)
    return set(files) | _inventory(run_dir) | {
        (ROOT / name).resolve(strict=True) for name in artifact_names
    } | {contained(context_manifest.parent, 'artifacts/' + name) for name in artifact_names} | {
        (ROOT / 'src/recommend_book' / name).resolve(strict=True)
        for name in ('database.py', 'normalization.py', 'resolution.py')
    } | {contained(context_manifest.parent, 'inputs/' + name) for name in ('ordered.jsonl', 'manifest.json')} | {Path(__file__).resolve()}


def _before_publish():
    """No-op boundary used by synthetic mutation tests."""


def readonly(path):
    wal = Path(str(path) + '-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Immutable source has nonempty WAL; owner must checkpoint first')
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def table_signatures(db):
    """Multiset of exact typed SQLite rows; retain duplicate rows and NULLs."""
    signatures = {}
    for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        rows = []
        for row in db.execute('SELECT * FROM ' + quoted):
            typed = [(type(v).__name__, v.hex() if isinstance(v, bytes) else v) for v in row]
            rows.append(value_sha(typed))
        signatures[name] = value_sha(sorted(rows))
    return signatures


def check_proof(proof, *, source_db, ordered_inputs, run_dir, execution_context_manifest,
                migration_manifest, cli_profile):
    if (proof.get('schema_version') != 'classification-terminal-proof-v1' or
        proof.get('process_exit_observed') is not True or type(proof.get('exit_code')) is not int or
        not isinstance(proof.get('session_id'), str) or not proof['session_id'].strip()):
        raise ValueError('Explicit process exit observation required')
    try:
        observed = datetime.fromisoformat(proof['observed_at'].replace('Z', '+00:00'))
        if observed.utcoffset() is None or observed.utcoffset().total_seconds() != 0:
            raise ValueError('Terminal observation must be UTC')
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError('UTC terminal observation required') from exc
    expected = {
        'manifest_sha256': file_sha(run_dir / 'manifest.json'),
        'results_sha256': file_sha(run_dir / 'results.jsonl'),
        'ordered_inputs_sha256': file_sha(ordered_inputs),
        'execution_context_manifest_sha256': file_sha(execution_context_manifest),
        'migration_manifest_sha256': file_sha(migration_manifest),
        'cli_profile_sha256_before': file_sha(execution_context_manifest),
        'cli_profile_sha256_after': file_sha(execution_context_manifest),
    }
    if any(proof.get(key) != value for key, value in expected.items()):
        raise ValueError('Terminal proof artifact SHA mismatch')
    if file_sha(cli_profile) != expected['execution_context_manifest_sha256']:
        raise ValueError('CLI profile must be the fixed execution context bytes')
    if proof.get('artifact_sha256', {}).get('database') != file_sha(source_db):
        raise ValueError('Terminal proof database SHA mismatch')


def check_context(context, ordered_inputs):
    scope = context['execution_context']
    config = scope['run_config']
    artifact_names = artifact_names_for_config(config)
    if (scope.get('artifact_count') != len(artifact_names) or
            set(scope['artifact_sha256']) != set(artifact_names)):
        raise ValueError('Execution artifact inventory differs from the saved recipe')
    if context['input_provenance']['input_sha256'] != file_sha(ordered_inputs):
        raise ValueError('Prepared execution input SHA mismatch')
    expected_copies = {'inputs/ordered.jsonl', 'inputs/manifest.json'} | {'artifacts/' + name for name in artifact_names}
    if set(context.get('output_sha256', {})) != expected_copies:
        raise ValueError('Prepared artifact copy inventory differs')
    for name in artifact_names:
        expected = scope['artifact_sha256'][name]
        if file_sha(ROOT / name) != expected:
            raise ValueError('Current execution artifact differs: ' + name)
    taxonomy = read_json(ROOT / 'config/taxonomy.json')
    expected_config = (build_run_config(config['model'], config['cli_version'], taxonomy, config['batch_size'],
                                        execution_spec=config['execution_spec'])
                       if config.get('execution_spec') is not None else
                       build_run_config(config['model'], config['cli_version'], taxonomy, config['batch_size']))
    if config != expected_config:
        raise ValueError('Current run configuration differs')
    return config, taxonomy


def check_inputs(source, ordered_inputs, context):
    sibling = read_json(ordered_inputs.with_name('manifest.json'))
    declared = [sibling[k] for k in ('ordered_inputs_sha256', 'work_inputs_sha256') if k in sibling]
    if not declared or any(v != file_sha(ordered_inputs) for v in declared):
        raise ValueError('Sibling input manifest SHA mismatch')
    if context['input_provenance']['manifest_sha256'] != file_sha(ordered_inputs.with_name('manifest.json')):
        raise ValueError('Prepared input manifest SHA mismatch')
    lines = ordered_inputs.read_bytes().splitlines()
    if any(not line.strip() for line in lines):
        raise ValueError('Empty input row')
    works = [WorkInput.model_validate(strict_json(line)) for line in lines]
    identities = [(w.work_id, w.expression_id) for w in works]
    if len(set(identities)) != len(works):
        raise ValueError('Duplicate work/expression input')
    if context['input_provenance']['work_expression_count'] != len(works):
        raise ValueError('Prepared input count mismatch')
    # Bypass DatasetDB.__init__: no migration, journal pragma or writable open.
    view = DatasetDB.__new__(DatasetDB)
    view.connection = source
    current = {(w.work_id, w.expression_id): w for w in view.work_inputs()}
    for work in works:
        reconstructed = current.get((work.work_id, work.expression_id))
        if reconstructed is None or input_hash(work) != input_hash(reconstructed):
            raise ValueError('Input differs from immutable DB reconstruction (scope/source refs/metadata/evidence)')
        if not work.source_record_ids or len(set(work.source_record_ids)) != len(work.source_record_ids):
            raise ValueError('Missing or duplicate immutable source version refs')
        for version in work.source_record_ids:
            row = source.execute('SELECT record_json FROM source_records WHERE version_id=?', (version,)).fetchone()
            if row is None or strict_json(row[0])['record_kind'] not in ('work', 'edition'):
                raise ValueError('Invalid immutable source version scope')
            if not source.execute('''SELECT 1 FROM works WHERE source_version_id=?
                    AND canonical_id=? AND resolved=1''', (version, work.work_id)).fetchone():
                raise ValueError('Source version belongs to another canonical work')
            if strict_json(row[0])['record_kind'] == 'edition':
                contents = source.execute('''SELECT ec.work_id,ec.expression_id,x.work_id AS expression_work
                    FROM editions d JOIN edition_contents ec ON ec.edition_id=d.edition_id
                    LEFT JOIN expressions x ON x.expression_id=ec.expression_id
                    WHERE d.source_version_id=?''', (version,)).fetchall()
                if not any(c['work_id'] == work.work_id for c in contents):
                    raise ValueError('Edition source has no selected work content link')
                if any(c['expression_id'] is not None and c['expression_work'] != c['work_id'] for c in contents):
                    raise ValueError('Edition content expression belongs to another work')
        if work.expression_id:
            row = source.execute('SELECT work_id FROM expressions WHERE expression_id=?', (work.expression_id,)).fetchone()
            if row is None or row[0] != work.work_id:
                raise ValueError('Expression belongs to another work')
    return works


def check_run(run_dir, manifest, proof, works, config, taxonomy):
    states = manifest['jobs']
    expected = {job_key(input_hash(w), config): w for w in works}
    if (manifest.get('run_id') != proof.get('run_id') or not manifest.get('run_id') or
        manifest.get('config') != config or manifest.get('stopped') is not False or
        manifest.get('human_quality_verified') is not False or
        type(manifest.get('pending')) is not int or manifest['pending'] != 0 or
        manifest.get('selected') != len(works) or set(states) != set(expected)):
        raise ValueError('Run is not terminal for exact selected input/config')
    completed = sum(s.get('status') == 'completed' for s in states.values())
    if (type(manifest.get('selected')) is not int or type(manifest.get('completed')) is not int or
        type(manifest.get('failed')) is not int or manifest['completed'] != completed or
        manifest['failed'] != len(works) - completed or
        manifest['completed'] + manifest['failed'] + manifest['pending'] != manifest['selected']):
        raise ValueError('Terminal job counts differ')
    durable = {}
    for key, work in expected.items():
        state = states[key]
        if state.get('status') not in ('completed', 'held', 'invalid_cached_result') or state.get('lease_until') is not None:
            raise ValueError('Nonterminal job state')
        if read_json(contained(run_dir, 'jobs/' + key + '.json')) != state:
            raise ValueError('Durable job differs from manifest')
        for field, value in {'key': key, 'work_id': work.work_id, 'expression_id': work.expression_id,
                             'input_hash': input_hash(work), 'config': config}.items():
            if field not in state or state[field] != value:
                raise ValueError('Job metadata differs: ' + field)
        default_result = run_dir / 'results' / (key + '.json')
        if state['status'] == 'completed':
            result_path = contained(run_dir, state.get('result_path'))
            if result_path != default_result.resolve(strict=True):
                raise ValueError('Durable result path differs from job key')
            result = read_json(result_path)
            validate_result(result, work, taxonomy)
            if result['model'] != config['model'] or result['cli_version'] != config['cli_version']:
                raise ValueError('Result model/CLI differs')
            durable[key] = result
        elif state['status'] == 'held':
            if state.get('result_path') or default_result.exists():
                raise ValueError('Held job unexpectedly has a result')
            if not state.get('error_category') and not state.get('error'):
                raise ValueError('Held reason absent')
    results = [strict_json(line) for line in (run_dir / 'results.jsonl').read_bytes().splitlines() if line.strip()]
    # Index must be precisely the selected completed order, with no extra or duplicate result.
    ordered = [durable[key] for key in expected if key in durable]
    if results != ordered:
        raise ValueError('Completed durable results differ from result index')
    return expected, durable


def insert_rows(db, source, manifest, context, migration, proof, provenance, expected, durable):
    held = sum(s['status']=='held' for s in manifest['jobs'].values())
    invalid = sum(s['status']=='invalid_cached_result' for s in manifest['jobs'].values())
    db.execute('INSERT INTO classification_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (
        manifest['run_id'], canonical_json(manifest['config']), canonical_json(context), canonical_json(migration),
        canonical_json(proof), canonical_json(provenance), len(expected), len(durable),
        len(expected)-len(durable), held, invalid, 0, 0))
    for position, (key, work) in enumerate(expected.items()):
        state = manifest['jobs'][key]
        result = durable.get(key)
        reason = None if result is not None else {'status': state['status'],
            'error_category': state.get('error_category'), 'error': state.get('error'), 'history': state.get('history', [])}
        db.execute('INSERT INTO classification_inputs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
            key, manifest['run_id'], position, work.work_id, work.expression_id, input_hash(work),
            canonical_json(work.model_dump(mode='json')), state['status'], canonical_json(state),
            canonical_json(result) if result is not None else None,
            value_sha(result) if result is not None else None, canonical_json(reason) if reason is not None else None))
        for ref_position, version in enumerate(work.source_record_ids):
            row = source.execute('SELECT * FROM source_records WHERE version_id=?', (version,)).fetchone()
            nodes = [r[0] for r in source.execute('SELECT work_id FROM works WHERE source_version_id=? ORDER BY work_id', (version,))]
            editions = [r[0] for r in source.execute('SELECT edition_id FROM editions WHERE source_version_id=? ORDER BY edition_id', (version,))]
            db.execute('INSERT INTO classification_source_refs VALUES(?,?,?,?,?,?,?,?)', (
                key, ref_position, version, strict_json(row['record_json'])['record_kind'],
                row['payload_sha256'], row['raw_sha256'], canonical_json(nodes), canonical_json(editions)))
        for evidence in work.evidence:
            matches = []
            for row in source.execute('''SELECT evidence_key,evidence_json FROM evidence
                WHERE work_id=? AND expression_id IS ? AND scope_valid=1 AND evidence_id=? ORDER BY evidence_key''',
                (work.work_id, work.expression_id, evidence.evidence_id)):
                if Evidence.model_validate_json(row['evidence_json']) == evidence:
                    matches.append(row['evidence_key'])
            if not matches or not evidence.llm_input_allowed:
                raise ValueError('Input evidence scope/rights/source lineage differs')
            db.execute('INSERT INTO classification_input_evidence VALUES(?,?,?,?)', (
                key, evidence.evidence_id, canonical_json(evidence.model_dump(mode='json')), canonical_json(matches)))
        if result is not None:
            for label in result['labels']:
                db.execute('INSERT INTO classification_labels VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    key, label['label_id'], label['axis'], label['decision'], label['scope'], label['expression_id'],
                    label['relevance'], label['raw_confidence'], label['calibrated_confidence'],
                    label['calibration_status'], label['calibration_version'], 0, canonical_json(label)))
                for index, ref in enumerate(label['evidence']):
                    db.execute('INSERT INTO classification_label_evidence VALUES(?,?,?,?,?,?,?,?,?)', (
                        key, label['label_id'], index, ref['evidence_id'], ref['locator'], ref['quote'],
                        ref['summary'], ref['directness'], canonical_json(ref)))


def import_classifications(source_db, ordered_inputs, run_dir, execution_context_manifest,
        migration_manifest, terminal_proof, output_dir, *, cli_profile):
    source_db, ordered_inputs, run_dir, execution_context_manifest, migration_manifest, terminal_proof, cli_profile = [
        Path(p).resolve(strict=True) for p in (source_db, ordered_inputs, run_dir,
            execution_context_manifest, migration_manifest, terminal_proof, cli_profile)]
    output = Path(output_dir).resolve()
    protected_trees = (source_db.parent, ordered_inputs.parent, run_dir, execution_context_manifest.parent,
                       migration_manifest.parent, terminal_proof.parent)
    if output.exists() or any(output.is_relative_to(tree) for tree in protected_trees):
        raise ValueError('Output must be new and outside all protected input directories')
    files = {source_db, ordered_inputs, ordered_inputs.with_name('manifest.json'), execution_context_manifest,
             migration_manifest, terminal_proof, cli_profile}
    context = read_json(execution_context_manifest)
    artifact_names = artifact_names_for_config(context['execution_context']['run_config'])
    protected = _protected(files, run_dir, execution_context_manifest, artifact_names)
    initial = {str(p): file_sha(p) for p in sorted(protected)}
    inventory = _inventory(run_dir)
    proof = read_json(terminal_proof)
    check_proof(proof, source_db=source_db, ordered_inputs=ordered_inputs, run_dir=run_dir,
                execution_context_manifest=execution_context_manifest, migration_manifest=migration_manifest,
                cli_profile=cli_profile)
    config, taxonomy = check_context(context, ordered_inputs)
    for name in artifact_names:
        if file_sha(contained(execution_context_manifest.parent, 'artifacts/' + name)) != context['execution_context']['artifact_sha256'][name]:
            raise ValueError('Archived execution artifact differs: ' + name)
    for name, expected_sha in context['output_sha256'].items():
        if file_sha(contained(execution_context_manifest.parent, name)) != expected_sha:
            raise ValueError('Prepared raw copy differs: ' + name)
    for relative, original in (('inputs/ordered.jsonl', ordered_inputs),
                               ('inputs/manifest.json', ordered_inputs.with_name('manifest.json'))):
        if file_sha(contained(execution_context_manifest.parent, relative)) != file_sha(original):
            raise ValueError('Prepared raw input copy differs')
    manifest, migration = read_json(run_dir / 'manifest.json'), read_json(migration_manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / ('.' + output.name + '.import-' + uuid.uuid4().hex)
    source = readonly(source_db)
    try:
        if source.execute('PRAGMA user_version').fetchone()[0] != 3:
            raise ValueError('Expected frozen bibliographic schema 3')
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or source.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Source DB integrity violation')
        baseline = table_signatures(source)
        if any(name.startswith('classification_') for name in baseline):
            raise ValueError('Source already contains classifications; select the original frozen bibliographic DB')
        works = check_inputs(source, ordered_inputs, context)
        expected, durable = check_run(run_dir, manifest, proof, works, config, taxonomy)
        provenance = {'source_database_sha256': file_sha(source_db), 'source_files_sha256': initial,
                      'input_processing_context': read_json(ordered_inputs.with_name('manifest.json')).get('processing_context'),
                      'writer': 'single local SQLite transaction', 'human_verified': False,
                      'semantic_quality_verified': False, 'model_values_changed': False}
        stage.mkdir()
        db = sqlite3.connect(stage / 'dataset.sqlite')
        try:
            source.backup(db)
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            for statement in DDL:
                db.execute(statement)
            insert_rows(db, source, manifest, context, migration, proof, provenance, expected, durable)
            after = table_signatures(db)
            if any(after.get(name) != signature for name, signature in baseline.items()):
                raise ValueError('Original bibliographic table values changed')
            if db.execute('PRAGMA foreign_key_check').fetchall() or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Derived DB integrity violation')
            db.commit()
        finally:
            db.close()
        report = {'schema_version': 'classification-derived-database-v1', 'run_id': manifest['run_id'],
                  'selected': len(expected), 'completed': len(durable), 'failed': len(expected)-len(durable),
                  'held': sum(s['status']=='held' for s in manifest['jobs'].values()),
                  'invalid_cached_result': sum(s['status']=='invalid_cached_result' for s in manifest['jobs'].values()),
                  'pending': 0, 'human_verified': False, 'semantic_quality_verified': False,
                  'calibration_status': 'uncalibrated', 'llm_started': False, 'web_requests': 0,
                  'sealed_derivative': True, 'source_files_unchanged': True,
                  'original_table_signatures': baseline, 'provenance': provenance,
                  'output_sha256': {'dataset.sqlite': file_sha(stage / 'dataset.sqlite')}}
        report_bytes = (canonical_json(report) + '\n').encode()
        (stage / 'manifest.json').write_bytes(report_bytes)
        _before_publish()
        wal = Path(str(source_db) + '-wal')
        if wal.exists() and wal.stat().st_size:
            raise ValueError('Protected source WAL changed before publication')
        if _inventory(run_dir) != inventory or any(not Path(p).is_file() or file_sha(p) != checksum for p, checksum in initial.items()):
            raise ValueError('Protected source artifact changed before publication')
        if _protected(files, run_dir, execution_context_manifest, artifact_names) != protected:
            raise ValueError('Protected source inventory changed before publication')
        expected_config = (build_run_config(config['model'], config['cli_version'], taxonomy, config['batch_size'],
                                            execution_spec=config['execution_spec'])
                           if config.get('execution_spec') is not None else
                           build_run_config(config['model'], config['cli_version'], taxonomy, config['batch_size']))
        if config != expected_config:
            raise ValueError('Current configuration changed before publication')
        if (file_sha(stage / 'dataset.sqlite') != report['output_sha256']['dataset.sqlite'] or
            (stage / 'manifest.json').read_bytes() != report_bytes):
            raise ValueError('Staged output changed before publication')
        if output.exists():
            raise ValueError('Output appeared before publication')
        stage.rename(output)
        return report
    finally:
        source.close()
        if stage.exists():
            if stage.resolve().parent != output.parent:
                raise ValueError('Owned stage escaped output parent')
            shutil.rmtree(stage)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('source-db', 'ordered-inputs', 'run-dir', 'execution-context-manifest',
                     'migration-manifest', 'terminal-proof', 'output-dir', 'cli-profile'):
        parser.add_argument('--' + argument, required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(import_classifications(**vars(args)), ensure_ascii=False))
