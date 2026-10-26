"""Synthetic held-SB index cards; never replay the saved index in tests."""
import gzip
import hashlib
import json

import pytest

from recommend_book.light_novel_sbcr_index_groups_v1 import verify_sbcr_index_groups


PAGE = 'https://ebook.sbcr.jp/lightnovel/series'
RIGHTS = 'Reference-only publisher page; no synopsis, image or AI-input reuse inferred'
USAGE = {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
         'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False}
ROBOTS = {'status': 'not_saved_in_existing_source_evidence', 'permission_inferred': False}
TITLES = ['（シリーズ：完全な索引題！）', 'ＧＡ文庫マガジン', '既確認の題']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def url(number):
    return PAGE+'/id/'+str(number)


def old_id(value):
    return 'publisher-series:'+digest(value.encode())[:24]


def card(number, title, count=2):
    return f'<a href="/lightnovel/series/id/{number}">「{title}」シリーズ ({count})</a>'


def fixture(tmp_path):
    (tmp_path/'coverage').mkdir()

    def save(name, value):
        data=json.dumps(value,ensure_ascii=False,indent=2).encode()
        (tmp_path/name).write_bytes(data)
        return {'path':name,'sha256':digest(data)}

    data=(''.join(card(n,t) for n,t in zip(range(7,10),TITLES))+'<p>SECRET_PROSE</p><img alt="SECRET_IMAGE">').encode()
    (tmp_path/'coverage/index.html').write_bytes(data)
    receipt={'source_url':PAGE,'final_url':PAGE,'http_status':200,'content_type':'text/html; charset=UTF-8',
        'rights':RIGHTS,'retrieved_at':'2026-10-25T00:00:00Z','raw_path':'index.html','sha256':digest(data),'byte_length':len(data)}
    frame={'id':'ga-ebook-series','url':PAGE,'kind':'series_index','index_pages_complete':True,
        'completion_reason':'all_linked_pages_exhausted','reference_series_count':3,
        'historical_entire_label_complete':False,'pages':[receipt]}
    frames=[{'id':k,'pages':[{'raw_path':'MUST_NOT_OPEN'}]} for k in ['hj-bunko-series','hj-novels-series','arianrose-series']]+[frame]
    rows=[{'reference_id':old_id(url(n)),'source_url':url(n),'title':title,'kind':'series','index_id':'ga-ebook-series',
        'index_source_url':PAGE,'index_source_sha256':receipt['sha256'],'index_retrieved_at':receipt['retrieved_at']}
        for n,title in zip(range(7,10),TITLES)]
    extra_urls=['https://firecross.jp/hjbunko/series/90','https://firecross.jp/hjbunko/series/91',
                'https://firecross.jp/hjnovels/series/92','https://arianrose.jp/novel/?series_id=93']
    rows += [{'reference_id':old_id(u),'source_url':u,'index_id':'other'} for u in extra_urls]
    held=[rows[0]['reference_id'],rows[1]['reference_id']]
    covered=[old_id(extra_urls[0])]
    parent_proof={'schema_version':'sbcr-selected-publisher-groups-proof-v2','selected_reference_ids':[rows[2]['reference_id']],
        'held_reference_ids':held,'covered_reference_ids':covered}
    handoff=[{**r,'publisher_reported_edition_count':2,'membership_index_complete':i==2,
        'membership_hold_reason':None if i==2 else 'source_count_or_pagination_unresolved',
        'explicit_book_memberships':[],'series_source_snapshot':{'raw_path':'MUST_NOT_OPEN_PARENT'},
        'author_credit':'UNVERIFIED_PERSON_PROXY'} for i,r in enumerate(rows[:3])]
    delta={'covered_reference_ids':covered,'next_provider_incomplete_membership_reference_ids':held}
    adoptions={key:{'status':'completed','selected_legacy_reference_ids':[rid]}
        for key,rid in [('SB215',rows[2]['reference_id']),('HJ137',old_id(extra_urls[1])),
                        ('HJN39',old_id(extra_urls[2])),('Arian49',old_id(extra_urls[3]))]}
    proof={'schema_version':'sbcr-held-index-groups-proof-v1','source_root':'coverage','source_rights_literal':RIGHTS,
        'usage_scope':USAGE,'robots_evidence':ROBOTS,'selected_reference_ids':held,
        'expected_counts':{'index_groups':3,'selected_groups':2,'selected_index_pages':1,'index_pages':1,'protected_references':5},
        'source_frames':save('coverage/frames.json',frames),'source_roster':save('coverage/roster.json',rows),
        'source_handoff':save('coverage/handoff.json',handoff),'coverage_report':save('delta.json',delta),
        'parent_source_proof':save('parent-proof.json',parent_proof)}
    candidates={'schema_version':'sbcr-held8-native-index-only-candidates-v1','selected_reference_ids':held,
        'groups':[{'legacy_source_reference_id':r['reference_id'],'title':r['title'],'source_url':r['source_url']} for r in rows[:2]]}
    env={'root':tmp_path,'save':save,'proof':proof,'frames':frames,'rows':rows,'handoff':handoff,
         'delta':delta,'parent_proof':parent_proof,'adoptions':adoptions,'candidates':candidates}
    repin(env)
    return env


def repin(env):
    for key,value in [('source_frames',env['frames']),('source_roster',env['rows']),('source_handoff',env['handoff']),
                      ('coverage_report',env['delta']),('parent_source_proof',env['parent_proof'])]:
        env['proof'][key]=env['save'](env['proof'][key]['path'],value)
    env['proof']['completed_adoptions']={k:env['save'](k+'.json',v) for k,v in env['adoptions'].items()}
    env['candidates']['completed_adoption_pins']=env['proof']['completed_adoptions']
    env['proof']['source_handoff_manifest']=env['save']('coverage/handoff-manifest.json',{
        'schema_version':'sbcr-publisher-series-handoff-v3','rows':len(env['handoff']),
        'snapshot_sha256':env['proof']['source_handoff']['sha256'],'national_coverage_proved':False})
    env['proof']['source_candidates']=env['save']('candidates.json',env['candidates'])
    env['pin']=env['save']('proof.json',env['proof'])


def run(env):
    return verify_sbcr_index_groups(env['root']/'proof.json',env['pin']['sha256'])


def change_raw(env, old, new, *, compressed=False):
    path=env['root']/'coverage/index.html';text=path.read_text(encoding='utf-8');assert old in text
    data=text.replace(old,new).encode()
    if compressed:data=gzip.compress(data)
    path.write_bytes(data);receipt=env['frames'][-1]['pages'][0]
    receipt.update(sha256=digest(data),byte_length=len(data))
    for row in env['rows'][:3]+env['handoff']:
        row['index_source_sha256']=receipt['sha256']
    repin(env)


def test_index_only_groups_keep_literal_titles_and_exact_cautions(tmp_path):
    env=fixture(tmp_path);groups=run(env)
    assert [g['title'] for g in groups]==TITLES[:2]
    assert [g['index_name_cautions'] for g in groups]==[['wrapped_index_display_title'],['publication_type_unconfirmed']]
    for row in groups:
        key={'provider':'sbcr-index-only','source_index':PAGE,'source_scope':'selected_native_index_cards_only','official_group_url':row['source_url']}
        assert row['local_reference_key']==key
        assert row['reference_id']=='sbcr-index-group-local:'+digest(json.dumps(key,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())
        assert row['schema_version']=='light-novel-sbcr-index-source-group-v1'
        assert row['source_unit']=='publisher_work_group'
        assert row['kind']=='official_publisher_native_index_group_reference'
        assert row['authors'] is None and row['index_credit_literals']==[]
        assert row['members']==[] and row['total_product_count'] is None
        assert row['frame_member_count']==row['known_verified_member_reference_count']==0
        assert row['native_index_card_complete'] is row['index_frame_pages_complete'] is True
        assert all(row[k] is None for k in ['frame_membership_complete','parent_page_membership_complete',
            'global_membership_complete','publisher','label','form','isbn','official_lightnovel_tag'])
        assert all(row[k] is False for k in ['reference_id_is_official','story_series_asserted',
            'original_work_identity_asserted','edition_identity_asserted','singleton_asserted'])
        assert row['robots_evidence']==ROBOTS and row['usage_permissions']==USAGE
    assert not any(s in json.dumps(groups) for s in ['SECRET','MUST_NOT_OPEN','PROXY'])


@pytest.mark.parametrize('artifact',['proof','source_frames','source_roster','source_handoff','source_handoff_manifest',
    'coverage_report','parent_source_proof','source_candidates','SB215','HJ137','HJN39','Arian49','raw'])
def test_all_fixed_inputs_require_hashes(tmp_path,artifact):
    env=fixture(tmp_path)
    if artifact in env['adoptions']:path=env['root']/env['proof']['completed_adoptions'][artifact]['path']
    else:path=env['root']/('proof.json' if artifact=='proof' else 'coverage/index.html' if artifact=='raw' else env['proof'][artifact]['path'])
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError):run(env)


@pytest.mark.parametrize('field,value',[('http_status',302),('final_url',PAGE+'/moved'),('rights','forged'),
    ('content_type','image/png'),('byte_length',1),('byte_length',5_000_001),('raw_path','../escape'),
    ('raw_path','C:/escape'),('location','/moved'),('retry_count',1)])
def test_receipt_boundaries(tmp_path,field,value):
    env=fixture(tmp_path);env['frames'][-1]['pages'][0][field]=value;repin(env)
    with pytest.raises(ValueError):run(env)


@pytest.mark.parametrize('kind',['subset','duplicate_selected','parent_proof_changed','initial_held_changed','covered_overlap',
    'adoption_overlap','adoption_incomplete','candidate_title','candidate_url','candidate_extra','roster_title',
    'handoff_title','handoff_complete','handoff_snapshot','incomplete_frame','second_index','wrong_count','usage','robots','escape'])
def test_exact_held_partition_and_metadata_pins(tmp_path,kind):
    env=fixture(tmp_path)
    if kind=='subset':env['proof']['selected_reference_ids']=env['proof']['selected_reference_ids'][:1]
    elif kind=='duplicate_selected':env['proof']['selected_reference_ids']*=2
    elif kind=='parent_proof_changed':env['parent_proof']['held_reference_ids']=[]
    elif kind=='initial_held_changed':env['delta']['next_provider_incomplete_membership_reference_ids']=[]
    elif kind=='covered_overlap':env['delta']['covered_reference_ids'].append(old_id(url(7)))
    elif kind=='adoption_overlap':env['adoptions']['HJ137']['selected_legacy_reference_ids']=[old_id(url(7))]
    elif kind=='adoption_incomplete':env['adoptions']['HJN39']['status']='held'
    elif kind=='candidate_title':env['candidates']['groups'][0]['title']='invented'
    elif kind=='candidate_url':env['candidates']['groups'][0]['source_url']=url(9)
    elif kind=='candidate_extra':env['candidates']['selected_reference_ids'].append(old_id(url(9)))
    elif kind=='roster_title':env['rows'][0]['title']='invented'
    elif kind=='handoff_title':env['handoff'][0]['title']='invented'
    elif kind=='handoff_complete':env['handoff'][0]['membership_index_complete']=True
    elif kind=='handoff_snapshot':env['proof']['source_handoff_manifest']['sha256']='0'*64
    elif kind=='incomplete_frame':env['frames'][-1]['index_pages_complete']=False
    elif kind=='second_index':env['frames'][-1]['pages']*=2
    elif kind=='wrong_count':env['proof']['expected_counts']['selected_groups']=9
    elif kind=='usage':env['proof']['usage_scope']={**USAGE,'commercial_reuse':True}
    elif kind=='robots':env['proof']['robots_evidence']={'status':'allowed','permission_inferred':True}
    elif kind=='escape':env['proof']['source_root']='..'
    repin(env)
    if kind=='handoff_snapshot':
        env['proof']['source_handoff_manifest']['sha256']='0'*64
        env['pin']=env['save']('proof.json',env['proof'])
    with pytest.raises(ValueError):run(env)


@pytest.mark.parametrize('replacement',[
    '',card(7,'違う題'),card(7,TITLES[0])*2,
    f'<a href="https://evil.invalid/lightnovel/series/id/7">「{TITLES[0]}」シリーズ (2)</a>',
    f'<a hidden href="/lightnovel/series/id/7">「{TITLES[0]}」シリーズ (2)</a>',
    f'<a href="/lightnovel/series/id/7"><img alt="secret">「{TITLES[0]}」シリーズ (2)</a>',
])
def test_raw_literal_title_and_own_url_must_be_unambiguous(tmp_path,replacement):
    env=fixture(tmp_path);change_raw(env,card(7,TITLES[0]),replacement)
    with pytest.raises(ValueError):run(env)


def test_plain_literal_title_has_no_caution_and_is_not_stripped(tmp_path):
    env=fixture(tmp_path);env['rows'][0]['title']='完全な通常題';env['handoff'][0]['title']='完全な通常題'
    env['candidates']['groups'][0]['title']='完全な通常題'
    change_raw(env,card(7,TITLES[0]),card(7,'完全な通常題'),compressed=True)
    assert run(env)[0]['index_name_cautions']==[]


def test_forged_caution_enum_and_person_proxy_do_not_escape(tmp_path):
    env=fixture(tmp_path);env['candidates']['groups'][0]['index_name_cautions']=['forged']
    repin(env)
    row=run(env)[0]
    assert row['index_name_cautions']==['wrapped_index_display_title']
    assert row['index_credit_literals']==[] and row['authors'] is None
