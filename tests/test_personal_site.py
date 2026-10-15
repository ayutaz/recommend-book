import json
import re
from html.parser import HTMLParser

from recommend_book.recommendation.personal_site import render_personal_site


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids = set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            assert attrs['id'] not in self.ids
            self.ids.add(attrs['id'])


def test_public_page_controls_unknown_read_status_and_featureless_unscored():
    delivery = {'ranking': [
        {'work_id': 'public:wd:Q1', 'title': '原題', 'authors': ['著者'], 'display_title_aliases': ['別題'], 'read_status': 'unknown', 'content_labels': ['genre.fantasy'], 'score': -0.25, 'score_status': 'supported', 'explanations': [{'feature_id': 'genre.fantasy', 'contribution': -0.25}]},
        {'work_id': 'public:wd:Q2', 'title': '特徴なし', 'content_labels': [], 'score': 0.9, 'score_status': 'supported'}], 'audit': {}}
    html = render_personal_site(delivery, {'genre.fantasy': 'ファンタジー'})
    ids = Page(html).ids
    assert {'search-query', 'label-filter', 'score-status', 'reset-filters', 'shown-count', 'empty-state', 'results', 'recommendation-data'} <= ids
    assert '品質は未検証' in html and '確率ではありません' in html
    assert '読書状態：不明' in html and '原分類の採用特徴なし' in html
    assert 'ファンタジー' in html and '別題' in html and '著者' in html
    assert 'スコア -0.25' in html
    data = json.loads(re.search(r'<script id="recommendation-data" type="application/json">(.*?)</script>', html, re.S).group(1))
    assert data['ranking'][1]['score_status'] == 'unsupported'
    assert data['ranking'][1]['score'] is None


def test_untrusted_strings_and_script_terminators_cannot_execute_or_export_extra_fields():
    attack = '</script><img src=x onerror=alert(1)>'
    delivery = {'ranking': [{'work_id': 'public:wd:Q1', 'title': attack, 'authors': [attack], 'display_title_aliases': [attack], 'content_labels': ['genre.fantasy'], 'score': 1, 'score_status': 'supported', 'explanations': [{'feature_id': 'genre.fantasy', 'contribution': 1}], 'private_extra': 'do-not-export'}], 'audit': {'private_extra': 'do-not-export'}}
    html = render_personal_site(delivery, {'genre.fantasy': attack})
    assert attack not in html and '<img' not in html
    assert 'do-not-export' not in html
    assert html.count('</script>') == 2
    assert 'innerHTML' not in html
    assert not re.search(r'<(?:script|link)[^>]+(?:src|href)=', html)


def test_source_declared_genre_is_distinguished_with_public_entity_source():
    row = {'work_id': 'public:wd:Q100', 'title': 'Declared genre',
           'content_labels': ['genre.fantasy'], 'classification_basis': 'source_declared_genre',
           'source_qid': 'Q100', 'private_extra': 'never-export'}
    html = render_personal_site({'ranking': [row]}, {'genre.fantasy': 'Fantasy'})
    assert '資料記載のジャンル（暫定）' in html
    assert 'href="https://www.wikidata.org/wiki/Q100"' in html
    assert 'never-export' not in html


def test_untrusted_source_link_and_basis_cannot_expand_public_allowlist():
    attack = 'Q1" onclick="alert(1)'
    rows = [{'title': 'A', 'classification_basis': 'source_declared_genre', 'source_qid': attack},
            {'title': 'B', 'classification_basis': 'private-owner-basis', 'source_qid': 'private-owner-id'}]
    html = render_personal_site({'ranking': rows}, {})
    assert 'onclick=' not in html and 'private-owner' not in html
    assert 'href="https://www.wikidata.org/wiki/' not in html
