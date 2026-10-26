"""Synthetic selected HJ index cards; real private source is never a fixture."""
import hashlib
import json

import pytest

from recommend_book.light_novel_hj_index_groups_v1 import verify_hj_index_groups


PAGE = 'https://firecross.jp/hjbunko/series'
NOVELS_PAGE = 'https://firecross.jp/hjnovels/series'
RIGHTS = 'Reference-only publisher page; no synopsis, image or AI-input reuse inferred'
USAGE = {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
         'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False}
ROBOTS = {'status': 'not_saved_in_existing_source_evidence', 'permission_inferred': False}
CONTRACT = {'role_boundary': 'at_most_one_direct_span.book-author-type_per_own_credit_item',
    'name_boundary': 'one_or_more_direct_a.book-author-name_in_same_own_credit_item',
    'legacy_projection': 'first_direct_name_for_each_explicit_role_item',
    'safe_projection': 'all_direct_name_literals_at_index_scope',
    'descendant_or_cross_item_name_fallback': False}


def digest(data): return hashlib.sha256(data).hexdigest()
def old_id(url): return 'publisher-series:' + digest(url.encode())[:24]


def card(number, title, role='著者', name='索引の人物', page=PAGE):
    return (f'<li class="seriesList_item"><a class="seriesList_itemTitle" href="{page}/{number}">{title}</a>'
            '<ul class="book-author-list"><li class="book-author-item">'
            f'<span class="book-author-type">{role}</span><a class="book-author-name">{name}</a></li></ul>'
            '<p>SECRET_PROSE</p><img alt="SECRET_COVER"></li>')


def fixture(tmp_path, profile='hj'):
    page=PAGE if profile=='hj' else NOVELS_PAGE
    index='hj-bunko-series' if profile=='hj' else 'hj-novels-series'
    source = tmp_path/'coverage'; source.mkdir()
    def save(name, value):
        data=json.dumps(value,ensure_ascii=False,indent=2).encode(); p=tmp_path/name
        p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(data)
        return {'path':name,'sha256':digest(data)}
    def receipt(number, document):
        data=document.encode(); name=f'page{number}.html'; (source/name).write_bytes(data)
        url=page if number==1 else page+f'?sort=1&page={number}'
        return {'source_url':url,'final_url':url,'http_status':200,'content_type':'text/html; charset=UTF-8',
            'rights':RIGHTS,'retrieved_at':'2026-10-25T00:00:00Z','raw_path':name,'sha256':digest(data),'byte_length':len(data)}
    snapshots=[receipt(1,'<ul>'+card(7,'完全な索引題 1',page=page)+'</ul>'),receipt(2,'<ul>'+card(8,'完全な索引題 2',page=page)+'</ul>'),
        {'source_url':page+'?sort=1&page=3','raw_path':'MUST_NOT_OPEN_UNSELECTED'}]
    frame={'id':index,'url':page,'kind':'series_index','index_pages_complete':True,
        'completion_reason':'all_linked_pages_exhausted','historical_entire_label_complete':False,
        'reference_series_count':3,'pages':snapshots}
    frames=[frame,*[{'id':x} for x in ['hj-bunko-series','hj-novels-series','arianrose-series','ga-ebook-series'] if x!=index]]
    rows=[]
    for n in range(7,10):
        snapshot=snapshots[n-7]
        rows.append({'reference_id':old_id(page+'/'+str(n)),'source_url':page+'/'+str(n),
            'title':'完全な索引題 '+str(n-6),'kind':'series','index_id':index,
            'index_source_url':snapshot['source_url'],'index_source_sha256':snapshot.get('sha256','0'*64),
            'index_retrieved_at':snapshot.get('retrieved_at'), 'credits':[{'role':'著者','name':'索引の人物'}],
            'author_credit':'著者：索引の人物'})
    delta={'provider_counts':{index:{'source_references':3,'covered':1,'uncovered':2,'conflicts':0}},
        'covered_reference_ids':[rows[2]['reference_id']],'uncovered_reference_ids':[r['reference_id'] for r in rows[:2]],
        'rows':[{**{k:r[k] for k in ['reference_id','source_url','index_id','index_source_url','index_source_sha256']},
            'status':'uncovered' if i<2 else 'covered'} for i,r in enumerate(rows)]}
    proof={'schema_version':('hj' if profile=='hj' else 'hjnovels')+'-selected-index-groups-proof-v1','source_root':'coverage',
        'artifact_version':2,'index_credit_contract':CONTRACT,
        'source_rights_literal':RIGHTS,'usage_scope':USAGE,'robots_evidence':ROBOTS,
        'source_frames':save('coverage/frames.json',frames),'source_roster':save('coverage/roster.json',rows),
        'coverage_report':save('delta.json',delta),'selected_reference_ids':delta['uncovered_reference_ids'],
        'covered_reference_ids':delta['covered_reference_ids'],
        'expected_counts':{'index_groups':3,'selected_groups':2,'selected_index_pages':2,'index_pages':3}}
    env={'root':tmp_path,'save':save,'proof':proof,'frames':frames,'rows':rows,'delta':delta,'profile':profile}
    repin(env);return env


def repin(env):
    for key,value in [('source_frames',env['frames']),('source_roster',env['rows']),('coverage_report',env['delta'])]:
        env['proof'][key]=env['save'](env['proof'][key]['path'],value)
    env['pin']=env['save']('proof.json',env['proof'])


def run(env):
    verifier=verify_hj_index_groups
    if env.get('profile')=='hjnovels':
        from recommend_book.light_novel_hj_index_groups_v1 import verify_hjnovels_index_groups
        verifier=verify_hjnovels_index_groups
    return verifier(env['root']/env['pin']['path'],env['pin']['sha256'])


def change_raw(env, old, new, page=0):
    receipt=env['frames'][0]['pages'][page]; p=env['root']/'coverage'/receipt['raw_path']
    data=p.read_text(encoding='utf-8');assert old in data
    p.write_text(data.replace(old,new),encoding='utf-8');receipt.update(sha256=digest(p.read_bytes()),byte_length=p.stat().st_size)
    for row in env['rows']:
        if row['index_source_url']==receipt['source_url']:
            row['index_source_sha256']=receipt['sha256']
            next(x for x in env['delta']['rows'] if x['reference_id']==row['reference_id'])['index_source_sha256']=receipt['sha256']
    repin(env)


def test_selected_cards_only_replayed_without_members_or_identity_claims(tmp_path):
    env=fixture(tmp_path); groups=run(env)
    assert len(groups)==2
    row=groups[0]
    key={'provider':'hj-bunko-index-only','source_index':PAGE,'source_scope':'selected_native_index_cards_only','official_group_url':PAGE+'/7'}
    assert row['local_reference_key']==key
    assert row['reference_id']=='hj-index-group-local:'+digest(json.dumps(key,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())
    assert row['legacy_source_reference_id']==env['rows'][0]['reference_id']
    assert row['index_credit_literals']==[{'role_literal':'著者','name':'索引の人物','role_scope':'publisher_index_card'}]
    assert row['native_index_card_complete'] is True and row['index_frame_pages_complete'] is True
    assert row['members']==[] and row['frame_member_count']==0 and row['total_product_count'] is None
    assert row['frame_membership_complete'] is None and row['parent_page_membership_complete'] is None
    assert row['global_membership_complete'] is None and row['authors'] is None
    assert row['kind']=='official_publisher_native_index_group_reference'
    assert row['source_unit']=='publisher_work_group' and row['light_novel_status']=='unknown'
    assert row['robots_evidence']==ROBOTS and row['usage_permissions']==USAGE
    assert all(row[k] is None for k in ['publisher','label','isbn','form','official_lightnovel_tag'])
    assert all(row[k] is False for k in ['story_series_identity_asserted','original_work_identity_asserted','edition_identity_asserted'])
    assert not any(secret in json.dumps(groups) for secret in ['SECRET','MUST_NOT_OPEN','PROSE','COVER'])


@pytest.mark.parametrize('which',['proof','source_frames','source_roster','coverage_report','raw'])
def test_all_pinned_artifacts_require_hashes(tmp_path,which):
    env=fixture(tmp_path)
    p=env['root']/('proof.json' if which=='proof' else 'coverage/page1.html' if which=='raw' else env['proof'][which]['path'])
    p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError):run(env)


@pytest.mark.parametrize('field,value',[('source_url','https://evil.invalid/'),('final_url',PAGE+'?bad=1'),
    ('http_status',302),('rights','permission forged'),('content_type','application/json'),
    ('byte_length',1),('raw_path','../escape'),('raw_path','C:/escape'),('location','/moved'),('retry_count',1)])
def test_selected_receipt_scope_status_and_paths_reject_tampering(tmp_path,field,value):
    env=fixture(tmp_path);env['frames'][0]['pages'][0][field]=value;repin(env)
    with pytest.raises(ValueError):run(env)


@pytest.mark.parametrize('kind',['selected_subset','covered_selected','duplicate_selection','wrong_count','incomplete_frame',
    'forged_title','forged_credit','wrong_index','duplicate_roster','url_id_conflict'])
def test_exact_partition_and_metadata_raw_agreement(tmp_path,kind):
    env=fixture(tmp_path)
    if kind=='selected_subset':env['proof']['selected_reference_ids']=env['proof']['selected_reference_ids'][:1]
    elif kind=='covered_selected':env['proof']['covered_reference_ids'].append(env['proof']['selected_reference_ids'][0])
    elif kind=='duplicate_selection':env['proof']['selected_reference_ids'].append(env['proof']['selected_reference_ids'][0])
    elif kind=='wrong_count':env['proof']['expected_counts']['selected_index_pages']=1
    elif kind=='incomplete_frame':env['frames'][0]['index_pages_complete']=False
    elif kind=='forged_title':env['rows'][0]['title']='完全な索引題'
    elif kind=='forged_credit':env['rows'][0]['credits'][0]['name']='別人'
    elif kind=='wrong_index':env['rows'][0]['index_id']='hj-novels-series'
    elif kind=='duplicate_roster':env['rows'].append(dict(env['rows'][0]))
    elif kind=='url_id_conflict':env['rows'][0]['source_url']=PAGE+'/99'
    repin(env)
    with pytest.raises(ValueError):run(env)


@pytest.mark.parametrize('old,new',[(PAGE+'/7',PAGE+'/71'),('完全な索引題 1','完全な索引題'),
    ('索引の人物','別人'),('>著者<','>監修<'),('class="seriesList_item"','class="related-item"'),
    ('class="seriesList_itemTitle"','class="related-title"')])
def test_own_card_full_title_url_and_explicit_role_name_are_authenticated(tmp_path,old,new):
    env=fixture(tmp_path);change_raw(env,old,new)
    with pytest.raises(ValueError):run(env)


def test_unrelated_links_and_credits_never_become_children_or_parent_authors(tmp_path):
    env=fixture(tmp_path)
    change_raw(env,'<ul>','<aside><a href="'+PAGE+'/7">別題</a><span class="book-author-type">著者</span>'
        '<a class="book-author-name">別人</a></aside><ul>')
    row=run(env)[0]
    assert row['title']=='完全な索引題 1' and row['authors'] is None and row['members']==[]


def test_duplicate_selected_card_is_ambiguous(tmp_path):
    env=fixture(tmp_path);change_raw(env,'</ul>', '</ul>'+card(7,'完全な索引題 1'))
    with pytest.raises(ValueError):run(env)


def test_metadata_only_candidate_fields_cannot_supply_missing_source_fields(tmp_path):
    env=fixture(tmp_path);env['proof']['groups']=[{'title':'FORGED','members':[{'title':'FAKE'}]}];repin(env)
    change_raw(env,'book-author-type','absent-role')
    with pytest.raises(ValueError):run(env)


def test_unmarked_index_credit_name_is_retained_with_unknown_role(tmp_path):
    env=fixture(tmp_path)
    change_raw(env,'<span class="book-author-type">著者</span>','')
    env['rows'][0].update(credits=[],author_credit='');repin(env)
    row=run(env)[0]
    assert row['index_credit_literals']==[{'role_literal':None,'name':'索引の人物','role_scope':'publisher_index_card'}]
    assert row['authors'] is None


def test_unrecognized_explicit_role_is_literal_and_never_canonical_author(tmp_path):
    env=fixture(tmp_path);change_raw(env,'>著者<','>作画協力<')
    env['rows'][0].update(credits=[{'role':'作画協力','name':'索引の人物'}],author_credit='作画協力：索引の人物');repin(env)
    row=run(env)[0]
    assert row['index_credit_literals'][0]['role_literal']=='作画協力'
    assert row['authors'] is None and 'role' not in row['index_credit_literals'][0]


def test_selected_native_card_repeated_on_another_selected_page_is_rejected(tmp_path):
    env=fixture(tmp_path)
    change_raw(env,'<ul>','<ul>'+card(7,'完全な索引題 1'),page=1)
    with pytest.raises(ValueError):run(env)


@pytest.mark.parametrize('old,new',[
    ('>完全な索引題 1<','><img alt="完全な索引題 1"><'),
    ('>完全な索引題 1<','><span hidden>完全な索引題 1</span><'),
    ('>索引の人物<','><input value="索引の人物"><'),
    ('>著者<','><span aria-hidden="true">著者</span><'),
])
def test_explicit_title_and_credit_fields_cannot_use_hidden_or_image_fallback(tmp_path,old,new):
    env=fixture(tmp_path);change_raw(env,old,new)
    with pytest.raises(ValueError):run(env)


@pytest.mark.parametrize('pin',['coverage_report','source_frames','source_roster'])
def test_metadata_artifact_paths_cannot_escape_evidence_root(tmp_path,pin):
    env=fixture(tmp_path);env['proof'][pin]['path']='../escape'
    env['pin']=env['save']('proof.json',env['proof'])
    with pytest.raises(ValueError):run(env)


def test_covered_metadata_partition_cannot_repeat_ids(tmp_path):
    env=fixture(tmp_path)
    # Replace the covered row with a duplicate selected row while keeping3 rows.
    env['delta']['rows'][2]={**env['delta']['rows'][0], 'status':'covered'};repin(env)
    with pytest.raises(ValueError):run(env)


def test_covered_metadata_ids_match_roster_even_when_counts_are_unchanged(tmp_path):
    env=fixture(tmp_path)
    extra={**env['rows'][2],'reference_id':old_id(PAGE+'/10'),'source_url':PAGE+'/10'}
    env['rows'].append(extra)
    env['delta']['rows'].append(dict(env['delta']['rows'][2]))
    env['delta']['covered_reference_ids'].append(extra['reference_id'])
    env['delta']['provider_counts']['hj-bunko-series'].update(source_references=4,covered=2)
    env['frames'][0]['reference_series_count']=4
    env['proof']['expected_counts']['index_groups']=4
    repin(env)
    with pytest.raises(ValueError):run(env)


def test_same_explicit_role_item_retains_all_direct_names_beyond_legacy_first_projection(tmp_path):
    env=fixture(tmp_path)
    original='<span class="book-author-type">著者</span><a class="book-author-name">索引の人物</a>'
    actual=original+'</li><li class="book-author-item"><span class="book-author-type">イラスト</span>' \
        '<a class="book-author-name">画家A</a><a class="book-author-name">画家B</a>'
    change_raw(env,original,actual)
    env['rows'][0]['credits'].append({'role':'イラスト','name':'画家A'})
    env['rows'][0]['author_credit']+=' イラスト：画家A';repin(env)
    row=run(env)[0]
    assert row['index_credit_literals']==[
        {'role_literal':'著者','name':'索引の人物','role_scope':'publisher_index_card'},
        {'role_literal':'イラスト','name':'画家A','role_scope':'publisher_index_card'},
        {'role_literal':'イラスト','name':'画家B','role_scope':'publisher_index_card'}]
    assert row['legacy_index_credit_projection']==env['rows'][0]['credits']
    assert row['legacy_projection_reproduced'] is True and row['authors'] is None


@pytest.mark.parametrize('inserted',[
    '<span class="book-author-type">イラスト</span>',
    '<div><a class="book-author-name">nested-name</a></div>',
    '<li class="book-author-item"><a class="book-author-name">nested-item-name</a></li>',
])
def test_ambiguous_roles_or_descendant_credit_names_are_not_merged(tmp_path,inserted):
    env=fixture(tmp_path)
    change_raw(env,'>索引の人物</a>','>索引の人物</a>'+inserted)
    with pytest.raises(ValueError):run(env)


def test_own_credit_failure_message_always_names_index_hash_card_and_reference(tmp_path):
    env=fixture(tmp_path)
    change_raw(env,'book-author-name','missing-name')
    with pytest.raises(ValueError) as error:run(env)
    message=str(error.value)
    for literal in [PAGE,env['frames'][0]['pages'][0]['sha256'],PAGE+'/7',old_id(PAGE+'/7'),'locator=']:
        assert literal in message


def test_metadata_source_disagreement_message_includes_own_card_context(tmp_path):
    env=fixture(tmp_path);env['rows'][0]['credits'][0]['name']='forged';repin(env)
    with pytest.raises(ValueError) as error:run(env)
    for literal in [PAGE,env['frames'][0]['pages'][0]['sha256'],PAGE+'/7',old_id(PAGE+'/7')]:
        assert literal in str(error.value)


def test_missing_selected_raw_message_retains_declared_card_and_source_context(tmp_path):
    env=fixture(tmp_path);(env['root']/'coverage/page1.html').unlink()
    with pytest.raises(ValueError) as error:run(env)
    for literal in [PAGE,env['frames'][0]['pages'][0]['sha256'],PAGE+'/7',old_id(PAGE+'/7'),
                    'declared_expected_own_card_context']:
        assert literal in str(error.value)


@pytest.mark.parametrize('change',['old_artifact','loosened_credit_boundary'])
def test_v2_credit_policy_is_explicitly_pinned_in_proof(tmp_path,change):
    env=fixture(tmp_path)
    if change=='old_artifact':env['proof'].pop('artifact_version')
    else:env['proof']['index_credit_contract']={**CONTRACT,'descendant_or_cross_item_name_fallback':True}
    repin(env)
    with pytest.raises(ValueError):run(env)


def test_hj_adopted_synthetic_output_is_byte_identical_after_profile_extension(tmp_path):
    env=fixture(tmp_path)
    encoded=json.dumps(run(env),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    assert digest(encoded)=='7d497b6ebdcff72468fe3341e7e7191c210f549b3748f4dd5452c6503698e544'


def test_hjnovels_uses_own_namespace_and_same_unknown_membership_contract(tmp_path):
    env=fixture(tmp_path,profile='hjnovels');row=run(env)[0]
    key={'provider':'hj-novels-index-only','source_index':NOVELS_PAGE,
         'source_scope':'selected_native_index_cards_only','official_group_url':NOVELS_PAGE+'/7'}
    assert row['local_reference_key']==key
    assert row['reference_id']=='hjnovels-index-group-local:'+digest(json.dumps(key,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())
    assert row['schema_version']=='light-novel-hjnovels-index-source-group-v1'
    assert row['members']==[] and row['frame_membership_complete'] is None and row['total_product_count'] is None
    assert row['authors'] is None and row['index_credit_literals'][0]['role_scope']=='publisher_index_card'


def test_hjnovels_never_opens_hj_profile_or_vice_versa(tmp_path):
    from recommend_book.light_novel_hj_index_groups_v1 import verify_hjnovels_index_groups
    env=fixture(tmp_path)
    with pytest.raises(ValueError):verify_hjnovels_index_groups(env['root']/env['pin']['path'],env['pin']['sha256'])
    env['proof']['schema_version']='hjnovels-selected-index-groups-proof-v1';repin(env)
    with pytest.raises(ValueError):run(env)


def test_hjnovels_has_its_own_smaller_selected_finite_cap(tmp_path):
    env=fixture(tmp_path,profile='hjnovels');env['proof']['expected_counts']['selected_groups']=40;repin(env)
    with pytest.raises(ValueError):run(env)
