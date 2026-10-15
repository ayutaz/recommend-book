"""Fresh frozen-runtime integration fixture. No Codex/model/network call occurs."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone

sys.path.append(str(Path(__file__).resolve().parent))
from test_classification_guarded import allowed_many_fixture
from recommend_book.classification import runner_v4, native_attempt_v4
from recommend_book.classification.validation import input_hash, load_taxonomy
from recommend_book.jobs import canonical_json
from recommend_book import next_database_v4 as database
from recommend_book.usage_guard_v4 import UsageGuard, build_input_entry, source_binding, model_hash


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(canonical_json(value)+'\n',encoding='utf-8')


def run(folder):
    history = folder/'history'
    history.mkdir()
    _,ledger,_,payload = allowed_many_fixture(history,2)
    baseline = folder/'baseline'
    database.build_next_database(source_database=history/'source-many.sqlite',output_dir=baseline)
    works = database.get_next_inputs(baseline/'dataset.sqlite')['classification']
    assert len(works)==2
    payload['inputs']={model_hash(w):build_input_entry(w,{ref:source_binding(baseline/'dataset.sqlite',ref) for ref in w.source_record_ids}) for w in works}
    config=runner_v4.build_run_config('gpt-6.1-sol','fake-1',load_taxonomy(),5,execution_spec='guarded-v3-baseline')
    config_sha=hashlib.sha256(canonical_json(config).encode()).hexdigest()
    verifier=Path(native_attempt_v4.__file__).resolve()
    payload['execution_recipes'][config_sha]={'verifier_path':str(verifier),'verifier_sha256':sha(verifier)}
    save(ledger,payload)
    tool=folder/'fixture_codex_protocol.py'
    tool.write_text('''import json,sys
from pathlib import Path
from recommend_book.models import WorkInput
sys.path.append('''+repr(str(Path(__file__).resolve().parent))+''')
from test_classification_guarded import unknown_result
if '--version' in sys.argv:
 print('fake-1');sys.exit(0)
data=json.loads(sys.stdin.buffer.read().decode().split('BEGIN_LOCAL_DATA_JSON\\n',1)[1].rsplit('\\nEND_LOCAL_DATA_JSON',1)[0])
entries=data['works'] if 'works' in data else [data]
results=[unknown_result(WorkInput.model_validate(entry['work'])) for entry in entries]
value={'results':results} if 'works' in data else results[0]
Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(value),encoding='utf-8')
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(value)}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':12}}))
''',encoding='utf-8')
    command=[sys.executable,str(tool)]
    # Explicit fixture command substitution; the Python child really starts,
    # captures exact stdin/output, records PID and exit, and never calls a model.
    runner_v4.resolve_executable=lambda executable=None:command
    inputs=folder/'inputs/ordered.jsonl'
    inputs.parent.mkdir()
    inputs.write_text(''.join(canonical_json(w.model_dump(mode='json'))+'\n' for w in works),encoding='utf-8')
    save(inputs.with_name('manifest.json'),{'ordered_inputs_sha256':sha(inputs)})
    import importlib.util
    root=Path(runner_v4.__file__).resolve().parents[3]
    spec=importlib.util.spec_from_file_location('fixture_freeze',root/'scripts/freeze_classification_execution.py')
    freeze=importlib.util.module_from_spec(spec);spec.loader.exec_module(freeze)
    freeze.build_run_config=lambda *args:config
    context=folder/'context/manifest.json'
    freeze.freeze_execution(inputs,context.parent,model='gpt-6.1-sol',cli_version='fake-1',batch_size=5)
    run_dir=folder/'run'
    initial=runner_v4.run_classification(works,run_dir,usage_ledger=ledger,batch_size=5)
    assert initial['completed']==2,initial
    assert initial['cli_invocations']==1
    resumed=runner_v4.run_classification(works,run_dir,usage_ledger=ledger,batch_size=5)
    assert resumed['completed']==2 and resumed['cli_invocations']==0 and resumed['reused']==2,resumed
    migration=folder/'migration/manifest.json';save(migration,{'version':'fixture-new-only-no-rebind','llm_started':False})
    proof=folder/'proof/terminal.json'
    save(proof,{'schema_version':'classification-terminal-proof-v1','observed_at':datetime.now(timezone.utc).isoformat(),
        'session_id':'fixture-real-python-process-no-model','exit_code':0,'process_exit_observed':True,'run_id':resumed['run_id'],
        'manifest_sha256':sha(run_dir/'manifest.json'),'results_sha256':sha(run_dir/'results.jsonl'),
        'ordered_inputs_sha256':sha(inputs),'execution_context_manifest_sha256':sha(context),'migration_manifest_sha256':sha(migration),
        'cli_profile_sha256_before':sha(context),'cli_profile_sha256_after':sha(context),'artifact_sha256':{'database':sha(baseline/'dataset.sqlite')}})
    args=dict(source_database=baseline/'dataset.sqlite',ordered_inputs=inputs,run_dir=run_dir,
        execution_context_manifest=context,migration_manifest=migration,terminal_proof=proof,cli_profile=context,
        output_dir=folder/'adopted',guard=UsageGuard(ledger))
    report=database.import_next_classifications(**args)
    assert report['completed']==2
    try:
        database.import_next_classifications(**args)
    except ValueError:
        pass
    else:
        raise AssertionError('Double adoption was accepted')
    before=tool.read_bytes()
    database._before_next_publish=lambda:tool.write_bytes(before+b'\n# publication drift\n')
    args['output_dir']=folder/'must-not-publish'
    try:
        database.import_next_classifications(**args)
    except ValueError as error:
        assert 'changed' in str(error),error
    else:
        raise AssertionError('Changed executable was adopted')
    finally:
        tool.write_bytes(before)
    assert not args['output_dir'].exists()
    print(json.dumps({'fixture_only':True,'actual_model_invocations':0,'completed':2,'resumed_without_child_calls':2,
        'duplicate_adoption_refused':True,'closing_drift_refused':True,'copied_import_helper_exercised':True}))


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='native-snapshot-fixture-') as temporary:
        run(Path(temporary))
        # The historical fixture keeps SQLite connections alive until GC.
        import gc
        gc.collect()
