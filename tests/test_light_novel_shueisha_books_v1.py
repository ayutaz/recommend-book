import hashlib
import json

import pytest

from recommend_book.light_novel_shueisha_books_v1 import verify_shueisha_books


def make_frame(tmp_path, *, next_page=False, isbn='978-4-08-631001-7'):
    source='https://dash.shueisha.co.jp/library/index/'
    book=f'https://dash.shueisha.co.jp/bookDetail/index/{isbn}'
    document=f'''<h3>検索結果：1件</h3>
    <section class="result_box"><h4><a href="{book}">試験小説 <span>２</span></a></h4>
    <p><span class="boldText">著者：架空 花子</span><br>
    <span class="boldText">イラスト：架空 太郎</span><br>
    <span class="boldText">発売日</span>：2020年01月01日<br>
    <span class="boldText">レーベル</span>：ダッシュエックス文庫</p>
    <p>この紹介文は出力しない</p></section>'''
    if next_page:document+='<a href="/library/index/2/">次の20件</a>'
    raw=document.encode(); (tmp_path/'page.bin').write_bytes(raw)
    manifest={'frame_id':'shueisha-dash-library-all-v1','complete':True,'publisher_reported_books':1,
              'visited_pages':[source],'sources':[{'source_url':source,'status':200,'sha256':hashlib.sha256(raw).hexdigest(),
              'raw_path':'page.bin','retrieved_at':'2026-10-25T00:00:00+00:00'}]}
    path=tmp_path/'manifest.json'; path.write_text(json.dumps(manifest),encoding='utf-8')
    return path,hashlib.sha256(path.read_bytes()).hexdigest()


def test_reproduces_bibliography_without_publisher_prose(tmp_path):
    path,sha=make_frame(tmp_path)
    rows=verify_shueisha_books(path,sha)
    assert len(rows)==1
    row=rows[0]
    assert row['title']=='試験小説 ２'
    assert row['isbn']=='9784086310017'
    assert row['credits']==[{'role':'著者','name':'架空 花子'},{'role':'イラスト','name':'架空 太郎'}]
    assert row['label']=='ダッシュエックス文庫'
    assert 'この紹介文' not in json.dumps(rows,ensure_ascii=False)


def test_rejects_changed_manifest_or_raw(tmp_path):
    path,sha=make_frame(tmp_path)
    with pytest.raises(ValueError,match='manifest hash'):
        verify_shueisha_books(path,'0'*64)
    (tmp_path/'page.bin').write_bytes(b'changed')
    with pytest.raises(ValueError,match='source hash'):
        verify_shueisha_books(path,sha)


def test_rejects_unfollowed_next_even_if_manifest_claims_complete(tmp_path):
    path,sha=make_frame(tmp_path,next_page=True)
    with pytest.raises(ValueError,match='pagination'):
        verify_shueisha_books(path,sha)


def test_preserves_invalid_publisher_isbn_without_authenticating_it(tmp_path):
    path,sha=make_frame(tmp_path,isbn='978-4-08-630192-X')
    row=verify_shueisha_books(path,sha)[0]
    assert row['isbn'] is None
    assert row['publisher_book_id']=='978-4-08-630192-X'


def test_rejects_raw_path_escape_and_count_mismatch(tmp_path):
    path,_=make_frame(tmp_path)
    m=json.loads(path.read_text(encoding='utf-8')); m['sources'][0]['raw_path']='../outside.bin'
    path.write_text(json.dumps(m),encoding='utf-8')
    with pytest.raises(ValueError,match='path'):
        verify_shueisha_books(path,hashlib.sha256(path.read_bytes()).hexdigest())
    path,_=make_frame(tmp_path); m=json.loads(path.read_text(encoding='utf-8'));m['publisher_reported_books']=2
    path.write_text(json.dumps(m),encoding='utf-8')
    with pytest.raises(ValueError,match='count'):
        verify_shueisha_books(path,hashlib.sha256(path.read_bytes()).hexdigest())


def test_page_boundary_overlap_requires_identical_bibliography(tmp_path):
    path,_=make_frame(tmp_path)
    second_raw=(tmp_path/'page.bin').read_bytes()
    path,_=make_frame(tmp_path,next_page=True)
    (tmp_path/'second.bin').write_bytes(second_raw)
    m=json.loads(path.read_text(encoding='utf-8'))
    second={**m['sources'][0],'source_url':'https://dash.shueisha.co.jp/library/index/2/',
            'raw_path':'second.bin','sha256':hashlib.sha256(second_raw).hexdigest()}
    m['sources'].append(second);m['visited_pages'].append(second['source_url'])
    path.write_text(json.dumps(m),encoding='utf-8')
    assert len(verify_shueisha_books(path,hashlib.sha256(path.read_bytes()).hexdigest()))==1
    changed=second_raw.replace('架空 花子'.encode(),'別の 人物'.encode())
    (tmp_path/'second.bin').write_bytes(changed);second['sha256']=hashlib.sha256(changed).hexdigest()
    path.write_text(json.dumps(m),encoding='utf-8')
    with pytest.raises(ValueError,match='metadata conflict'):
        verify_shueisha_books(path,hashlib.sha256(path.read_bytes()).hexdigest())
