"""Synthetic evidence only; the saved Arian index is not replayed here."""
import gzip
import hashlib
import json

import pytest

from recommend_book.light_novel_arian_index_groups_v1 import verify_arian_index_groups


PAGE = 'https://arianrose.jp/series/'
RIGHTS = 'Reference-only publisher page; no synopsis, image or AI-input reuse inferred'
USAGE = {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
         'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False}
ROBOTS = {'status': 'not_saved_in_existing_source_evidence', 'permission_inferred': False}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def url(number):
    return 'https://arianrose.jp/novel/?series_id='+str(number)


def old_id(value):
    return 'publisher-series:'+digest(value.encode())[:24]


def article(number, title, credit='著：索引の人 イラスト：画の人'):
    return (f'<article><a href="{url(number)}"><img alt="SECRET_IMAGE"></a>'
            f'<h3>{title}</h3><p class="author">{credit}</p><p>SECRET_PROSE</p></article>')


def fixture(tmp_path):
    (tmp_path/'coverage').mkdir()

    def save(name, value):
        raw=json.dumps(value,ensure_ascii=False,indent=2).encode()
        (tmp_path/name).write_bytes(raw)
        return {'path':name,'sha256':digest(raw)}

    raw=(article(7,'完全な索引題')+article(8,'旧確認題')).encode()
    (tmp_path/'coverage/index.html').write_bytes(raw)
    receipt={'source_url':PAGE,'final_url':PAGE,'http_status':200,'content_type':'text/html; charset=UTF-8',
        'rights':RIGHTS,'retrieved_at':'2026-10-25T00:00:00Z','raw_path':'index.html','sha256':digest(raw),'byte_length':len(raw)}
    frame={'id':'arianrose-series','url':PAGE,'kind':'series_index','index_pages_complete':True,
        'completion_reason':'all_linked_pages_exhausted','reference_series_count':2,
        'historical_entire_label_complete':False,'pages':[receipt]}
    frames=[{'id':k,'pages':[{'raw_path':'MUST_NOT_OPEN'}]} for k in ['hj-bunko-series','hj-novels-series','ga-ebook-series']]+[frame]
    rows=[{'reference_id':old_id(url(n)),'source_url':url(n),'title':title,'kind':'series',
        'index_id':'arianrose-series','index_source_url':PAGE,'index_source_sha256':receipt['sha256'],
        'index_retrieved_at':receipt['retrieved_at'],'author_credit':'著：索引の人 イラスト：画の人'}
        for n,title in [(7,'完全な索引題'),(8,'旧確認題')]]
    delta={'provider_counts':{'arianrose-series':{'source_references':2,'covered':1,'uncovered':1,'conflicts':0}},
        'covered_reference_ids':[old_id(url(8))],'uncovered_reference_ids':[old_id(url(7))],
        'rows':[{**{k:x[k] for k in ['reference_id','source_url','index_id','index_source_url','index_source_sha256']},
                 'status':'uncovered' if i==0 else 'covered'} for i,x in enumerate(rows)]}
    candidates={'schema_version':'hjn39-arian49-native-index-only-candidates-v1',
        'selected_by_provider':{'arianrose-series':[old_id(url(7))]},
        'groups':[{k:rows[0][k] for k in ['reference_id','source_url','title']} | {'provider_profile_candidate':'arianrose-index-only'}]}
    proof={'schema_version':'arian-selected-index-groups-proof-v1','source_root':'coverage',
        'source_rights_literal':RIGHTS,'usage_scope':USAGE,'robots_evidence':ROBOTS,
        'source_frames':save('coverage/frames.json',frames),'source_roster':save('coverage/roster.json',rows),
        'coverage_report':save('delta.json',delta),'source_candidates':save('candidates.json',candidates),
        'selected_reference_ids':[old_id(url(7))],'covered_reference_ids':[old_id(url(8))],
        'expected_counts':{'index_groups':2,'selected_groups':1,'selected_index_pages':1,'index_pages':1}}
    env={'root':tmp_path,'save':save,'proof':proof,'frames':frames,'rows':rows,'delta':delta,'candidates':candidates}
    repin(env)
    return env


def repin(env):
    for key,value in [('source_frames',env['frames']),('source_roster',env['rows']),
                      ('coverage_report',env['delta']),('source_candidates',env['candidates'])]:
        env['proof'][key]=env['save'](env['proof'][key]['path'],value)
    env['pin']=env['save']('proof.json',env['proof'])


def run(env):
    return verify_arian_index_groups(env['root']/'proof.json',env['pin']['sha256'])


def change_raw(env, old, new, *, compressed=False):
    p=env['root']/'coverage/index.html'
    text=p.read_text(encoding='utf-8');assert old in text
    raw=text.replace(old,new).encode()
    if compressed:
        raw=gzip.compress(raw)
    p.write_bytes(raw)
    receipt=env['frames'][-1]['pages'][0]
    receipt.update(sha256=digest(raw),byte_length=len(raw))
    for row in env['rows']:
        row['index_source_sha256']=receipt['sha256']
    for row in env['delta']['rows']:
        row['index_source_sha256']=receipt['sha256']
    repin(env)


def test_safe_group_matches_hj_index_only_contract(tmp_path):
    env=fixture(tmp_path);group,=run(env)
    key={'provider':'arian-rose-index-only','source_index':PAGE,
         'source_scope':'selected_native_index_cards_only','official_group_url':url(7)}
    assert group['local_reference_key']==key
    assert group['reference_id']=='arian-index-group-local:'+digest(json.dumps(key,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())
    assert group['schema_version']=='light-novel-arian-index-source-group-v1'
    assert group['source_unit']=='publisher_work_group'
    assert group['kind']=='official_publisher_native_index_group_reference'
    assert group['title']=='完全な索引題' and group['source_url']==url(7)
    assert group['authors'] is None
    assert group['index_credit_literals']==[
        {'role_literal':'著','name':'索引の人','role_scope':'publisher_index_card'},
        {'role_literal':'イラスト','name':'画の人','role_scope':'publisher_index_card'}]
    assert group['members']==[] and group['frame_member_count']==0
    assert group['known_verified_member_reference_count']==0 and group['total_product_count'] is None
    assert group['native_index_card_complete'] is True
    assert group['index_frame_pages_complete'] is True
    assert all(group[k] is None for k in ['publisher','label','form','isbn','official_lightnovel_tag',
        'frame_membership_complete','parent_page_membership_complete','global_membership_complete'])
    assert all(group[k] is False for k in ['reference_id_is_official','story_series_asserted',
        'original_work_identity_asserted','edition_identity_asserted','singleton_asserted'])
    assert group['usage_permissions']==USAGE and group['robots_evidence']==ROBOTS
    assert all(e['raw_sha256']==env['frames'][-1]['pages'][0]['sha256'] for e in group['source_evidence'])
    assert not any(secret in json.dumps(group) for secret in ['SECRET','MUST_NOT_OPEN','PROSE','IMAGE'])


@pytest.mark.parametrize('field', ['proof','source_frames','source_roster','coverage_report','source_candidates','raw'])
def test_all_artifacts_are_hash_pinned(tmp_path,field):
    env=fixture(tmp_path)
    p=env['root']/('proof.json' if field=='proof' else 'coverage/index.html' if field=='raw' else env['proof'][field]['path'])
    p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError):
        run(env)


@pytest.mark.parametrize('field,value',[('http_status',302),('final_url',url(7)),('rights','forged'),
    ('content_type','image/png'),('byte_length',1),('byte_length',5_000_001),
    ('raw_path','../escape'),('raw_path','C:/escape'),('location','/moved'),('retry_count',1)])
def test_receipt_scope_and_bounds(tmp_path,field,value):
    env=fixture(tmp_path);env['frames'][-1]['pages'][0][field]=value;repin(env)
    with pytest.raises(ValueError):
        run(env)


@pytest.mark.parametrize('kind',['selected_subset','selected_covered','duplicate_selected','candidate_extra',
    'candidate_title','candidate_url','roster_title','roster_url','duplicate_roster','wrong_index_sha',
    'uncovered_status','incomplete_frame','second_page','wrong_count','usage','robots','source_escape'])
def test_selection_metadata_and_usage_are_fixed(tmp_path,kind):
    env=fixture(tmp_path)
    if kind=='selected_subset':env['proof']['selected_reference_ids']=[]
    elif kind=='selected_covered':env['proof']['covered_reference_ids'].append(old_id(url(7)))
    elif kind=='duplicate_selected':env['proof']['selected_reference_ids']*=2
    elif kind=='candidate_extra':env['candidates']['selected_by_provider']['arianrose-series'].append(old_id(url(8)))
    elif kind=='candidate_title':env['candidates']['groups'][0]['title']='invented'
    elif kind=='candidate_url':env['candidates']['groups'][0]['source_url']=url(8)
    elif kind=='roster_title':env['rows'][0]['title']='invented'
    elif kind=='roster_url':env['rows'][0]['source_url']=url(8)
    elif kind=='duplicate_roster':env['rows'].append(env['rows'][0])
    elif kind=='wrong_index_sha':env['rows'][0]['index_source_sha256']='0'*64
    elif kind=='uncovered_status':env['delta']['rows'][0]['status']='covered'
    elif kind=='incomplete_frame':env['frames'][-1]['index_pages_complete']=False
    elif kind=='second_page':env['frames'][-1]['pages'].append(env['frames'][-1]['pages'][0])
    elif kind=='wrong_count':env['proof']['expected_counts']['selected_groups']=50
    elif kind=='usage':env['proof']['usage_scope']={**USAGE,'commercial_reuse':True}
    elif kind=='robots':env['proof']['robots_evidence']={'status':'allowed','permission_inferred':True}
    elif kind=='source_escape':env['proof']['source_root']='..'
    repin(env)
    with pytest.raises(ValueError):
        run(env)


@pytest.mark.parametrize('replacement',[
    '<article><h3>完全な索引題</h3></article>',
    '<h3>完全な索引題</h3><a href="https://arianrose.jp/novel/?series_id=7">outside</a>',
    '<article><a href="https://evil.invalid/novel/?series_id=7">x</a><h3>完全な索引題</h3></article>',
    article(7,'違う題'),article(7,'完全な索引題')*2,
    '<article><a href="https://arianrose.jp/novel/?series_id=7">x</a><a href="https://arianrose.jp/novel/?series_id=8">y</a><h3>完全な索引題</h3></article>',
    '<article><a href="https://arianrose.jp/novel/?series_id=7">x</a><h3>完全な索引題</h3><h3>別題</h3></article>',
    '<article><a href="https://arianrose.jp/novel/?series_id=7">x</a><h3><img alt="完全な索引題"></h3></article>',
    '<article><a href="https://arianrose.jp/novel/?series_id=7">x</a><h3 hidden>完全な索引題</h3></article>',
    article(7,'完全な索引題','著：別の人'),
])
def test_native_article_binding_and_full_text_reject_ambiguity(tmp_path,replacement):
    env=fixture(tmp_path);change_raw(env,article(7,'完全な索引題'),replacement)
    with pytest.raises(ValueError):
        run(env)


def test_missing_role_literal_remains_unknown_index_display(tmp_path):
    env=fixture(tmp_path);env['rows'][0]['author_credit']='役割のない索引表示'
    change_raw(env,'著：索引の人 イラスト：画の人','役割のない索引表示')
    group,=run(env)
    assert group['authors'] is None
    assert group['index_credit_literals']==[{'role_literal':None,'name':'役割のない索引表示','role_scope':'publisher_index_card'}]


def test_absent_credit_is_unknown_and_cannot_be_filled_from_neighbors(tmp_path):
    env=fixture(tmp_path);env['rows'][0]['author_credit']=None
    change_raw(env,'<p class="author">著：索引の人 イラスト：画の人</p>','')
    group,=run(env)
    assert group['index_credit_literals']==[] and group['authors'] is None


def test_multiple_link_presentations_same_native_url_are_one_reference(tmp_path):
    env=fixture(tmp_path)
    change_raw(env,'<h3>完全な索引題</h3>',f'<h3><a href="{url(7)}">完全な索引題</a></h3>',compressed=True)
    assert len(run(env))==1


def test_unselected_unsafe_card_is_not_used_to_fill_selected_fields(tmp_path):
    env=fixture(tmp_path)
    change_raw(env,article(8,'旧確認題'),'<article><a href="'+url(8)+'">x</a><h3><input value="SECRET"></h3></article>')
    assert len(run(env))==1


def test_credit_display_does_not_assume_person_name_tags(tmp_path):
    env=fixture(tmp_path)
    env['rows'][0]['author_credit']='著： 索引の人 イラスト： 画の人'
    replacement=('<p class="author"><span>著：</span><em>索引の人</em>'
                 ' <b>イラスト：</b><span>画の人</span></p>')
    change_raw(env,'<p class="author">著：索引の人 イラスト：画の人</p>',replacement)
    group,=run(env)
    assert group['index_credit_literals']==[
        {'role_literal':'著','name':'索引の人','role_scope':'publisher_index_card'},
        {'role_literal':'イラスト','name':'画の人','role_scope':'publisher_index_card'}]
    assert group['authors'] is None


def test_gzip_expansion_is_bounded(tmp_path):
    env=fixture(tmp_path)
    change_raw(env,article(7,'完全な索引題'),'x'*5_000_001,compressed=True)
    with pytest.raises(ValueError,match='bounded|decompressed'):
        run(env)
