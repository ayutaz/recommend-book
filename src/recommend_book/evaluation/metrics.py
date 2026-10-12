"""Human, reference and LLM agreement are separate estimands, never interchangeable."""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import random
from statistics import mean


def records(items):
    if items is None:
        return []
    if isinstance(items, dict):
        if 'work_id' in items:
            items = [items]
        else:
            items = list(items.values())
    return [item.model_dump(mode='json') if hasattr(item, 'model_dump') else dict(item) for item in items]


def decision(label):
    value = label.get('decision', label.get('verdict', 'unknown'))
    if value not in ('present', 'absent', 'unknown'):
        raise ValueError(f'invalid decision: {value}')
    return value


def validate_gold(record, label_ids=None):
    provenance = record.get('provenance')
    if provenance not in ('human', 'curated_reference', 'llm'):
        raise ValueError('gold provenance must be human, curated_reference or llm')
    if provenance == 'human':
        if not record.get('reviewer_id'):
            raise ValueError('human annotation requires reviewer_id')
        if record.get('truth_level') not in ('evidence', 'work'):
            raise ValueError('human annotation requires explicit truth_level')
        if record['truth_level'] == 'work' and not (
            record.get('has_read_work') is True or
            (record.get('sufficient_documentation') is True and record.get('work_truth_sources'))
        ):
            raise ValueError('work truth requires read-work declaration or sufficient documented sources')
        if record.get('partition') not in ('dev', 'calibration', 'holdout'):
            raise ValueError('unknown evaluation partition')
    seen = set()
    for label in record.get('labels', []):
        label_id = label['label_id']
        if label_id in seen:
            raise ValueError('duplicate gold label')
        if label_ids is not None and label_id not in label_ids:
            raise ValueError(f'unknown label: {label_id}')
        seen.add(label_id)
        if 'decision' not in label and 'verdict' not in label:
            raise ValueError('gold label requires explicit decision')
        decision(label)
        value = label.get('relevance')
        if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError('relevance must be null or finite 0..1')


