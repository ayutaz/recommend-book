"""Synthetic purpose receipts exercise output isolation; no private rating gold."""
from copy import deepcopy
import pytest
from recommend_book import next_delivery as nd
from recommend_book.recommendation.next_evaluation import owned_title_spec
from recommend_book.usage_guard import UsageDecision,model_hash
from recommend_book.models import WorkInput


class Guard:
    def check_derived(self,payload,origins,usage,**kwargs):
        return UsageDecision(allowed='held' not in origins and payload.get('work_id')!='held' and payload.get('title')!='held alias',usage=usage,ledger_path='synthetic',ledger_sha256='a'*64,
            input_hashes=origins,payload_sha256=model_hash(payload))
    def check_private_reference_aggregate(self,payload,origins,**kwargs):
        return UsageDecision(allowed=True,usage='local_storage',ledger_path='synthetic',ledger_sha256='a'*64,
            input_hashes=origins,payload_sha256=model_hash(payload))


def world(work='w',**updates):
    row={'work_id':work,'title':'AB world','authors':[],'original_language':None,'first_publication_year':None,'region':[],'series':None,'features':{},'feature_provenance':{},
        'series_ids':[],'read_status':'unknown','rating':None,'external_ids':[],
        'bibliographic_originating_input_hashes':['safe'],
        'classification_originating_input_hashes':[],**updates}
    fields=('title','authors','original_language','first_publication_year','region','series')
    original=WorkInput(work_id=work,**{f:row[f] for f in fields})
    payload={'work_id':work,**{f:row[f] for f in fields}}
    origins=[model_hash(original)]
    if not updates.get('bibliographic_originating_input_hashes')==[]:row['bibliographic_originating_input_hashes']=origins
    row['bibliographic_derivation']={'kind':'bibliographic_metadata','payload':payload,'value_sha256':model_hash(payload),
        'originating_input_hashes':origins,'source_version_ids':[], 'input_json':original.model_dump(mode='json'),'input_hash':origins[0],
        'recipe':{'path':'synthetic','sha256':'c'*64}}
    for alias in row.get('display_title_aliases',[]):
        title=alias['title'];a=WorkInput(work_id=work,title=title)
        alias['work_id']=work
        alias['originating_input_hashes']=[model_hash(a)]
        value={'work_id':work,'title':title,'language':alias['language'],'role':alias['role']}
        alias['derivation_packet']={'kind':'title_alias','payload':value,'value_sha256':model_hash(value),
            'originating_input_hashes':alias['originating_input_hashes'],'source_version_ids':[],
            'input_json':a.model_dump(mode='json'),'input_hash':model_hash(a),'recipe':{'path':'synthetic','sha256':'c'*64}}
    return row


def build(rows,**kwargs):
    return nd.build_next_delivery(rows,[{'work_id':'private-123','title':'AB personal secret','rating':5}],
        guard=Guard(),origin_ids=['own'],spec=owned_title_spec('b'*64),**kwargs)


def test_delivery_lexical_personal_and_metadata_baseline_private_output_zero():
    rows=[world()];before=deepcopy(rows);got=build(rows)
    assert got['ranking'][0]['score']==2
    assert got['bibliographic_baseline'][0]['score'] is None
    assert got['audit']['confirmed_unread_n']==0 and got['audit']['user_tasks']==0
    assert 'private-123' not in str(got) and 'personal secret' not in str(got) and rows==before
    assert '書名' in nd.render_next_delivery(got)


def test_whole_classification_origin_held_even_no_quotes():
    row=world(features={'theme.growth':1},feature_provenance={'theme.growth':[{'scope':'work','quote':None}]},
        classification_originating_input_hashes=['safe','held'])
    got=build([row],mode='classified-reference')
    assert got['audit']['content_feature_held_n']==1
    assert got['audit']['content_feature_eligible_n']==0
    assert 'theme.growth' not in str(got['ranking'])
    assert len(got['bibliographic_baseline'])==1


def test_held_bibliography_never_displayed_or_scored():
    got=build([world('safe'),world('held',title='unsafe raw',bibliographic_originating_input_hashes=['held'])])
    assert [r['work_id'] for r in got['ranking']]==['safe']
    assert 'unsafe raw' not in nd.render_next_delivery(got)


def test_style_translation_scope_not_promoted_and_html_escaped():
    got=build([world(title='<script>alert(1)</script>AB',features={'style.simple':1},
        feature_provenance={'style.simple':[{'scope':'translation','expression_id':'edition'}]},
        classification_originating_input_hashes=['safe'])],mode='classified-reference')
    assert got['audit']['scope_feature_held_n']==1
    html=nd.render_next_delivery(got)
    assert '<script>alert(1)</script>' not in html and '&lt;script&gt;' in html
    assert '<form' not in html


def test_bool_receipt_or_missing_origin_is_not_permission():
    with pytest.raises(ValueError,match='bibliographic'):
        build([world(bibliographic_originating_input_hashes=[])])


