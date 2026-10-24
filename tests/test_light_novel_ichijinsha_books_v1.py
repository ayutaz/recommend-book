import gzip
import hashlib
import json

import pytest

from recommend_book.light_novel_ichijinsha_books_v1 import verify_ichijinsha_books


ROOT_URL = 'https://www.ichijinsha.co.jp/iris/title/'
FACET_URL = ROOT_URL + '2025/iris/'
BOOK_URL = ROOT_URL + 'sample/'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(tmp_path, *, detail=False, compressed=False):
    def snapshot(name, url, text):
        raw = text.encode()
        if compressed:
            raw = gzip.compress(raw)
        (tmp_path / name).write_bytes(raw)
        return {'raw_path': name, 'sha256': digest(raw), 'source_url': url, 'final_url': url,
                'http_status': 200, 'content_type': 'text/html;charset=UTF-8'}

    card = '<article class="c-title-card"><a href="' + BOOK_URL + '"></a><h1 class="c-title-card__title">空の物語 2</h1><span class="c-creators__creator">著：作者 太郎</span><span class="c-creators__creator">絵：画家 花子</span></article>'
    root = snapshot('root.html', ROOT_URL, '<img alt="一迅社文庫アイリス"><a href="' + FACET_URL + '">2025年</a>')
    facet = snapshot('facet.html', FACET_URL, '<img alt="一迅社文庫アイリス">' + card)
    pages = [{'source_snapshot': root}, {'source_snapshot': facet}]
    roster = [{'reference_id': 'publisher-book:' + digest(BOOK_URL.encode())[:24], 'source_url': BOOK_URL,
               'title': '空の物語 2', 'credits': ['著：作者 太郎', '絵：画家 花子'],
               'label_year_proofs': [{'label': '一迅社文庫アイリス', 'year': 2025, 'source_snapshot': facet}]}]
    (tmp_path / 'roster.json').write_text(json.dumps(roster), encoding='utf-8')
    (tmp_path / 'pages.jsonl').write_text('\n'.join(json.dumps(page) for page in pages), encoding='utf-8')
    manifest = {'schema_version': 'ichijinsha-iris-book-proof-v1', 'roster_path': 'roster.json',
                'roster_sha256': digest((tmp_path / 'roster.json').read_bytes()), 'pages_path': 'pages.jsonl',
                'pages_sha256': digest((tmp_path / 'pages.jsonl').read_bytes())}
    if detail:
        receipt = snapshot('detail.html', BOOK_URL, '<h1 class="c-article-header__title">空の物語 2</h1><span class="c-creators__creator">著：作者 太郎</span><span class="c-creators__creator">絵：画家 花子</span><table><tr><th>ISBN</th><td>9784758097918</td></tr></table>')
        (tmp_path / 'details.json').write_text(json.dumps([{'source_url': BOOK_URL, 'source_snapshot': receipt}]), encoding='utf-8')
        manifest.update(details_path='details.json', details_sha256=digest((tmp_path / 'details.json').read_bytes()))
    return write_manifest(tmp_path, manifest)


def write_manifest(tmp_path, value):
    path = tmp_path / 'proof.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return path, digest(path.read_bytes())


def test_reproduces_book_role_and_brand_without_inventing_isbn_or_series(tmp_path):
    path, sha = fixture(tmp_path)
    rows = verify_ichijinsha_books(path, sha)
    assert len(rows) == 1
    assert rows[0]['title'] == '空の物語 2'
    assert rows[0]['isbn'] is None
    assert rows[0]['label'] == '一迅社文庫アイリス'
    assert rows[0]['credits'] == [{'role': 'author', 'name': '作者 太郎'}, {'role': 'illustrator', 'name': '画家 花子'}]
    assert 'series_id' not in rows[0]


def test_gzip_bytes_and_linked_detail_isbn_replay(tmp_path):
    path, sha = fixture(tmp_path, detail=True, compressed=True)
    assert verify_ichijinsha_books(path, sha)[0]['isbn'] == '9784758097918'


def test_tampered_raw_rejected(tmp_path):
    path, sha = fixture(tmp_path)
    (tmp_path / 'facet.html').write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='hash'):
        verify_ichijinsha_books(path, sha)


def test_manifest_hash_rejected(tmp_path):
    path, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match='hash'):
        verify_ichijinsha_books(path, '0' * 64)


def test_forged_creator_or_label_rejected_even_when_roster_hash_updated(tmp_path):
    path, _ = fixture(tmp_path)
    roster_path = tmp_path / 'roster.json'
    rows = json.loads(roster_path.read_text())
    rows[0]['credits'][0] = '著：画家 花子'
    roster_path.write_text(json.dumps(rows))
    manifest = json.loads(path.read_text())
    manifest['roster_sha256'] = digest(roster_path.read_bytes())
    path, sha = write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match='card'):
        verify_ichijinsha_books(path, sha)


