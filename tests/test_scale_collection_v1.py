import importlib.util
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
import pytest

PATH=Path(__file__).parents[1]/'scripts/collect_scale_wikidata_v1.py'
spec=importlib.util.spec_from_file_location('scale_collect',PATH)
collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)

def test_resume_preserves_frame_and_does_not_repeat_request(tmp_path):
    calls=[]
    class Fetcher:
        def get(self,url,**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status='success',content=json.dumps({'results':{'bindings':[{'work':{'value':'http://www.wikidata.org/entity/Q123'}}]}}).encode(),artifact=SimpleNamespace(path='saved.json',sha256='a'*64),error=None)
    first=collector.discover(tmp_path,Fetcher(),groups=[('fixture',['Q1860'],1)],target=1)
    second=collector.discover(tmp_path,Fetcher(),groups=[('fixture',['Q1860'],1)],target=1)
    assert first==second==['Q123'] and len(calls)==1
    with pytest.raises(ValueError,match='frame'):
        collector.discover(tmp_path,Fetcher(),groups=[('fixture',['Q5287'],1)],target=1)
    assert len(calls)==1

def test_failed_page_is_recorded_without_retry_on_resume(tmp_path):
    calls=[]
    class Fetcher:
        def get(self,*args,**kwargs):
            calls.append(1)
            return SimpleNamespace(status='failed',error='HTTP429',artifact=None,content=b'')
    assert collector.discover(tmp_path,Fetcher(),groups=[('fixture',[],1)],target=1)==[]
    assert collector.discover(tmp_path,Fetcher(),groups=[('fixture',[],1)],target=1)==[]
    assert len(calls)==1

def test_discovery_requests_novels_and_genres_without_short_stories():
    query=collector.discovery_query(['Q5287'],limit=500,offset=1000)
    assert 'wd:Q8261' in query and 'wd:Q747381' in query
    assert 'wd:Q49084' not in query and 'wdt:P136' in query and 'P629' in query
    with pytest.raises(ValueError):collector.discovery_query(['Q1} UNION {?x ?y ?z'],limit=50,offset=0)


def test_one_held_page_does_not_block_later_pages_or_retry_on_resume(tmp_path):
    calls=[]
    class Fetcher:
        def get(self,url,**kwargs):
            query=kwargs['params']['query'];calls.append(query)
            if query.endswith('OFFSET 0'):
                return SimpleNamespace(status='failed',error='HTTP502',artifact=None,content=b'')
            payload={'results':{'bindings':[{'work':{'value':'http://www.wikidata.org/entity/Q456'}}]}}
            return SimpleNamespace(status='success',content=json.dumps(payload).encode(),
                artifact=SimpleNamespace(path='saved.json',sha256='b'*64),error=None)
    for _ in range(2):
        assert collector.discover(tmp_path,Fetcher(),groups=[('fixture',[],1000)],target=1000)==['Q456']
    assert len(calls)==2


def test_rate_limit_stops_new_pages_and_new_groups_on_resume(tmp_path):
    calls=[]
    class Fetcher:
        def get(self,*args,**kwargs):
            calls.append(1)
            return SimpleNamespace(status='failed',status_code=429,error='HTTP 429',artifact=None,content=b'')
    for _ in range(2):
        assert collector.discover(tmp_path,Fetcher(),groups=[('first',[],1000),('second',[],1000)],target=2000)==[]
    assert len(calls)==1


def test_explicit_server_cooldown_release_allows_only_unsent_pages(tmp_path,monkeypatch):
    calls=[]
    gate=tmp_path/'gate.sqlite'
    with sqlite3.connect(gate) as db:
        db.execute('CREATE TABLE cooldowns(host TEXT,not_before REAL)')
        db.execute('INSERT INTO cooldowns VALUES(?,?)',('query.wikidata.org',time.time()+120))
    class Fetcher:
        gate_path=gate
        def get(self,url,**kwargs):
            query=kwargs['params']['query'];calls.append(query)
            if len(calls)==1:
                return SimpleNamespace(status='failed',status_code=429,error='HTTP 429',artifact=None,content=b'')
            payload={'results':{'bindings':[{'work':{'value':'http://www.wikidata.org/entity/Q789'}}]}}
            return SimpleNamespace(status='success',content=json.dumps(payload).encode(),
                artifact=SimpleNamespace(path='saved.json',sha256='c'*64),error=None)
    frame={'groups':[('fixture',[],1000)],'target':1000}
    assert collector.discover(tmp_path,Fetcher(),**frame)==[]
    assert collector.discover(tmp_path,Fetcher(),**frame)==[]
    after_release=time.time()+121
    monkeypatch.setattr(collector.time,'time',lambda:after_release)
    assert collector.discover(tmp_path,Fetcher(),**frame)==['Q789']
    assert len(calls)==2 and calls[1].endswith('OFFSET 500')


def test_old_cooldown_cannot_release_new_429_without_server_deadline(tmp_path):
    gate=tmp_path/'gate.sqlite'
    with sqlite3.connect(gate) as db:
        db.execute('CREATE TABLE cooldowns(host TEXT,not_before REAL)')
        db.execute('INSERT INTO cooldowns VALUES(?,?)',('query.wikidata.org',time.time()-300))
    fetcher=SimpleNamespace(gate_path=gate)
    assert collector.rate_limit_active(fetcher,'HTTP 429',collector.now()) is True
