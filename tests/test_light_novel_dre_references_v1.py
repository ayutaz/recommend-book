import hashlib
import json

import pytest

from recommend_book.light_novel_dre_references_v1 import ENTRY, parse_year, verify_dre_references
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work


CARD = '''<option value="2024" selected>2024年</option>
<li class="bookColumn__item"><a href="https://drecom-media.jp/drenovels/product/21">
<p class="comicProductList_label drenovels">DREノベルス</p>
<p class="bookColumn__title">作品名2 ～完全な副題～</p>
<span class="comicProductList_author">著 / 著者甲</span>
<span class="comicProductList_author">イラスト / 画家乙</span>
<span class="productColumn__infoLabel">発売日</span><span class="productColumn__infoValue">2024年7月10日</span>
<span class="productColumn__infoLabel">ISBN</span><span class="productColumn__infoValue">978-4-434-31101-7</span>
</a></li>'''


def proof(tmp_path, text=CARD):
    raw=tmp_path/'page.html'; raw.write_text(text,encoding='utf-8')
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'version':'dre-official-yearly-bibliography-v1','pages':[
        {'year':2024,'url':ENTRY+'?y=2024','status':'success','status_code':200,
         'artifact':{'path':str(raw),'sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),
                     'size':len(raw.read_bytes()),'source_url':ENTRY+'?y=2024'}}]}),encoding='utf-8')
    return manifest,hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_literal_bibliography_and_unknown_identity(tmp_path):
    path,pin=proof(tmp_path)
    row,=verify_dre_references(path,pin)
    assert row['title']=='作品名2 ～完全な副題～'
    assert row['isbn']=='9784434311017'
    assert row['credits'][0]=={'role':'author','role_display':'著','name':'著者甲'}
    assert row['form'] is row['official_lightnovel_tag'] is None
    assert row['story_series_asserted'] is False
    cat=LightNovelCatalogue(tmp_path/'catalogue.sqlite')
    result=cat.import_dre_publisher_references(path,pin,reference_ids=['dre-product:21'])
    assert result['inserted']==1
    assert len(result['reference_entries'])==1
    identity=result['reference_entries']['dre-product:21']
    shown=public_work(cat.work_detail(identity))
    assert shown['publisher_reference_kind']=='dre_product_reference'
    assert shown['isbn']==['9784434311017']
    assert shown['publisher_product_reference']['credits'][0]['role']=='author'
    assert not shown.get('recommendable')


@pytest.mark.parametrize('before,after',[
    ('value="2024" selected','value="2024"'),
    ('DREノベルス</p>','DREコミックス</p>'),
    ('2024年7月10日','2023年7月10日'),
    ('著 / 著者甲','漫画 / 著者甲'),
    ('978-4-434-31101-7','978-4-434-31101-0'),
    ('https://drecom-media.jp/drenovels/product/21','https://example.com/drenovels/product/21'),
])
def test_invalid_card_rejected(before,after):
    with pytest.raises(ValueError):parse_year(CARD.replace(before,after),2024,ENTRY+'?y=2024','a'*64)


def test_raw_tamper_rejected(tmp_path):
    path,pin=proof(tmp_path)
    (tmp_path/'page.html').write_text(CARD+'x',encoding='utf-8')
    with pytest.raises(ValueError,match='hash'):verify_dre_references(path,pin)


def test_missing_isbn_is_not_invented():
    text=CARD.replace('<span class="productColumn__infoLabel">ISBN</span><span class="productColumn__infoValue">978-4-434-31101-7</span>','')
    assert parse_year(text,2024,ENTRY+'?y=2024','a'*64)[0]['isbn'] is None


def test_duplicate_product_rejected():
    with pytest.raises(ValueError,match='duplicated'):
        parse_year(CARD+CARD[CARD.index('<li'):],2024,ENTRY+'?y=2024','a'*64)
