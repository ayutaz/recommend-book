"""Observe a completed frozen classification run without asserting human accuracy."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import math
from pathlib import Path
import re
import statistics

from recommend_book.classification.validation import ROOT, input_hash, load_taxonomy, validate_result
from recommend_book.jobs import canonical_json, job_key
from recommend_book.models import WorkInput


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _jsonl(content, name):
    rows=[]
    for number,line in enumerate(content.decode('utf-8-sig').splitlines(),1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f'{name}: invalid JSONL line {number}: {error.msg}') from error
    return rows


def _key(row):
    return row['work_id'],row.get('expression_id')


def _confidence(labels):
    values=sorted(label['raw_confidence'] for label in labels if label['decision']!='unknown' and label.get('raw_confidence') is not None)
    return {'n':len(values),'mean':statistics.mean(values) if values else None,
        'min':min(values) if values else None,'max':max(values) if values else None,
        'p50':statistics.median(values) if values else None,
        'p95':values[max(0,math.ceil(.95*len(values))-1)] if values else None,
        'counts_at_or_above':{str(level):sum(value>=level for value in values) for level in (.5,.7,.9,.95)},
        'meaning':'Self-reported raw confidence of emitted present/absent decisions; correctness and calibration are unmeasured.'}


def _normalize(value):
    return re.sub(r'\s+',' ',value).strip()


def _references(pairs, inputs):
    references=[]
    for key,label in pairs:
        permitted={item['evidence_id']:item for item in inputs[key]['evidence'] if item['llm_input_allowed']}
        for ref in label['evidence']:
            evidence=permitted[ref['evidence_id']]
            references.append((key,ref,evidence))
    return {'reference_count':len(references),
        'unique_input_evidence_count':len({(key,ref['evidence_id']) for key,ref,_ in references}),
        'evidence_kind_counts':dict(sorted(Counter(evidence['kind'] for _,_,evidence in references).items())),
        'rights_policy_id_counts':dict(sorted(Counter(evidence['rights_policy_id'] for _,_,evidence in references).items())),
        'directness_counts':dict(sorted(Counter(ref['directness'] for _,ref,_ in references).items())),
        'quote_count':sum(bool(ref['quote']) for _,ref,_ in references),
        'quote_matched_count':sum(bool(ref['quote']) and _normalize(ref['quote']) in _normalize(evidence['text']) for _,ref,evidence in references),
        'summary_count':sum(bool(ref['summary']) for _,ref,_ in references),
        'summary_only_count':sum(bool(ref['summary']) and not ref['quote'] for _,ref,_ in references),
        'quote_and_summary_count':sum(bool(ref['summary']) and bool(ref['quote']) for _,ref,_ in references),
        'structural_reference_status':'validated_against_fixed_allowed_input',
        'semantic_faithfulness':None}


def _stats(keys, input_index, result_index, taxonomy, axis=None):
    label_ids={label['label_id'] for label in taxonomy['labels'] if axis is None or label['axis']==axis}
    pairs=[(key,label) for key in keys for label in result_index[key]['labels'] if label['label_id'] in label_ids]
    labels=[label for _,label in pairs]
    counts=Counter(label['decision'] for label in labels)
    decided={key for key,label in pairs if label['decision']!='unknown'}
    n=len(keys);slots=n*len(label_ids)
    known=counts['present']+counts['absent']
    result={'input_count':n,'distinct_work_count':len({key[0] for key in keys}),
        'decided_work_count':len(decided),'decided_work_coverage':len(decided)/n if n else None,
        'taxonomy_label_slots':slots,'present_labels':counts['present'],'absent_labels':counts['absent'],
        'explicit_unknown_labels':counts['unknown'],'emitted_label_count':len(labels),
        'unreported_label_slots':slots-len(labels),'decided_label_slots':known,
        'label_decision_coverage':known/slots if slots else None,
        'unknown_or_unreported_slots':slots-known,
        'raw_confidence':_confidence(labels),'evidence_references':_references(pairs,input_index)}
    if axis is not None:
        result['unknown_axis_count']=sum(any(item['axis']==axis for item in result_index[key]['missing_axes']) for key in keys)
        result['unknown_axis_rate']=result['unknown_axis_count']/n if n else None
    else:
        result['all_axes_unknown_input_count']=n-len(decided)
        result['needs_human_review_input_count']=sum(result_index[key]['needs_human_review'] for key in keys)
        result['axes']={item['axis_id']:_stats(keys,input_index,result_index,taxonomy,item['axis_id']) for item in taxonomy['axes']}
    return result


def summarize_run(inputs_path, run_dir, *, taxonomy_path=None, freeze_manifest_path=None):
    """Strict read-only reconciliation of complete index, jobs and durable results."""
    inputs_path=Path(inputs_path).resolve();run_dir=Path(run_dir).resolve()
    paths={'inputs':inputs_path,'results':run_dir/'results.jsonl','manifest':run_dir/'manifest.json'}
    blobs={name:path.read_bytes() for name,path in paths.items()}
    inputs=[WorkInput.model_validate(row) for row in _jsonl(blobs['inputs'],str(inputs_path))]
    input_index={_key(work.model_dump()):work.model_dump(mode='json') for work in inputs}
    if len(input_index)!=len(inputs) or not inputs:
        raise ValueError('One distinct nonempty work/expression snapshot per input is required')
    rows=_jsonl(blobs['results'],str(paths['results']))
    manifest=json.loads(blobs['manifest'])
    if manifest.get('pending')!=0 or manifest.get('failed')!=0 or manifest.get('stopped') is not False:
        raise ValueError('Only a fully completed run can be summarized; incomplete run was not read further')
    if manifest.get('selected')!=len(inputs) or manifest.get('completed')!=len(rows) or len(rows)!=len(inputs):
        raise ValueError('Completed manifest, result index and fixed input counts differ')
    config=manifest['config'];taxonomy=load_taxonomy(Path(taxonomy_path) if taxonomy_path else None)
    expected={'taxonomy_hash':_sha(canonical_json(taxonomy).encode()),
        'schema_hash':_sha((ROOT/'schemas/classification.schema.json').read_bytes()),
        'prompt_hash':_sha((ROOT/'src/recommend_book/classification/prompt.py').read_bytes())}
    if any(config.get(key)!=value for key,value in expected.items()):
        raise ValueError('Run taxonomy/schema/prompt hashes differ from the supplied current validation artifacts')
    jobs=manifest.get('jobs',{})
    if len(jobs)!=len(inputs) or {_key(job) for job in jobs.values()}!=set(input_index):
        raise ValueError('Manifest jobs differ from fixed work/expression inputs')
    result_index={}
    durable={}
    for result in rows:
        key=_key(result)
        if key not in input_index or key in result_index:
            raise ValueError('Unknown or duplicate result work/expression')
        work=WorkInput.model_validate(input_index[key])
        validate_result(result,work,taxonomy)
        for field in ('model','cli_version','prompt_version','taxonomy_version'):
            if result[field]!=config.get(field):
                raise ValueError(f'Result {field} differs from manifest config')
        identity=job_key(input_hash(work),config);job=jobs.get(identity)
        if job is None or _key(job)!=key or job.get('key')!=identity or job.get('input_hash')!=input_hash(work) or job.get('config')!=config or job.get('status')!='completed':
            raise ValueError('Job config/hash/status does not match the fixed input and run config')
        path=(run_dir/job['result_path']).resolve()
        if not path.is_relative_to(run_dir):
            raise ValueError('Durable result path escapes run directory')
        content=path.read_bytes()
        if json.loads(content)!=result:
            raise ValueError('Durable result differs from result index')
        durable[str(path.relative_to(run_dir))]=_sha(content)
        result_index[key]=result
    freeze={}
    candidate=Path(freeze_manifest_path).resolve() if freeze_manifest_path else inputs_path.parent/'manifest.json'
    if candidate.exists():
        content=candidate.read_bytes();frozen=json.loads(content)
        matches=[key for key in ('work_inputs_sha256','pilot_inputs_sha256') if frozen.get(key)==_sha(blobs['inputs'])]
        if not matches:
            raise ValueError('Input file SHA does not match frozen manifest')
        freeze={'path':str(candidate),'manifest_sha256':_sha(content),'matched_hash_fields':matches,
                'work_count':frozen.get('work_count'),'pilot_count':frozen.get('pilot_count')}
    elif freeze_manifest_path:
        raise FileNotFoundError(candidate)
    keys=list(input_index)
    def grouped(selector):
        groups={}
        for key in keys:
            for value in selector(input_index[key]):
                groups.setdefault(value,[]).append(key)
        return {value:_stats(group,input_index,result_index,taxonomy) for value,group in sorted(groups.items())}
    def kinds(row):
        return sorted({item['kind'] for item in row['evidence'] if item['llm_input_allowed']})
    def depth(row):
        allowed=set(kinds(row))
        return [next((kind for kind in ('excerpt','synopsis','note') if kind in allowed),'facts')]
    samples=[]
    for key in keys:
        evidence={item['evidence_id']:item for item in input_index[key]['evidence'] if item['llm_input_allowed']}
        for label in result_index[key]['labels']:
            for ref in label['evidence']:
                source=evidence[ref['evidence_id']]
                samples.append({'work_id':key[0],'expression_id':key[1],'title':input_index[key]['title'],
                    'label_id':label['label_id'],'decision':label['decision'],'directness':ref['directness'],
                    'evidence_id':ref['evidence_id'],'evidence_kind':source['kind'],'locator':ref['locator'],
                    'source_url':source['source_url'],'rights_policy_id':source['rights_policy_id'],
                    'quote_excerpt':ref['quote'][:240] if ref['quote'] else None,'summary_excerpt':ref['summary'][:240] if ref['summary'] else None,
                    'excerpt_truncated':any(value and len(value)>240 for value in (ref['quote'],ref['summary'])),
                    'summary_faithfulness':None})
    # Detect file mutations while reading; no run directory is ever written.
    if any(path.read_bytes()!=blobs[name] for name,path in paths.items()):
        raise ValueError('Run/input snapshot changed while being observed')
    if any(_sha((run_dir/relative).read_bytes())!=digest for relative,digest in durable.items()):
        raise ValueError('Durable results changed while being observed')
    return {'schema_version':'1','observation_status':'completed_run_structurally_reconciled',
        'provenance':{'input_path':str(inputs_path),'input_file_sha256':_sha(blobs['inputs']),
            'result_index_path':str(paths['results']),'result_index_sha256':_sha(blobs['results']),
            'manifest_path':str(paths['manifest']),'manifest_file_sha256':_sha(blobs['manifest']),
            'run_id':manifest.get('run_id'),'config':config,'config_sha256':_sha(canonical_json(config).encode()),
            'frozen_manifest':freeze,'durable_result_count':len(durable),'durable_result_hashes_sha256':_sha(canonical_json(durable).encode()),
            'validation_artifact_hashes':expected,'manifest_human_quality_verified':manifest.get('human_quality_verified'),
            'declared_usage':manifest.get('usage'),'usage_source':manifest.get('usage_source'),
            'elapsed_seconds':manifest.get('elapsed_seconds'),'initial_concurrency':manifest.get('initial_concurrency'),'final_concurrency':manifest.get('final_concurrency')},
        'overall':_stats(keys,input_index,result_index,taxonomy),
        'by_original_language':grouped(lambda row:[row['original_language'] or 'unknown']),
        'by_evidence_kind':grouped(lambda row:kinds(row) or ['none']),
        'by_evidence_depth':grouped(depth),'evidence_reference_samples':samples[:50],
        'sample_limit':50,'total_reference_samples_available':len(samples),
        'quality':{'status':'human_validation_pending','human_gold_count':0,'human_quality_verified':False,
            **{name:None for name in ('semantic_correctness','precision','recall','f1','summary_faithfulness','relevance_accuracy','calibration','ece','brier')}},
        'definitions':{'axis_coverage':'At least one emitted present/absent decision for an axis per work/expression input.',
            'label_coverage':'Emitted present/absent decisions divided by all fixed inputs times registered taxonomy labels; unreported labels are not absent.',
            'unknown':'Explicit unknown labels, entirely unknown axes, and unreported label slots are reported separately.',
            'strata':'Original language comes only from fixed original_language. Evidence kinds overlap; depth prefers excerpt, synopsis, note, then facts. Permission flags are declarations, not an independent rights audit.',
            'references':'Allowed evidence ID, locator and whitespace-normalized quote occurrence are structural checks. Summary faithfulness and label correctness require human review.'}}


def write_summary(report, output_dir):
    output=Path(output_dir)
    if output.exists():
        raise ValueError('Observation output must be a new directory; existing reports are preserved')
    output.mkdir(parents=True)
    json_path=output/'classification-observation.json';html_path=output/'classification-observation.html'
    json_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    esc=lambda value:html.escape(str(value),quote=True)
    pct=lambda value:'未計測' if value is None else f'{100*value:.2f}%'
    def table(headers,rows):
        return '<table><thead><tr>'+''.join('<th>'+esc(value)+'</th>' for value in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(value)+'</td>' for value in row)+'</tr>' for row in rows)+'</tbody></table>'
    def metric_rows(groups):
        return [(name,value['input_count'],value['decided_work_count'],pct(value['decided_work_coverage']),
                 value.get('unknown_axis_count',value.get('all_axes_unknown_input_count')),
                 value['decided_label_slots'],pct(value['label_decision_coverage']),value['explicit_unknown_labels'],
                 value['unreported_label_slots'],value['raw_confidence']['mean']) for name,value in groups.items()]
    sections=['<h2>軸別</h2>'+table(['軸','入力数','判断作品数','作品判断率','軸unknown作品数','判断ラベル数','全ラベル枠判断率','explicit unknownラベル','未出力ラベル枠','raw confidence平均'],metric_rows(report['overall']['axes']))]
    for name in ('by_original_language','by_evidence_kind','by_evidence_depth'):
        sections.append('<h2>'+esc(name)+'</h2>'+table(['範囲','入力数','判断作品数','作品判断率','全軸unknown作品数','判断ラベル数','全ラベル枠判断率','explicit unknownラベル','未出力ラベル枠','raw confidence平均'],metric_rows(report[name])))
    sections.append('<h2>根拠参照</h2><pre>'+esc(json.dumps(report['overall']['evidence_references'],ensure_ascii=False,indent=2))+'</pre>')
    sections.append('<h2>quote / summaryの先頭50参照（各240文字まで）</h2>'+table(['作品','ラベル','資料','directness','quote','summary'],[(sample['title'],sample['label_id'],sample['evidence_id'],sample['directness'],sample['quote_excerpt'],sample['summary_excerpt']) for sample in report['evidence_reference_samples']]))
    sections.append('<h2>固定入力とrun provenance</h2><pre>'+esc(json.dumps(report['provenance'],ensure_ascii=False,indent=2))+'</pre>')
    overall=report['overall']
    page='<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>分類run観測</title><style>body{font:15px system-ui;max-width:1200px;margin:auto;padding:24px;background:#f5f6f8}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #ccd;vertical-align:top;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}.pending{background:#fff0cd;padding:16px}h2{margin-top:32px}</style><h1>完了済み分類runの観測</h1><p class="pending">human_validation_pending：人手gold0件。意味の正しさ・precision・summary忠実性・校正は未計測です。raw confidenceは自己申告値です。</p><p>'+str(overall['input_count'])+'入力を固定元と再照合。判断作品率 '+pct(overall['decided_work_coverage'])+'、taxonomy全ラベル枠の判断率 '+pct(overall['label_decision_coverage'])+'。</p><a href="classification-observation.json" download>JSONを保存</a>'+''.join(sections)+'<h2>指標の定義</h2><ul>'+''.join('<li>'+esc(value)+'</li>' for value in report['definitions'].values())+'</ul></html>'
    html_path.write_text(page,encoding='utf-8')
    return {'json':str(json_path),'html':str(html_path)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--taxonomy',type=Path)
    parser.add_argument('--freeze-manifest',type=Path)
    args=parser.parse_args(argv)
    report=summarize_run(args.inputs,args.run,taxonomy_path=args.taxonomy,freeze_manifest_path=args.freeze_manifest)
    artifacts=write_summary(report,args.output)
    print(json.dumps({'input_count':report['overall']['input_count'],'decided_work_coverage':report['overall']['decided_work_coverage'],
        'label_decision_coverage':report['overall']['label_decision_coverage'],'quality':report['quality'],'artifacts':artifacts},ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
