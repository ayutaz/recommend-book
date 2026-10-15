"""Compare completed runs on identical frozen inputs; agreement is not truth."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import importlib.util
from itertools import combinations
import json
import math
from pathlib import Path
import statistics

from recommend_book.classification.validation import load_taxonomy

_spec=importlib.util.spec_from_file_location('stability_observer',Path(__file__).with_name('summarize_classification_run.py'))
_observer=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_observer)
KNOWN={'present','absent'}


def _state(result, label_id):
    label=next((item for item in result['labels'] if item['label_id']==label_id),None)
    if label is None:
        return 'unreported',None
    return ('explicit_unknown' if label['decision']=='unknown' else label['decision']),label.get('raw_confidence')


def _delta(pairs):
    values=[right-left for left,right in pairs]
    absolute=sorted(abs(value) for value in values)
    return {'paired_known_labels':len(values),'mean_signed_delta_right_minus_left':statistics.mean(values) if values else None,
        'mean_absolute_delta':statistics.mean(absolute) if absolute else None,
        'median_absolute_delta':statistics.median(absolute) if absolute else None,
        'p95_absolute_delta':absolute[max(0,math.ceil(.95*len(absolute))-1)] if absolute else None,
        'max_absolute_delta':max(absolute) if absolute else None,
        'equal_count':sum(abs(value)<1e-12 for value in values),
        'higher_right_count':sum(value>1e-12 for value in values),'lower_right_count':sum(value< -1e-12 for value in values),
        'meaning':'Differences of self-reported raw confidence for paired present/absent outputs, not calibration or correctness.'}


def _pair_stats(keys,left,right,taxonomy,axis=None):
    label_ids=[label['label_id'] for label in taxonomy['labels'] if axis is None or label['axis']==axis]
    transitions=Counter();both_confidence=[];same_confidence=[]
    eligible_work=0;same_sets=0;both_empty=0
    for key in keys:
        left_set=set();right_set=set()
        for label_id in label_ids:
            a,confidence_a=_state(left[key],label_id);b,confidence_b=_state(right[key],label_id)
            transitions[a+'->'+b]+=1
            if a in KNOWN:
                left_set.add((label_id,a))
            if b in KNOWN:
                right_set.add((label_id,b))
            if a in KNOWN and b in KNOWN:
                both_confidence.append((confidence_a,confidence_b))
                if a==b:
                    same_confidence.append((confidence_a,confidence_b))
        if left_set or right_set:
            eligible_work+=1;same_sets+=left_set==right_set
        else:
            both_empty+=1
    def count(predicate):
        return sum(n for transition,n in transitions.items() if predicate(*transition.split('->')))
    union=count(lambda a,b:a in KNOWN or b in KNOWN)
    both=count(lambda a,b:a in KNOWN and b in KNOWN)
    same=count(lambda a,b:a in KNOWN and a==b)
    axes=[item['axis_id'] for item in taxonomy['axes'] if axis is None or item['axis_id']==axis]
    axis_transitions={}
    for axis_id in axes:
        values=Counter()
        for key in keys:
            a='unknown' if any(item['axis']==axis_id for item in left[key]['missing_axes']) else 'decided'
            b='unknown' if any(item['axis']==axis_id for item in right[key]['missing_axes']) else 'decided'
            values[a+'->'+b]+=1
        axis_transitions[axis_id]=dict(sorted(values.items()))
    return {'input_count':len(keys),'registered_label_slots':len(keys)*len(label_ids),
        'known_union_slots':union,'both_known_slots':both,'same_known_state_slots':same,
        'known_union_agreement_rate':same/union if union else None,'both_known_agreement_rate':same/both if both else None,
        'known_union_disagreement_slots':union-same,
        'present_absent_flip_count':count(lambda a,b:a in KNOWN and b in KNOWN and a!=b),
        'known_to_unreported_count':count(lambda a,b:a in KNOWN and b=='unreported'),
        'unreported_to_known_count':count(lambda a,b:a=='unreported' and b in KNOWN),
        'known_to_explicit_unknown_count':count(lambda a,b:a in KNOWN and b=='explicit_unknown'),
        'explicit_unknown_to_known_count':count(lambda a,b:a=='explicit_unknown' and b in KNOWN),
        'neither_known_slots':len(keys)*len(label_ids)-union,
        'state_transitions':dict(sorted(transitions.items())),
        'eligible_known_set_inputs':eligible_work,'matching_known_set_inputs':same_sets,
        'known_set_input_agreement_rate':same_sets/eligible_work if eligible_work else None,
        'both_empty_known_set_inputs':both_empty,'axis_status_transitions':axis_transitions,
        'all_both_known_confidence':_delta(both_confidence),'same_decision_confidence':_delta(same_confidence)}


def compare_runs(inputs_path,run_dirs,*,freeze_manifest_path=None):
    """Read only explicitly named complete runs, using existing strict verifier."""
    if len(run_dirs)<2 or len(set(run_dirs))!=len(run_dirs):
        raise ValueError('At least two distinctly named runs are required')
    resolved={name:Path(path).resolve() for name,path in run_dirs.items()}
    if len(set(resolved.values()))!=len(resolved):
        raise ValueError('Repeated run directory cannot establish independent run stability')
    observed={name:_observer.summarize_run(inputs_path,path,freeze_manifest_path=freeze_manifest_path) for name,path in resolved.items()}
    configs={report['provenance']['config_sha256'] for report in observed.values()}
    input_hashes={report['provenance']['input_file_sha256'] for report in observed.values()}
    if len(configs)!=1 or len(input_hashes)!=1:
        raise ValueError('Stability comparison requires identical fixed input and run configuration hashes')
    content=Path(inputs_path).read_bytes()
    if hashlib.sha256(content).hexdigest()!=next(iter(input_hashes)):
        raise ValueError('Fixed input changed after run verification')
    inputs=_observer._jsonl(content,str(inputs_path));input_index={_observer._key(row):row for row in inputs};keys=list(input_index)
    taxonomy=load_taxonomy()
    result_indexes={}
    for name,path in resolved.items():
        data=(path/'results.jsonl').read_bytes()
        if hashlib.sha256(data).hexdigest()!=observed[name]['provenance']['result_index_sha256']:
            raise ValueError('Run result index changed after verification')
        result_indexes[name]={_observer._key(row):row for row in _observer._jsonl(data,str(path/'results.jsonl'))}
    def depth(row):
        kinds={item['kind'] for item in row['evidence'] if item['llm_input_allowed']}
        return next((kind for kind in ('excerpt','synopsis','note') if kind in kinds),'facts')
    languages={language:[key for key in keys if (input_index[key]['original_language'] or 'unknown')==language] for language in sorted({row['original_language'] or 'unknown' for row in inputs})}
    depths={value:[key for key in keys if depth(input_index[key])==value] for value in sorted({depth(row) for row in inputs})}
    pairs={}
    differences=[]
    names=list(resolved)
    for left_name,right_name in combinations(names,2):
        left=result_indexes[left_name];right=result_indexes[right_name]
        pair_name=left_name+'__'+right_name
        pairs[pair_name]={'left_run':left_name,'right_run':right_name,'overall':_pair_stats(keys,left,right,taxonomy),
            'by_axis':{axis['axis_id']:_pair_stats(keys,left,right,taxonomy,axis['axis_id']) for axis in taxonomy['axes']},
            'by_original_language':{language:_pair_stats(group,left,right,taxonomy) for language,group in languages.items()},
            'by_evidence_depth':{value:_pair_stats(group,left,right,taxonomy) for value,group in depths.items()}}
        for key in keys:
            for label in taxonomy['labels']:
                a,confidence_a=_state(left[key],label['label_id']);b,confidence_b=_state(right[key],label['label_id'])
                if a!=b:
                    differences.append({'pair':pair_name,'work_id':key[0],'expression_id':key[1],
                        'title':input_index[key]['title'],'label_id':label['label_id'],'axis':label['axis'],
                        'left_state':a,'right_state':b,'left_raw_confidence':confidence_a,'right_raw_confidence':confidence_b,
                        'observation_only':True})
    union=0;all_known=0;unanimous=0
    for key in keys:
        for label in taxonomy['labels']:
            states=[_state(result_indexes[name][key],label['label_id'])[0] for name in names]
            union+=any(state in KNOWN for state in states)
            all_known+=all(state in KNOWN for state in states)
            unanimous+=all(state in KNOWN for state in states) and len(set(states))==1
    return {'schema_version':'1','status':'offline_stability_observation','run_names':names,
        'input_file_sha256':next(iter(input_hashes)),'config_sha256':next(iter(configs)),
        'run_provenance':{name:report['provenance'] for name,report in observed.items()},
        'per_run_coverage':{name:{key:report['overall'][key] for key in ('input_count','decided_work_count','decided_work_coverage','present_labels','absent_labels','explicit_unknown_labels','unreported_label_slots','raw_confidence')} for name,report in observed.items()},
        'all_runs':{'registered_label_slots':len(keys)*len(taxonomy['labels']),'known_union_slots':union,
            'known_in_all_runs_slots':all_known,'unanimous_known_state_slots':unanimous,
            'unanimous_known_union_rate':unanimous/union if union else None},
        'pairs':pairs,'state_difference_observations':differences,
        'quality':{'status':'human_validation_pending','human_gold_count':0,'human_quality_verified':False,
            'accuracy':None,'precision':None,'semantic_correctness':None,'calibration':None,'ece':None,'brier':None},
        'definitions':{'states':'present, absent, explicit_unknown, unreported are distinct; missing axes never turn unreported labels into explicit unknown or absent.',
            'known_union_agreement':'Same present/absent state divided by slots with a present/absent decision in at least one run. Neither-known slots are excluded from the denominator.',
            'both_known_agreement':'Same present/absent state divided by slots judged present/absent in both runs. Coverage losses are reported separately.',
            'known_set_inputs':'Exact equality of emitted present/absent label-state sets, only among inputs with at least one such decision in either run; empty/empty inputs are excluded.',
            'confidence':'Right minus left self-reported confidence for both-known slots; same-decision subset is also reported. This is not a calibrated probability error.',
            'interpretation':'LLM agreement is not correctness, human gold, calibration or a causal effect of concurrency. No majority-vote predictions or pseudo-gold are created.',
            'scope':'Only named run directories and fixed pilot inputs were read. Quotes and meaning are not compared or judged; standard structural run validation is reused.'}}


def write_comparison(report,output_dir):
    output=Path(output_dir)
    if output.exists():
        raise ValueError('New stability output directory required; existing observations remain unchanged')
    output.mkdir(parents=True)
    json_path=output/'classification-stability.json';html_path=output/'classification-stability.html'
    json_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    esc=lambda value:html.escape(str(value),quote=True)
    rate=lambda value:'未計測' if value is None else f'{100*value:.2f}%'
    def table(headers,rows):
        return '<table><thead><tr>'+''.join('<th>'+esc(item)+'</th>' for item in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(item)+'</td>' for item in row)+'</tr>' for row in rows)+'</tbody></table>'
    headers=['範囲','known union枠','both known枠','同じknown状態','known union一致率','present/absent反転','known→未出力','未出力→known','confidence絶対差平均']
    def rows(groups):
        return [(name,value['known_union_slots'],value['both_known_slots'],value['same_known_state_slots'],rate(value['known_union_agreement_rate']),
            value['present_absent_flip_count'],value['known_to_unreported_count'],value['unreported_to_known_count'],value['all_both_known_confidence']['mean_absolute_delta']) for name,value in groups.items()]
    sections=['<h2>ペア全体</h2>'+table(headers,rows({name:pair['overall'] for name,pair in report['pairs'].items()}))]
    for name,pair in report['pairs'].items():
        sections.append('<h2>'+esc(name)+'</h2>')
        for group in ('by_axis','by_original_language','by_evidence_depth'):
            sections.append('<h3>'+esc(group)+'</h3>'+table(headers,rows(pair[group])))
        sections.append('<h3>4状態の遷移件数（neither-knownは一致率の分母外）</h3>'+table(['遷移','件数'],pair['overall']['state_transitions'].items()))
        sections.append('<h3>軸の判断／unknown変化</h3><pre>'+esc(json.dumps(pair['overall']['axis_status_transitions'],ensure_ascii=False,indent=2))+'</pre>')
    sections.append('<h2>状態が異なるラベル枠（正解裁定ではない）</h2>'+table(['ペア','作品','ラベル','左状態','右状態'],[(item['pair'],item['title'],item['label_id'],item['left_state'],item['right_state']) for item in report['state_difference_observations']]))
    sections.append('<h2>provenance</h2><pre>'+esc(json.dumps(report['run_provenance'],ensure_ascii=False,indent=2))+'</pre>')
    page='<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>分類runの判定安定性</title><style>body{font:15px system-ui;max-width:1200px;margin:auto;padding:24px;background:#f5f6f8}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #ccd;text-align:left;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere}.pending{background:#fff0cd;padding:16px}</style><h1>分類runのオフライン判定安定性</h1><p class="pending">人手gold0。LLM一致は正解率・precision・校正ではありません。未出力同士の一致を一致率へ算入しません。</p><a href="classification-stability.json" download>JSONを保存</a>'+''.join(sections)+'<h2>指標の定義</h2><ul>'+''.join('<li>'+esc(value)+'</li>' for value in report['definitions'].values())+'</ul></html>'
    html_path.write_text(page,encoding='utf-8')
    return {'json':str(json_path),'html':str(html_path)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--run',action='append',required=True,help='NAME=DIRECTORY, repeat for each completed run')
    parser.add_argument('--freeze-manifest',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    runs={}
    for entry in args.run:
        name,separator,path=entry.partition('=')
        if not separator or not name or name in runs:
            raise ValueError('Each --run must have a unique NAME=DIRECTORY')
        runs[name]=Path(path)
    report=compare_runs(args.inputs,runs,freeze_manifest_path=args.freeze_manifest)
    artifacts=write_comparison(report,args.output)
    print(json.dumps({'all_runs':report['all_runs'],'pairs':{name:pair['overall'] for name,pair in report['pairs'].items()},'quality':report['quality'],'artifacts':artifacts},ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
