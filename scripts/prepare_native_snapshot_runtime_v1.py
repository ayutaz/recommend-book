"""Build an isolated version-four runtime; never edits historical source bytes.

The transform is deterministic and each source anchor is counted. Preparation
and execution run in fresh interpreters with explicit runtime PYTHONPATH.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

from recommend_book.classification.native_reference_store_v1 import (
    BINDING_NAME, REFERENCE_NAMES, store_references, verify_binding)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def replace(text, before, after, count=1):
    if text.count(before) != count:
        raise ValueError('Version-four transform anchor count differs: ' + before[:100])
    return text.replace(before, after)


def transform(relative, raw):
    text = raw.decode('utf-8').replace('\r\n', '\n')
    if relative.endswith('/runner.py'):
        text = replace(text, 'from recommend_book.usage_guard import ', 'from recommend_book.usage_guard_v4 import ', 2)
        text = text.replace('src/recommend_book/usage_guard.py', 'src/recommend_book/usage_guard_v4.py')
        text = text.replace('src/recommend_book/classification/native_attempt.py', 'src/recommend_book/classification/native_attempt_v4.py')
        text = replace(text, 'guarded-runner-v3.2', 'guarded-runner-v4.0')
        text = replace(text, "        if execution_spec.startswith('native-'):\n",
            "        from .native_reference_store_v1 import BINDING_NAME, reference_artifact_path\n"
            "        config['dependency_sha256']['src/recommend_book/classification/native_reference_store_v1.py'] = hashlib.sha256((ROOT / 'src/recommend_book/classification/native_reference_store_v1.py').read_bytes()).hexdigest()\n"
            "        if (ROOT / BINDING_NAME).exists():\n"
            "            config['dependency_sha256'][BINDING_NAME] = hashlib.sha256((ROOT / BINDING_NAME).read_bytes()).hexdigest()\n"
            "        if execution_spec.startswith('native-'):\n")
        text = replace(text, "hashlib.sha256((ROOT / (codebook+p)).read_bytes()).hexdigest()",
                       "hashlib.sha256(reference_artifact_path(ROOT, codebook+p).read_bytes()).hexdigest()")
        # Future snapshots hold failures immediately; unchanged inputs are not
        # automatically submitted again, including on a same-namespace resume.
        text = replace(text, '    store = JobStore(output_dir)\n',
                       '    store = JobStore(output_dir, max_attempts=1)\n')
        anchor = '        config["dependency_sha256"] = {p:hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in dependencies}\n'
        text = replace(text, anchor, anchor + '''        config['runtime_version'] = 'native-snapshot-runtime-v1'
        config['runtime_source_sha256'] = {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted([*(ROOT / 'src/recommend_book').rglob('*.py'), *(ROOT / 'scripts').glob('*.py')])}
        config['runtime_transform_sha256'] = hashlib.sha256((ROOT / 'scripts/prepare_native_snapshot_runtime_v1.py').read_bytes()).hexdigest()
''')
        text = replace(text, '            value = operation()\n', '''            if phase in ('result_adoption', 'cache_derived', 'result_derived'):
                with usage_authorizer.native_scopes.validation():
                    value = operation()
            else:
                value = operation()
''')
        text = replace(text, "'kind':'classification-native-attempt-v3'", "'kind':'classification-native-attempt-v4'")
        start = text.index('        for key, work in jobs:\n', text.index('    finally:\n', text.index('    def process(')))
        end = text.index('        _atomic_lines(output_dir / "results.jsonl", results)', start)
        text = text[:start] + '        with usage_authorizer.native_scopes.validation():\n' + ''.join('    ' + line if line.strip() else line for line in text[start:end].splitlines(keepends=True)) + text[end:]
        text = replace(text, '            "human_quality_verified":False}', '            "native_artifact_snapshot_reports":usage_authorizer.native_scopes.reports,\n            "human_quality_verified":False}')
        before = '''                completed = subprocess.run(argv, input=stdin_bytes,
                    capture_output=True, text=False, timeout=timeout, shell=False)
                stdout_raw, stderr_raw, returncode = _bytes(completed.stdout), _bytes(completed.stderr), completed.returncode'''
        after = '''                actual_cli_started = False
                with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False) as child:
                    actual_cli_started = True
                    process_pid = child.pid
                    try:
                        stdout_raw, stderr_raw = child.communicate(stdin_bytes, timeout=timeout)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        stdout_raw, stderr_raw = child.communicate()
                        raise subprocess.TimeoutExpired(argv, timeout, output=stdout_raw, stderr=stderr_raw)
                    returncode = child.returncode'''
        text = replace(text, before, after)
        text = replace(text, '            protocol_attempt = {"run_id":run_id,', '            protocol_attempt = {"run_id":run_id,"process_pid":locals().get("process_pid"),"process_exit_observed":returncode is not None,')
    elif relative.endswith('/native_attempt.py'):
        text = replace(text, 'expected_config_sha256, works):', 'expected_config_sha256, works, artifact_session=None):')
        text = replace(text, '    from .runner import ', '    from .runner_v4 import ')
        text = replace(text, "'classification-native-attempt-v3'", "'classification-native-attempt-v4'")
        text = replace(text, "    descriptor = source_manifest\n", "    if artifact_session is None:\n        raise ValueError('An active native validation scope is required')\n    descriptor = source_manifest\n")
        text = replace(text, "    config = request['config']\n", "    config = request['config']\n    from .validation import ROOT\n    artifact_session.check_source_receipts(ROOT, config['runtime_source_sha256'])\n")
        text = replace(text, "    if version['argv'][-1] != '--version' or command_artifact_hashes(command) != request['executable_sha256']:\n        raise ValueError('Native executable artifact differs')", "    if version['argv'][-1] != '--version':\n        raise ValueError('Native executable version argv differs')\n    artifact_session.check_receipt(command, request['executable_sha256'])")
        text = replace(text, "    if attempt['actual_exit_code'] != 0", "    if not attempt.get('process_pid') or attempt.get('process_exit_observed') is not True:\n        raise ValueError('Native process PID/observed exit is absent')\n    if attempt['actual_exit_code'] != 0")
    elif relative.endswith('/usage_guard.py'):
        text = replace(text, "GUARD_VERSION = 'usage-guard-v3.7'", "GUARD_VERSION = 'usage-guard-v4.0'")
        text = replace(text, '        self.ledger_path = Path(ledger_path).resolve()\n', '        from recommend_book.classification.native_validation_scope_v1 import NativeValidationScopes\n        self.native_scopes = NativeValidationScopes()\n        self.ledger_path = Path(ledger_path).resolve()\n')
        text = replace(text, 'from recommend_book.classification import native_attempt', 'from recommend_book.classification import native_attempt_v4 as native_attempt', 2)
        text = replace(text, 'from recommend_book import next_database', 'from recommend_book import next_database_v4 as next_database')
        text = replace(text, 'expected_config_sha256=config_sha, works=works)', 'expected_config_sha256=config_sha, works=works, artifact_session=self.native_scopes.current)')
        text = replace(text, '            if source_manifest is not None:\n', "            if source_manifest is not None:\n                if source_manifest.get('kind') != 'classification-native-attempt-v4':\n                    raise ValueError('Version-four guard requires new native lineage')\n")
    elif relative.endswith('/next_database.py'):
        text = replace(text, 'from recommend_book.classification.runner import build_run_config', 'from recommend_book.classification.runner_v4 import build_run_config')
        text = replace(text, 'from recommend_book.classification.native_attempt import verify_native_attempt', 'from recommend_book.classification.native_attempt_v4 import verify_native_attempt')
        text = replace(text, 'def import_next_classifications(*, source_database,', 'def _import_next_classifications_v4(*, _snapshot_boundary, source_database,')
        text = replace(text, 'expected_config_sha256=_value_sha(config),works=batch)', 'expected_config_sha256=_value_sha(config),works=batch,artifact_session=guard.native_scopes.current)')
        text = replace(text, "request.get('run_id')!=manifest['run_id']", "request.get('run_id')!=descriptor['run_id']")
        text = replace(text, "            raise ValueError('Classification source changed before publication')\n        stage.rename(output)", "            raise ValueError('Classification source changed before publication')\n        _snapshot_boundary()\n        stage.rename(output)")
        text = replace(text, 'def load_next_world_database(path, *, guard,', 'def _load_next_world_database_v4(path, *, guard,')
        text += '''\n\ndef import_next_classifications(**kwargs):
    with kwargs['guard'].native_scopes.validation() as scope:
        return _import_next_classifications_v4(_snapshot_boundary=scope.close, **kwargs)


def load_next_world_database(path, *, guard, **kwargs):
    with guard.native_scopes.validation():
        return _load_next_world_database_v4(path, guard=guard, **kwargs)
'''
    else:
        raise ValueError('Unsupported transformed source: ' + relative)
    compile(text, relative, 'exec')
    return text.encode('utf-8')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def build(output):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'data/autonomous-next-v3/preparation'):
        raise ValueError('Fresh runtime preparation namespace required')
    paths = list((ROOT / 'src/recommend_book').rglob('*.py')) + list((ROOT / 'schemas').glob('*.json')) + list((ROOT / 'config').glob('*.json'))
    names = ('scripts/run_guarded_classification.py', 'scripts/prepare_native_snapshot_runtime_v1.py',
        'scripts/import_classification_database.py', 'scripts/freeze_classification_execution.py',
        'scripts/build_next_database.py', 'scripts/native_snapshot_terminal_v1.py', 'pyproject.toml', 'uv.lock', 'config/autonomous-next-v3.json', 'docs/source-rights-review-scope-v3.md')
    paths += [ROOT / name for name in names]
    codebook = ROOT / 'data/autonomous-next-v3/reference/research-codebook'
    paths += [codebook / name for name in ('native-v3-source-contract.json', 'keywords.xml', 'book.pdf')]
    originals = {path.relative_to(ROOT).as_posix():path.read_bytes() for path in sorted(set(paths))}
    binding, cache_new_bytes = store_references(ROOT / 'data/autonomous-next-v3/reference/native-runtime-cache-v1', originals)
    output.mkdir(parents=True)
    runtime = output / 'runtime'
    for relative, raw in originals.items():
        if relative in REFERENCE_NAMES:
            continue
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    save(runtime / BINDING_NAME, binding)
    transformed = {}
    for relative in ('src/recommend_book/classification/runner.py', 'src/recommend_book/classification/native_attempt.py', 'src/recommend_book/usage_guard.py', 'src/recommend_book/next_database.py'):
        target = relative[:-3] + '_v4.py'
        value = transform(relative, originals[relative])
        (runtime / target).write_bytes(value)
        transformed[target] = {'source':relative,'original_sha256':sha(originals[relative]),'sha256':sha(value)}
    script = originals['scripts/run_guarded_classification.py'].decode('utf-8')
    script = script.replace('classification.runner import ', 'classification.runner_v4 import ').replace('recommend_book.usage_guard import ', 'recommend_book.usage_guard_v4 import ')
    script = replace(script, '    for relative,expected in artifacts.items():\n',
        '    from recommend_book.classification.native_reference_store_v1 import reference_artifact_path\n'
        '    for relative,expected in artifacts.items():\n')
    script = replace(script, '        source_artifact=ROOT/relative\n',
        '        source_artifact=reference_artifact_path(ROOT, relative)\n')
    script = replace(script, "'src/recommend_book/classification/runner.py':config['runner_hash']", "'src/recommend_book/classification/runner_v4.py':config['runner_hash']")
    script = replace(script, "'src/recommend_book/usage_guard.py':config['usage_guard_hash']", "'src/recommend_book/usage_guard_v4.py':config['usage_guard_hash']")
    (runtime / 'scripts/run_guarded_classification_v4.py').write_text(script, encoding='utf-8')
    save(output / 'source-snapshot-manifest.json', {'version':'native-snapshot-runtime-v1','runtime_root':str(runtime),
        'copied_original_sha256':{name:sha(raw) for name,raw in originals.items() if name not in REFERENCE_NAMES},'transformed':transformed,
        'reference_storage':{'version':'native-reference-storage-v1','binding_sha256':sha((runtime / BINDING_NAME).read_bytes()),
            'artifacts':binding['artifacts'],'cache_new_content_bytes':cache_new_bytes,'runtime_reference_content_bytes':0,
            'protection':'independent cache inodes; read-only accidental-write protection; verify exact bytes on use; never repair in place'},
        'data_db_raw_body_model_executable_copied':False})
    verify_source(output)
    return {'stage':'runtime_built_not_started','runtime_root':str(runtime),'transformed_files':len(transformed)}


def verify_source(output):
    output = Path(output).resolve()
    report = json.loads((output / 'source-snapshot-manifest.json').read_bytes())
    runtime = Path(report['runtime_root'])
    shared = report.get('reference_storage')
    if shared is not None:
        raw = (runtime / BINDING_NAME).read_bytes()
        if sha(raw) != shared['binding_sha256']:
            raise ValueError('Shared reference binding drift')
        binding = json.loads(raw)
        if binding['artifacts'] != shared['artifacts']:
            raise ValueError('Shared reference manifest drift')
        verify_binding(binding)
    original_receipts = {**report['copied_original_sha256'],
        **({name:item['sha256'] for name,item in shared['artifacts'].items()} if shared else {})}
    for name, expected in original_receipts.items():
        target = Path(shared['artifacts'][name]['path']) if shared and name in shared['artifacts'] else runtime / name
        if sha((ROOT / name).read_bytes()) != expected or sha(target.read_bytes()) != expected:
            raise ValueError('Original source/copy drift: ' + name)
    for name, binding in report['transformed'].items():
        if sha((runtime / name).read_bytes()) != binding['sha256']:
            raise ValueError('Version-four source drift: ' + name)
    return report


def positive_int(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise argparse.ArgumentTypeError('A positive integer is required')
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError('A positive integer is required') from error
    if result <= 0:
        raise argparse.ArgumentTypeError('A positive integer is required')
    return result


def preflight(output, inputs, ledger_path, cli_version, execution_spec, run_name, max_observed_tokens=500000):
    """Called in a fresh interpreter with the frozen runtime on PYTHONPATH."""
    max_observed_tokens = positive_int(max_observed_tokens)
    import importlib.util
    from recommend_book.classification.runner_v4 import build_run_config, resolve_executable, command_artifact_hashes
    from recommend_book.classification.validation import load_taxonomy
    from recommend_book.classification import native_attempt_v4
    from recommend_book.jobs import canonical_json
    from recommend_book import next_database_v4
    from recommend_book.usage_guard_v4 import UsageGuard
    output = Path(output).resolve()
    runtime = output / 'runtime'
    if not Path(native_attempt_v4.__file__).resolve().is_relative_to(runtime):
        raise ValueError('Fresh interpreter did not import the frozen version-four runtime')
    config = build_run_config('gpt-6.1-sol', cli_version, load_taxonomy(), 5, execution_spec=execution_spec)
    config_sha = sha(canonical_json(config).encode('utf-8'))
    source_ledger = Path(ledger_path).resolve(strict=True)
    original = json.loads(source_ledger.read_bytes())
    ledger = json.loads(source_ledger.read_bytes())
    verifier = Path(native_attempt_v4.__file__).resolve()
    ledger.setdefault('execution_recipes', {})[config_sha] = {'verifier_path':str(verifier),'verifier_sha256':sha(verifier.read_bytes())}
    implementation = Path(next_database_v4.__file__).resolve()
    ledger.setdefault('feature_recipes', {})['work-present-relevance-max-v1'] = {'path':str(implementation),'sha256':sha(implementation.read_bytes())}
    if {k:v for k,v in ledger.items() if k not in ('execution_recipes','feature_recipes')} != {k:v for k,v in original.items() if k not in ('execution_recipes','feature_recipes')}:
        raise ValueError('Purpose/input ledger material changed during recipe preparation')
    frozen_ledger = output / 'usage-ledger.v4.json'
    if frozen_ledger.exists():
        if json.loads(frozen_ledger.read_bytes()) != ledger:
            raise ValueError('Existing future ledger/config differs; use a new runtime namespace')
    else:
        save(frozen_ledger, ledger)
    module_spec = importlib.util.spec_from_file_location('new_guarded_preparation', runtime / 'scripts/run_guarded_classification_v4.py')
    helper = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(helper)
    if not run_name or Path(run_name).name != run_name or run_name in ('.','..') or '/' in run_name or '\\' in run_name:
        raise ValueError('One safe run-name component is required')
    namespace = runtime / 'data/autonomous-next-v3/classification' / run_name
    selected = output / ('selected-inputs-' + run_name)
    selected.mkdir()
    input_raw = Path(inputs).read_bytes()
    if not 1 <= len([line for line in input_raw.splitlines() if line.strip()]) <= 100:
        raise ValueError('Future pilot is bounded to 1..100 fixed inputs')
    (selected / 'ordered.jsonl').write_bytes(input_raw)
    save(selected / 'manifest.json', {'version':'new-world-pilot-input-snapshot-v1','ordered_inputs_sha256':sha(input_raw),
        'source_path':str(Path(inputs).resolve()),'work_expression_count':len([line for line in input_raw.splitlines() if line.strip()])})
    result = helper.prepare(selected / 'ordered.jsonl', frozen_ledger, namespace, cli_version=cli_version, execution_spec=execution_spec,
        batch_size=5, concurrency=2, timeout=900, max_run_seconds=1800, max_observed_tokens=max_observed_tokens)
    freeze_spec = importlib.util.spec_from_file_location('new_execution_freeze', runtime / 'scripts/freeze_classification_execution.py')
    freeze = importlib.util.module_from_spec(freeze_spec)
    freeze_spec.loader.exec_module(freeze)
    frozen_spec = execution_spec
    def frozen_config(model, version, taxonomy, batch_size, *, execution_spec=None):
        return build_run_config(model, version, taxonomy, batch_size, execution_spec=frozen_spec)
    freeze.build_run_config = frozen_config
    context = output / ('execution-context-' + run_name)
    freeze.freeze_execution(selected / 'ordered.jsonl',context,model='gpt-6.1-sol',cli_version=cli_version,
        batch_size=5,concurrency=2,timeout=900,max_run_seconds=1800,max_observed_tokens=max_observed_tokens,
        stop_on_rate_limit=True,execution_spec=execution_spec)
    command = resolve_executable()
    report = {'version':'native-snapshot-preflight-v1','stage':'prepared_not_started','namespace':str(namespace),
        'preparation_sha256':sha((namespace / 'preparation-manifest.json').read_bytes()),'config':config,'config_sha256':config_sha,
        'usage_ledger_path':str(frozen_ledger),'usage_ledger_sha256':sha(frozen_ledger.read_bytes()),
        'source_ledger_path':str(source_ledger),'source_ledger_sha256':sha(source_ledger.read_bytes()),
        'ordered_inputs_path':str(selected / 'ordered.jsonl'),'execution_context_manifest':str(context / 'manifest.json'),
        'executable_command':command,'executable_sha256':command_artifact_hashes(command),
        'model_invocations':0,'guard_version':'usage-guard-v4.0','old_recipes_rebound':False}
    save(output / ('preflight.' + run_name + '.json'), report)
    return {'stage':report['stage'],'selected':result['selected'],'namespace':str(namespace),'preparation_sha256':report['preparation_sha256'],'config_sha256':config_sha}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build','verify','preflight','_preflight'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--usage-ledger', type=Path)
    parser.add_argument('--cli-version')
    parser.add_argument('--execution-spec', default='guarded-v3-baseline')
    parser.add_argument('--run-name', default='first10')
    parser.add_argument('--max-observed-tokens', type=positive_int, default=500000)
    args = parser.parse_args()
    if args.command == 'build':
        result = build(args.output)
    elif args.command == 'verify':
        result = verify_source(args.output)
    elif args.command == 'preflight':
        verify_source(args.output)
        env = dict(os.environ, PYTHONPATH=str(args.output.resolve() / 'runtime/src'), PYTHONDONTWRITEBYTECODE='1')
        command = [sys.executable,str(Path(__file__).resolve()),'_preflight','--output',str(args.output.resolve()),
            '--inputs',str(args.inputs.resolve()),'--usage-ledger',str(args.usage_ledger.resolve()),'--cli-version',args.cli_version,'--execution-spec',args.execution_spec,'--run-name',args.run_name,
            '--max-observed-tokens',str(args.max_observed_tokens)]
        process = subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=False,shell=False,timeout=300)
        (args.output / ('preflight.' + args.run_name + '.stdout.txt')).write_bytes(process.stdout)
        (args.output / ('preflight.' + args.run_name + '.stderr.txt')).write_bytes(process.stderr)
        if process.returncode:
            raise ValueError('Frozen-runtime preflight failed; inspect preflight.stderr.txt')
        print(process.stdout.decode('utf-8'))
        return
    else:
        result = preflight(args.output,args.inputs,args.usage_ledger,args.cli_version,args.execution_spec,args.run_name,args.max_observed_tokens)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
