"""Build an isolated small source runtime, preflight only; never invoke a model.

Original specs/runner/guard/models remain byte-identical. Fresh processes import
runtime/src with the original cwd so unchanged corpus provenance stays in its
original workspace. No data DB, source body/raw or model/executable is copied.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC_NAME = 'native-french-genre-v1'
EXPECTED_INPUT_SHA = '68fd322d3df1226ed981100f27602d0fa8e65e9ab3a7686c3408a6a860483e7a'
BASE_HASHES = {
    'src/recommend_book/classification/specs.py': '3d636e6df6238dabc8fedf863ca450e59fd3a67e00ca58b3e98051d364e3f92d',
    'src/recommend_book/classification/runner.py': '7159af45334bda86a12d36b7733a64907b17d584b933c402d3d5cbf4c0a3e1b4',
}
PRIMARY = {
    'cligs_readme': ('data/autonomous-next-v3/reference/external-genre/raw/c0a32ba2d951f05775d1c2dbaa9992c7d6b98f2dbeffd028c80bd9679d2fbc21', 'c0a32ba2d951f05775d1c2dbaa9992c7d6b98f2dbeffd028c80bd9679d2fbc21'),
    'cligs_codebook': ('data/autonomous-next-v3/reference/external-genre/raw/6e5c1aa8e4fe5383f3855dde37a64f44681ed532bebe2d7ae3d1d4b9760922f9', '6e5c1aa8e4fe5383f3855dde37a64f44681ed532bebe2d7ae3d1d4b9760922f9'),
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    with Path(path).open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Runtime patch anchor must occur exactly once: ' + before[:70])
    return text.replace(before, after, 1)


def patch_source(relative, raw):
    if relative not in BASE_HASHES or sha(raw) != BASE_HASHES[relative]:
        raise ValueError('Frozen original patch base SHA differs: ' + relative)
    text = raw.decode('utf8').replace('\r\n', '\n')
    if relative.endswith('/specs.py'):
        replacements = [
            ('from . import native_v3\n', 'from . import native_v3\nfrom . import native_genre_v1\n'),
            ('SPEC_NAMES = ("guarded-v3-baseline", *EXPERIMENT_CANDIDATES, *NATIVE_SPECS)',
             'SPEC_NAMES = ("guarded-v3-baseline", *EXPERIMENT_CANDIDATES, *NATIVE_SPECS, native_genre_v1.SPEC_NAME)'),
            ('_NATIVE_BATCH = native_v3.build_batch_prompt', '_NATIVE_BATCH = native_v3.build_batch_prompt\n_FRENCH_SINGLE = native_genre_v1.build_prompt\n_FRENCH_BATCH = native_genre_v1.build_batch_prompt'),
            ('        kwargs["profile"] = self.name\n', '        kwargs["profile"] = self.name\n        if self.name == native_genre_v1.SPEC_NAME:\n            return _FRENCH_BATCH(works, **kwargs) if len(works) > 1 else _FRENCH_SINGLE(works[0], **kwargs)\n'),
            ('        if self.name in NATIVE_SPECS:\n            return native_v3.build_prompt',
             '        if self.name == native_genre_v1.SPEC_NAME:\n            return native_genre_v1.build_prompt(work,profile=self.name,**kwargs)\n        if self.name in NATIVE_SPECS:\n            return native_v3.build_prompt'),
            ('        if self.name in NATIVE_SPECS:\n            return native_v3.build_batch_prompt',
             '        if self.name == native_genre_v1.SPEC_NAME:\n            return native_genre_v1.build_batch_prompt(works,profile=self.name,**kwargs)\n        if self.name in NATIVE_SPECS:\n            return native_v3.build_batch_prompt'),
            ('        if self.name in NATIVE_SPECS:\n            return native_v3.validate_native',
             '        if self.name == native_genre_v1.SPEC_NAME:\n            return native_genre_v1.validate_native(result,work)\n        if self.name in NATIVE_SPECS:\n            return native_v3.validate_native'),
            ('    baseline = name == "guarded-v3-baseline"',
             '    if name == native_genre_v1.SPEC_NAME:\n        return ExecutionSpec(name,native_genre_v1.PROMPT_VERSION,\n            root / "src/recommend_book/classification/native_genre_v1.py",root / "schemas/native-french-genre-v1.schema.json")\n    baseline = name == "guarded-v3-baseline"'),
        ]
    else:
        before = "        if execution_spec.startswith('native-'):\n"
        after = '''        if execution_spec == 'native-french-genre-v1':
            contract_path = ROOT / 'config/native-french-genre-source-contract-v1.json'
            contract = json.loads(contract_path.read_bytes())
            if contract['execution_spec'] != execution_spec or contract['version'] != 'native-french-genre-source-contract-v1':
                raise ValueError('French native source contract differs')
            config['native_codebook_sha256'] = {'config/native-french-genre-source-contract-v1.json':hashlib.sha256(contract_path.read_bytes()).hexdigest()}
            config['external_native_codebook_sha256'] = {}
            for path, expected in contract['original_source_sha256'].items():
                if not Path(path).is_absolute() or hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                    raise ValueError('French original source/codebook SHA differs')
                config['external_native_codebook_sha256'][path] = expected
            for path in ('src/recommend_book/classification/native_genre_v1.py','schemas/native-french-genre-v1.schema.json','scripts/prepare_native_french_runtime.py'):
                config['dependency_sha256'][path] = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        elif execution_spec.startswith('native-'):
'''
        replacements = [(before, after)]
    for before, after in replacements:
        text = replace_once(text, before, after)
    compile(text, relative, 'exec')
    return text.encode('utf8'), len(replacements)


def build(output):
    output = Path(output).resolve()
    allowed = ROOT / 'data/autonomous-next-v3/preparation'
    if output.exists() or not output.is_relative_to(allowed):
        raise ValueError('Fresh isolated preparation namespace required')
    original = {}
    paths = list((ROOT / 'src/recommend_book').rglob('*.py')) + list((ROOT / 'schemas').glob('*.json')) + list((ROOT / 'config').glob('*.json'))
    paths += [ROOT / name for name in ('scripts/run_guarded_classification.py', 'scripts/prepare_native_french_runtime.py',
        'pyproject.toml', 'uv.lock', 'docs/source-rights-review-scope-v3.md')]
    for path in sorted(set(paths)):
        original[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for relative, expected in BASE_HASHES.items():
        if sha(original[relative]) != expected:
            raise ValueError('Original frozen base changed before copy')
    protected = {name:sha(original[name]) for name in ('src/recommend_book/classification/specs.py',
        'src/recommend_book/classification/runner.py','src/recommend_book/classification/native_attempt.py',
        'src/recommend_book/usage_guard.py','src/recommend_book/models.py')}
    primary_base = ROOT / 'data/autonomous-next-v3/reference/external-genre'
    primary_manifest = primary_base / 'primary-artifacts.json'
    primary_rows = json.loads(primary_manifest.read_bytes())
    source_pins = {str(primary_manifest):sha(primary_manifest.read_bytes())}
    for role, (relative, expected) in PRIMARY.items():
        path = ROOT / relative
        matching = [row for row in primary_rows if row['role'] == role]
        if len(matching) != 1 or matching[0]['sha256'] != expected or sha(path.read_bytes()) != expected:
            raise ValueError('Verified primary collection/codebook binding differs')
        source_pins[str(path)] = expected
    # Hash binding only: reference assignments are not copied into this runtime.
    metadata = ROOT / 'data/reference/cligs-french-roman19-metadata.csv'
    source_pins[str(metadata)] = sha(metadata.read_bytes())
    contract = {'version':'native-french-genre-source-contract-v1','execution_spec':SPEC_NAME,
        'source_commit':'1b91e940475c8a7e866457a3953fed5edb0a29f1',
        'source_labels':['adventure','crime-fiction','education','fantastic'],
        'source_definition_limit':'Collection names/positive editorial assignments, not a full operational annotation manual; prompt explanations are explicit model conventions.',
        'original_source_sha256':source_pins,'reference_assignments_copied':False,
        'world72_mapping':False,'runtime_patch_recipe_sha256':sha(Path(__file__).read_bytes())}
    output.mkdir(parents=True)
    runtime = output / 'runtime'
    copied, patches = {}, {}
    for relative, raw in original.items():
        value = raw
        if relative in BASE_HASHES:
            value, count = patch_source(relative, raw)
            patches[relative] = {'original_sha256':sha(raw),'runtime_sha256':sha(value),'exact_once_patch_n':count}
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(value)
        copied[relative] = {'original_sha256':sha(raw),'runtime_sha256':sha(value),'bytes':len(value)}
    save(runtime / 'config/native-french-genre-source-contract-v1.json',contract)
    observation = ROOT / 'data/autonomous-next-v3/preparation/cli-observation-v1/observation.json'
    observed = json.loads(observation.read_bytes())
    if observed['exit_code'] != 0 or not observed['stdout_utf8'].strip():
        raise ValueError('Existing exact CLI identity observation unavailable')
    cli_version = observed['stdout_utf8'].strip()
    save(output / 'source-snapshot-manifest.json', {'version':'native-french-runtime-source-snapshot-v1',
        'copied':copied,'patched_runtime_only':patches,'protected_original_sha256':protected,
        'source_contract':contract,'cli_observation':{'path':str(observation),'sha256':sha(observation.read_bytes()),'cli_version':cli_version},
        'runtime_root':str(runtime),'guard_provenance_cwd':str(ROOT),
        'copied_source_bytes':sum(item['bytes'] for item in copied.values()),
        'data_db_raw_body_model_executable_copied':False})
    env = dict(os.environ)
    env['PYTHONPATH'] = str(runtime / 'src')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    command = [sys.executable,str(Path(__file__).resolve()),'_preflight','--output',str(output),'--cli-version',cli_version]
    completed = subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=False,shell=False,timeout=300)
    for name, payload in (('preflight.stdout.txt',completed.stdout),('preflight.stderr.txt',completed.stderr)):
        with (output / name).open('xb') as stream:
            stream.write(payload)
    save(output / 'preflight-process.json',{'argv':command,'exit_code':completed.returncode,'shell':False,
        'cwd':str(ROOT),'PYTHONPATH':env['PYTHONPATH'],'model_or_cli_invocations':0})
    if completed.returncode != 0:
        raise ValueError('Fresh runtime preflight failed; inspect preserved stderr')
    if any(sha((ROOT / name).read_bytes()) != expected for name,expected in protected.items()):
        raise ValueError('Protected original source changed during snapshot/preflight')
    print(json.dumps({'stage':'runtime_prepared_no_model_started','output':str(output),
        'runtime_root':str(runtime),'copied_source_bytes':sum(v['bytes'] for v in copied.values()),
        'original_core_unchanged':True,'preflight':str(output / 'preflight.json')}))


def preflight(output, cli_version):
    # This function is reached only in a fresh interpreter with runtime PYTHONPATH.
    from recommend_book.classification.runner import build_run_config
    from recommend_book.classification.specs import get_spec
    from recommend_book.classification.validation import load_taxonomy
    from recommend_book.classification import native_attempt, native_genre_v1
    from recommend_book.jobs import canonical_json
    from recommend_book.models import WorkInput
    from recommend_book.usage_guard import UsageGuard
    output = Path(output).resolve()
    runtime = output / 'runtime'
    if not Path(native_attempt.__file__).resolve().is_relative_to(runtime):
        raise ValueError('Fresh process failed to isolate runtime imports')
    original_ledger = ROOT / 'data/autonomous-next-v3/rights/french-admission-v2/usage-ledger.json'
    inputs = ROOT / 'data/autonomous-next-v3/rights/french-admission-v2/approved-inputs.jsonl'
    if sha(inputs.read_bytes()) != EXPECTED_INPUT_SHA:
        raise ValueError('Fixed five-input SHA differs')
    works = [WorkInput.model_validate_json(line) for line in inputs.read_bytes().splitlines() if line.strip()]
    if len(works) != 5:
        raise ValueError('Fixed five inputs required')
    config = build_run_config('gpt-6.1-sol',cli_version,load_taxonomy(),5,execution_spec=SPEC_NAME)
    config_sha = sha(canonical_json(config).encode('utf8'))
    old = json.loads(original_ledger.read_bytes())
    ledger = json.loads(original_ledger.read_bytes())
    recipes = ledger.setdefault('execution_recipes',{})
    if recipes:
        raise ValueError('Original French source ledger unexpectedly has execution recipes')
    recipes[config_sha] = {'verifier_path':str(Path(native_attempt.__file__).resolve()),
        'verifier_sha256':sha(Path(native_attempt.__file__).read_bytes())}
    if {k:v for k,v in ledger.items() if k!='execution_recipes'} != {k:v for k,v in old.items() if k!='execution_recipes'}:
        raise ValueError('Original source rights/material bindings altered')
    ledger_path = output / 'usage-ledger.json'
    save(ledger_path,ledger)
    spec = get_spec(SPEC_NAME)
    guard = UsageGuard(ledger_path,expected_ledger_sha256=sha(ledger_path.read_bytes()))
    material = guard.assert_inputs_allowed(works,'external_llm')
    execution = guard.assert_execution_allowed(config_sha,'external_llm')
    batch = spec.build_batch_prompt(works,model=config['model'],cli_version=cli_version,taxonomy=load_taxonomy()).encode('utf8')
    expected = spec.expected_prompt(works,model=config['model'],cli_version=cli_version,taxonomy=load_taxonomy()).encode('utf8')
    if batch != expected:
        raise ValueError('Independent full-batch prompt differs')
    receipt = guard.assert_prompt_allowed(batch,works,'external_llm',expected_prompt_sha256=sha(expected))
    with (output / 'batch.stdin.utf8').open('xb') as stream:
        stream.write(batch)
    save(output / 'batch.prompt-receipt.json',receipt.model_dump(mode='json'))
    # Standard entrypoint owns preparation, controls, argv/schema and later lineage.
    spec_import = importlib.util.spec_from_file_location('isolated_guarded_entrypoint',runtime / 'scripts/run_guarded_classification.py')
    guarded = importlib.util.module_from_spec(spec_import)
    spec_import.loader.exec_module(guarded)
    namespace = runtime / 'data/autonomous-next-v3/classification/phase3-french-native-pilot-v1'
    prepared = guarded.prepare(inputs,ledger_path,namespace,cli_version=cli_version,
        execution_spec=SPEC_NAME,model=config['model'],batch_size=5,concurrency=1,timeout=900,
        max_run_seconds=900,max_observed_tokens=100000)
    if guarded.verify_prepared(namespace)['config'] != config:
        raise ValueError('Standard guarded runtime preparation did not reproduce')
    originals = json.loads((output / 'source-snapshot-manifest.json').read_bytes())
    # Historical spec prompts must remain byte-identical despite added dispatch.
    original_spec = ROOT / 'src/recommend_book/classification/specs.py'
    if sha(original_spec.read_bytes()) != originals['protected_original_sha256']['src/recommend_book/classification/specs.py']:
        raise ValueError('Original specs mutated')
    report = {'version':'native-french-runtime-preflight-v1','stage':'prepared_not_started','selected':5,
        'config':config,'config_sha256':config_sha,'input_sha256':EXPECTED_INPUT_SHA,
        'source_ledger_path':str(original_ledger),'source_ledger_sha256':sha(original_ledger.read_bytes()),
        'usage_ledger_path':str(ledger_path),'usage_ledger_sha256':sha(ledger_path.read_bytes()),
        'runtime_native_verifier_path':str(Path(native_attempt.__file__).resolve()),
        'runtime_native_verifier_sha256':sha(Path(native_attempt.__file__).read_bytes()),
        'input_usage_receipts':[d.model_dump(mode='json') for d in material],
        'execution_usage_receipt':execution.model_dump(mode='json'),
        'batch_prompt_sha256':sha(batch),'batch_prompt_bytes':len(batch),
        'batch_prompt_receipt':receipt.model_dump(mode='json'),
        'standard_preparation_path':str(namespace / 'preparation-manifest.json'),
        'standard_preparation_sha256':sha((namespace / 'preparation-manifest.json').read_bytes()),
        'guard_provenance_cwd':str(ROOT),'runtime_root':str(runtime),'schema':str(spec.schema_path),
        'external_source_bindings':config['external_native_codebook_sha256'],
        'model_or_cli_invocations':0,'independent_root_go':False,
        'root_run_recipe':{'argv':[sys.executable,str(runtime / 'scripts/run_guarded_classification.py'),'run',
            '--namespace',str(namespace),'--preparation-sha256',sha((namespace / 'preparation-manifest.json').read_bytes())],
            'cwd':str(ROOT),'environment':{'PYTHONPATH':str(runtime / 'src'),'PYTHONDONTWRITEBYTECODE':'1'}},
        'limits':prepared['controls'],'quality_verified':False,'reference_answers_in_prompt':False,
        'source_definition_limit':'Native editor names only; model conventions are not an annotation manual.',
        'mechanical_grounding_is_not_semantic_validation':True}
    save(output / 'preflight.json',report)
    save(output / 'runtime-file-manifest.json',{'version':'native-french-runtime-files-v1','files':{
        str(path.relative_to(runtime)):{'sha256':sha(path.read_bytes()),'bytes':path.stat().st_size}
        for path in sorted(runtime.rglob('*')) if path.is_file() and '__pycache__' not in path.parts}})
    print(json.dumps({'stage':'prepared_not_started','selected':5,'config_sha256':config_sha,
        'batch_prompt_sha256':sha(batch),'standard_preparation_sha256':report['standard_preparation_sha256'],
        'model_or_cli_invocations':0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['build','_preflight'])
    parser.add_argument('--output',type=Path,default=ROOT / 'data/autonomous-next-v3/preparation/phase3-french-native-runtime-v1')
    parser.add_argument('--cli-version')
    args = parser.parse_args()
    build(args.output) if args.command == 'build' else preflight(args.output,args.cli_version)


if __name__ == '__main__':
    main()
