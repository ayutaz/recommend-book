"""Candidate-only external reference audit. This module never authorizes an LLM."""
from __future__ import annotations
import hashlib,re,unicodedata
import xml.etree.ElementTree as ET
import csv,io,json
from pathlib import Path
from recommend_book.models import Evidence,SourceRecord,WorkInput
from recommend_book.autonomous_reference import build_reference_input as legacy_source_audit
from recommend_book.autonomous_reference import _parse, NS, sha256, extract_body, strict_json, METADATA_SHA, COMMIT, CC_BY

VERSION="next-reference-v3-v1"
HOLD_CATEGORIES={
    "unverified print-source date":"edition_date_missing",
    "restricted/missing source availability":"source_restricted",
    "US publication term not expired in 2026":"publication_term_unexpired",
    "underlying edition after reviewed US cutoff":"edition_term_or_additions_unverified",
    "unreviewed body tag castList":"body_scope_unreviewed",
    "outside conservative author term review":"author_term_unverified",
}

def hold_category(reason:str)->str:
    if reason not in HOLD_CATEGORIES:raise ValueError("unknown historical hold reason; preserve for manual code review")
    return HOLD_CATEGORIES[reason]

def author_keys(row:dict)->set[str]:
    keys=set()
    for field,prefix in (("author.viaf","viaf"),("author.bne","bne")):
        v=row.get(field,"")
        if not isinstance(v,str):raise ValueError("author identifier must be original metadata string")
        v=v.strip()
        if v.casefold() in {"","nan","none","unknown","?"}:continue
        if field=="author.viaf":
            if not re.fullmatch(r"\d+(?:\.0)?",v):raise ValueError("non-integral VIAF identifier")
            v=v.removesuffix(".0")
        keys.add(prefix+":"+v.casefold())
    name=row.get("author.name.full","")
    if not isinstance(name,str):raise ValueError("author name must be string")
    name=" ".join(unicodedata.normalize("NFKC",name).strip().casefold().split())
    if name and name not in {"unknown","?","anonymous"}:keys.add("name_block:"+name)
    return keys