@pytest.mark.parametrize('mode',['lexical-reference','classified-reference'])
def test_alias_is_individually_guarded_and_held_alias_cannot_support(mode):
    aliases=[{'title':'held alias','language':'ja','role':'unknown','originating_input_hashes':['held'],'source_version_ids':['s']}]
    got=nd.build_next_delivery([world(title='QQ',display_title_aliases=aliases)],
        [{'work_id':'p','title':'held alias','rating':5}],guard=Guard(),origin_ids=['own'],
        spec=owned_title_spec('b'*64,title_alias_mode=True),mode=mode)
    assert got['ranking'][0]['score'] is None and got['audit']['title_alias_held_n']==1
    assert got['ranking'][0]['display_title_aliases']==[]
    aliases[0]['originating_input_hashes']=['safe'];aliases[0]['title']='AB'
    got=nd.build_next_delivery([world(title='QQ',display_title_aliases=aliases)],
        [{'work_id':'p','title':'AB','rating':5}],guard=Guard(),origin_ids=['own'],
        spec=owned_title_spec('b'*64,title_alias_mode=True),mode=mode)
    assert got['ranking'][0]['score']==2 and got['audit']['title_alias_used_n']==1
    assert got['ranking'][0]['display_title_aliases']==['AB']


def test_external_llm_permission_never_substitutes_display():
    class WrongPurpose(Guard):
        def check_derived(self,*args,**kwargs):
            decision=super().check_derived(*args,**kwargs)
            return decision.model_copy(update={'usage':'external_llm'})
    with pytest.raises(ValueError,match='Display purpose'):
        nd.build_next_delivery([world()],[{'work_id':'p','title':'AB','rating':5}],guard=WrongPurpose(),
            origin_ids=['own'],spec=owned_title_spec('a'*64))


def test_claimed_hash_cannot_authorize_changed_title_or_secret_extra_packet():
    row=world();row['title']='unbound title'
    with pytest.raises(ValueError,match='typed'):
        build([row])
    row=world();row['bibliographic_derivation']['payload']['private_annotation']='secret'
    with pytest.raises(ValueError,match='typed'):
        build([row])


def test_bibliographic_only_needs_no_owned_data_and_ignores_all_old_features():
    class MetadataGuard(Guard):
        def check_private_reference_aggregate(self,*args,**kwargs):
            raise AssertionError('Owned data must not be admitted in metadata mode')
    class NoClassAccess(dict):
        def get(self,key,*args):
            if key in ('features','feature_provenance','classification_derivation','classification_originating_input_hashes'):
                raise AssertionError('Historical classification was inspected')
            return super().get(key,*args)
    row=NoClassAccess(world(features={'unsafe.old':1},classification_originating_input_hashes=['held']))
    got=nd.build_next_delivery([row],guard=MetadataGuard(),mode='bibliographic-only')
    assert got['ranking'][0]['score'] is None and got['ranking'][0]['score_status']=='unsupported'
    assert got['method']=='bibliographic_unscored' and got['purpose'] is None
    assert got['audit']['classification_features_read'] is False
    assert 'unsafe.old' not in str(got) and '書誌のみ' in nd.render_next_delivery(got)


def test_cli_bibliography_explicit_mode_does_not_load_owned_or_content(tmp_path,monkeypatch):
    import importlib.util
    from pathlib import Path
    import sys
    import recommend_book.next_database as dbmodule
    source=Path(__file__).resolve().parents[1]/'scripts/build_next_delivery.py'
    spec=importlib.util.spec_from_file_location('next_delivery_cli_fixture',source)
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    worlddb=tmp_path/'world.sqlite';worlddb.write_bytes(b'synthetic')
    seen=[]
    def load(path,**kwargs):
        seen.append(kwargs['content_classified'])
        return {'rows':[world()],'audit':{'content_classified':False}}
    monkeypatch.setattr(dbmodule,'load_next_world_database',load)
    monkeypatch.setattr(cli,'UsageGuard',lambda *a,**kw:Guard())
    monkeypatch.setattr(cli,'load_owned_ratings',lambda *a,**kw:pytest.fail('Owned lookup not allowed'))
    monkeypatch.setattr(sys,'argv',['build_next_delivery.py','--mode','bibliographic-only','--database',str(worlddb),
        '--ledger','synthetic','--ledger-sha256','a'*64,'--output',str(tmp_path/'output')])
    cli.main()
    assert seen==[False] and (tmp_path/'output/index.html').exists()


def test_bibliography_alias_display_is_guarded_without_a_lexical_score():
    aliases=[{'title':'<b>JA alias</b>','language':'ja','role':'unknown','originating_input_hashes':['safe'],'source_version_ids':['s']},
        {'title':'held alias','language':'ja','role':'unknown','originating_input_hashes':['held'],'source_version_ids':['s']}]
    got=nd.build_next_delivery([world(display_title_aliases=aliases)],guard=Guard(),mode='bibliographic-only')
    assert got['ranking'][0]['display_title_aliases']==['<b>JA alias</b>']
    assert got['ranking'][0]['score'] is None
    assert got['audit']['title_alias_used_n']==1 and got['audit']['title_alias_held_n']==1
    html=nd.render_next_delivery(got)
    assert '&lt;b&gt;JA alias&lt;/b&gt;' in html and 'held alias' not in html


