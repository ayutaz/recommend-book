"""Read-only verifier for an actual native classification attempt, never a CLI."""
import hashlib
import json
from pathlib import Path

from recommend_book.jobs import canonical_json
from .validation import input_hash, load_taxonomy


def _sha(binary):
    return hashlib.sha256(binary).hexdigest()


def verify_native_attempt(source_manifest, payload, originating_input_hashes, *, expected_config_sha256, works):
    """Reproduce the complete original batch before permitting a result projection.

    The caller supplies independently authorized whole inputs and a preapproved
    config hash. Merely asserting success in a descriptor cannot authorize it.
    This establishes machine protocol/grounding lineage, not human correctness.
    """
    from .runner import bind_final_message, build_run_config, command_artifact_hashes
    from .specs import get_spec
    descriptor = source_manifest
    if descriptor.get('kind') != 'classification-native-attempt-v3':
        raise ValueError('Unrecognized native result lineage')
    run_dir=Path(descriptor['run_dir']).resolve(strict=True)
    if not Path(descriptor['run_dir']).is_absolute():raise ValueError('Native run namespace must be absolute')
    stem=Path(descriptor['attempt_stem'])
    if stem.is_absolute() or stem.parent!=Path('attempts'):
        raise ValueError('Native attempt stem escapes namespace')
    expected_suffixes={'final':'.final.json','prompt':'.stdin.utf8','request':'.request.json',
        'attempt':'.protocol-attempt.json','stdout':'.stdout.jsonl','stderr':'.stderr.txt'}
    expected_paths={name:run_dir/Path(str(stem)+suffix)for name,suffix in expected_suffixes.items()}
    expected_paths['admission']=run_dir/f"run.{descriptor['run_id']}.admission.json"
    origins = [input_hash(w) for w in works]
    if len(set(origins)) != len(origins) or not origins or origins != list(originating_input_hashes) or origins != descriptor['originating_input_hashes']:
        raise ValueError('Native complete batch origins differ')
    binary = {}
    for name in ('final','prompt','request','attempt','stdout','stderr','admission'):
        artifact = descriptor['artifacts'][name]
        path=Path(artifact['path']).resolve(strict=True)
        if not path.is_relative_to(run_dir) or path!=expected_paths[name].resolve():
            raise ValueError('Native artifact is outside original run namespace/attempt: '+name)
        binary[name] = path.read_bytes()
        if _sha(binary[name]) != artifact['sha256']:
            raise ValueError('Native artifact SHA differs: ' + name)
    request,attempt = json.loads(binary['request']),json.loads(binary['attempt'])
    admission=json.loads(binary['admission'])
    if request['run_id']!=descriptor['run_id'] or request['run_dir']!=str(run_dir) or request['attempt_stem']!=str(stem):
        raise ValueError('Native request differs from original run/attempt')
    if admission['run_id']!=descriptor['run_id'] or admission['run_dir']!=str(run_dir) or admission['config']!=request['config']:
        raise ValueError('Native original admission differs from run')
    if admission['usage_ledger_sha256']!=descriptor['usage_ledger_sha256']:
        raise ValueError('Native admission frozen ledger differs')
    config = request['config']
    config_sha = _sha(canonical_json(config).encode('utf-8'))
    if config_sha != expected_config_sha256 or descriptor['config_sha256'] != config_sha or request['config_sha256'] != config_sha:
        raise ValueError('Native execution recipe differs')
    if config['execution_spec'] != descriptor['execution_spec']:
        raise ValueError('Native execution spec differs')
    taxonomy = load_taxonomy()
    if build_run_config(config['model'],config['cli_version'],taxonomy,config['batch_size'],execution_spec=config['execution_spec']) != config:
        raise ValueError('Current execution artifacts differ from native recipe')
    if request['input_hashes'] != origins or request['shell'] is not False or request['text'] is not False:
        raise ValueError('Native invocation controls or complete batch differ')
    from recommend_book.jobs import job_key
    if request['job_keys']!=[job_key(key,config)for key in origins]:
        raise ValueError('Native complete batch job keys differ')
    if not set(origins).issubset({i['input_hash']for i in admission['input_identities']}):
        raise ValueError('Native batch origins are absent from original admission')
    if attempt['actual_exit_code'] != 0 or attempt['actual_cli_started'] is not True or attempt['error_category'] is not None:
        raise ValueError('Native attempt did not succeed')
    if attempt['run_id'] != request['run_id'] or attempt['job_keys'] != request['job_keys']:
        raise ValueError('Native attempt/request identity differs')
    version = request['version_observation']
    if version['exit_code'] != 0 or version['stdout_utf8'].strip() != config['cli_version']:
        raise ValueError('Native executable version differs')
    command = version['argv'][:-1]
    if version['argv'][-1] != '--version' or command_artifact_hashes(command) != request['executable_sha256']:
        raise ValueError('Native executable artifact differs')
    spec = get_spec(descriptor['execution_spec'])
    expected_prompt = spec.expected_prompt(works,model=config['model'],cli_version=config['cli_version'],taxonomy=taxonomy).encode('utf-8')
    if binary['prompt'] != expected_prompt or request['expected_prompt_sha256'] != _sha(expected_prompt) or request['stdin_sha256'] != _sha(binary['prompt']):
        raise ValueError('Native full prompt recipe differs')
    receipt = request['prompt_usage_receipt']
    if descriptor.get('usage_ledger_sha256') != request['usage_ledger_sha256']:
        raise ValueError('Native descriptor frozen ledger differs')
    if receipt['allowed'] is not True or receipt['input_hashes'] != origins or receipt['prompt_sha256'] != _sha(binary['prompt']) or receipt['ledger_sha256'] != request['usage_ledger_sha256']:
        raise ValueError('Native prompt authorization receipt differs')
    argv = request['argv']
    schema_path = argv[argv.index('--output-schema')+1]
    output_path = argv[argv.index('-o')+1]
    workspace = argv[argv.index('--cd')+1]
    expected_argv = [*command,'exec','--model',config['model'],'--sandbox','read-only','--ephemeral',
        '--ignore-user-config','--skip-git-repo-check','--json','--color','never',
        '-c','model_reasoning_effort="medium"','-c','web_search="disabled"','-c','features.multi_agent=false',
        '--cd',workspace,'--output-schema',schema_path,'-o',output_path,'-']
    if argv != expected_argv or Path(output_path).resolve() != Path(descriptor['artifacts']['final']['path']).resolve():
        raise ValueError('Native argv differs from fixed invocation recipe')
    actual_schema = json.loads(Path(schema_path).read_bytes())
    single_schema = json.loads(spec.schema_path.read_bytes())
    expected_schema = single_schema if len(works)==1 else {'type':'object','additionalProperties':False,'required':['results'],
        'properties':{'results':{'type':'array','items':single_schema}}}
    if actual_schema != expected_schema:
        raise ValueError('Native output schema differs')
    for name,key in (('prompt','stdin_sha256'),('stdout','stdout_sha256'),('stderr','stderr_sha256'),('final','final_sha256')):
        if attempt[key] != _sha(binary[name]):
            raise ValueError('Native attempt artifact binding differs: '+name)
    binding = bind_final_message(binary['stdout'],binary['final'])
    if binding['events']['tool_calls'] or binding['events']['event_errors'] or attempt['final_value_sha256'] != binding['final_value_sha256']:
        raise ValueError('Native tools/errors or final binding differ')
    final = binding['value']
    results = final['results'] if len(works)>1 else [final]
    expected_ids = {(w.work_id,w.expression_id):w for w in works}
    if not isinstance(results,list) or len(results)!=len(works) or len({(r['work_id'],r['expression_id']) for r in results})!=len(works):
        raise ValueError('Native full batch identity shape differs')
    for result in results:
        identity = (result['work_id'],result['expression_id'])
        if identity not in expected_ids:
            raise ValueError('Native full batch has foreign identity')
        spec.validate_result(result,expected_ids[identity],taxonomy)
        if result['model']!=config['model'] or result['cli_version']!=config['cli_version']:
            raise ValueError('Native result model/CLI differs')
    allowed_values = [final,*results]
    if canonical_json(payload) not in [canonical_json(v) for v in allowed_values]:
        raise ValueError('Derived payload is not an exact native final/result value')
    return {'allowed':True,'actual_exit_code':0,'input_hashes':origins,'config_sha256':config_sha,
        'payload_sha256':_sha(canonical_json(payload).encode('utf-8')),
        'final_value_sha256':binding['final_value_sha256'],'proof_hashes':[_sha(v) for v in binary.values()]}
