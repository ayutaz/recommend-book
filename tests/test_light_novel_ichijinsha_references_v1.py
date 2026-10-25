import hashlib
import json
import pytest
from recommend_book.light_novel_ichijinsha_references_v1 import verify_ichijinsha_references, ENTRY


def save(path, value):
    raw = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def frame(tmp_path):
    url = ENTRY + 'example/'
    identity = 'publisher-book:' + hashlib.sha256(url.encode()).hexdigest()[:24]
    facet = ENTRY + '2025/iris/'
    def receipt(name, url, body):
        raw = tmp_path / name
        sha = save(raw, body)
        return {'source_url': url, 'final_url': url, 'http_status': 200, 'content_type': 'text/html; charset=utf-8',
                'sha256': sha, 'byte_length': raw.stat().st_size, 'raw_path': name}
    root = receipt('root.html', ENTRY, '<a href="' + facet + '">2025年</a>')
    card = '<article class="c-title-card"><a href="' + url + '"><h1 class="c-title-card__title">完全な題 ２ 副題</h1></a><span class="c-creators__creator">著： 架空 著者</span><span class="c-creators__creator">監修： 未確認 氏</span><p>PRIVATE_PROSE</p></article>'
    own = receipt('facet.html', facet, '<img alt="一迅社文庫アイリス">' + card)
    other = receipt('other.html', ENTRY + '2024/iris/', 'unrelated raw')
    detail = receipt('detail.html', url, '<h1 class="c-article-header__title">完全な題 ２ 副題</h1><span class="c-creators__creator">著： 架空 著者</span><span class="c-creators__creator">監修： 未確認 氏</span><table><th>ISBN</th><td>978-4-7580-4767-8</td></table><p>PRIVATE_PROSE</p>')
    roster = [{'reference_id': identity, 'source_url': url, 'title': '完全な題 ２ 副題', 'credits': ['著： 架空 著者', '監修： 未確認 氏'],
               'label_year_proofs': [{'label': '一迅社文庫アイリス', 'year': 2025, 'source_snapshot': own}]}]
    pages = [{'source_snapshot': root, 'outgoing_urls': [facet]}, {'source_snapshot': own, 'outgoing_urls': []}, {'source_snapshot': other, 'outgoing_urls': []}]
    core = {'schema_version': 'ichijinsha-iris-book-proof-v1', 'roster_path': 'roster.json', 'roster_sha256': save(tmp_path / 'roster.json', roster),
            'pages_path': 'pages.jsonl', 'pages_sha256': save(tmp_path / 'pages.jsonl', '\n'.join(json.dumps(p, ensure_ascii=False) for p in pages)),
            'details_path': 'details.json', 'details_sha256': save(tmp_path / 'details.json', [{'source_url': url, 'source_snapshot': detail}])}
    proof = {'schema_version': 'ichijinsha-iris-selected-references-v1', 'core_manifest_path': 'core.json',
             'core_manifest_sha256': save(tmp_path / 'core.json', core), 'reference_ids': [identity]}
    path = tmp_path / 'selected.json'
    return path, save(path, proof), proof, core, roster, pages, detail


def repin(frame):
    path, _, proof, core, roster, pages, detail = frame
    root = path.parent
    core['roster_sha256'] = save(root / 'roster.json', roster)
    core['pages_sha256'] = save(root / 'pages.jsonl', '\n'.join(json.dumps(p, ensure_ascii=False) for p in pages))
    core['details_sha256'] = save(root / 'details.json', [{'source_url': detail['source_url'], 'source_snapshot': detail}])
    proof['core_manifest_sha256'] = save(root / 'core.json', core)
    return save(path, proof)


def test_selected_raw_scope_explicit_roles_and_unknown_identity(frame, monkeypatch):
    import recommend_book.light_novel_ichijinsha_references_v1 as source
    path, sha, *_ = frame
    read = source._document
    reads = []
    def checked(root, pin):
        reads.append(pin['source_url'])
        return read(root, pin)
    monkeypatch.setattr(source, '_document', checked)
    (path.parent / 'other.html').write_bytes(b'corrupted unrelated raw')
    rows = verify_ichijinsha_references(path, sha)
    assert len(rows) == 1 and len(reads) == 3 and len(set(reads)) == 3
    row = rows[0]
    assert row['isbn'] == '9784758047678' and row['form'] is None
    assert row['official_lightnovel_tag'] is None and row['story_series_asserted'] is False
    assert row['credits'] == [{'role': 'author', 'role_display': '著', 'name': '架空 著者'}, {'role': 'unknown', 'role_display': '監修', 'name': '未確認 氏'}]
    assert 'PRIVATE_PROSE' not in json.dumps(rows) and str(path.parent) not in json.dumps(rows)


@pytest.mark.parametrize('change', ['raw', 'path_escape', 'navigation', 'brand', 'title', 'role', 'duplicate_card', 'label', 'selection', 'detail_isbn', 'detail_title'])
def test_selected_evidence_tampering_cannot_become_a_reference(frame, change):
    path, sha, proof, core, roster, pages, detail = frame
    if change == 'raw':
        (path.parent / 'facet.html').write_bytes(b'changed')
    elif change == 'path_escape':
        pages[1]['source_snapshot']['raw_path'] = '../outside.html'
        sha = repin(frame)
    elif change in ('navigation', 'brand', 'title', 'role', 'duplicate_card'):
        target = path.parent / ('root.html' if change == 'navigation' else 'facet.html')
        value = target.read_text(encoding='utf-8')
        if change == 'navigation': value = '<p>no actual facet link</p>'
        elif change == 'brand': value = value.replace('一迅社文庫アイリス', 'コミック')
        elif change == 'title': value = value.replace('完全な題', '別の題')
        elif change == 'role': value = value.replace('監修：', '著：')
        else: value += value
        pin = pages[0 if change == 'navigation' else 1]['source_snapshot']
        pin.update(sha256=save(target, value), byte_length=target.stat().st_size)
        sha = repin(frame)
    elif change == 'label':
        roster[0]['label_year_proofs'][0]['label'] = 'アイリスNEO'
        sha = repin(frame)
    elif change == 'selection':
        proof['reference_ids'] *= 2
        sha = save(path, proof)
    else:
        target = path.parent / 'detail.html'
        value = target.read_text(encoding='utf-8')
        value = value.replace('978-4-7580-4767-8', '978-4-7580-4767-1') if change == 'detail_isbn' else value.replace('完全な題', '別の題')
        detail.update(sha256=save(target, value), byte_length=target.stat().st_size)
        sha = repin(frame)
    with pytest.raises(ValueError):
        verify_ichijinsha_references(path, sha)


def test_missing_detail_does_not_infer_isbn_from_url_or_roster(frame):
    path, _, proof, core, *_ = frame
    core.pop('details_path'); core.pop('details_sha256')
    proof['core_manifest_sha256'] = save(path.parent / 'core.json', core)
    rows = verify_ichijinsha_references(path, save(path, proof))
    assert rows[0]['isbn'] is None