@pytest.fixture
def formal_native_rows(tmp_path,monkeypatch):
    # Borrow the database lane's real import/loader packet fixture rather than
    # inventing feature/native dictionaries. Native subprocess is fully mocked.
    from test_next_database import next_run
    import recommend_book.next_database as dbapi
    fixture=next_run.__wrapped__(tmp_path,monkeypatch)
    dbapi.import_next_classifications(**fixture)
    loaded=dbapi.load_next_world_database(fixture['output_dir']/'dataset.sqlite',guard=fixture['guard'],content_classified=True)
    inputs=dbapi.get_next_inputs(fixture['output_dir']/'dataset.sqlite')['classification']
    return loaded['rows'],inputs


def test_formal_database_native_and_feature_packets_are_verified_separately(formal_native_rows):
    import json
    from pathlib import Path
    from recommend_book.classification.native_attempt import verify_native_attempt
    from recommend_book.next_database import extract_classification_features
    rows,works=formal_native_rows;calls=[]
    class NativeGuard(Guard):
        def check_derived(self,payload,origins,usage,**kwargs):
            if payload==rows[0]['classification_derivation']['payload'] or payload==rows[1]['classification_derivation']['payload'] or 'features' in payload:
                assert kwargs.get('source_manifest'), 'native descriptor must be passed'
                manifest=kwargs['source_manifest'];native=payload
                request=json.loads(Path(manifest['artifacts']['request']['path']).read_bytes())
                selected=[work for work in works if model_hash(work) in origins]
                if 'features' in payload:
                    assert kwargs.get('derivation_recipe'), 'feature recipe must be passed'
                    recipe=kwargs['derivation_recipe']
                    original=json.loads(Path(manifest['artifacts']['final']['path']).read_bytes())
                    candidates=original.get('results',[original]);native=next(r for r in candidates if r['work_id']==payload['work_id'])
                    assert recipe['native_result_sha256']==model_hash(native)
                    chosen=next(w for w in selected if w.work_id==native['work_id'])
                    key=request['job_keys'][origins.index(model_hash(chosen))]
                    assert extract_classification_features(chosen,native,key,request['run_id'])==payload
                    calls.append('features')
                else:calls.append('native')
                verify_native_attempt(manifest,native,origins,expected_config_sha256=manifest['config_sha256'],works=selected)
            return super().check_derived(payload,origins,usage,**kwargs)
    got=nd.build_next_delivery(rows,[{'work_id':'p','title':'Book','rating':5}],guard=NativeGuard(),origin_ids=['own'],
        spec=owned_title_spec('b'*64),mode='classified-reference')
    assert calls==['native','features','native','features']
    assert got['audit']['content_feature_eligible_n']==2
    assert all(row['content_labels']==['genre.mystery'] for row in got['ranking'])


@pytest.mark.parametrize('missing',['classification_derivation','feature_derivation','source_manifest','features_recipe'])
def test_missing_formal_classification_packet_is_held_without_keyerror(formal_native_rows,missing):
    rows,_=formal_native_rows;rows=deepcopy(rows)
    if missing in ('classification_derivation','feature_derivation'):rows[0].pop(missing)
    else:rows[0]['feature_derivation'].pop(missing)
    got=nd.build_next_delivery(rows,[{'work_id':'p','title':'Book','rating':5}],guard=Guard(),origin_ids=['own'],
        spec=owned_title_spec('b'*64),mode='classified-reference')
    assert got['audit']['content_feature_held_n']==1
    assert got['ranking'][0]['content_labels']==[]


def test_formal_packet_whole_batch_origin_is_never_reduced_to_cited_work(formal_native_rows):
    rows,_=formal_native_rows;rows=deepcopy(rows);seen=[]
    # A held, uncited fellow input is still a whole-batch origin.
    row=rows[0];origins=row['classification_originating_input_hashes']+['held']
    row['classification_originating_input_hashes']=origins
    row['classification_derivation']['originating_input_hashes']=origins
    row['feature_derivation']['originating_input_hashes']=origins
    class BatchGuard(Guard):
        def check_derived(self,payload,fullorigins,usage,**kwargs):
            if kwargs.get('source_manifest'):seen.append(fullorigins)
            return super().check_derived(payload,fullorigins,usage,**kwargs)
    got=nd.build_next_delivery(rows,[{'work_id':'p','title':'Book','rating':5}],guard=BatchGuard(),origin_ids=['own'],
        spec=owned_title_spec('b'*64),mode='classified-reference')
    assert seen[0]==origins and got['audit']['content_feature_held_n']==1
    assert got['ranking'][0]['content_labels']==[]


def test_feature_packet_cannot_claim_a_different_native_result(formal_native_rows):
    rows,_=formal_native_rows;rows=deepcopy(rows)
    rows[0]['feature_derivation']['features_recipe']['native_result_sha256']='0'*64
    with pytest.raises(ValueError,match='native classification/feature'):
        nd.build_next_delivery(rows,[{'work_id':'p','title':'Book','rating':5}],guard=Guard(),origin_ids=['own'],
            spec=owned_title_spec('b'*64),mode='classified-reference')
