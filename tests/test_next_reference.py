"""Offline cohort/scope contracts; no model or source calls."""
import pytest
from recommend_book.next_reference import plan_cohort, evaluation_contract, hold_category

def row(ident,name="Writer",viaf="123",bne=""):
    return {"idno":ident,"author.name.full":name,"author.viaf":viaf,"author.bne":bne}

def test_observed_author_is_dev_even_for_new_work():
    plan=plan_cohort([row("ne0001"),row("ne0002"),row("ne0003","Other","999")],{"ne0001"})
    assert [r["cohort_role"] for r in plan]==["observed_dev","unobserved_author_overlap_dev","unobserved_author_disjoint_candidate"]
    assert all(r["holdout_claim_permitted"] is False for r in plan)

def test_transitive_author_alias_bridge_blocks_leakage():
    rows=[row("ne0001","Writer","1"),row("ne0002","Alias","1","XX2"),row("ne0003","Third","3","XX2")]
    plan=plan_cohort(rows,{"ne0001"})
    assert len({r["author_group"] for r in plan})==1
    assert plan[2]["cohort_role"]=="unobserved_author_overlap_dev"

def test_same_name_conservative_block_is_not_identity_merge():
    p=plan_cohort([row("ne0001","Ａ Name","1"),row("ne0002","a name","2")],{"ne0001"})
    assert p[1]["cohort_role"]=="unobserved_author_overlap_dev"
    assert p[1]["grouping_is_canonical_author_identity"] is False

def test_unknown_author_is_not_independent_holdout():
    p=plan_cohort([row("ne0001","","","")],set())
    assert p[0]["cohort_role"]=="unresolved_author_hold"

@pytest.mark.parametrize("rows,observed",[([row("ne0001"),row("ne0001")],set()),([row("ne0001")],{"ne9999"})])
def test_foreign_or_duplicate_identity_refused(rows,observed):
    with pytest.raises(ValueError):plan_cohort(rows,observed)

@pytest.mark.parametrize("reason,category",[("unverified print-source date","edition_date_missing"),("restricted/missing source availability","source_restricted"),("US publication term not expired in 2026","publication_term_unexpired"),("underlying edition after reviewed US cutoff","edition_term_or_additions_unverified"),("unreviewed body tag castList","body_scope_unreviewed"),("outside conservative author term review","author_term_unverified")])
def test_all_old_hold_reasons_preserved(reason,category):
    assert hold_category(reason)==category

def test_new_reason_cannot_be_silently_reclassified():
    with pytest.raises(ValueError):hold_category("unrecognized policy failure")

def test_two_candidate_positive_only_contract():
    c=evaluation_contract()
    assert len(c["candidates"])==2 and c["taxonomy_version_changed"] is False
    assert c["maximum_candidate_count"]==2
    assert c["forbidden_metrics"]==["precision","F1","closed_world_accuracy","calibration"]
    assert c["evaluated_world_axes"]==["protagonist"]
    assert set(c["unevaluated_world_axes"])=={"genre","theme","structure","style","atmosphere"}
    assert c["existing_60_role"]=="observed_dev_only"
    assert c["new_holdout_ready"] is False and c["llm_execution_authorized"] is False

def test_native_source_audit_cannot_emit_approved_input(monkeypatch):
    from recommend_book import next_reference as m
    seen={}
    def fake(raw,metadata,reference,**kwargs):
        seen.update(reference)
        return object(),{"raw_sha256":"fixture","sampling":{"body_chars":50000},"rights":{"external_llm_allowed_for_reviewed_text":True}}
    monkeypatch.setattr(m,"legacy_source_audit",fake)
    md={"idno":"ne0001","protagonist.age":"young","protagonist.profession":"poet"}
    result=m.audit_native_source(b"fixture",md,{"native_labels":{"protagonist.age":"young","protagonist.profession":"poet"}},source_url="fixture")
    assert result["external_llm_approved"] is False
    assert result["classifier_input_written"] is False and "work_input" not in result
    assert seen["labels"][0]["native_value"]=="young"
    assert "label_id" not in seen["labels"][0]

def test_native_reference_metadata_mismatch_refused():
    from recommend_book.next_reference import audit_native_source
    with pytest.raises(ValueError,match="native source/reference metadata differs"):
        audit_native_source(b"fixture",{"idno":"ne0001","protagonist.age":"adult","protagonist.profession":"poet"},{"native_labels":{"protagonist.age":"child","protagonist.profession":"poet"}},source_url="fixture")

