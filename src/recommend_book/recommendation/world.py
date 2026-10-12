"""Immutable classification DB -> work recommendation rows, without inference."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from recommend_book.classification.validation import input_hash, load_taxonomy, validate_result
from recommend_book.jobs import canonical_json
from recommend_book.models import Evidence, WorkInput

NULL_RELEVANCE_STRENGTH = 1.0
POLICY_VERSION = 'world-work-features-v1'


def _sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(1024*1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json(raw):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('Duplicate JSON property: '+key)
            value[key]=item
        return value
    def nonfinite(value):raise ValueError('Non-finite JSON value: '+value)
    def floating(value):
        number=float(value)
        return number if math.isfinite(number) else nonfinite(value)
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=nonfinite,parse_float=floating)


def _value_sha(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _same_fields(saved, expected, fields, message):
    if any(saved[field]!=expected[field] for field in fields):raise ValueError(message)


def _read(db):
    inputs=db.execute('SELECT * FROM classification_inputs ORDER BY selected_position,job_key').fetchall()
    labels=defaultdict(list)
    for label in db.execute('SELECT * FROM classification_labels ORDER BY job_key,label_id'):
        labels[label['job_key']].append(label)
    refs=defaultdict(list)
    for ref in db.execute('SELECT * FROM classification_label_evidence ORDER BY job_key,label_id,position'):
        refs[ref['job_key'],ref['label_id']].append(ref)
    stored_evidence={}
    for row in db.execute('SELECT * FROM classification_input_evidence'):
        stored_evidence[row['job_key'],row['evidence_id']]=row
    sources=defaultdict(list)
    for row in db.execute('SELECT * FROM classification_source_refs ORDER BY job_key,position'):
        sources[row['job_key']].append(row)
    source_records={r['version_id']:r for r in db.execute('SELECT version_id,payload_sha256,raw_sha256,source_url FROM source_records')}
    original_evidence={r['evidence_key']:r for r in db.execute('SELECT evidence_key,work_id,expression_id,scope_valid,evidence_json FROM evidence')}
    authors=defaultdict(set)
    # A stored source-local name hash alone is insufficient authority evidence.
    for row in db.execute('''SELECT DISTINCT w.canonical_id,c.agent_id FROM contributions c
        JOIN works w ON w.work_id=c.work_id JOIN agents a ON a.agent_id=c.agent_id
        WHERE c.role='author' AND w.resolved=1 AND EXISTS
        (SELECT 1 FROM external_ids e WHERE e.entity_id=c.agent_id AND e.scope='agent')'''):
        authors[row[0]].add(row[1])
    series=defaultdict(set)
    for row in db.execute('''SELECT DISTINCT w.canonical_id,r.series_id FROM work_relations r
        JOIN works w ON w.work_id=r.work_id JOIN series s ON s.series_id=r.series_id
        WHERE r.relation_type='series_member' AND w.resolved=1'''):
        series[row[0]].add(row[1])
    external=defaultdict(set)
    for row in db.execute('''SELECT DISTINCT w.canonical_id,e.source,e.value FROM external_ids e
        JOIN works w ON w.work_id=e.entity_id WHERE e.scope='work' AND w.resolved=1'''):
        external[row[0]].add((row[1],row[2]))
    taxonomy=load_taxonomy()
    grouped=defaultdict(list)
    audit={'policy_version':POLICY_VERSION,'null_relevance_strength':NULL_RELEVANCE_STRENGTH,
           'scope_policy':'work only; original/expression/translation held',
           'aggregation':'max strength per work/feature; all contributing provenance retained',
           'raw_confidence_used_as_weight':False,'ratings_inferred':False,'read_status_inferred':False,
           'author_identity_policy':'DB agent ID with explicit agent-scope external identifier; never input author name',
           'selected_inputs':len(inputs),'labels':sum(map(len,labels.values())),
           'label_evidence_refs':sum(map(len,refs.values())),
           'scope_excluded_labels':0,'scope_excluded_by_scope':Counter(),
           'nonpresent_labels':Counter(),'null_relevance_features':0,'duplicate_feature_observations':0,
           'display_primary_policy':'first selected-position original input; expression input only if no original',
           'metadata_conflicts':[]}
    status=Counter()
    seen=set()
    for row in inputs:
        key=row['job_key'];work=WorkInput.model_validate(_json(row['input_json']))
        if row['input_hash']!=input_hash(work) or row['work_id']!=work.work_id or row['expression_id']!=work.expression_id:
            raise ValueError('Saved input identity/hash differs')
        identity=row['run_id'],work.work_id,work.expression_id
        if identity in seen:raise ValueError('Duplicate saved work/expression input')
        seen.add(identity)
        if [r['source_version_id'] for r in sources[key]]!=work.source_record_ids:
            raise ValueError('Immutable source version refs differ from input')
        for source in sources[key]:
            original=source_records.get(source['source_version_id'])
            if original is None:raise ValueError('Immutable source version absent')
            _same_fields(source,original,('payload_sha256','raw_sha256'),'Immutable source lineage differs')
        for evidence in work.evidence:
            saved=stored_evidence.get((key,evidence.evidence_id))
            if saved is None or Evidence.model_validate(_json(saved['evidence_json']))!=evidence:
                raise ValueError('Saved input evidence differs')
            evidence_keys=_json(saved['source_evidence_keys_json'])
            if not evidence_keys:raise ValueError('Saved evidence source lineage absent')
            for evidence_key in evidence_keys:
                original=original_evidence.get(evidence_key)
                if (original is None or original['scope_valid']!=1 or original['work_id']!=work.work_id or
                    original['expression_id']!=work.expression_id or Evidence.model_validate(_json(original['evidence_json']))!=evidence):
                    raise ValueError('Saved evidence scope/rights/source lineage differs')
        status[row['status']]+=1
        grouped[work.work_id].append((row,work))
        if row['status']!='completed':
            if row['status'] not in ('held','invalid_cached_result') or row['result_json'] is not None or labels[key]:
                raise ValueError('Noncompleted input unexpectedly has result/labels')
            continue
        result=_json(row['result_json'])
        if row['result_sha256']!=_value_sha(result):raise ValueError('Saved result hash differs')
        validate_result(result,work,taxonomy)
        by_label={label['label_id']:label for label in result['labels']}
        if {label['label_id'] for label in labels[key]}!=set(by_label) or len(labels[key])!=len(by_label):
            raise ValueError('Saved labels differ from result')
        for saved in labels[key]:
            label=by_label[saved['label_id']]
            if _json(saved['label_json'])!=label:raise ValueError('Saved label JSON differs')
            _same_fields(saved,label,('label_id','axis','decision','scope','expression_id','relevance','raw_confidence',
                'calibrated_confidence','calibration_status','calibration_version'),'Saved label fields differ')
            if saved['human_verified']!=0:raise ValueError('Model labels cannot become human verification')
            saved_refs=refs[key,label['label_id']]
            if len(saved_refs)!=len(label['evidence']):raise ValueError('Saved label evidence count differs')
            for saved_ref,reference in zip(saved_refs,label['evidence']):
                if _json(saved_ref['reference_json'])!=reference:raise ValueError('Saved reference JSON differs')
                _same_fields(saved_ref,reference,('evidence_id','locator','quote','summary','directness'),'Saved reference fields differ')
    rows=[]
    for work_id,members in sorted(grouped.items()):
        members.sort(key=lambda pair:(pair[1].expression_id is not None,pair[0]['selected_position'],pair[0]['job_key']))
        primary=members[0][1]
        source_urls=set()
        for _,work in members:
            source_urls.update(item.source_url for item in work.evidence if item.source_url)
            source_urls.update(source_records[version]['source_url'] for version in work.source_record_ids
                               if source_records[version]['source_url'])
        for field in ('title','authors','original_language','first_publication_year','region','series'):
            variants={canonical_json(getattr(work,field)):getattr(work,field) for _,work in members}
            if len(variants)>1:
                audit['metadata_conflicts'].append({'work_id':work_id,'field':field,
                    'primary_value':getattr(primary,field),'observed_values':[variants[k] for k in sorted(variants)]})
        features={};provenance=defaultdict(list)
        for row,work in members:
            if row['status']!='completed':continue
            result=_json(row['result_json'])
            evidence={item.evidence_id:item for item in work.evidence}
            for label in result['labels']:
                if label['decision']!='present':
                    audit['nonpresent_labels'][label['decision']]+=1;continue
                if label['scope']!='work':
                    audit['scope_excluded_labels']+=1
                    audit['scope_excluded_by_scope'][label['scope']]+=1;continue
                strength=float(label['relevance']) if label['relevance'] is not None else NULL_RELEVANCE_STRENGTH
                if not math.isfinite(strength) or strength<0:raise ValueError('Invalid feature strength')
                feature=label['label_id']
                if feature in features:audit['duplicate_feature_observations']+=1
                features[feature]=max(features.get(feature,0.0),strength)
                if label['relevance'] is None:audit['null_relevance_features']+=1
                provenance[feature].append({'job_key':row['job_key'],'run_id':row['run_id'],
                    'input_hash':row['input_hash'],'source_record_ids':list(work.source_record_ids),
                    'label_id':feature,'axis':label['axis'],'decision':label['decision'],'scope':label['scope'],
                    'input_expression_id':work.expression_id,'expression_id':label['expression_id'],
                    'target_language':result['target_language'],'relevance':label['relevance'],
                    'feature_strength':strength,'null_relevance_policy':NULL_RELEVANCE_STRENGTH,
                    'raw_confidence':label['raw_confidence'],'raw_confidence_used_as_weight':False,
                    'calibration_status':label['calibration_status'],'human_verified':False,
                    'evidence':[{'evidence_id':ref['evidence_id'],'locator':ref['locator'],
                        'source_url':evidence[ref['evidence_id']].source_url,'kind':evidence[ref['evidence_id']].kind,
                        'rights_policy_id':evidence[ref['evidence_id']].rights_policy_id,
                        'llm_input_allowed':evidence[ref['evidence_id']].llm_input_allowed,
                        'language':evidence[ref['evidence_id']].language,'directness':ref['directness']}
                        for ref in label['evidence']]})
        rows.append({'work_id':work_id,'title':primary.title,'features':dict(sorted(features.items())),
            'rating':None,'author_ids':sorted(authors[work_id]),'series_ids':sorted(series[work_id]),
            'read_status':'unknown','external_ids':[{'source':source,'value':value} for source,value in sorted(external[work_id])],
            'feature_provenance':dict(sorted(provenance.items())),'popularity':None,
            'authors':list(primary.authors),'original_language':primary.original_language,
            'first_publication_year':primary.first_publication_year,'region':list(primary.region),
            'series':primary.series,'source_urls':sorted(source_urls)})
    audit.update(distinct_works=len(rows),completed_inputs=status['completed'],held_inputs=status['held'],
                 invalid_cached_result_inputs=status['invalid_cached_result'],empty_features=sum(not row['features'] for row in rows),
                 works_with_features=sum(bool(row['features']) for row in rows),
                 works_with_author_ids=sum(bool(row['author_ids']) for row in rows),
                 works_with_series_ids=sum(bool(row['series_ids']) for row in rows),
                 title_conflicts=sum(len({work.title for _,work in members})>1 for members in grouped.values()))
    audit['metadata_conflict_count']=len(audit['metadata_conflicts'])
    audit['scope_excluded_by_scope']=dict(audit['scope_excluded_by_scope'])
    audit['nonpresent_labels']=dict(audit['nonpresent_labels'])
    return {'rows':rows,'audit':audit}


def load_world_database(path)->dict:
    """Return all selected works; never opens DatasetDB or writes SQLite/sidecars."""
    path=Path(path).resolve(strict=True)
    wal=Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size:raise ValueError('Immutable world DB has nonempty WAL')
    before=_sha(path)
    db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('World DB foreign key violation')
        result=_read(db)
    finally:
        db.close()
    if _sha(path)!=before or wal.exists() and wal.stat().st_size:
        raise ValueError('World DB changed during immutable read')
    result['audit'].update(database_sha256=before,database_unchanged=True,sqlite_mode='ro+immutable+query_only',
                           input_hashes_changed=False,model_results_changed=False)
    return result