def test_unlinked_year_facet_is_not_proof(tmp_path):
    path, _ = fixture(tmp_path)
    (tmp_path / 'root.html').write_text('<img alt="一迅社文庫アイリス">', encoding='utf-8')
    pages = [json.loads(line) for line in (tmp_path / 'pages.jsonl').read_text().splitlines()]
    pages[0]['source_snapshot']['sha256'] = digest((tmp_path / 'root.html').read_bytes())
    (tmp_path / 'pages.jsonl').write_text('\n'.join(json.dumps(row) for row in pages))
    manifest = json.loads(path.read_text())
    manifest['pages_sha256'] = digest((tmp_path / 'pages.jsonl').read_bytes())
    path, sha = write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match='reachable'):
        verify_ichijinsha_books(path, sha)


def test_relabelled_facet_rejected(tmp_path):
    path, _ = fixture(tmp_path)
    roster_path = tmp_path / 'roster.json'
    rows = json.loads(roster_path.read_text(encoding='utf-8'))
    rows[0]['label_year_proofs'][0]['label'] = 'アイリスNEO'
    roster_path.write_text(json.dumps(rows), encoding='utf-8')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest['roster_sha256'] = digest(roster_path.read_bytes())
    path, sha = write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match='label/year'):
        verify_ichijinsha_books(path, sha)


def test_detail_for_another_book_cannot_supply_isbn(tmp_path):
    path, _ = fixture(tmp_path, detail=True)
    detail_path = tmp_path / 'detail.html'
    detail_path.write_text(detail_path.read_text(encoding='utf-8').replace('空の物語 2', '別の物語'), encoding='utf-8')
    details_path = tmp_path / 'details.json'
    rows = json.loads(details_path.read_text(encoding='utf-8'))
    rows[0]['source_snapshot']['sha256'] = digest(detail_path.read_bytes())
    details_path.write_text(json.dumps(rows), encoding='utf-8')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest['details_sha256'] = digest(details_path.read_bytes())
    path, sha = write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match='detail title/creator'):
        verify_ichijinsha_books(path, sha)


def test_evidence_path_cannot_escape_snapshot_root(tmp_path):
    path, _ = fixture(tmp_path)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest['roster_path'] = '../roster.json'
    path, sha = write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match='escapes'):
        verify_ichijinsha_books(path, sha)


def test_observed_slash_redirect_connects_canonical_facet(tmp_path):
    path, _ = fixture(tmp_path)
    root_path = tmp_path / 'root.html'
    root_path.write_text(root_path.read_text(encoding='utf-8').replace(FACET_URL, FACET_URL.rstrip('/')), encoding='utf-8')
    pages_path = tmp_path / 'pages.jsonl'
    pages = [json.loads(line) for line in pages_path.read_text(encoding='utf-8').splitlines()]
    pages[0]['source_snapshot']['sha256'] = digest(root_path.read_bytes())
    redirected = dict(pages[1]['source_snapshot'], source_url=FACET_URL.rstrip('/'), final_url=FACET_URL)
    pages.append({'source_snapshot': redirected})
    pages_path.write_text('\n'.join(json.dumps(row) for row in pages), encoding='utf-8')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest['pages_sha256'] = digest(pages_path.read_bytes())
    path, sha = write_manifest(tmp_path, manifest)
    assert len(verify_ichijinsha_books(path, sha)) == 1


def test_commented_card_is_not_a_visible_publisher_assertion(tmp_path):
    path, _ = fixture(tmp_path)
    facet_path = tmp_path / 'facet.html'
    value = facet_path.read_text(encoding='utf-8').replace('<article ', '<!-- <article ').replace('</article>', '</article> -->')
    facet_path.write_text(value, encoding='utf-8')
    pages_path = tmp_path / 'pages.jsonl'
    pages = [json.loads(line) for line in pages_path.read_text(encoding='utf-8').splitlines()]
    receipt = pages[1]['source_snapshot']
    receipt['sha256'] = digest(facet_path.read_bytes())
    pages_path.write_text('\n'.join(json.dumps(row) for row in pages), encoding='utf-8')
    roster_path = tmp_path / 'roster.json'
    rows = json.loads(roster_path.read_text(encoding='utf-8'))
    rows[0]['label_year_proofs'][0]['source_snapshot'] = receipt
    roster_path.write_text(json.dumps(rows), encoding='utf-8')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest.update(pages_sha256=digest(pages_path.read_bytes()), roster_sha256=digest(roster_path.read_bytes()))
    path, sha = write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match='card'):
        verify_ichijinsha_books(path, sha)
