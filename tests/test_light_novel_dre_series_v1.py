import pytest

from recommend_book.light_novel_ndl_series_v1 import _cards


DETAIL='''<div class="lineupColumn__label drenovels">DREノベルス</div>
<h1 class="lineupColumn__title">シリーズ甲2 <span class="sub">完全副題</span></h1>
<span class="lineupColumn__author">著 / 著者甲</span>
<span class="lineupColumn__author">イラスト / 画家乙</span>
<a class="lineupColumn__linkButton" href="https://drecom-media.jp/drenovels/series/example">シリーズ紹介</a>
<a class="pickupSeriesList_item" href="https://drecom-media.jp/drenovels/series/example">
<p class="pickupSeriesList_title">シリーズ甲</p><p class="pickupSeriesList_author">役割不明な別名</p></a>'''


def test_series_title_requires_own_explicit_series_link_and_direct_author():
    row,=_cards('dre-novels-series',DETAIL)
    assert row['title']=='シリーズ甲'
    assert row['authors']==['著者甲']
    assert row['representative_product_title']=='シリーズ甲2 完全副題'


def test_similar_title_without_explicit_series_link_is_not_a_series_assertion():
    assert _cards('dre-novels-series',DETAIL.replace('>シリーズ紹介</a>','>詳細</a>'))==[]


@pytest.mark.parametrize('before,after',[
    ('DREノベルス</div>','DREコミックス</div>'),
    ('著 / 著者甲','漫画 / 著者甲'),
    ('<p class="pickupSeriesList_title">','<p class="unknown">'),
    ('href="https://drecom-media.jp/drenovels/series/example">\n<p','href="https://drecom-media.jp/drenovels/series/other">\n<p'),
])
def test_ambiguous_or_non_novel_series_evidence_rejected(before,after):
    with pytest.raises(ValueError):_cards('dre-novels-series',DETAIL.replace(before,after))