def _prf(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return {'tp':tp, 'fp':fp, 'fn':fn, 'precision':precision, 'recall':recall, 'f1':f1}


def _wilson(k, n):
    if not n:
        return None
    z = 1.959963984540054
    center = (k / n + z*z / (2*n)) / (1 + z*z / n)
    half = z * math.sqrt(k/n * (1-k/n)/n + z*z/(4*n*n)) / (1+z*z/n)
    return [max(0,center-half),min(1,center+half)]


def _rank(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    output = [0.] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        for index in order[i:j]:
            output[index] = (i+j-1)/2
        i = j
    return output


def _spearman(pairs):
    if len(pairs) < 3:
        return None
    x, y = _rank([p[0] for p in pairs]), _rank([p[1] for p in pairs])
    xm, ym = mean(x), mean(y)
    divisor = math.sqrt(sum((v-xm)**2 for v in x) * sum((v-ym)**2 for v in y))
    return sum((a-xm)*(b-ym) for a,b in zip(x,y)) / divisor if divisor else None


def _calibration(rows, field='confidence'):
    scored = [r for r in rows if r['truth'] != 'unknown' and r['prediction'] != 'unknown' and r.get(field) is not None]
    bins = []
    for index in range(10):
        values = [r for r in scored if min(9, int(r[field]*10)) == index]
        if values:
            correct = sum(r['truth'] == r['prediction'] for r in values)
            bins.append({'lower':index/10,'upper':(index+1)/10,'n':len(values),
                         'confidence':mean(r[field] for r in values),'accuracy':correct/len(values),
                         'accuracy_ci95':_wilson(correct,len(values))})
    return {'n':len(scored),'ece':sum(b['n']*abs(b['accuracy']-b['confidence']) for b in bins)/len(scored) if scored else None,
            'brier':mean((r[field] - int(r['truth']==r['prediction']))**2 for r in scored) if scored else None,
            'definition':'confidence in correctness of present/absent decision; abstentions excluded', 'bins':bins}


def _summary(rows, labels):
    metrics = {}
    for label_id in labels:
        values = [r for r in rows if r['label_id'] == label_id and r['truth'] != 'unknown']
        tp = sum(r['truth']=='present' and r['prediction']=='present' for r in values)
        fp = sum(r['truth']=='absent' and r['prediction']=='present' for r in values)
        fn = sum(r['truth']=='present' and r['prediction']!='present' for r in values)
        support = sum(r['truth']=='present' for r in values)
        metrics[label_id] = {**_prf(tp,fp,fn),'n':len(values),'positive_support':support,
            'precision_ci95':_wilson(tp,tp+fp),'recall_ci95':_wilson(tp,tp+fn),
            'status':'supported' if support >= 20 else 'insufficient_positive_support'}
    micro = _prf(sum(m['tp'] for m in metrics.values()),sum(m['fp'] for m in metrics.values()),sum(m['fn'] for m in metrics.values()))
    eligible = [r for r in rows if r['truth'] != 'unknown']
    undecidable = [r for r in rows if r['truth'] == 'unknown']
    unsupported = sum(r['prediction'] != 'unknown' for r in undecidable)
    pairs = [(r['pred_relevance'],r['gold_relevance']) for r in rows if r['truth'] != 'unknown' and r['pred_relevance'] is not None and r['gold_relevance'] is not None]
    adoption=[]
    for threshold in (.5,.7,.9,.95):
        selected=[r for r in rows if r['prediction']!='unknown' and r['confidence'] is not None and r['confidence']>=threshold]
        positives=[r for r in selected if r['prediction']=='present' and r['truth']!='unknown']
        correct=sum(r['truth']=='present' for r in positives)
        adoption.append({'threshold':threshold,'adopted_n':len(selected),'all_adoption_rate':len(selected)/len(rows) if rows else None,
                         'positive_precision':correct/len(positives) if positives else None,'positive_precision_ci95':_wilson(correct,len(positives)),
                         'unsupported_adopted_n':sum(r['truth']=='unknown' for r in selected)})
    return {'per_label':metrics,'micro':micro,'macro':{key:mean([m[key] for m in metrics.values() if m[key] is not None]) if any(m[key] is not None for m in metrics.values()) else None for key in ('precision','recall','f1')},
        'coverage':{'all':sum(r['prediction']!='unknown' for r in rows)/len(rows) if rows else None,'eligible':sum(r['prediction']!='unknown' for r in eligible)/len(eligible) if eligible else None,'all_n':len(rows),'eligible_n':len(eligible)},
        'unsupported_assertion':{'n':unsupported,'denominator':len(undecidable),'rate':unsupported/len(undecidable) if undecidable else None,'ci95':_wilson(unsupported,len(undecidable))},
        'relevance':{'n':len(pairs),'mae':mean(abs(a-b) for a,b in pairs) if pairs else None,'spearman':_spearman(pairs)},
        'raw_confidence_diagnostics':_calibration(rows),'confidence_adoption':adoption}


def _bootstrap(rows, labels, seed=1729, count=300):
    groups = defaultdict(list)
    for row in rows:
        groups[row['group_id']].append(row)
    if len(groups) < 2:
        return {'status':'insufficient_independent_groups','groups':len(groups),'seed':seed,'replicates':count,'micro_f1_ci95':None}
    rng = random.Random(seed)
    keys = sorted(groups)
    estimates = defaultdict(list)
    for _ in range(count):
        sample = [r for key in rng.choices(keys,k=len(keys)) for r in groups[key]]
        eligible=[r for r in sample if r['truth']!='unknown']
        prf=_prf(sum(r['truth']=='present' and r['prediction']=='present' for r in eligible),sum(r['truth']=='absent' and r['prediction']=='present' for r in eligible),sum(r['truth']=='present' and r['prediction']!='present' for r in eligible))
        diagnostics=_calibration(sample)
        pairs=[r for r in eligible if r['pred_relevance'] is not None and r['gold_relevance'] is not None]
        values={**{f'micro_{key}':prf[key] for key in ('precision','recall','f1')},'relevance_mae':mean(abs(r['pred_relevance']-r['gold_relevance']) for r in pairs) if pairs else None,'ece':diagnostics['ece'],'brier':diagnostics['brier']}
        for key,value in values.items():
            if value is not None:
                estimates[key].append(value)
    intervals={key+'_ci95':None for key in ('micro_precision','micro_recall','micro_f1','relevance_mae','ece','brier')}
    for key,values in estimates.items():
        values.sort()
        intervals[key+'_ci95']=[values[int((len(values)-1)*q)] for q in (.025,.975)]
    return {'status':'estimated','groups':len(groups),'seed':seed,'replicates':count,**intervals,'method':'percentile cluster bootstrap; authors/series union groups'}


def _year(year):
    if year is None:
        return 'unknown'
    return 'pre1900' if year < 1900 else '1900-1949' if year < 1950 else '1950-1999' if year < 2000 else '2000+'


def _scope(row):
    return '|'.join((row['label_id'],row['language'],row['evidence_kind']))


def _fit_calibrator(rows):
    scopes = defaultdict(list)
    for row in rows:
        if row['truth']!='unknown' and row['prediction']!='unknown' and row['confidence'] is not None:
            scopes[_scope(row)].append(row)
    models = {}
    for scope,values in scopes.items():
        if len(values) < 40 or any(part=='unknown' for part in scope.split('|')[1:]):
            continue
        contexts={r['calibration_context'] for r in values}
        if len(contexts)!=1:
            continue
        bins=[]
        # Two bins, each at least 20. Small cells stay uncalibrated.
        for lower,upper in ((0,.5),(.5,1.0000001)):
            band=[r for r in values if lower <= r['confidence'] < upper]
            if len(band)>=20:
                bins.append({'lower':lower,'upper':min(upper,1),'n':len(band), 'probability':sum(r['truth']==r['prediction'] for r in band)/len(band),'context':list(next(iter(contexts)))})
        if bins:
            models[scope]=bins
    return {'status':'calibrated_scopes_available' if models else 'uncalibrated', 'version':'human-calibration-bins-v1',
            'fit_partition':'calibration','minimum_scope_n':40,'minimum_bin_n':20,'scopes':models}


def evaluate_results(results, gold, taxonomy):
    """Evaluate each provenance, truth level and frozen partition independently.

    Multiple independent reviewers must be adjudicated before this API; duplicate
    work/truth/partition/provenance rows are rejected rather than cherry-picked.
    """
    from .splits import connected_groups
    taxonomy = taxonomy.model_dump() if hasattr(taxonomy,'model_dump') else taxonomy
    labels = {x['label_id']:x for x in taxonomy['labels']}
    predictions={}
    for result in records(results):
        if result['work_id'] in predictions:
            raise ValueError('duplicate prediction work; evaluate expressions separately')
        predictions[result['work_id']]=result
        result_labels=[label['label_id'] for label in result.get('labels',[])]
        if len(result_labels)!=len(set(result_labels)) or not set(result_labels)<=set(labels):
            raise ValueError('duplicate or unknown prediction label')
    truth=records(gold)
    seen=set()
    for item in truth:
        validate_gold(item,labels)
        key=(item['work_id'],item['provenance'],item.get('truth_level','reference'),item.get('partition','holdout'))
        if key in seen:
            raise ValueError('duplicate gold; adjudicate independent reviewers first')
        seen.add(key)
    group_inputs=[{'work_id':r['work_id'],'authors':r.get('authors',[]),'series':r.get('series')} for r in truth]
    group_map=connected_groups(group_inputs)
    group_partition={}
    for item in truth:
        if item['provenance']!='human':
            continue
        group=group_map[item['work_id']]
        partition=item['partition']
        if group in group_partition and group_partition[group]!=partition:
            raise ValueError('author/series/work group crosses evaluation partitions; calibration leakage')
        group_partition[group]=partition
    buckets=defaultdict(list)
    for item in truth:
        prediction=predictions.get(item['work_id'],{})
        by_label={x['label_id']:x for x in prediction.get('labels',[])}
        for target in item.get('labels',[]):
            predicted=by_label.get(target['label_id'],{})
            confidence=predicted.get('raw_confidence',predicted.get('confidence_uncalibrated',predicted.get('confidence')))
            if confidence is not None and (not isinstance(confidence,(int,float)) or not math.isfinite(confidence) or not 0<=confidence<=1):
                raise ValueError('confidence must be finite 0..1')
            row={'work_id':item['work_id'],'group_id':item.get('group_id',group_map[item['work_id']]),'label_id':target['label_id'],
                 'truth':decision(target),'prediction':decision(predicted),'confidence':confidence,
                 'pred_relevance':predicted.get('relevance'),'gold_relevance':target.get('relevance'),
                 'language':item.get('original_language') or 'unknown','year':_year(item.get('first_publication_year')),
                 'regions':item.get('region') or ['unknown'],'evidence_kind':item.get('evidence_kind') or 'unknown',
                 'calibration_context':tuple(prediction.get(field) for field in ('model','prompt_version','taxonomy_version','cli_version','target_language'))}
            buckets[(item['provenance'],item.get('truth_level','reference'),item.get('partition','holdout'))].append(row)
    report={'schema_version':'1','taxonomy_version':taxonomy.get('version'),'prediction_work_count':len(predictions),'gold_work_count':len({r['work_id'] for r in truth}),
            'validation_status':'human_validation_pending','human':{},'reference':{},'llm_agreement':{},'calibration':{'status':'uncalibrated','reason':'No eligible human calibration partition'},
            'notes':['Human unknown excluded from truth metrics; assertions on unknown reported separately.','Missing/unknown predictions count as positive false negatives.','Reference and LLM agreement do not validate human accuracy.','Wilson intervals are descriptive per-item intervals; cluster bootstrap accounts for author/series dependence.']}
    universe=set(predictions)|{r['work_id'] for r in truth}
    total_slots=len(universe)*len(labels)
    decided=sum(decision(l)!='unknown' for p in predictions.values() for l in p.get('labels',[]))
    report['dataset_coverage']={'registered_work_n':len(universe),'taxonomy_label_slots':total_slots,'decided_label_slots':decided,'all_registered':decided/total_slots if total_slots else None,
                                'eligible_scope':'Eligible coverage is evaluated separately on human-decidable reviewed labels; unreviewed work eligibility is unknown.'}
    for (provenance,level,partition),rows in buckets.items():
        metric=_summary(rows,labels)
        metric['cluster_bootstrap']=_bootstrap(rows,labels)
        metric['axes']={axis:_summary([r for r in rows if labels[r['label_id']].get('axis')==axis],[lid for lid,l in labels.items() if l.get('axis')==axis]) for axis in sorted({l.get('axis','unknown') for l in labels.values()})}
        strata={}
        for dimension in ('language','year','regions'):
            cells=defaultdict(list)
            for row in rows:
                for value in row[dimension] if dimension=='regions' else [row[dimension]]:
                    cells[value].append(row)
            strata[dimension]={name:_summary(values,labels) for name,values in cells.items()}
        metric['strata']=strata
        if provenance=='human':
            report['human'].setdefault(level,{})[partition]=metric
            report['validation_status']='human_evaluation_available'
        elif provenance=='curated_reference':
            # Reference benchmark has no human split claim.
            report['reference']=metric
        else:
            report['llm_agreement']=metric
    calibrators={}
    for level in ('evidence','work'):
        train=buckets.get(('human',level,'calibration'),[])
        model=_fit_calibrator(train)
        calibrators[level]=model
        holdout=buckets.get(('human',level,'holdout'),[])
        for row in holdout:
            row['calibrated']=None
            for band in model['scopes'].get(_scope(row),[]):
                if list(row['calibration_context'])==band['context'] and row['confidence'] is not None and band['lower']<=row['confidence']<=band['upper']:
                    row['calibrated']=band['probability']
                    break
        if holdout:
            report['human'][level]['holdout']['calibrated_confidence_diagnostics']=_calibration(holdout,'calibrated')
    report['calibration']={'status':'calibrated_scopes_available' if any(m['scopes'] for m in calibrators.values()) else 'uncalibrated','models':calibrators}
    return report


def write_report(report, output_dir):
    output=Path(output_dir)
    output.mkdir(parents=True,exist_ok=True)
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    lines=['# Classification evaluation', '',f"Validation: {report['validation_status']}",f"Calibration: {report['calibration']['status']}",'', 'Human results are restricted to the declared truth level and partition. Reference/LLM agreement is separate.','']
    for level,partitions in report['human'].items():
        for partition,metric in partitions.items():
            lines.extend([f'## {level} / {partition}','',f"Micro F1: {metric['micro']['f1']}; macro F1: {metric['macro']['f1']}; eligible coverage: {metric['coverage']['eligible']}",f"Group-bootstrap 95% interval: {metric['cluster_bootstrap']['micro_f1_ci95']}",'','| Label | Positive support | Precision | Recall | F1 | Status |','|---|---:|---:|---:|---:|---|'])
            for label,m in metric['per_label'].items():
                lines.append(f"| {label} | {m['positive_support']} | {m['precision']} | {m['recall']} | {m['f1']} | {m['status']} |")
            lines.append('')
    if not report['human']:
        lines.extend(['Human accuracy and calibration have not been validated. No human annotations were supplied.',''])
    (output/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return {'json':str(output/'report.json'),'markdown':str(output/'report.md')}


def evaluate_resolution_pairs(predictions, gold, candidates=None):
    """Pair benchmark; uncertain truth excluded, uncertain predicted same is FN."""
    predicted={tuple(sorted((r['left_id'],r['right_id']))):r['decision'] for r in records(predictions)}
    candidate_keys={tuple(sorted(pair)) for pair in candidates} if candidates is not None else set(predicted)
    human=[]
    for row in records(gold):
        if row.get('provenance')=='human' and row.get('reviewer_id'):
            human.append(row)
    known=[r for r in human if r['decision'] in ('same','different')]
    tp=fp=fn=missed=0
    types=defaultdict(list)
    for row in known:
        key=tuple(sorted((row['left_id'],row['right_id'])))
        decision_=predicted.get(key,'uncertain')
        tp+=row['decision']=='same' and decision_=='same'
        fp+=row['decision']=='different' and decision_=='same'
        fn+=row['decision']=='same' and decision_!='same'
        missed+=row['decision']=='same' and key not in candidate_keys
        types[row.get('pair_type','unknown')].append(row)
    positives=sum(r['decision']=='same' for r in known)
    return {'validation_status':'human_evaluation_available' if known else 'human_validation_pending','human_pairs':len(human),'eligible_pairs':len(known),'planned_pairs':{'minimum':300,'maximum':500},'planned_size_met':300<=len(known)<=500,
            'merge':{**_prf(tp,fp,fn),'precision_ci95':_wilson(tp,tp+fp)},'candidate_recall':(positives-missed)/positives if positives else None,
            'pair_type_support':{name:len(rows) for name,rows in types.items()},'cluster_error':'not evaluated from pairs alone; requires complete adjudicated cluster truth'}
