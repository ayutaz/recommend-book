"""Read-only structural and provenance audit. No inferred language or human gold."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

from recommend_book.models import WorkInput
from recommend_book.rights import RightsRegistry
from .metrics import records, validate_gold, decision
from .splits import connected_groups, _subset


def _rate(n, denominator):
    return n / denominator if denominator else None


def _year(value):
    return 'unknown' if value is None else 'pre1900' if value < 1900 else '1900-1949' if value < 1950 else '1950-1999' if value < 2000 else '2000+'


def _url(value):
    if not isinstance(value,str):
        return False
    parsed=urlparse(value)
    return parsed.scheme in ('https','http') and bool(parsed.netloc)


def review_preconditions(inputs):
    """Preflight, without creating or changing the final split manifest."""
    rows=records(inputs)
    by_work={}
    duplicate_base=0
    for row in rows:
        work=row['work_id']
        if work in by_work and row.get('expression_id') is None and by_work[work].get('expression_id') is None:
            duplicate_base+=1
        if work not in by_work or row.get('expression_id') is None:
            by_work[work]=row
    base=list(by_work.values())
    groups=defaultdict(list)
    group_ids=connected_groups(base)
    for row in base:
        groups[group_ids[row['work_id']]].append(row['work_id'])
    ordered=[sorted(value) for _,value in sorted(groups.items())]
    exact=False
    if len(base)>=400:
        for attempt in range(min(100,max(1,len(ordered)))):
            available=ordered[attempt:]+ordered[:attempt]
            success=True
            for size in (150,100,150):
                chosen=_subset(available,size)
                if chosen is None:
                    success=False
                    break
                selected=set(chosen)
                available=[group for index,group in enumerate(available) if index not in selected]
            if success:
                exact=True
                break
    return {'input_rows':len(rows),'distinct_work_count':len(base),'expression_rows':sum(r.get('expression_id') is not None for r in rows),
        'duplicate_base_rows':duplicate_base,'human400':{'work_count_ready':len(base)>=400,'exact_grouped_split_ready':exact,
            'independent_author_series_groups':len(groups),'largest_group_works':max((len(g) for g in groups.values()),default=0),
            'required_partition_sizes':{'dev':150,'calibration':100,'holdout':150},
            'input_requirement':'Use exactly one selected work-level input per work_id; preserve expression inputs separately. Snapshot, metadata and rights flags must be frozen before creating immutable manifest.',
            'author_missing_works':sum(not r.get('authors') for r in base),
            'limitations':['Preflight uses normalized author names and series names, not authoritative author IDs. Missing identity or aliases can conceal leakage.',
                'This is a feasibility probe of grouped counts; the actual deterministic language/year ordering can still fail. Final manifest creation is authoritative.',
                'Exact sizes are never obtained by splitting a connected author/series group.']},
        'trial20':{'work_count_ready':len(base)>=20,'target_works':20,'purpose':'Measure real reviewer time, undecidable labels and required additional sources; pack generation is not completed human review.'}}


def _input_sources(sources, active_ids):
    """Enrich author labels from the active immutable authority snapshot only.

    This reproduces DatasetDB's agent-ID closure without opening its writer or
    changing the original payloads used by independent provenance checks.
    """
    ordered=sorted((source for source in sources.values() if source['version_id'] in active_ids),
                   key=lambda source:(source['source'],source['source_id']))
    authorities=[source['payload'] for source in ordered if source['payload']['record_kind']=='agent']
    authority_ids=[{(item['source'],item['value']) for item in authority.get('identifiers',[]) if item['scope']=='agent'} for authority in authorities]
    enriched=dict(sources)
    for source in ordered:
        payload=source['payload']
        if payload['record_kind']=='agent':
            continue
        contributors=[]
        for contributor in payload.get('contributors',[]):
            identifiers={(item['source'],item['value']) for item in contributor.get('identifiers',[]) if item['scope']=='agent'}
            matches=[]
            remaining=list(range(len(authorities))) if identifiers else []
            changed=True
            while changed:
                changed=False
                for index in list(remaining):
                    if identifiers & authority_ids[index]:
                        identifiers.update(authority_ids[index])
                        matches.append(authorities[index])
                        remaining.remove(index)
                        changed=True
            name=next((authority['titles'][0]['value'] for authority in matches if authority.get('titles')),contributor['name'])
            contributors.append({**contributor,'name':name})
        enriched[source['version_id']]={**source,'payload':{**payload,'contributors':contributors}}
    return enriched


def _work_metadata(source_rows):
    """Match work-only source priority and per-field fallback in work_inputs."""
    priority={'wikidata':0,'openlibrary':1,'wikipedia':3}
    ordered=sorted(source_rows,key=lambda source:(source['payload']['record_kind']!='work',
        priority.get(source['source'],2),source['source'],source['source_id']))
    values=[source['payload'] for source in ordered if source['payload']['record_kind']=='work']
    representative=values[0] if values else {}
    def first_known(field):
        return next((value.get(field) for value in values if value.get(field) not in (None,[],'')),None)
    author_source=next((value for value in values if any(item.get('role','author')=='author' for item in value.get('contributors',[]))),representative)
    fallback=representative['titles'][0]['value'] if representative.get('titles') else ''
    title=next((title['value'] for value in values for title in value.get('titles',[])
                if title.get('value') and title['value']!=value['source_id']),fallback)
    return {'title':title,'authors':list(dict.fromkeys(item['name'] for item in author_source.get('contributors',[]) if item.get('role','author')=='author')),
        'original_language':first_known('original_language'),'first_publication_year':first_known('first_publication_year'),
        'region':first_known('region') or [],'series':first_known('series'),'work_kind':first_known('work_kind'),
        'source_record_ids':[source['version_id'] for source in ordered]}


def _derive_inputs(works, sources, evidence):
    """Mirror stored work inputs while avoiding DatasetDB migrations/writes."""
    grouped=defaultdict(list)
    for work in works:
        if work['resolved']:
            grouped[work['canonical_id']].append(work)
    out=[]
    for canonical,nodes in sorted(grouped.items()):
        source_rows=[sources[n['source_version_id']] for n in nodes]
        metadata=_work_metadata(source_rows)
        scopes=defaultdict(list)
        for item in sorted(evidence,key=lambda x:x['evidence_key']):
            if item['work_id']==canonical and item.get('scope_valid',1) and item['payload'].get('llm_input_allowed') is True:
                scopes[item['expression_id']].append(item['payload'])
        base={'work_id':canonical,**{field:value for field,value in metadata.items() if field!='work_kind'}}
        out.append({**base,'expression_id':None,'evidence':scopes.get(None,[])})
        for expression,items in sorted(((key,value) for key,value in scopes.items() if key is not None)):
            out.append({**base,'expression_id':expression,'evidence':items})
    return out


def audit_dataset(database_path, *, inputs=None, results=None, gold=None, rights_registry=None, root_dir=None, verify_raw=True):
    """Audit actual SQLite snapshot plus optional fixed inputs/classification/gold.

    Uses SQLite URI mode=ro and a read transaction; never initializes a missing
    DB, applies migrations, infers bibliographic facts or fabricates annotations.
    """
    database_path=Path(database_path).resolve()
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    root=Path(root_dir or Path.cwd()).resolve()
    rights=rights_registry or RightsRegistry.default()
    connection=sqlite3.connect(database_path.as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        schema_version=connection.execute('PRAGMA user_version').fetchone()[0]
        def table(name):
            return [dict(row) for row in connection.execute(f'SELECT * FROM {name}')]
        sources=table('source_records')
        heads=table('source_heads')
        works=table('works')
        evidence=table('evidence')
        assertions=table('field_assertions')
        expressions=table('expressions')
        editions=table('editions')
        resolutions=table('resolution_decisions')
        reviews=table('review_items')
        foreign_key_errors=[list(row) for row in connection.execute('PRAGMA foreign_key_check')]
    finally:
        connection.close()
    findings=[]
    def finding(code,entity,detail,severity='error'):
        findings.append({'code':code,'entity_id':entity,'detail':str(detail),'severity':severity})
    for source in sources:
        source['payload']=json.loads(source['record_json'])
    by_source={source['version_id']:source for source in sources}
    for item in evidence:
        item['payload']=json.loads(item['evidence_json'])
    active_ids={h['version_id'] for h in heads}
    active=[s for s in sources if s['version_id'] in active_ids]
    input_sources=_input_sources(by_source,active_ids)
    resolved_ids={w['canonical_id'] for w in works if w['resolved']}
    unresolved_ids={w['canonical_id'] for w in works if not w['resolved']}
    row_inputs=records(inputs) if inputs is not None else _derive_inputs(works,input_sources,evidence)
    invalid_input_keys=set()
    by_work={}
    for row in row_inputs:
        if row['work_id'] not in resolved_ids:
            finding('unknown_input_work',row['work_id'],'Fixed input work is not a resolved canonical ID in DB')
            invalid_input_keys.add((row['work_id'],row.get('expression_id')))
        if row['work_id'] not in by_work or row.get('expression_id') is None:
            by_work[row['work_id']]=row
    # Distributions are DB facts, not translated labels or model target language.
    canonical=defaultdict(list)
    for work in works:
        if work['resolved']:
            canonical[work['canonical_id']].append(work)
    metadata=[]
    for work_id,nodes in sorted(canonical.items()):
        source_rows=[input_sources[node['source_version_id']] for node in nodes]
        values=[source['payload'] for source in source_rows if source['payload']['record_kind']=='work']
        claims={row.get('original_language') for row in values if row.get('original_language')}
        if len(claims)>1:
            finding('conflicting_original_languages',work_id,', '.join(sorted(claims)),'warning')
        row={'work_id':work_id,**_work_metadata(source_rows)}
        metadata.append(row)
        if work_id in by_work:
            for field in ('original_language','first_publication_year'):
                if by_work[work_id].get(field)!=row[field]:
                    finding('input_metadata_differs',work_id,f'{field}: input={by_work[work_id].get(field)!r}; DB={row[field]!r}','warning')
                    invalid_input_keys.add((work_id,by_work[work_id].get('expression_id')))
    n=len(metadata)
    distributions={'original_language':dict(sorted(Counter(r['original_language'] or 'unknown' for r in metadata).items())),
        'first_publication_period':dict(sorted(Counter(_year(r['first_publication_year']) for r in metadata).items())),
        'region':dict(sorted(Counter(region for row in metadata for region in row['region'] or ['unknown']).items())),
        'work_kind':dict(sorted(Counter(r['work_kind'] or 'unknown' for r in metadata).items())),
        'active_source':dict(sorted(Counter(r['source'] for r in active).items()))}
    missingness={field:{'n':sum(not row.get(field) if field!='first_publication_year' else row.get(field) is None for row in metadata),
        'denominator':n} for field in ('title','authors','original_language','first_publication_year','region','work_kind','series')}
    for metric in missingness.values():
        metric['rate']=_rate(metric['n'],metric['denominator'])
    source_valid=0
    raw_valid=0
    raw_hash_cache={}
    for source in active:
        policy=rights.policies.get(source['rights_policy_id'])
        valid=True
        for field in ('source','source_id','provider_id','retrieved_at','rights_policy_id','raw_sha256','raw_path'):
            if not source.get(field):
                finding('missing_source_provenance',source['version_id'],field)
                valid=False
        if not _url(source.get('source_url')):
            finding('invalid_source_url',source['version_id'],source.get('source_url'))
            valid=False
        if policy is None:
            finding('unknown_source_policy',source['version_id'],source['rights_policy_id'])
            valid=False
        elif policy.source!=source['source'] or policy.provider_id not in ('*',source['provider_id']) or not policy.evidence_urls or not policy.verified_at:
            finding('source_policy_binding_mismatch',source['version_id'],source['rights_policy_id'])
            valid=False
        source_valid+=valid
        if verify_raw:
            raw=Path(source['raw_path'])
            raw=(root/raw).resolve() if not raw.is_absolute() else raw.resolve()
            if not raw.is_relative_to(root):
                finding('raw_path_outside_audit_root',source['version_id'],'Not read outside explicitly supplied audit root')
            elif not raw.is_file():
                finding('missing_raw_artifact',source['version_id'],source['raw_path'])
            else:
                if raw not in raw_hash_cache:
                    digest=hashlib.sha256()
                    with raw.open('rb') as stream:
                        for block in iter(lambda:stream.read(1024*1024),b''):
                            digest.update(block)
                    raw_hash_cache[raw]=digest.hexdigest()
                if raw_hash_cache[raw]!=source['raw_sha256']:
                    finding('raw_hash_mismatch',source['version_id'],source['raw_path'])
                else:
                    raw_valid+=1
    evidence_valid=0
    permitted_work_ids=set()
    declared_work_ids=set()
    kinds=defaultdict(lambda:{'stored_evidence_n':0,'stored_works':set(),'declared_llm_works':set(),'audited_llm_works':set()})
    conditional_pending=[]
    invalid_scope=0
    for item in evidence:
        value=item['payload']
        work_id=item['work_id']
        policy=rights.policies.get(value.get('rights_policy_id'))
        valid=True
        if not _url(value.get('source_url')):
            finding('invalid_evidence_url',value['evidence_id'],value.get('source_url'))
            valid=False
        if not value.get('evidence_id') or not value.get('kind'):
            finding('missing_evidence_provenance',item['evidence_key'],'evidence_id/kind')
            valid=False
        if policy is None:
            finding('unknown_evidence_policy',value['evidence_id'],value.get('rights_policy_id'))
            valid=False
        elif value.get('kind') not in policy.fields or not policy.evidence_urls or not policy.verified_at:
            finding('evidence_policy_scope_mismatch',value['evidence_id'],value.get('rights_policy_id'))
            valid=False
        if item['source_version_id'] not in by_source:
            finding('unknown_evidence_source',value['evidence_id'],item['source_version_id'])
            valid=False
        evidence_valid+=valid
        scope_valid=bool(item.get('scope_valid',1))
        invalid_scope+=not scope_valid
        kind=kinds[value.get('kind','unknown')]
        kind['stored_evidence_n']+=1
        if work_id in resolved_ids:
            kind['stored_works'].add(work_id)
        if value.get('llm_input_allowed') is True and scope_valid and work_id in resolved_ids:
            declared_work_ids.add(work_id)
            kind['declared_llm_works'].add(work_id)
            state=policy.usages.get('external_llm','unresolved') if policy else 'unresolved'
            if valid and state=='allowed':
                permitted_work_ids.add(work_id)
                kind['audited_llm_works'].add(work_id)
            elif valid and state=='conditional':
                conditional_pending.append(value['evidence_id'])
                finding('conditional_obligations_not_machine_verified',value['evidence_id'],', '.join(policy.obligations),'warning')
            else:
                finding('llm_flag_not_supported_by_policy',value['evidence_id'],state)
    field_valid=0
    for assertion in assertions:
        source=by_source.get(assertion['source_version_id'])
        policy=rights.policies.get(assertion['rights_policy_id'])
        valid=source is not None and policy is not None and _url(assertion.get('source_url')) and bool(assertion.get('provider_id')) and bool(assertion.get('retrieved_at'))
        if source and policy:
            valid=valid and policy.source==source['source'] and policy.provider_id in ('*',source['provider_id'])
        field_valid+=valid
        if not valid:
            finding('invalid_field_provenance',assertion['assertion_id'],assertion['field_name'])
    input_ref_valid=0
    db_evidence=defaultdict(list)
    for item in evidence:
        if item.get('scope_valid',1):
            db_evidence[(item['work_id'],item['expression_id'])].append(item['payload'])
    canonical_sources=defaultdict(set)
    for work in works:
        canonical_sources[work['canonical_id']].add(work['source_version_id'])
    for row in row_inputs:
        input_key=(row['work_id'],row.get('expression_id'))
        refs=row.get('source_record_ids') or []
        valid=bool(refs) and all(ref in by_source for ref in refs) and set(refs)==canonical_sources[row['work_id']]
        input_ref_valid+=valid
        if not valid:
            finding('invalid_input_source_refs',row['work_id'],'No source versions or unknown source version')
            invalid_input_keys.add(input_key)
        expected=db_evidence[input_key]
        for item in row.get('evidence',[]):
            if item not in expected:
                finding('input_evidence_not_in_db',row['work_id'],item.get('evidence_id','unknown'))
                invalid_input_keys.add(input_key)
    for error in foreign_key_errors:
        finding('foreign_key_violation',str(error[0]),str(error))
    provenance={'source_records':{'valid_n':source_valid,'denominator':len(active),'valid_rate':_rate(source_valid,len(active))},
        'evidence':{'valid_n':evidence_valid,'denominator':len(evidence),'valid_rate':_rate(evidence_valid,len(evidence))},
        'field_assertions':{'valid_n':field_valid,'denominator':len(assertions),'valid_rate':_rate(field_valid,len(assertions))},
        'input_source_references':{'valid_n':input_ref_valid,'denominator':len(row_inputs),'valid_rate':_rate(input_ref_valid,len(row_inputs))},
        'raw_integrity':{'status':'verified' if verify_raw else 'not_checked','valid_n':raw_valid if verify_raw else None,'denominator':len(active),'valid_rate':_rate(raw_valid,len(active)) if verify_raw else None},
        'foreign_key_errors':foreign_key_errors,
        'definition':'Reference presence, registry scope/binding and URL form; this does not establish blanket commercial permission or independent verification of legal obligations.'}
    provenance['all_required_references_100_percent']=bool(active) and source_valid==len(active) and evidence_valid==len(evidence) and field_valid==len(assertions) and input_ref_valid==len(row_inputs) and not foreign_key_errors
    result_rows=records(results)
    input_index={(row['work_id'],row.get('expression_id')):row for row in row_inputs}
    valid_results=[]
    duplicates=set()
    result_keys=set()
    from recommend_book.classification.validation import validate_result, load_taxonomy
    taxonomy=load_taxonomy()
    for result in result_rows:
        key=(result.get('work_id'),result.get('expression_id'))
        if key in result_keys:
            duplicates.add(key)
            finding('duplicate_classification_result',str(key),'One result per fixed work/expression snapshot is required')
            continue
        result_keys.add(key)
        if key not in input_index:
            finding('classification_without_input',str(key),'Unknown work/expression input')
            continue
        if key in invalid_input_keys:
            finding('classification_input_snapshot_invalid',str(key),'Fixed input does not match actual DB source/evidence snapshot')
            continue
        try:
            validate_result(result,WorkInput.model_validate(input_index[key]),taxonomy)
        except ValueError as error:
            finding('invalid_classification_result',str(key),str(error)[:600])
        else:
            valid_results.append(result)
    decisions=Counter(label['decision'] for result in valid_results for label in result['labels'])
    valid_works={r['work_id'] for r in valid_results}
    decided_works={r['work_id'] for r in valid_results if any(l['decision']!='unknown' for l in r['labels'])}
    decided_label_slots={(r['work_id'],label['label_id']) for r in valid_results for label in r['labels'] if label['decision']!='unknown'}
    axis_counts=defaultdict(lambda:{'present':0,'absent':0,'unknown':0,'decided_works':set()})
    for result in valid_results:
        for label in result['labels']:
            axis_counts[label['axis']][label['decision']]+=1
            if label['decision']!='unknown':
                axis_counts[label['axis']]['decided_works'].add(result['work_id'])
    for value in axis_counts.values():
        value['decided_works']=len(value['decided_works'])
        value['all_registered_work_coverage']=_rate(value['decided_works'],n)
    human=[]
    excluded_gold=[]
    for target in records(gold):
        if target.get('work_id') not in resolved_ids:
            excluded_gold.append({'work_id':target.get('work_id'),'reason':'Unknown canonical DB work'})
            finding('unknown_gold_work',target.get('work_id','unknown'),'Unknown canonical DB work')
            continue
        try:
            validate_gold(target,{label['label_id'] for label in taxonomy['labels']})
        except ValueError as error:
            excluded_gold.append({'work_id':target.get('work_id'),'reason':str(error)})
            finding('invalid_gold_annotation',target.get('work_id','unknown'),str(error))
        else:
            if target['provenance']=='human':
                human.append(target)
    human_works={target['work_id'] for target in human}
    human_evidence={target['work_id'] for target in human if target['truth_level']=='evidence'}
    human_work={target['work_id'] for target in human if target['truth_level']=='work'}
    partitions=Counter(target['partition'] for target in human)
    primary={}
    for level in ('evidence','work'):
        support=Counter(label['label_id'] for target in human if target['partition']=='holdout' and target['truth_level']==level for label in target['labels'] if decision(label)=='present')
        primary[level]={label['label_id']:support[label['label_id']] for label in taxonomy['labels'] if label.get('primary')}
    quality_status='human_annotations_available_evaluation_required' if human else 'human_validation_pending'
    report={'schema_version':'1','created_at':datetime.now(timezone.utc).isoformat(),'database':str(database_path),'database_schema_version':schema_version,
        'snapshot':{'source_head_ids_sha256':hashlib.sha256(json.dumps(sorted(active_ids)).encode()).hexdigest(),
                    'inputs_sha256':hashlib.sha256(json.dumps(row_inputs,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                    'read_only':True},
        'counts':{'resolved_works':n,'unresolved_works':len(unresolved_ids),'work_nodes':len(works),'source_versions':len(sources),'active_source_records':len(active),
                  'expressions':len(expressions),'editions':len(editions),'evidence_rows':len(evidence),'field_assertions':len(assertions),'input_rows':len(row_inputs),
                  'distinct_input_works':len(by_work),'classification_result_rows':len(result_rows),'validated_result_rows':len(valid_results),'validated_result_works':len(valid_works),
                  'works_with_supported_decisions':len(decided_works),'review_queue':len(reviews),'active_resolution_decisions':sum(row['active'] and not row['undone'] for row in resolutions)},
        'distributions':distributions,'missingness':missingness,'provenance':provenance,
        'evidence_coverage':{'declared_llm_evidence_works':len(declared_work_ids),'declared_llm_rate':_rate(len(declared_work_ids),n),
            'audited_llm_evidence_works':len(permitted_work_ids),'audited_llm_rate':_rate(len(permitted_work_ids),n),
            'no_declared_llm_evidence_works':n-len(declared_work_ids),'invalid_scope_evidence_rows':invalid_scope,
            'conditional_obligations_pending_evidence_ids':conditional_pending,
            'by_kind':{kind:{'stored_evidence_n':value['stored_evidence_n'],**{field:len(value[field]) for field in ('stored_works','declared_llm_works','audited_llm_works')},'audited_llm_all_work_rate':_rate(len(value['audited_llm_works']),n)} for kind,value in sorted(kinds.items())},
            'limitation':'Bibliographic facts, subjects and disambiguating descriptions are not a synopsis or original-language excerpt; their availability does not establish style/structure coverage.'},
        'classification':{'schema_and_reference_valid_rate':_rate(len(valid_results),len(result_rows)), 'all_work_processed_coverage':_rate(len(valid_works),n),
            'all_work_decided_coverage':_rate(len(decided_works),n),'decision_counts':dict(decisions),'axes':dict(axis_counts),
            'all_work_taxonomy_label_slots':n*len(taxonomy['labels']),'distinct_decided_work_label_slots':len(decided_label_slots),
            'all_work_label_decision_coverage':_rate(len(decided_label_slots),n*len(taxonomy['labels'])),
            'label_coverage_definition':'Any validated work/expression decision covers a work-label slot once. This does not establish original-style correctness or human-eligible coverage.',
            'calibration_status':'uncalibrated' if not any(label.get('calibration_status')=='calibrated' for row in valid_results for label in row['labels']) else 'requires_independent_calibration_audit',
            'accuracy_status':'not_measured_by_dataset_audit'},
        'quality':{'status':quality_status,'quality_completed':False,'human_annotation_works':len(human_works),'human_evidence_truth_works':len(human_evidence),'human_work_truth_works':len(human_work),
            'partition_annotation_rows':dict(partitions),'holdout_primary_positive_support':primary,'major_labels_with_support_below20':{level:[label for label,count in counts.items() if count<20] for level,counts in primary.items()},
            'declared_reviewer_count':len({row['reviewer_id'] for row in human}),
            'excluded_gold':excluded_gold,
            'unverified':['Human precision/recall/F1 and grouped confidence intervals','Relevance MAE/rank correlation against human judgments','Input fidelity and unsupported assertions on human-undecidable items',
                'Work-level content truth, original-language style and ending/structure judgments','Calibration, high-confidence adoption precision and coverage','Independent two-reviewer agreement and adjudication','Resolution pair precision/recall and complete-cluster false merges']},
        'human_requirements':{'trial':{'target_works':20,'completed_work_count':0,'completed_measurement_status':'not_supplied','record':'Real active reviewer minutes, interruptions, reviewer count, unknown fraction, additional source/reading time'},
            'classification':{'target_works':400,'dev':150,'calibration':100,'holdout':150,'independent_reviewers':2,'expected_independent_work_reviews':800,'adjudication_required':True},
            'work_knowledge_benchmark':{'initial_minimum_works':50,'initial_maximum_works':100,'required':'Knowledgeable human reader or explicitly sufficient documented sources; preserve evidence/work truth levels separately'},
            'resolution_pairs':{'minimum':300,'maximum':500,'required_types':['original-translation','different-translators','edition-change','same-title-different-work','different-volumes','story-vs-anthology','adaptation']}},
        'review_preconditions':{**review_preconditions(row_inputs),'input_snapshot_matches_db':not invalid_input_keys,'input_source_references_100_percent':input_ref_valid==len(row_inputs)},'findings':findings,
        'notes':['Original language uses explicit original_language metadata only; an English translated title, English Wikidata label, evidence language or target_language is never original-language evidence.',
            'Work metadata uses work records only, prioritized Wikidata, Open Library, other sources, then Wikipedia, with per-field nonempty fallback. Edition language, publication date, region and series never fill original-work metadata. Source references still retain edition records.',
            'Region is explicit work/author origin metadata; story setting, citizenship and language are not substituted.',
            'Region memberships overlap. Missing series means not supplied or not applicable, not evidence of a data error.',
            'Counts and provenance checks do not establish classification quality or readiness for commercial publication.']}
    return report


def write_audit_report(report, output_dir):
    output=Path(output_dir)
    output.mkdir(parents=True,exist_ok=True)
    json_path=output/'dataset-audit.json'
    html_path=output/'dataset-audit.html'
    json_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    escape=lambda value:html.escape(str(value),quote=True)
    def table(headers,rows):
        return '<table><thead><tr>'+''.join('<th>'+escape(v)+'</th>' for v in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'
    def percent(value):
        return '未計測' if value is None else f'{value*100:.1f}%'
    sections=[]
    counts=report['counts']
    sections.append('<section><h2>作品数と実行数</h2>'+table(['項目','件数'],counts.items())+'</section>')
    for name,values in report['distributions'].items():
        sections.append('<section><h2>'+escape(name)+'</h2>'+table(['値','件数'],values.items())+'</section>')
    sections.append('<section><h2>欠損</h2>'+table(['項目','欠損数','作品数','欠損率'],[(k,v['n'],v['denominator'],percent(v['rate'])) for k,v in report['missingness'].items()])+'</section>')
    sections.append('<section><h2>出典・権利参照</h2><p>必須参照100%: '+escape(report['provenance']['all_required_references_100_percent'])+'</p>'+table(['項目','有効数','母数','割合'],[(k,v['valid_n'],v['denominator'],percent(v['valid_rate'])) for k,v in report['provenance'].items() if isinstance(v,dict) and 'valid_rate' in v])+'<p>'+escape(report['provenance']['definition'])+'</p></section>')
    evidence=report['evidence_coverage']
    sections.append('<section><h2>分類資料のcoverage</h2><p>許可フラグ付き: '+str(evidence['declared_llm_evidence_works'])+'作品 / 権利台帳で無条件許可まで確認: '+str(evidence['audited_llm_evidence_works'])+'作品</p>'+table(['資料種別','保存資料数','保存作品数','許可宣言作品数','監査済み作品数','全作品coverage'],[(k,v['stored_evidence_n'],v['stored_works'],v['declared_llm_works'],v['audited_llm_works'],percent(v['audited_llm_all_work_rate'])) for k,v in evidence['by_kind'].items()])+'<p>'+escape(evidence['limitation'])+'</p></section>')
    sections.append('<section><h2>人手評価の必要量</h2>'+table(['対象','必要量'],[('時間計測試行','20作品。人手作業実施は別途記録'),('入力忠実性','400作品、開発150・校正100・最終150'),('独立人手判定','2名×400作品=800作品レビュー＋不一致裁定'),('作品内容の詳細評価','初期50〜100作品。読了または十分な資料が必要'),('名寄せ正解','300〜500ペア、難例種類を含む'),('主要ラベル','最終評価の正例20件以上を各ラベルに要求')])+'</section>')
    preflight=report['review_preconditions']
    sections.append('<section><h2>400作品分割と20作品試行の前提</h2>'+table(['項目','値'],[('異なる入力作品数',preflight['distinct_work_count']),('400作品件数条件',preflight['human400']['work_count_ready']),('正確なグループ分割の事前検査',preflight['human400']['exact_grouped_split_ready']),('著者・シリーズのグループ数',preflight['human400']['independent_author_series_groups']),('最大グループ作品数',preflight['human400']['largest_group_works']),('20作品件数条件',preflight['trial20']['work_count_ready'])])+'<p>'+escape(preflight['human400']['input_requirement'])+'</p><ul>'+''.join('<li>'+escape(v)+'</li>' for v in preflight['human400']['limitations'])+'</ul></section>')
    sections.append('<section><h2>未検証の品質項目</h2><ul>'+''.join('<li>'+escape(value)+'</li>' for value in report['quality']['unverified'])+'</ul></section>')
    sections.append('<section><h2>監査指摘</h2>'+table(['重要度','種類','対象','内容'],[(f['severity'],f['code'],f['entity_id'],f['detail']) for f in report['findings']])+'</section>')
    pending=report['quality']['status']=='human_validation_pending'
    banner='人手評価未実施：分類精度・校正・品質完了は未確認です。' if pending else '人手注釈があります。精度評価・独立レビュー・支持件数の確認が必要です。'
    page='<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>小説データセット監査</title><style>body{font:15px system-ui;margin:auto;max-width:1100px;padding:24px;background:#f5f6f8;color:#19202a}section{background:white;margin:16px 0;padding:20px;border-radius:10px;overflow:auto}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #dde1e6;padding:8px;text-align:left;vertical-align:top}th{background:#f5f6f8}.pending{background:#fff0cd;border-left:5px solid #c78c05;padding:16px}h1{margin-bottom:8px}p{line-height:1.7}code{word-break:break-all}</style><h1>小説データセット監査</h1><p class="pending">'+escape(banner)+'</p><p>DB: <code>'+escape(report['database'])+'</code><br>作成: '+escape(report['created_at'])+' / <a href="dataset-audit.json" download>JSONを保存</a></p><p>作品・表現・版・取得レコードの件数を分けています。英語の翻訳題・表示ラベルから原言語を推測しません。</p>'+''.join(sections)+'<section><h2>解釈上の制約</h2><ul>'+''.join('<li>'+escape(value)+'</li>' for value in report['notes'])+'</ul></section></html>'
    html_path.write_text(page,encoding='utf-8')
    return {'json':str(json_path),'html':str(html_path),'quality_status':report['quality']['status']}