def plan_cohort(rows:list[dict],observed_ids:set[str])->list[dict]:
    ids=[r.get("idno") for r in rows]
    if any(not isinstance(i,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]*",i) or '..' in i for i in ids):raise ValueError("invalid reference ID")
    if len(set(ids))!=len(ids) or not observed_ids<=set(ids):raise ValueError("duplicate/foreign reference identity")
    parents={i:i for i in ids};key_owner={};keys={r["idno"]:author_keys(r) for r in rows}
    def find(i):
        while parents[i]!=i:parents[i]=parents[parents[i]];i=parents[i]
        return i
    for i in ids:
        for k in sorted(keys[i]):
            if k in key_owner:
                a,b=find(i),find(key_owner[k]);parents[max(a,b)]=min(a,b)
            else:key_owner[k]=i
    groups={}
    for i in ids:groups.setdefault(find(i),set()).update(keys[i])
    observed_groups={find(i) for i in observed_ids}
    output=[]
    for i in ids:
        root=find(i)
        role=("observed_dev" if i in observed_ids else "unresolved_author_hold" if not keys[i]
              else "unobserved_author_overlap_dev" if root in observed_groups else "unobserved_author_disjoint_candidate")
        output.append({"reference_id":i,"cohort_role":role,
            "author_group":hashlib.sha256("\n".join(sorted(groups[root])).encode()).hexdigest() if keys[i] else None,
            "author_group_keys":sorted(keys[i]),"grouping_is_canonical_author_identity":False,
            "holdout_claim_permitted":False,"external_llm_approved":False,
            "approval_reason":"candidate-only; new source/use guard, fixed input and metric contract still required"})
    return output

def evaluation_contract()->dict:
    return {"version":VERSION,"maximum_candidate_count":2,"taxonomy_version_changed":False,
        "existing_60_role":"observed_dev_only","new_holdout_ready":False,"llm_execution_authorized":False,
        "candidates":[{"id":"definitions-v3","input":"fixed approved dev excerpts unchanged",
            "change":"Clarify central characters vs narrator; native single protagonist remains a separate task"},
            {"id":"context-v3","input":"same definitions plus deterministically wider approved original-text context",
             "change":"Fixed context recipe before new author-disjoint evaluation; no label-guided passage selection"}],
        "evaluated_world_axes":["protagonist"],
        "supported_positive_label_ids":["protagonist.child","protagonist.adult","protagonist.student","protagonist.artist"],
        "unevaluated_world_axes":["genre","theme","structure","style","atmosphere"],
        "native_tasks":["single_protagonist.age ordinal strings","single_protagonist.profession open nominal strings"],
        "metrics":["positive_detection","input_coverage","unknown_coverage","always_present_counterfactual","always_unknown"],
        "forbidden_metrics":["precision","F1","closed_world_accuracy","calibration"],
        "translation_style_scope":"original text or explicitly identified translation expression only; no cross-edition transfer",
        "annotation_leakage_policy":"annotations/metadata/expert summaries are excluded from every model input",
        "scope_limit":"Four positive correspondence hypotheses; native and multi-character taxonomy reported separately; not exhaustive six-axis truth"}


def context_ranges(body_chars:int,recipe:str)->list[tuple[int,int]]:
    """Fixed label-independent A/B recipes; offsets index extracted Unicode text."""
    if type(body_chars) is not int or body_chars<1:raise ValueError('positive integer body length required')
    if recipe=='definitions-v3':
        if body_chars<=18000:return [(0,body_chars)]
        return [(start,start+6000) for start in (0,(body_chars-6000)//2,body_chars-6000)]
    if recipe=='context-v3':
        if body_chars<=72000:return [(0,body_chars)]
        return [(i*(body_chars-12000)//5,i*(body_chars-12000)//5+12000) for i in range(6)]
    raise ValueError('unknown fixed context recipe')


def context_proof(raw:bytes,recipe:str)->dict:
    """Reproducible source-only sidecar, without text or source answers."""
    filtered,filter_audit=exclude_cast_lists(raw)
    body,extraction=extract_body(filtered)
    ranges=context_ranges(len(body),recipe)
    return {'recipe':recipe,'original_raw_sha256':sha256(raw),'body_sha256':sha256(body.encode('utf-8')),
        'body_chars':len(body),'filter':filter_audit,'extraction':extraction,
        'chunks':[{'char_range':[start,end],'text_sha256':sha256(body[start:end].encode('utf-8')),
                   'chars':end-start} for start,end in ranges],
        'input_chars':sum(end-start for start,end in ranges),'selection_uses_annotations':False,
        'external_llm_approved':False,'classifier_input_written':False}

def exclude_cast_lists(raw:bytes)->tuple[bytes,dict]:
    root=_parse(raw);body=root.find('t:text/t:body',NS)
    if body is None:raise ValueError('no original body')
    count=0
    def visit(parent):
        nonlocal count
        for child in list(parent):
            if child.tag=='{'+NS['t']+'}castList':
                children=list(parent);pos=children.index(child);tail=child.tail or ''
                if pos:children[pos-1].tail=(children[pos-1].tail or '')+tail
                else:parent.text=(parent.text or '')+tail
                count+=1;parent.remove(child)
            else:visit(child)
    visit(body);filtered=ET.tostring(root,encoding='utf-8')
    return filtered,{'recipe':'original-body-castList-exclusion-v1','excluded_castList_subtrees':count,
        'original_raw_sha256':sha256(raw),'filtered_xml_sha256':sha256(filtered),
        'scope':'Entire castList/dramatis-personae subtree removed; following narrative tail preserved',
        'primary_definition':'https://www.tei-c.org/release/doc/tei-p5-doc/en/html/ref-castList.html'}

def audit_native_source(raw:bytes,metadata:dict,reference:dict,*,source_url:str,exclude_cast_list:bool=False)->dict:
    fields=("protagonist.age","protagonist.profession")
    native=reference.get("native_labels",{})
    if any(not isinstance(native.get(f),str) or native[f]!=metadata.get(f) for f in fields):
        raise ValueError("native source/reference metadata differs")
    # These are original native fields used for identity checks, not new taxonomy
    # gold rows. The old auditor's temporary WorkInput is discarded here.
    ident=metadata["idno"]
    shim={"reference_id":ident,"work_id":"reference:conssa:"+ident,
        "labels":[{"native_field":f,"native_value":native[f]} for f in fields]}
    if type(exclude_cast_list) is not bool:raise ValueError('explicit recipe boolean required')
    original_sha=sha256(raw);filter_audit=None
    if exclude_cast_list:raw,filter_audit=exclude_cast_lists(raw)
    _,audit=legacy_source_audit(raw,metadata,shim,source_url=source_url)
    return {**audit,"raw_sha256":original_sha,"body_filter":filter_audit,"native_fields":{f:native[f] for f in fields},
        "legacy_term_envelope_passed":True,"external_llm_approved":False,
        "classifier_input_written":False,"new_human_annotation_generated":False,
        "usage_guard_state":"pending_new_v3_source_and_use_approval",
        "annotation_scope":"Original native single-protagonist attributes; no 72-label conversion"}


def verify_conssa_evidence(record,evidence,proof:dict,*,root:Path)->dict:
    """Reproduce fixed source facts only; the caller decides per-use permission.

    Annotation-bearing artifacts are consumed solely by this local verifier and
    are never returned as prompt material. Every descriptor is a raw byte bind.
    """
    root=Path(root).resolve();data=(root/'data').resolve();hashes=[]
    def bound(descriptor):
        path=Path(descriptor['path']);path=(path if path.is_absolute() else root/path).resolve()
        if not path.is_relative_to(data):raise ValueError('proof path outside data')
        expected=descriptor['sha256']
        if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('invalid proof hash')
        payload=path.read_bytes()
        if sha256(payload)!=expected:raise ValueError('proof raw hash differs')
        hashes.append(expected);return payload
    ident=proof['reference_id'];recipe=proof['recipe']
    if not isinstance(ident,str) or not re.fullmatch('ne[0-9]{4}',ident):raise ValueError('invalid source identity')
    source_url=f'https://raw.githubusercontent.com/cligs/conssa/{COMMIT}/master/master/{ident}.xml'
    if (record.source,record.provider_id,record.source_id,record.source_url,record.rights_policy_id)!=(
        'conssa','cligs',ident,source_url,'conssa-reviewed-original-v3'):raise ValueError('source policy identity differs')
    raw=bound({'path':record.raw_path,'sha256':record.raw_sha256})
    metadata_raw=bound(proof['metadata'])
    if sha256(metadata_raw)!=METADATA_SHA:raise ValueError('fixed metadata hash differs')
    metadata_rows=list(csv.DictReader(io.StringIO(metadata_raw.decode('utf-8')),delimiter='\t'))
    matches=[r for r in metadata_rows if r['idno']==ident]
    if len(matches)!=1:raise ValueError('metadata source identity not unique')
    metadata=matches[0]
    def select(descriptor,predicate):
        rows=[strict_json(x) for x in bound(descriptor).splitlines() if x]
        matches=[r for r in rows if predicate(r)]
        if len(matches)!=1:raise ValueError('source proof selector not unique')
        return matches[0]
    audit=select(proof['source_audit'],lambda r:r['reference_id']==ident)
    if audit['status']!='body_scope_candidate' or audit['source_url']!=source_url or audit['raw_sha256']!=record.raw_sha256:
        raise ValueError('audited source differs')
    reproduced=audit_native_source(raw,metadata,{'native_labels':audit['native_fields']},source_url=source_url,exclude_cast_list=True)
    expected=context_proof(raw,recipe)
    context=select(proof['context_proofs'],lambda r:r['reference_id']==ident and r['recipe']==recipe)
    for key,value in expected.items():
        if context.get(key)!=value:raise ValueError('context proof differs: '+key)
    if expected['body_sha256']!=reproduced['body_sha256'] or audit['source_audit']['body_sha256']!=expected['body_sha256']:
        raise ValueError('audited body hash differs')
    expression=f'conssa:{ident}:{COMMIT}'
    if context['expression_id']!=expression or evidence.expression_ref!=expression:raise ValueError('expression scope differs')
    locator=strict_json(evidence.locator)
    keys={'scope','body_sha256','char_range','recipe','original_raw_sha256','filtered_xml_sha256'}
    if not isinstance(locator,dict) or set(locator)!=keys:raise ValueError('exact locator fields required')
    if locator!={**locator,'scope':'corpus_original_body_excerpt','body_sha256':expected['body_sha256'],
        'recipe':recipe,'original_raw_sha256':record.raw_sha256,'filtered_xml_sha256':expected['filter']['filtered_xml_sha256']}:
        raise ValueError('evidence locator source differs')
    ranges=[c['char_range'] for c in expected['chunks']]
    if locator['char_range'] not in ranges:raise ValueError('evidence is outside fixed context ranges')
    index=ranges.index(locator['char_range']);start,end=locator['char_range']
    if any(type(v) is not int for v in (start,end)):raise ValueError('range requires exact integers')
    filtered,_=exclude_cast_lists(raw);body,_=extract_body(filtered)
    if evidence.text!=body[start:end] or sha256(evidence.text.encode())!=expected['chunks'][index]['text_sha256']:
        raise ValueError('evidence text not exact original body range')
    if (evidence.evidence_id,evidence.kind,evidence.language,evidence.source_url,evidence.rights_policy_id)!=(
        f'{ident}:{recipe}:body:{index+1}','excerpt','es',source_url,'conssa-reviewed-original-v3'):
        raise ValueError('evidence source/field differs')
    attribution=evidence.attribution
    if evidence.license_url!=CC_BY or attribution.get('creator')!='CLiGS / Jose Calvo Tello' or attribution.get('source')!=source_url or attribution.get('license')!=CC_BY:
        raise ValueError('source credit/license differs')
    changes=attribution.get('changes','')
    if not isinstance(changes,str) or not all(term in changes for term in ('header','attributes','annotations','castList','editorial',recipe)):
        raise ValueError('source modification disclosure incomplete')
    return {'reproduced':True,'proof_hashes':sorted(set(hashes)),
        'facts':{'reference_id':ident,'recipe':recipe,'language':'es','expression_id':expression,
            'source_audit_sha256':proof['source_audit']['sha256'],'context_proofs_sha256':proof['context_proofs']['sha256'],
            'original_raw_sha256':record.raw_sha256,'body_sha256':expected['body_sha256'],
            'char_range':[start,end],'text_sha256':expected['chunks'][index]['text_sha256'],
            'filter':expected['filter'],'editions':reproduced['editions'],
            'legacy_reviewed_regions':reproduced['rights']['reviewed_regions'],
            'no_permission_decision':True}}


def candidate_materials(raw:bytes,row:dict,recipe:str,*,descriptors:dict,retrieved_at:str):
    """Make unapproved input proposals; admission still requires the use guard."""
    if row['status']!='body_scope_candidate' or sha256(raw)!=row['raw_sha256']:raise ValueError('unverified candidate source')
    ident=row['reference_id'];source=f'https://raw.githubusercontent.com/cligs/conssa/{COMMIT}/master/master/{ident}.xml'
    if not re.fullmatch('ne[0-9]{4}',ident) or row['source_url']!=source:raise ValueError('candidate source identity differs')
    context=context_proof(raw,recipe)
    if context['body_sha256']!=row['source_audit']['body_sha256']:raise ValueError('candidate body differs')
    filtered,_=exclude_cast_lists(raw);body,_=extract_body(filtered)
    expression=f'conssa:{ident}:{COMMIT}';evidence=[];proofs={}
    for index,chunk in enumerate(context['chunks'],1):
        start,end=chunk['char_range'];evidence_id=f'{ident}:{recipe}:body:{index}'
        locator={'scope':'corpus_original_body_excerpt','body_sha256':context['body_sha256'],
            'char_range':[start,end],'recipe':recipe,'original_raw_sha256':row['raw_sha256'],
            'filtered_xml_sha256':context['filter']['filtered_xml_sha256']}
        evidence.append(Evidence(evidence_id=evidence_id,kind='excerpt',text=body[start:end],source_url=source,
            rights_policy_id='conssa-reviewed-original-v3',llm_input_allowed=False,language='es',expression_ref=expression,
            locator=json.dumps(locator,sort_keys=True),license='Original text individual review pending / CC-BY-4.0 XML',license_url=CC_BY,
            attribution={'creator':'CLiGS / Jose Calvo Tello','source':source,'license':CC_BY,
                'changes':f'TEI header, attributes, annotations, castList and editorial matter removed; fixed {recipe} body windows'},
            inherited_restrictions=['Individual source/use review pending; no worldwide PD claim; original expression only']))
        proofs[evidence_id]={**descriptors,'reference_id':ident,'recipe':recipe}
    record=SourceRecord(source='conssa',source_id=ident,source_url=source,retrieved_at=retrieved_at,provider_id='cligs',
        record_kind='edition',original_language='es',language='es',evidence=evidence,raw_sha256=row['raw_sha256'],raw_path=row['raw_path'],
        rights_policy_id='conssa-reviewed-original-v3',extra={'expression_id':expression,'candidate_only':True,
            'source_commit':COMMIT,'recipe':recipe,'annotation_answers_in_record':False})
    work=WorkInput(work_id='reference:conssa:'+ident,expression_id=expression,title='',authors=[],original_language='es',evidence=evidence)
    return record,work,proofs
