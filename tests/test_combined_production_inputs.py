"""Synthetic byte/order fixtures; no human annotation gold."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from recommend_book.classification.validation import input_hash
from recommend_book.models import WorkInput


def script():
    spec=importlib.util.spec_from_file_location('prepare_combined_production_inputs',Path(__file__).parents[1]/'scripts/prepare_combined_production_inputs.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def fixture(tmp_path):
    ordered=tmp_path/'ordered';combined=tmp_path/'combined';frozen=tmp_path/'frozen';repo=tmp_path/'repo'
    for path in (ordered,combined,frozen,repo/'config'):path.mkdir(parents=True)
    context_bytes=b'fixed config\n';(repo/'config/sources.json').write_bytes(context_bytes);(frozen/'config-sources.json').write_bytes(context_bytes)
    context={'config/sources.json':{'snapshot':'config-sources.json','sha256':hashlib.sha256(context_bytes).hexdigest()}}
    (frozen/'processing-context.json').write_text(json.dumps(context),encoding='utf8')
    main=[WorkInput(work_id='work_z',title='Japanese 日本語',authors=['Shared Author'],source_record_ids=['src_z']),WorkInput(work_id='work_a',title='Second',source_record_ids=['src_a'])]
    extra=WorkInput(work_id='work_extra',title='Prose original',authors=['Shared Author'],original_language='en',source_record_ids=['src_extra','src_authority'])
    raw=b''.join(json.dumps(w.model_dump(mode='json'),ensure_ascii=False,indent=None).encode('utf8')+b'\r\n' for w in main)
    (ordered/'ordered-inputs.jsonl').write_bytes(raw)
    (frozen/'work-inputs.jsonl').write_bytes(raw)
    (frozen/'manifest.json').write_text(json.dumps({'work_count':2,'work_inputs_sha256':hashlib.sha256(raw).hexdigest()}))
    (ordered/'manifest.json').write_text(json.dumps({'work_count':2,'ordered_inputs_sha256':hashlib.sha256(raw).hexdigest(),'source_sha256':hashlib.sha256(raw).hexdigest(),'processing_context':context,'stage_size':1}))
    tail=extra.model_dump_json().encode()+b'\n';(combined/'prose-work-inputs.jsonl').write_bytes(tail)
    (combined/'work-inputs.jsonl').write_text(''.join(w.model_dump_json()+'\n' for w in [extra,*reversed(main)]),encoding='utf8')
    cross={'old_style_work_id':'ws:en:1','combined_canonical_work_id':extra.work_id,'combined_input_hash':input_hash(extra),'combined_active_source_version_id':'src_extra','original_source_version_preserved':True,'old_classification_valid_for_new_input':False,'human_verified':False,'independent_identity_confirmation':False}
    (combined/'prose-id-crosswalk.json').write_text(json.dumps([cross]))
    refresh_combined(combined)
    return ordered,combined,frozen,repo


def refresh_combined(combined):
    (combined/'manifest.json').write_text(json.dumps({'canonical_works':3,'output_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in combined.iterdir() if p.name!='manifest.json'}}))


def prepare(paths,output):
    ordered,combined,frozen,repo=paths
    return script().prepare(ordered,combined,frozen,output,repo=repo,review_pack=None,expected_main=2,expected_prose=1)


def test_raw_main_prefix_order_and_prose_tail_are_exact_and_scopes_are_separate(tmp_path):
    paths=fixture(tmp_path);ordered,combined,frozen,repo=paths
    before={p:p.read_bytes() for root in paths for p in root.rglob('*') if p.is_file()}
    output=tmp_path/'new';report=prepare(paths,output)
    base=(ordered/'ordered-inputs.jsonl').read_bytes();tail=(combined/'prose-work-inputs.jsonl').read_bytes()
    assert (output/'ordered-inputs.jsonl').read_bytes()==base+tail
    assert report['main_rows_preserved']==2 and report['appended_canonical_prose_rows']==1
    assert report['stage_one_prefix_sha256']==hashlib.sha256(base.splitlines(keepends=True)[0]).hexdigest()
    assert report['classification_rebound'] is False and report['llm_started'] is False
    assert report['execution_context_created'] is False
    assert report['execution_context_auto_updated'] is False
    scope=json.loads((output/'supplement-evaluation-scope.json').read_text())
    assert scope['independent_holdout'] is False and scope['main_review_partitions_changed'] is False
    assert scope['observed_display_author_overlap']==['shared author']
    assert all(p.read_bytes()==raw for p,raw in before.items())


@pytest.mark.parametrize('mutation,reason',[('duplicate','duplicate'),('legacy','legacy ws:'),('hash','crosswalk input hash'),('refs','source-version refs')])
def test_duplicate_legacy_or_crosswalk_identity_drift_is_rejected(tmp_path,mutation,reason):
    paths=fixture(tmp_path);ordered,combined,frozen,repo=paths
    rows=[json.loads(line) for line in (combined/'work-inputs.jsonl').read_text(encoding='utf8').splitlines()]
    extra=rows[0];cross=json.loads((combined/'prose-id-crosswalk.json').read_text())
    if mutation=='duplicate':extra['work_id']='work_z';cross[0]['combined_canonical_work_id']='work_z'
    elif mutation=='legacy':extra['work_id']='ws:en:1';cross[0]['combined_canonical_work_id']='ws:en:1'
    elif mutation=='hash':cross[0]['combined_input_hash']='0'*64
    elif mutation=='refs':cross[0]['combined_active_source_version_id']='src_wrong'
    if mutation in ('duplicate','legacy'):
        cross[0]['combined_input_hash']=input_hash(WorkInput.model_validate(extra))
        (combined/'prose-work-inputs.jsonl').write_text(json.dumps(extra)+'\n')
        (combined/'work-inputs.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    (combined/'prose-id-crosswalk.json').write_text(json.dumps(cross));refresh_combined(combined)
    with pytest.raises(ValueError,match=reason):prepare(paths,tmp_path/'new')
    assert not (tmp_path/'new').exists()


def test_context_and_combined_durable_hash_drift_are_rejected(tmp_path):
    paths=fixture(tmp_path);ordered,combined,frozen,repo=paths
    (repo/'config/sources.json').write_bytes(b'changed')
    with pytest.raises(ValueError,match='context drift'):prepare(paths,tmp_path/'new')
    (repo/'config/sources.json').write_bytes((frozen/'config-sources.json').read_bytes())
    (combined/'prose-work-inputs.jsonl').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='combined durable output SHA'):prepare(paths,tmp_path/'new')


def test_output_must_be_new_and_outside_input_trees(tmp_path):
    paths=fixture(tmp_path);existing=tmp_path/'existing';existing.mkdir();(existing/'keep').write_text('unchanged')
    with pytest.raises(ValueError,match='must be new'):prepare(paths,existing)
    for root in paths[:3]:
        with pytest.raises(ValueError,match='input tree'):prepare(paths,root/'new')
    assert (existing/'keep').read_text()=='unchanged'


def test_unterminated_main_last_row_is_rejected_without_rewriting_its_bytes(tmp_path):
    paths=fixture(tmp_path);ordered=paths[0];path=ordered/'ordered-inputs.jsonl';raw=path.read_bytes().rstrip(b'\r\n');path.write_bytes(raw)
    manifest=json.loads((ordered/'manifest.json').read_text());manifest['ordered_inputs_sha256']=hashlib.sha256(raw).hexdigest();(ordered/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='newline-terminated'):prepare(paths,tmp_path/'new')
    assert path.read_bytes()==raw