def test_castlist_exclusion_preserves_tail_and_refuses_other_unknown_tags():
    from recommend_book.next_reference import exclude_cast_lists
    from recommend_book.autonomous_reference import extract_body
    raw=b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><p>Before<castList><head>SECRET LIST</head><castItem>SECRET ROLE</castItem></castList>After.</p></body></text></TEI>'
    transformed,audit=exclude_cast_lists(raw)
    body,_=extract_body(transformed)
    assert 'BeforeAfter.'==body and 'SECRET' not in body
    assert audit['excluded_castList_subtrees']==1
    assert audit['original_raw_sha256']!=audit['filtered_xml_sha256']
    with pytest.raises(ValueError):extract_body(transformed.replace(b'After.',b'<UNKNOWN>Forbidden</UNKNOWN>'))

def test_cross_source_author_ids_are_grouped_without_path_identifiers():
    rows=[row('conssa:ne0001','Same','1'),row('roman19:fr0001','Alias','1'),row('roman19:fr0002','New','2')]
    p=plan_cohort(rows,{'conssa:ne0001'})
    assert p[1]['cohort_role']=='unobserved_author_overlap_dev'
    assert p[2]['cohort_role']=='unobserved_author_disjoint_candidate'
    with pytest.raises(ValueError):plan_cohort([row('../escape')],set())


def test_fixed_context_recipes_have_exact_nonoverlapping_ranges():
    from recommend_book.next_reference import context_ranges
    assert context_ranges(100000,'definitions-v3')==[(0,6000),(47000,53000),(94000,100000)]
    ranges=context_ranges(100000,'context-v3')
    assert len(ranges)==6 and ranges[0]==(0,12000) and ranges[-1]==(88000,100000)
    assert sum(end-start for start,end in ranges)==72000
    assert all(ranges[i][1]<=ranges[i+1][0] for i in range(5))
    assert context_ranges(70000,'context-v3')==[(0,70000)]
    assert context_ranges(18000,'definitions-v3')==[(0,18000)]


@pytest.mark.parametrize('size,recipe',[(True,'context-v3'),(0,'context-v3'),(5,'unknown')])
def test_context_recipe_cannot_be_changed_by_parameters(size,recipe):
    from recommend_book.next_reference import context_ranges
    with pytest.raises(ValueError):context_ranges(size,recipe)


def test_context_proof_excludes_annotations_and_tracks_exact_bytes():
    from recommend_book.next_reference import context_proof
    from recommend_book.autonomous_reference import sha256
    raw=b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader>SECRET AGE ANSWER</teiHeader><text><body><p>Original.<castList><castItem>SECRET ROLE</castItem></castList>Tail.</p></body></text></TEI>'
    proof=context_proof(raw,'context-v3')
    assert proof['body_sha256']==sha256(b'Original.Tail.')
    assert proof['chunks']==[{'char_range':[0,14],'text_sha256':sha256(b'Original.Tail.'),'chars':14}]
    assert proof['original_raw_sha256']==sha256(raw)
    assert proof['external_llm_approved'] is False
    assert 'SECRET' not in str(proof) and 'text' not in proof['chunks'][0]


def test_context_preparation_rejects_duplicate_identity_before_output(tmp_path):
    import importlib.util,json
    from pathlib import Path
    from recommend_book.autonomous_reference import sha256
    repo=Path(__file__).resolve().parents[1]
    script=tmp_path/'scripts/build_next_reference.py';script.parent.mkdir()
    script.write_bytes((repo/'scripts/build_next_reference.py').read_bytes())
    for rel in ('src/recommend_book/next_reference.py','tests/test_next_reference.py'):
        target=tmp_path/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((repo/rel).read_bytes())
    spec=importlib.util.spec_from_file_location('context_builder_fixture',script)
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    base=tmp_path/'data/autonomous-next-v3/reference';audit=base/'audit';audit.mkdir(parents=True)
    raw=b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><p>Text</p></body></text></TEI>'
    path=base/'raw.xml';path.write_bytes(raw)
    row={'reference_id':'ne0001','status':'body_scope_candidate','raw_path':str(path.relative_to(tmp_path)),
         'raw_sha256':sha256(raw),'source_audit':{'body_sha256':sha256(b'Text')},
         'source_url':'fixture-only','author_group':'fixture','cohort_role':'observed_dev'}
    payload=''.join(json.dumps(row)+'\n' for _ in range(2)).encode()
    (audit/'cohort-audit.jsonl').write_bytes(payload)
    (audit/'manifest.json').write_text(json.dumps({'files':{'cohort-audit.jsonl':sha256(payload)},'body_scope_candidate_count':2}))
    out=base/'output'
    with pytest.raises(ValueError,match='duplicate'):
        builder.prepare_context_proofs(audit,out)
    assert not out.exists()


