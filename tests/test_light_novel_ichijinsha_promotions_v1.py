import hashlib
import json
import pytest
from recommend_book.light_novel_ichijinsha_promotions_v1 import ENTRY, verify_ichijinsha_promotions


def save(path, value):
    raw = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def frame(tmp_path):
    def pin(name, url, text):
        file = tmp_path / name; digest = save(file, text)
        return {'source_url': url, 'final_url': url, 'http_status': 200, 'content_type': 'text/html; charset=utf-8',
                'sha256': digest, 'byte_length': file.stat().st_size, 'raw_path': name}
    url = 'https://www.ichijinsha.co.jp/iris/example/'
    otherurl = 'https://www.ichijinsha.co.jp/iris/other/'
    index = pin('root.html', ENTRY, '<a href="' + url + '"><img alt="作品特集"></a><a href="' + otherurl + '"><img alt="別特集"></a>')
    body = '<div class="book_info"><div class="book_header"><h3>旧作の題 ２ 副題</h3><div>著：架空 著者 装画：架空 画家</div></div><div class="book_data">2008年1月20日発売 ISBN 978-4-7580-4767-8</div><p>PRIVATE_PROSE</p></div>'
    own = pin('own.html', url, body)
    other = pin('other.html', otherurl, body)
    frames = [{'source_url': u, 'source_snapshot': p, 'index_source_snapshot': index, 'series_assertion_admitted': False} for u, p in [(url, own), (otherurl, other)]]
    identity = 'publisher-promotion-book:' + hashlib.sha256((url + '#1').encode()).hexdigest()[:24]
    rows = [{'reference_id': identity, 'kind': 'publisher_promotion_listed_book', 'title': '旧作の題 ２ 副題', 'isbn': '9784758047678',
             'creator_display': '著：架空 著者 装画：架空 画家', 'source_url': url, 'locator': 'book_info[1]/book_header+book_data',
             'source_snapshot': own, 'novel_form': 'unknown_may_include_comic_adaptations', 'story_series_identity': 'unassessed'}]
    proof = {'schema_version': 'ichijinsha-iris-selected-promotion-references-v1', 'roster_path': 'roster.json',
             'roster_sha256': save(tmp_path / 'roster.json', rows), 'sourceframes_path': 'frames.jsonl',
             'sourceframes_sha256': save(tmp_path / 'frames.jsonl', '\n'.join(json.dumps(r, ensure_ascii=False) for r in frames)), 'reference_ids': [identity]}
    path = tmp_path / 'proof.json'
    return path, save(path, proof), proof, rows, frames


def repin(bundle):
    path, _, proof, rows, frames = bundle
    proof['roster_sha256'] = save(path.parent / 'roster.json', rows)
    proof['sourceframes_sha256'] = save(path.parent / 'frames.jsonl', '\n'.join(json.dumps(r, ensure_ascii=False) for r in frames))
    return save(path, proof)


def test_selected_promotion_replays_only_root_and_own_page_preserving_unknowns(frame, monkeypatch):
    import recommend_book.light_novel_ichijinsha_promotions_v1 as source
    path, sha, *_ = frame
    source_read = source._source; reads = []
    def checked(root, pin, url):
        reads.append(url)
        return source_read(root, pin, url)
    monkeypatch.setattr(source, '_source', checked)
    (path.parent / 'other.html').write_bytes(b'changed unselected raw')
    (row,) = verify_ichijinsha_promotions(path, sha)
    assert len(reads) == len(set(reads)) == 2
    assert row['isbn'] == '9784758047678'
    assert row['credits'] == [{'role': 'unknown', 'name': '著：架空 著者 装画：架空 画家'}]
    assert row['publisher'] is row['label'] is row['form'] is row['official_lightnovel_tag'] is None
    assert row['label_literals'] == [] and row['story_series_asserted'] is False
    assert 'PRIVATE_PROSE' not in json.dumps(row) and str(path.parent) not in json.dumps(row)