def test_conssa_verifier_reproduces_source_without_permission(tmp_path,monkeypatch):
    import csv,io,json
    from recommend_book import next_reference as m
    from recommend_book.autonomous_reference import sha256
    from recommend_book.models import SourceRecord,Evidence
    base=tmp_path/'data';base.mkdir()
    ident='ne0001';url=f'https://raw.githubusercontent.com/cligs/conssa/{m.COMMIT}/master/master/{ident}.xml'
    raw=b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader>SECRET ANSWER</teiHeader><text><body><p>Original prose.</p></body></text></TEI>'
    rawpath=base/'raw.xml';rawpath.write_bytes(raw)
    md={'idno':ident,'protagonist.age':'adult','protagonist.profession':'artist'}
    stream=io.StringIO();w=csv.DictWriter(stream,fieldnames=list(md),delimiter='\t');w.writeheader();w.writerow(md)
    mp=base/'metadata.tsv';mp.write_bytes(stream.getvalue().encode());monkeypatch.setattr(m,'METADATA_SHA',sha256(mp.read_bytes()))
    source={'reference_id':ident,'status':'body_scope_candidate','source_url':url,'raw_sha256':sha256(raw),
            'native_fields':{k:v for k,v in md.items() if k!='idno'},'source_audit':{'body_sha256':sha256(b'Original prose.')}}
    cp={**m.context_proof(raw,'definitions-v3'),'reference_id':ident,'expression_id':f'conssa:{ident}:{m.COMMIT}'}
    ap=base/'audit.jsonl';ap.write_text(json.dumps(source)+'\n');p=base/'context.jsonl';p.write_text(json.dumps(cp)+'\n')
    descriptors={key:{'path':str(path.relative_to(tmp_path)),'sha256':sha256(path.read_bytes())}
                 for key,path in [('metadata',mp),('source_audit',ap),('context_proofs',p)]}
    proof={**descriptors,'reference_id':ident,'recipe':'definitions-v3'}
    ev=Evidence(evidence_id=f'{ident}:definitions-v3:body:1',kind='excerpt',text='Original prose.',source_url=url,
        rights_policy_id='conssa-reviewed-original-v3',language='es',expression_ref=cp['expression_id'],
        locator=json.dumps({'scope':'corpus_original_body_excerpt','body_sha256':cp['body_sha256'],'char_range':[0,15],
            'recipe':cp['recipe'],'original_raw_sha256':cp['original_raw_sha256'],'filtered_xml_sha256':cp['filter']['filtered_xml_sha256']}),
        license_url=m.CC_BY,attribution={'creator':'CLiGS / Jose Calvo Tello','source':url,'license':m.CC_BY,
        'changes':'TEI header, attributes, annotations, castList and editorial matter removed; fixed definitions-v3 body windows'})
    rec=SourceRecord(source='conssa',source_id=ident,source_url=url,retrieved_at='2026-10-13T00:00:00Z',provider_id='cligs',
        record_kind='edition',raw_path=str(rawpath),raw_sha256=sha256(raw),rights_policy_id=ev.rights_policy_id,evidence=[ev])
    monkeypatch.setattr(m,'audit_native_source',lambda *a,**k:{'body_sha256':cp['body_sha256'],'editions':{'edition-first':{'year':1800}},'rights':{'reviewed_regions':['Japan','United States','Spain']},'extraction':cp['extraction']})
    result=m.verify_conssa_evidence(rec,ev,proof,root=tmp_path)
    assert result['reproduced'] is True and result['facts']['language']=='es'
    assert 'allowed' not in result and 'external_llm_allowed' not in result
    assert sha256(raw) in result['proof_hashes']
    with pytest.raises(ValueError,match='text'):
        m.verify_conssa_evidence(rec,ev.model_copy(update={'text':'SECRET ANSWER'}),proof,root=tmp_path)
    p.write_text('{}\n')
    with pytest.raises(ValueError,match='hash'):
        m.verify_conssa_evidence(rec,ev,proof,root=tmp_path)


def test_candidate_materials_keep_answers_out_and_permission_false():
    from recommend_book.next_reference import candidate_materials,COMMIT
    from recommend_book.autonomous_reference import sha256
    raw=b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader>SECRET AGE</teiHeader><text><body><p>Original prose.</p></body></text></TEI>'
    ident='ne0001';row={'reference_id':ident,'status':'body_scope_candidate','raw_path':'data/raw.xml',
        'raw_sha256':sha256(raw),'source_url':f'https://raw.githubusercontent.com/cligs/conssa/{COMMIT}/master/master/{ident}.xml',
        'source_audit':{'body_sha256':sha256(b'Original prose.')},'native_fields':{'protagonist.age':'SECRET AGE'}}
    record,work,proofs=candidate_materials(raw,row,'definitions-v3',descriptors={},retrieved_at='2026-10-13T00:00:00Z')
    assert work.title=='' and work.authors==[] and work.first_publication_year is None
    assert work.original_language=='es' and work.expression_id==f'conssa:{ident}:{COMMIT}'
    assert len(work.evidence)==1 and work.evidence[0].text=='Original prose.'
    assert work.evidence[0].llm_input_allowed is False
    assert record.record_kind=='edition' and record.source_id==ident
    assert 'SECRET' not in record.model_dump_json()+work.model_dump_json()+str(proofs)