@pytest.mark.parametrize('change', ['raw', 'root_link', 'no_image', 'title', 'isbn', 'creator', 'duplicate_isbn', 'ordinal', 'duplicate_id', 'novel_claim', 'path_escape', 'receipt_mismatch'])
def test_selected_promotion_tampering_cannot_assert_bibliography(frame, change):
    path, sha, proof, rows, frames = frame
    if change == 'raw':
        (path.parent / 'own.html').write_bytes(b'changed raw')
    elif change in ('root_link', 'no_image', 'title', 'isbn', 'creator', 'duplicate_isbn'):
        root_change = change in ('root_link', 'no_image')
        file = path.parent / ('root.html' if root_change else 'own.html')
        text = file.read_text(encoding='utf-8')
        if change == 'root_link': text = text.replace('iris/example/', 'iris/missing/')
        elif change == 'no_image': text = text.replace('<img alt="作品特集">', '作品特集')
        elif change == 'title': text = text.replace('旧作の題', '別の題')
        elif change == 'isbn': text = text.replace('4767-8', '4767-1')
        elif change == 'creator': text = text.replace('装画：', '著：')
        else: text = text.replace('2008年', 'ISBN 9784758047678 2008年')
        receipt = frames[0]['index_source_snapshot'] if root_change else frames[0]['source_snapshot']
        receipt.update(sha256=save(file, text), byte_length=file.stat().st_size)
        sha = repin(frame)
    elif change == 'ordinal':
        rows[0]['locator'] = 'book_info[2]/book_header+book_data'; sha = repin(frame)
    elif change == 'duplicate_id':
        proof['reference_ids'] *= 2; sha = save(path, proof)
    elif change == 'novel_claim':
        rows[0]['novel_form'] = 'novel'; sha = repin(frame)
    elif change == 'path_escape':
        frames[0]['source_snapshot']['raw_path'] = '../escape.html'; sha = repin(frame)
    else:
        rows[0]['source_snapshot'] = dict(rows[0]['source_snapshot'], source_url='https://evil.example/'); sha = repin(frame)
    with pytest.raises(ValueError):
        verify_ichijinsha_promotions(path, sha)


def test_promotion_import_public_search_keeps_independent_book_scope(frame):
    from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
    from recommend_book.recommendation.scale_site_v1 import public_work
    path, sha, proof, *_ = frame
    cat = LightNovelCatalogue(path.parent / 'catalogue.sqlite')
    result = cat.import_ichijinsha_iris_special_publisher_references(path, sha, reference_ids=proof['reference_ids'])
    assert result['inserted'] == 1
    identity = result['reference_entries'][proof['reference_ids'][0]]
    row = cat.work_detail(identity); shown = public_work(row)
    assert row['authors'] == [] and row['members'] == [] and row['read_status'] == 'unknown'
    assert all(row[k] is False for k in ('light_novel_confirmed', 'light_novel_identity_asserted', 'edition_identity_asserted', 'story_series_identity_asserted', 'original_work_identity_asserted', 'recommendable'))
    assert shown['publisher_reference_kind'] == 'ichijinsha_iris_promotion_reference'
    assert shown['isbn'] == ['9784758047678']
    assert shown['publisher_product_reference']['label'] is None and shown['publisher_product_reference']['form'] is None
    assert shown['publisher_product_reference']['credits'] == [{'role': 'unknown', 'name': '著：架空 著者 装画：架空 画家'}]
    assert shown['publisher_product_reference']['credit_literals'] == []
    assert cat.search_catalogue('旧作の題', entry_kind='publisher_reference')['total'] == 1
    assert cat.search_catalogue('架空 著者', entry_kind='publisher_reference')['total'] == 1
    again = cat.import_ichijinsha_iris_special_publisher_references(path, sha, reference_ids=proof['reference_ids'])
    assert again['unchanged'] == 1 and again['inserted'] == again['updated'] == 0
