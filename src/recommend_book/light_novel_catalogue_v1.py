"""Separate, raw-backed Japanese light-novel catalogue with typed identities.

Metadata never grants permission over Wikipedia prose. Every public operation
revalidates its local evidence; request-local caches only avoid duplicate reads.
Series features are verified by their own export verifier and never copied to a
volume. Read state and the original scale catalogue remain independent.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import sqlite3
import unicodedata
import uuid

READ_STATUSES = {'unknown', 'unread', 'reading', 'read'}
AXES = ('genre', 'theme', 'structure', 'protagonist', 'style', 'atmosphere')
CONFLICT_TYPES = {'Q5398426', 'Q639669', 'Q21198342', 'Q8274', 'Q202866',
                  'Q4167410', 'Q4167836', 'Q11424', 'Q24856', 'Q11346433', 'Q136747113'}
RELATIONS = ('P179', 'P527', 'P629', 'P155', 'P156', 'P144')
ECOBALT_REFERENCE_SCHEMA = 'light-novel-ecobalt-publisher-reference-v1'
FUTABA_REFERENCE_SCHEMA = 'light-novel-futaba-publisher-reference-v1'
KADOKAWA_REFERENCE_SCHEMA = 'light-novel-kadokawa-publisher-reference-v1'
KADOKAWA_PRINT_REFERENCE_SCHEMA = 'light-novel-kadokawa-print-publisher-reference-v1'
KADOKAWA_TRADE_REFERENCE_SCHEMA = 'light-novel-kadokawa-trade-publisher-reference-v1'
TOBOOKS_REFERENCE_SCHEMA = 'light-novel-tobooks-publisher-reference-v1'
GAGAGA_REFERENCE_SCHEMA = 'light-novel-gagaga-publisher-reference-v1'
KODANSHA_REFERENCE_SCHEMA = 'light-novel-kodansha-publisher-reference-v1'
OVERLAP_BUNKO_DETAIL_REFERENCE_SCHEMA = 'light-novel-overlap-bunko-detail-publisher-reference-v1'
OVERLAP_DETAIL_REFERENCE_SCHEMA = 'light-novel-overlap-detail-publisher-reference-v1'
SHUEISHA_DX_REFERENCE_SCHEMA = 'light-novel-shueisha-dx-publisher-reference-v1'
GC_REFERENCE_SCHEMA = 'light-novel-gc-publisher-reference-v1'
OVERLAP_BUNKO_REFERENCE_SCHEMA = 'light-novel-overlap-bunko-publisher-reference-v1'
ALPHAPOLIS_REFERENCE_SCHEMA = 'light-novel-alphapolis-publisher-reference-v1'
HIFUMI_REFERENCE_SCHEMA = 'light-novel-hifumi-publisher-reference-v1'
ICHIJINSHA_IRIS_REFERENCE_SCHEMA = 'light-novel-ichijinsha-iris-publisher-reference-v1'
ICHIJINSHA_PROMOTION_REFERENCE_SCHEMA = 'light-novel-ichijinsha-iris-promotion-reference-v1'
EARTHSTAR_REFERENCE_SCHEMA = 'light-novel-earthstar-publisher-reference-v1'
PUBLISHER_REFERENCE_PROVIDERS = {
    'dre': {'schema': 'light-novel-dre-publisher-reference-v1', 'record_provider':'dre',
        'loader':'_dre_publisher_references', 'fields':'_finite_listing_reference_fields',
        'reference_pattern':r'dre-product:[1-9][0-9]*', 'source_unit':'publisher_product_reference',
        'source_url_pattern':r'https://drecom-media\.jp/drenovels/product/[1-9][0-9]*',
        'source_publisher':'ドリコム', 'source_roles':('author','illustrator'),
        'public_kind':'dre_product_reference',
        'credit':'ローカル書誌参照: DREノベルス公式年別一覧（掲載参照）'},
    'earthstar': {'schema': EARTHSTAR_REFERENCE_SCHEMA, 'record_provider': 'earthstar',
        'loader': '_earthstar_publisher_references', 'fields': '_finite_listing_reference_fields',
        'reference_pattern': r'earthstar-work:[A-Za-z0-9_-]+', 'source_unit': 'publisher_work_reference',
        'source_url_pattern': r'https://earthstar-novel\.com/book/detail/[A-Za-z0-9_-]+/',
        'source_publisher': 'アース・スター エンターテイメント', 'source_roles': ('author', 'illustrator', 'unknown'),
        'public_kind': 'earthstar_work_reference',
        'credit': 'ローカル書誌参照: アース・スター公式作品一覧（作品・版・物語シリーズの同一性未確認）'},
    'ichijinshairisspecial': {'schema': ICHIJINSHA_PROMOTION_REFERENCE_SCHEMA, 'record_provider': 'ichijinsha-iris-special-reference',
        'loader': '_ichijinsha_iris_special_publisher_references', 'fields': '_finite_listing_reference_fields',
        'reference_pattern': r'publisher-promotion-book:[0-9a-f]{24}', 'source_unit': 'publisher_product_reference',
        'source_url_pattern': r'https://www\.ichijinsha\.co\.jp/iris/[A-Za-z0-9_-]+/?',
        'source_publisher': None, 'source_roles': ('unknown',), 'public_kind': 'ichijinsha_iris_promotion_reference',
        'credit': 'ローカル書誌参照: 一迅社公式特集の掲載書誌（小説・漫画の区別未確認）'},
    'ichijinshairis': {'schema': ICHIJINSHA_IRIS_REFERENCE_SCHEMA, 'record_provider': 'ichijinsha-iris-reference',
        'loader': '_ichijinsha_iris_publisher_references', 'fields': '_finite_listing_reference_fields',
        'reference_pattern': r'publisher-book:[0-9a-f]{24}', 'source_unit': 'publisher_product_reference',
        'source_url_pattern': r'https://www\.ichijinsha\.co\.jp/iris/title/[^/?#]+/',
        'source_publisher': '一迅社', 'source_roles': ('author', 'illustrator', 'unknown'),
        'public_kind': 'ichijinsha_iris_product_reference',
        'credit': 'ローカル書誌参照: 一迅社文庫アイリス・アイリスNEO公式年別一覧（掲載参照）'},
    'ecobalt': {'schema': ECOBALT_REFERENCE_SCHEMA, 'record_provider': 'ecobalt',
        'loader': '_ecobalt_publisher_references', 'fields': '_ecobalt_reference_fields',
        'reference_pattern': r'ecobalt-product-local:[0-9a-f]{64}',
        'public_kind': 'ecobalt_author_index_product_reference',
        'credit': '掲載書誌・著者索引表示: 集英社eコバルト文庫の書籍一覧'},
    'futaba': {'schema': FUTABA_REFERENCE_SCHEMA, 'record_provider': 'futaba-mnovels',
        'loader': '_futaba_publisher_references', 'fields': '_futaba_reference_fields',
        'reference_pattern': r'futaba-product:[A-Za-z0-9_-]+',
        'public_kind': 'futaba_mnovels_product_reference',
        'credit': '掲載書誌: 双葉社のＭノベルス・Ｍノベルスｆ商品一覧（書誌参照のみ）'},
    'kadokawa': {'schema': KADOKAWA_REFERENCE_SCHEMA, 'record_provider': 'kadokawa',
        'loader': '_kadokawa_publisher_references', 'fields': '_kadokawa_reference_fields',
        'reference_pattern': r'kadokawa-product:[0-9]{12}',
        'public_kind': 'kadokawa_product_reference',
        'credit': 'ローカル書誌参照: KADOKAWA公式商品一覧（電子専売の掲載書誌）'},
    'kadokawaprint': {'schema': KADOKAWA_PRINT_REFERENCE_SCHEMA, 'record_provider': 'kadokawa-print',
        'loader': '_kadokawa_print_publisher_references', 'fields': '_kadokawa_print_reference_fields',
        'paper_verifier': 'verify_kadokawa_print_references', 'paper_form_id': 3, 'paper_form_name': '文庫',
        'paper_source_scope': 'form3_valid_isbn_product_reference', 'paper_cache_tag': 'kadokawa_print_product_references',
        'reference_pattern': r'kadokawa-product:[0-9]{12}',
        'public_kind': 'kadokawa_print_product_reference',
        'credit': 'ローカル書誌参照: KADOKAWA公式商品一覧（紙の掲載書誌）'},
    'kadokawatrade': {'schema': KADOKAWA_TRADE_REFERENCE_SCHEMA, 'record_provider': 'kadokawa-trade',
        'loader': '_kadokawa_trade_publisher_references', 'fields': '_kadokawa_print_reference_fields',
        'paper_verifier': 'verify_kadokawa_trade_references', 'paper_form_id': 1, 'paper_form_name': '単行本',
        'paper_source_scope': 'form1_valid_isbn_product_reference', 'paper_cache_tag': 'kadokawa_trade_product_references',
        'reference_pattern': r'kadokawa-product:[0-9]{12}',
        'public_kind': 'kadokawa_trade_product_reference',
        'credit': 'ローカル書誌参照: KADOKAWA公式商品一覧（単行本の掲載書誌）'},
    'tobooks': {'schema': TOBOOKS_REFERENCE_SCHEMA, 'record_provider': 'tobooks',
        'loader': '_tobooks_publisher_references', 'fields': '_tobooks_reference_fields',
        'reference_pattern': r'tobooks-product:[0-9]+',
        'public_kind': 'tobooks_product_reference',
        'credit': 'ローカル書誌参照: TOブックス公式商品一覧（掲載参照・役割未確認）'},
    'gagaga': {'schema': GAGAGA_REFERENCE_SCHEMA, 'record_provider': 'gagaga',
        'loader': '_gagaga_publisher_references', 'fields': '_finite_listing_reference_fields',
        'reference_pattern': r'publisher-lineup(?:-snapshot)?:[0-9a-f]{24}',
        'source_unit': 'publisher_lineup_item_reference',
        'source_url_pattern': r'https://gagagabunko\.jp/(?:lineup/(?:[0-9]{6}|hard|mook)\.html|release/index\.html)#[A-Za-z0-9_.-]+',
        'source_publisher': '小学館',
        'source_roles': ('キャラクター原案', 'アニメーション製作', 'アニメーション制作', 'イラスト', '原案',
                         '原作', '監修', '著', '編', '漫画', '編集', '文', '作', '写真', '撮影'),
        'public_kind': 'gagaga_product_reference',
        'credit': 'ローカル書誌参照: 小学館ガガガ公式刊行一覧（掲載参照）'},
    'kodansha': {'schema': KODANSHA_REFERENCE_SCHEMA, 'record_provider': 'kodansha',
        'loader': '_kodansha_publisher_references', 'fields': '_finite_listing_reference_fields',
        'reference_pattern': r'kodansha-product:[0-9]+',
        'source_unit': 'publisher_product_reference',
        'source_url_pattern': r'https://www\.kodansha\.co\.jp/comic/products/[0-9]+',
        'source_publisher': None,
        'source_roles': ('author', 'illustrator', 'original_author', 'original_concept', 'supervisor', 'unknown'),
        'public_kind': 'kodansha_product_reference',
        'credit': 'ローカル書誌参照: 講談社ラノベ文庫公式商品一覧（掲載参照）'},
    'overlapdetails': {'schema': OVERLAP_DETAIL_REFERENCE_SCHEMA, 'record_provider': 'overlap-details',
        'loader': '_overlap_detail_publisher_references', 'fields': '_overlap_detail_reference_fields',
        'reference_pattern': r'overlap-(?:nvl|nvf|nvr)-product:[A-Za-z0-9_-]+',
        'public_kind': 'overlap_detail_product_reference',
        'credit': 'ローカル書誌参照: OVERLAP公式商品詳細と掲載カード（書誌参照のみ）'},
    'overlapbunkodetails': {'schema': OVERLAP_BUNKO_DETAIL_REFERENCE_SCHEMA,
        'record_provider': 'overlap-bunko-details', 'loader': '_overlap_bunko_detail_publisher_references',
        'fields': '_overlap_detail_reference_fields',
        'reference_pattern': r'overlap-bnk-detail-product:[A-Za-z0-9_-]+',
        'public_kind': 'overlap_bunko_detail_product_reference',
        'credit': 'ローカル書誌参照: OVERLAP文庫公式商品詳細と掲載カード（書誌参照のみ）'},
    'shueishadx': {'schema': SHUEISHA_DX_REFERENCE_SCHEMA, 'record_provider': 'shueisha-dx',
        'loader': '_shueisha_dx_publisher_references', 'fields': '_shueisha_dx_reference_fields',
        'reference_pattern': r'[0-9Xx-]+',
        'novel_labels': ('スーパーダッシュ文庫', 'ダッシュエックス文庫', 'Dノベルf／ダッシュエックスノベルエフ',
                         'Dノベル／ダッシュエックスノベル', 'ダッシュエックス文庫DIGITAL'),
        'public_kind': 'shueisha_dx_product_reference',
        'credit': 'ローカル書誌参照: 集英社ダッシュエックス公式書籍一覧（掲載参照・独立ISBN不明）'},
    'gc': {'schema': GC_REFERENCE_SCHEMA, 'record_provider': 'gc',
        'loader': '_gc_publisher_references', 'fields': '_gc_reference_fields',
        'reference_pattern': r'gc-product:[0-9]+', 'source_unit': 'publisher_product_reference',
        'source_url_pattern': r'https://gcnovels\.jp/book/[0-9]+', 'source_publisher': None,
        'source_roles': ('author', 'illustrator', 'original_author', 'manga_artist', 'supervisor', 'editor', 'unknown'),
        'public_kind': 'gc_product_reference',
        'credit': 'ローカル書誌参照: GC・GCN共通公式一覧の取得済み部分（個別ブランド未確認）'},
    'overlap': {'schema': OVERLAP_BUNKO_REFERENCE_SCHEMA, 'record_provider': 'overlap-bunko',
        'loader': '_overlap_bunko_publisher_references', 'fields': '_overlap_bunko_reference_fields',
        'reference_pattern': r'overlap-product:[A-Za-z0-9_-]+', 'source_unit': 'publisher_product_reference',
        'source_url_pattern': r'https://over-lap\.co\.jp/[^/?#]+/product/0/[A-Za-z0-9_-]+/\?[^#]+',
        'source_publisher': None, 'source_roles': (),
        'public_kind': 'overlap_bunko_product_reference',
        'credit': 'ローカル書誌参照: OVERLAP文庫公式掲載カード（ISBN・人物役割未確認）'},
    'alphapolis': {'schema': ALPHAPOLIS_REFERENCE_SCHEMA, 'record_provider': 'alphapolis',
        'loader': '_alphapolis_publisher_references', 'fields': '_alphapolis_reference_fields',
        'reference_pattern': r'alphapolis-commercial-book:[0-9]+/[0-9]+', 'source_unit': 'publisher_product_reference',
        'source_url_pattern': r'https://www\.alphapolis\.co\.jp/book/detail/[0-9]+/[0-9]+',
        'source_publisher': None, 'source_roles': ('author', 'editor', 'illustrator', 'original_story_credit', 'unknown'),
        'public_kind': 'alphapolis_commercial_book_reference',
        'credit': 'ローカル書誌参照: アルファポリス・レジーナ公式書籍一覧（掲載参照）'},
    'hifumi': {'schema': HIFUMI_REFERENCE_SCHEMA, 'record_provider': 'hifumi',
        'loader': '_hifumi_publisher_references', 'fields': '_hifumi_reference_fields',
        'reference_pattern': r'hifumi-product:[A-Za-z0-9_-]+', 'source_unit': 'publisher_product_reference',
        'source_url_pattern': r'https://hifumi\.co\.jp/lineup/[A-Za-z0-9_-]+/',
        'source_publisher': None, 'source_roles': ('author', 'illustrator', 'unknown'),
        'public_kind': 'hifumi_product_reference',
        'credit': 'ローカル書誌参照: 一二三書房の公式三レーベル書籍一覧（掲載参照）'},
}
SOURCE_GROUP_PROVIDERS = {
    'futaba': {
        'schema': 'light-novel-futaba-source-group-v1', 'record_provider': 'futaba-mnovels',
        'module': 'light_novel_futaba_workgroups_v1', 'verifier': 'verify_futaba_mnovels_workgroups',
        'loader': '_futaba_workgroups', 'reference_pattern': r'futaba-grouping-local:[0-9a-f]{64}',
        'member_pattern': r'futaba-product:[A-Za-z0-9_-]+',
        'source_kind': 'official_publisher_field_work_group_reference',
        'public_kind': 'futaba_mnovels_source_group', 'complete_field': 'frame_membership_complete',
        'credit': '作品グループ名・保存枠内の掲載参照: 双葉社の書籍一覧',
    },
    'orange': {
        'schema': 'light-novel-orange-source-group-v1', 'record_provider': 'orange',
        'module': 'light_novel_orange_groups_v1', 'verifier': 'verify_orange_groups',
        'loader': '_orange_workgroups', 'reference_pattern': r'orange-taxonomy-url:[0-9a-f]{64}',
        'member_pattern': r'shueisha-orange-book:[^/?#\s]+',
        'source_kind': 'official_publisher_listing_group_reference',
        'public_kind': 'orange_native_taxonomy_source_group', 'complete_field': 'parent_page_membership_complete',
        'credit': '掲載グループ名・保存枠内の掲載参照: 集英社オレンジ文庫の書籍一覧',
    },
    'ecobalt': {
        'schema': 'light-novel-ecobalt-source-group-v1', 'record_provider': 'ecobalt',
        'module': 'light_novel_ecobalt_groups_v1', 'verifier': 'verify_ecobalt_groups',
        'loader': '_ecobalt_workgroups', 'reference_pattern': r'ecobalt-heading-local:[0-9a-f]{64}',
        'member_pattern': r'shueisha-ebook:[A-Za-z0-9]+',
        'source_kind': 'official_publisher_listing_group_reference',
        'public_kind': 'ecobalt_author_index_source_group', 'complete_field': 'parent_page_membership_complete',
        'credit': '掲載グループ名・著者索引表示・保存枠内の掲載参照: 集英社eコバルト文庫の書籍一覧',
    },
    'sbcr': {
        'schema': 'light-novel-sbcr-source-group-v1', 'record_provider': 'sbcr',
        'module': 'light_novel_sbcr_groups_v1', 'verifier': 'verify_sbcr_groups',
        'loader': '_sbcr_workgroups', 'reference_pattern': r'sbcr-group-local:[0-9a-f]{64}',
        'member_pattern': r'sbcr-product:[A-Za-z0-9]+',
        'source_kind': 'official_publisher_listing_group_reference',
        'public_kind': 'sbcr_ebook_source_group', 'complete_field': 'parent_page_membership_complete',
        'credit': '掲載グループ名・保存枠内の掲載参照: SBクリエイティブの電子書籍一覧',
    },
    'hj': {
        'schema': 'light-novel-hj-index-source-group-v1', 'record_provider': 'hj-bunko-index-only',
        'module': 'light_novel_hj_index_groups_v1', 'verifier': 'verify_hj_index_groups',
        'loader': '_hj_index_workgroups', 'reference_pattern': r'hj-index-group-local:[0-9a-f]{64}',
        'member_pattern': r'(?!)', 'index_only': True,
        'source_kind': 'official_publisher_native_index_group_reference',
        'public_kind': 'hj_native_index_source_group', 'complete_field': 'native_index_card_complete',
        'credit': '索引掲載グループ名・索引内クレジット: ホビージャパンHJ文庫のシリーズ一覧',
    },
    'arian': {
        'schema': 'light-novel-arian-index-source-group-v1', 'record_provider': 'arian-rose-index-only',
        'module': 'light_novel_arian_index_groups_v1', 'verifier': 'verify_arian_index_groups',
        'loader': '_arian_index_workgroups', 'reference_pattern': r'arian-index-group-local:[0-9a-f]{64}',
        'member_pattern': r'(?!)', 'index_only': True,
        'source_kind': 'official_publisher_native_index_group_reference',
        'public_kind': 'arian_native_index_source_group', 'complete_field': 'native_index_card_complete',
        'credit': '索引掲載グループ名・索引内クレジット: アリアンローズのシリーズ一覧',
    },
    'hjnovels': {
        'schema': 'light-novel-hjnovels-index-source-group-v1', 'record_provider': 'hj-novels-index-only',
        'module': 'light_novel_hj_index_groups_v1', 'verifier': 'verify_hjnovels_index_groups',
        'loader': '_hjnovels_index_workgroups', 'reference_pattern': r'hjnovels-index-group-local:[0-9a-f]{64}',
        'member_pattern': r'(?!)', 'index_only': True,
        'source_kind': 'official_publisher_native_index_group_reference',
        'public_kind': 'hj_native_index_source_group', 'complete_field': 'native_index_card_complete',
        'credit': '索引掲載グループ名・索引内クレジット: ホビージャパンHJノベルスのシリーズ一覧',
    },
    'sbcrindex': {
        'schema': 'light-novel-sbcr-index-source-group-v1', 'record_provider': 'sbcr-index-only',
        'module': 'light_novel_sbcr_index_groups_v1', 'verifier': 'verify_sbcr_index_groups',
        'loader': '_sbcr_index_workgroups', 'reference_pattern': r'sbcr-index-group-local:[0-9a-f]{64}',
        'member_pattern': r'(?!)', 'index_only': True,
        'source_kind': 'official_publisher_native_index_group_reference',
        'public_kind': 'sbcr_native_index_source_group', 'complete_field': 'native_index_card_complete',
        'credit': '索引掲載グループ名: SBクリエイティブの電子書籍シリーズ一覧',
    },
}


_HIRAGANA_SEARCH_TRANSLATION = str.maketrans({chr(code): chr(code + 0x60) for code in range(0x3041, 0x3097)})


def normalize_search(value):
    text = unicodedata.normalize('NFKC', str(value)).casefold()
    return ''.join(text.split()).translate(_HIRAGANA_SEARCH_TRANSLATION)


def _search_without_punctuation(text):
    """Relax discovery only; identity and membership retain literal punctuation."""
    return ''.join(c for c in text if not unicodedata.category(c).startswith('P'))


def _search_text(fields):
    # Keep field boundaries: a title ending in 5 and an alias starting in 2
    # must remain two numeric sequences, rather than becoming volume 52.
    return ' '.join(normalize_search(field) for field in fields)


def _search_terms(query):
    # Publisher titles often place the volume before the subtitle, while ISBD
    # titles place it last. These terms widen search, never identity matching.
    text = unicodedata.normalize('NFKC', str(query))
    separated = ''.join(' ' if c.isspace() or unicodedata.category(c).startswith('P') else c for c in text)
    terms = tuple(dict.fromkeys(normalize_search(part) for part in re.findall(r'\d+|[^\d\s]+', separated)))
    return terms if len(terms) >= 2 and any(not term.isdecimal() for term in terms) else ()


def _search_contains(text, key, punctuationless_key, terms):
    if key in text:
        return True
    relaxed = _search_without_punctuation(text)
    relaxed_match = bool(punctuationless_key) and punctuationless_key in relaxed
    if not relaxed_match and not terms:
        return False
    numbers = {number.lstrip('0') or '0' for number in re.findall(r'\d+', text)}
    query_numbers = {number.lstrip('0') or '0' for number in re.findall(r'\d+', key)}
    if relaxed_match and query_numbers <= numbers:
        return True
    return bool(terms) and all((term.lstrip('0') or '0') in numbers if term.isdecimal() else term in relaxed for term in terms)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _resolved_path(path, cache):
    """Canonicalize each spelling once per operation, without caching evidence."""
    key = ('resolved_path', str(path))
    if key not in cache:
        cache[key] = Path(path).resolve()
    return cache[key]


def _qid(value):
    if not isinstance(value, str) or not re.fullmatch(r'Q[1-9][0-9]*', value):
        raise ValueError('invalid Wikidata QID')
    return value


def _entry(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:ln[sw]:wd:Q[1-9][0-9]*|ln[se]:ndl:[A-Za-z0-9._-]+|lng:pub:(?:futaba|orange|ecobalt|sbcr|hj|arian|hjnovels|sbcrindex):[0-9a-f]{32}|lnr:pub:(?:ecobalt|futaba|kadokawa|kadokawaprint|kadokawatrade|tobooks|gagaga|kodansha|overlapdetails|overlapbunkodetails|shueishadx|gc|overlap|alphapolis|hifumi|ichijinshairis|ichijinshairisspecial|earthstar|dre):[0-9a-f]{32})', value):
        raise ValueError('a typed light-novel entry ID is required')
    return value


def _items(entity, prop):
    result = []
    for st in entity.get('claims', {}).get(prop, []):
        snak = st.get('mainsnak', {})
        value = snak.get('datavalue', {}).get('value')
        if st.get('rank') != 'deprecated' and snak.get('snaktype') == 'value' and isinstance(value, dict) and re.fullmatch(r'Q[1-9][0-9]*', value.get('id', '')):
            result.append(value['id'])
    return list(dict.fromkeys(result))


def _safe(value):
    """Public evidence allowlist: internal artifact paths never cross the API."""
    allowed = {'source', 'source_id', 'source_url', 'url', 'title', 'label', 'qid', 'license', 'license_id', 'license_url', 'kind',
               'attribution', 'credit', 'revision', 'entity_revision', 'scope', 'evidence_id',
               'locator', 'quote', 'summary', 'directness', 'display_allowed', 'creator',
               'rights_policy_id', 'retrieved_at', 'modified_at', 'provider', 'record_id'}
    result = {k: v for k, v in value.items() if k in allowed and isinstance(v, (str, int, bool, type(None)))}
    if isinstance(value.get('attribution'), dict):
        keys = {'revision_id', 'page_id', 'title', 'article_title', 'source_url', 'history_url', 'permalink',
                'license_url', 'license', 'changes', 'credit', 'provider', 'revision_url', 'authors_url'}
        result['attribution'] = {k: v for k, v in value['attribution'].items()
                                 if k in keys and isinstance(v, (str, int, bool, type(None)))}
    return result


def _content_source(source, *, work_id=None, archive_context=None, fresh_classification=None):
    """Publish permissions only for the source contract the verifier replays."""
    result = _safe(source)
    attribution = source.get('attribution', {})
    attribution = attribution if isinstance(attribution, dict) else {}
    allowed = (source.get('rights_policy_id') == 'light-novel-series-wikipedia-screened-v1'
               and source.get('license') == 'CC-BY-SA-4.0')
    if work_id is not None:
        allowed = False
        match = re.fullmatch(r'lnw:wd:(Q[1-9][0-9]*)', work_id) if isinstance(work_id, str) else None
        language, revision = source.get('language'), attribution.get('revision_id')
        page_id = attribution.get('page_id')
        obligations = {'attribution', 'mark_changes', 'share_alike', 'third_party_text_screened'}
        restrictions = source.get('inherited_restrictions')
        fulfilled = attribution.get('fulfilled_obligations')
        profile = (match is not None and source.get('kind') == 'synopsis'
            and source.get('license') == 'CC-BY-SA-4.0'
            and source.get('license_url') == 'https://creativecommons.org/licenses/by-sa/4.0/'
            and language in ('en', 'ja') and type(revision) is int and revision > 0
            and type(page_id) is int and page_id > 0
            and isinstance(restrictions, (list, tuple)) and all(isinstance(v, str) for v in restrictions)
            and obligations <= set(restrictions)
            and isinstance(fulfilled, (list, tuple)) and all(isinstance(v, str) for v in fulfilled)
            and obligations <= set(fulfilled))
        if profile:
            permalink = f'https://{language}.wikipedia.org/w/index.php?oldid={revision}'
            history = attribution.get('history_url')
            profile = (source.get('source_url') == permalink and attribution.get('permalink') == permalink
                and isinstance(history, str)
                and history.startswith(f'https://{language}.wikipedia.org/w/index.php?title=')
                and history.endswith('&action=history'))
        if profile and source.get('rights_policy_id') == 'light-novel-work-wikipedia-screened-v1':
            allowed = (archive_context is None and fresh_classification is not False
                and attribution.get('scope') == 'work' and attribution.get('work_id') == work_id)
        elif profile and source.get('rights_policy_id') == 'wikipedia-screened-prose':
            proof = attribution.get('identity_proof')
            allowed = (archive_context == 'historical_mixed_metadata_lead_and_plot'
                and fresh_classification is False and isinstance(proof, dict)
                and proof.get('wikidata_work_id') == match[1]
                and proof.get('pageprops_wikibase_item') == match[1]
                and proof.get('language') == language and proof.get('page_id') == page_id
                and proof.get('revision_id') == revision)
    result.update(display_allowed=allowed, quote_allowed=False,
                  evidence_ids=[source['evidence_id']] if isinstance(source.get('evidence_id'), str) else [],
                  revision=attribution.get('revision_id'), permalink=attribution.get('permalink'),
                  history_url=attribution.get('history_url'), changed_from_source=attribution.get('changes'))
    if allowed:
        result.update(output_license='CC-BY-SA-4.0', output_license_url='https://creativecommons.org/licenses/by-sa/4.0/')
    return result


class LightNovelCatalogue:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS entries(
                    entry_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS read_status(
                    entry_id TEXT PRIMARY KEY REFERENCES entries(entry_id),
                    status TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS features(
                    entry_id TEXT PRIMARY KEY REFERENCES entries(entry_id),
                    manifest_path TEXT NOT NULL, manifest_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ingestion_failures(
                    source_id TEXT, reason TEXT, recorded_at TEXT);
                CREATE TABLE IF NOT EXISTS edition_series_memberships(
                    edition_id TEXT PRIMARY KEY REFERENCES entries(entry_id),
                    series_id TEXT NOT NULL REFERENCES entries(entry_id),
                    proof_json TEXT NOT NULL,proof_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS edition_book_confirmations(
                    edition_id TEXT NOT NULL REFERENCES entries(entry_id),provider TEXT NOT NULL,
                    proof_json TEXT NOT NULL,proof_sha256 TEXT NOT NULL,
                    PRIMARY KEY(edition_id,provider));
                CREATE TABLE IF NOT EXISTS publisher_bibliographic_correspondences(
                    edition_id TEXT NOT NULL REFERENCES entries(entry_id),provider TEXT NOT NULL,
                    proof_json TEXT NOT NULL,proof_sha256 TEXT NOT NULL,
                    PRIMARY KEY(edition_id,provider));
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _artifact(self, path, expected, cache):
        key = (str(_resolved_path(path, cache)), expected)
        if key not in cache:
            raw = Path(path).read_bytes()
            if not isinstance(expected, str) or hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('source artifact SHA mismatch')
            cache[key] = raw
        return cache[key]

    def _payload(self, path, expected, cache):
        key = ('json', str(_resolved_path(path, cache)), expected)
        if key not in cache:
            cache[key] = json.loads(self._artifact(path, expected, cache))
        return cache[key]

    def _conditions(self, provenance, source, cache, *, provider=None):
        document = self._payload(provenance['conditions_path'], provenance['conditions_sha256'], cache)
        national = source == 'ndl' and provider == 'iss-ndl-opac-national'
        expected_version = 'light-novel-source-conditions-bulk-v2' if national else 'light-novel-source-conditions-v1'
        if document.get('version') != expected_version:
            raise ValueError('unsupported source conditions')
        policy = document.get(source, {})
        if not all(policy.get('usages', {}).get(u) is True for u in ('local_storage', 'metadata_display', 'metadata_recommendation')):
            raise ValueError('metadata usage held')
        wanted = {'https://www.wikidata.org/wiki/Wikidata:Licensing'} if source == 'wikidata' else {'https://www.ndl.go.jp/data/data_service/quickguide', 'https://ndlsearch.ndl.go.jp/help/api'}
        if national:
            wanted |= {'https://ndlsearch.ndl.go.jp/bib/help', 'https://www.ndl.go.jp/data/data_service/jnb'}
        snapshots = {s['url']: s for s in document.get('snapshots', [])}
        if not wanted <= snapshots.keys():
            raise ValueError('source conditions snapshots absent')
        for url in wanted:
            proof = snapshots[url]
            self._artifact(proof['raw_path'], proof['raw_sha256'], cache)
        if source == 'wikidata' and (policy.get('license') != 'CC0' or policy.get('prose_excluded') is not True):
            raise ValueError('structured Wikidata metadata policy differs')
        if source == 'ndl' and policy.get('provider') != (provider or 'iss-ndl-opac'):
            raise ValueError('NDL-created metadata policy absent')
        if national and (policy.get('source_provider_evidence_url') != 'https://ndlsearch.ndl.go.jp/bib/help'
                         or policy.get('usages', {}).get('llm_input') is not False):
            raise ValueError('national bibliography scope/prose boundary absent')
        return policy

    def _project(self, record, cache):
        if record.get('schema_version') == ECOBALT_REFERENCE_SCHEMA:
            return self._project_ecobalt_publisher_reference(record, cache)
        if record.get('schema_version') == FUTABA_REFERENCE_SCHEMA:
            return self._project_publisher_reference('futaba', record, cache)
        if record.get('schema_version') == KADOKAWA_REFERENCE_SCHEMA:
            return self._project_publisher_reference('kadokawa', record, cache)
        if record.get('schema_version') == KADOKAWA_PRINT_REFERENCE_SCHEMA:
            return self._project_publisher_reference('kadokawaprint', record, cache)
        if record.get('schema_version') == KADOKAWA_TRADE_REFERENCE_SCHEMA:
            return self._project_publisher_reference('kadokawatrade', record, cache)
        if record.get('schema_version') == TOBOOKS_REFERENCE_SCHEMA:
            return self._project_publisher_reference('tobooks', record, cache)
        for provider in ('gagaga', 'kodansha', 'overlapdetails', 'overlapbunkodetails', 'shueishadx', 'gc', 'overlap', 'alphapolis', 'hifumi', 'ichijinshairis', 'ichijinshairisspecial', 'earthstar', 'dre'):
            if record.get('schema_version') == PUBLISHER_REFERENCE_PROVIDERS[provider]['schema']:
                return self._project_publisher_reference(provider, record, cache)
        for provider, profile in SOURCE_GROUP_PROVIDERS.items():
            if record.get('schema_version') == profile['schema']:
                return self._project_publisher_source_group(provider, record, cache)
        if record.get('schema_version') == 'light-novel-ndl-series-v1':
            return self._project_ndl_series(record, cache)
        if record.get('schema_version') in {'light-novel-edition-v1', 'light-novel-edition-bulk-v1'}:
            return self._project_edition(record, cache)
        if record.get('schema_version') not in {'light-novel-metadata-v1', 'light-novel-metadata-v2', 'light-novel-metadata-v3', 'light-novel-work-metadata-v3'} or record.get('source') != 'wikidata':
            raise ValueError('unsupported metadata contract')
        if record.get('admission', {}).get('accepted') is not True or record['admission'].get('reasons'):
            raise ValueError('metadata admission held')
        qid = _qid(record['id'])
        proof = record['provenance']
        self._conditions(proof, 'wikidata', cache)
        payload = self._payload(proof['raw_path'], proof['raw_sha256'], cache)
        entity = payload.get('entities', {}).get(qid, {})
        if entity.get('id') != qid or 'missing' in entity or entity.get('type', 'item') != 'item':
            raise ValueError('raw identity differs')
        if proof.get('url') != f'https://www.wikidata.org/wiki/{qid}' or proof.get('entity_revision') != entity.get('lastrevid'):
            raise ValueError('source URL/revision differs')
        types = set(_items(entity, 'P31') + _items(entity, 'P7937'))
        if record.get('schema_version') == 'light-novel-metadata-v3':
            from .light_novel_admission_v3 import verify_metadata
            verify_metadata(record)
            kind = 'series'
        elif record.get('schema_version') == 'light-novel-work-metadata-v3':
            from .light_novel_work_admission_v3 import verify_metadata
            verify_metadata(record)
            kind = 'work'
        else:
            kind = 'series' if 'Q104213567' in types else 'work' if 'Q747381' in types else None
        if kind is None or record.get('kind') != kind or types & CONFLICT_TYPES or _items(entity, 'P629'):
            raise ValueError('light novel scope absent or conflicting')
        languages = _items(entity, 'P407')
        identity_basis = 'explicit_original_language'
        if record.get('original_languages') != languages:
            raise ValueError('Japanese original language unproved')
        if set(languages) != {'Q5287'}:
            if languages or record.get('schema_version') != 'light-novel-metadata-v2':
                raise ValueError('Japanese original language unproved')
            resolution = record.get('original_language_resolution', {})
            if resolution.get('method') != 'wikidata_description_explicit_japanese_literary_original_v1':
                raise ValueError('Japanese literary identity evidence absent')
            resolved = self._payload(resolution['raw_path'], resolution['raw_sha256'], cache).get('entities', {}).get(qid, {})
            text = resolved.get('descriptions', {}).get('en', {}).get('value', '')
            forms = r'(?:light novel series|novel series)' if kind == 'series' else r'light novel'
            if (resolved.get('id') != qid or resolution.get('source_url') != proof['url']
                    or resolution.get('entity_revision') != resolved.get('lastrevid')
                    or resolution.get('locator') not in {'descriptions.en', 'descriptions.en.value', f'entities.{qid}.descriptions.en.value'}
                    or resolution.get('text') != text
                    or not re.match(r'^(?:a\s+)?Japanese\s+' + forms + r'\b', text, re.IGNORECASE)):
                raise ValueError('Japanese literary description not reproduced by raw')
            identity_basis = 'explicit_wikidata_description'
        labels = entity.get('labels', {})
        titles = list(dict.fromkeys([v['value'] for v in labels.values()] +
                     [v['value'] for group in entity.get('aliases', {}).values() for v in group]))
        title = next((labels[x]['value'] for x in ('ja', 'en') if x in labels), qid)
        if record.get('title') != title or record.get('aliases', []) != titles:
            raise ValueError('metadata title/aliases not reproduced by raw')

        def named(prop, field):
            values = []
            for ref in _items(entity, prop):
                linked = payload.get('entities', {}).get(ref, {'id': ref})
                origin = proof.get('linked_provenance', {}).get(ref)
                if origin:
                    linked = self._payload(origin['raw_path'], origin['raw_sha256'], cache).get('entities', {}).get(ref, {})
                    if linked.get('id') != ref:
                        raise ValueError('linked authority identity differs')
                labels = linked.get('labels', {})
                name = next((labels[x]['value'] for x in ('ja', 'en') if x in labels), ref)
                values.append({'id': ref, 'label': name})
            if record.get(field, []) != values:
                raise ValueError('linked metadata not reproduced by raw')
            return [{'qid': v['id'], 'name': v['label']} if field == 'authors' else
                    {'qid': v['id'], 'label': v['label']} for v in values]

        authors = named('P50', 'authors')
        genres = named('P136', 'genres')
        subjects = named('P921', 'subjects')
        publishers = named('P123', 'publishers')
        dates = [s.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('time')
                 for s in entity.get('claims', {}).get('P577', []) if s.get('rank') != 'deprecated']
        dates = [d for d in dates if d]
        if record.get('publication_dates', []) != dates or record.get('sitelinks', {}) != entity.get('sitelinks', {}):
            raise ValueError('publication dates/sitelinks not reproduced')
        relations = {p: _items(entity, p) for p in RELATIONS}
        if any(record.get('relations', {}).get(p, []) != refs for p, refs in relations.items()):
            raise ValueError('relations not reproduced by raw')
        years = [int(m.group(1)) for d in dates if (m := re.match(r'^\+(\d+)-', d))]
        entry_id = ('lns:wd:' if kind == 'series' else 'lnw:wd:') + qid
        return {'entry_id': entry_id, 'entry_kind': kind, 'work_id': entry_id, 'qid': qid,
                'family': 'light_novel', 'title': title, 'aliases': [v for v in titles if v != title],
                'authors': authors, 'genres': genres, 'subjects': subjects, 'publishers': publishers,
                'original_language': 'ja' if languages else None,
                'original_language_status': 'explicit_source_assertion' if languages else 'unknown',
                'japanese_identity_basis': identity_basis, 'year': min(years) if years else None,
                'publication_dates': dates, 'sitelinks': entity.get('sitelinks', {}),
                'source_url': proof['url'], 'entity_revision': proof.get('entity_revision'),
                'relations': relations, 'read_status': 'unknown', 'recommendable': bool(genres),
                'llm_features': [], 'native_features': [], 'strict72_features': [],
                'content_sources': [], 'source_attributions': [{
                    'source': 'wikidata', 'source_url': proof['url'], 'license': 'CC0',
                    'scope': kind, 'entity_revision': proof.get('entity_revision')}],
                'classification_scope': kind, 'content_features_adopted': False,
                'recommendation_mode': 'source_similarity' if genres else 'insufficient_evidence'}

    def _project_edition(self, record, cache):
        """Reproduce NDL-created editions, leaving story/work relations unknown."""
        import xml.etree.ElementTree as ET
        from .ingestion.core import RawArtifact
        from .ingestion.ndl import NDLAdapter, SRU
        provider = record.get('provider')
        national = record.get('schema_version') == 'light-novel-edition-bulk-v1'
        if record.get('source') != 'ndl' or provider != ('iss-ndl-opac-national' if national else 'iss-ndl-opac'):
            raise ValueError('only NDL-created bibliographic editions are admitted')
        source_id = record['id']
        if not re.fullmatch(r'R100000002-[A-Za-z0-9._-]+', source_id):
            raise ValueError('NDL-created repository identity absent')
        if record.get('original_work_id') or record.get('series_entry_id'):
            raise ValueError('NDL edition to story relation requires separate source review')
        proof = record['provenance']
        self._conditions(proof, 'ndl', cache, provider=provider)
        source_record = record['source_record']
        raw = self._artifact(proof['raw_path'], proof['raw_sha256'], cache)
        key = ('ndl_records', proof['raw_path'], proof['raw_sha256'], source_record['retrieved_at'])
        node_key = ('ndl_nodes', proof['raw_path'], proof['raw_sha256'])
        receipt = None
        if national:
            from .light_novel_ndl_bulk_v1 import verify_bulk_receipt
            receipt_key = ('national_receipt', proof['receipt_path'], proof['receipt_sha256'])
            if receipt_key not in cache:
                cache[receipt_key] = verify_bulk_receipt(proof['receipt_path'], proof['receipt_sha256'], self._artifact, cache)
            receipt, documents = cache[receipt_key]
            if any(proof.get(k) != receipt.get(k) for k in ('raw_path', 'raw_sha256', 'conditions_path', 'conditions_sha256')):
                raise ValueError('bulk provenance differs from fixed receipt')
            if node_key not in cache:
                cache[node_key] = {row['source_id']: (row['node'], row['index']) for row in documents}
            if source_id not in cache[node_key] or proof.get('document_index') != cache[node_key][source_id][1]:
                raise ValueError('bulk own bibliography document index differs')
        if node_key not in cache:
            try:
                xml = ET.fromstring(raw)
            except ET.ParseError as exc:
                raise ValueError('NDL raw XML is malformed') from exc
            nodes = xml.findall(f'.//{{{SRU}}}recordData')
            if not nodes:
                nodes = [xml]
            indexed = {}
            for index, node in enumerate(nodes, 1):
                if len(node) == 0 and node.text:
                    try:
                        node = ET.fromstring(node.text)
                    except ET.ParseError as exc:
                        raise ValueError('NDL record XML is malformed') from exc
                identities = {v.split('#', 1)[0].rsplit('/', 1)[-1] for child in node.iter()
                              for v in child.attrib.values() if v.startswith('https://ndlsearch.ndl.go.jp/books/')}
                for identity in identities:
                    if identity in indexed:
                        raise ValueError('duplicate NDL identity within raw response')
                    indexed[identity] = (node, index)
            cache[node_key] = indexed
        if key not in cache:
            cache[key] = {}
        if source_id not in cache[key] and source_id in cache[node_key]:
            node, index = cache[node_key][source_id]
            adapter = NDLAdapter(None, approved_providers={provider})
            artifact = RawArtifact(proof['raw_sha256'], proof['raw_path'], len(raw),
                receipt['retrieved_at'] if national else source_record['retrieved_at'],
                receipt['source_url'] if national else 'https://ndlsearch.ndl.go.jp/api/sru')
            locator = f'bulk-docs[{index}]/rdf:RDF' if national else f'records[{index}]/recordData'
            item = adapter._normalize(node, artifact, provider, locator=locator)
            cache[key][source_id] = item.model_dump(mode='json')
        actual = cache[key].get(source_id)
        if not actual or actual != source_record:
            raise ValueError('NDL source record not reproduced by raw')
        wanted_url = f'https://ndlsearch.ndl.go.jp/books/{source_id}'
        if proof.get('url') != wanted_url or record.get('source_url') != wanted_url or actual['source_url'] != wanted_url:
            raise ValueError('NDL record URL identity differs')
        fields = {'title': actual['titles'][0]['value'] if actual['titles'] else '',
                  'volume': actual['volume'], 'authors': [c['name'] for c in actual['contributors'] if c['role'] == 'author'],
                  'publisher': actual['publisher'], 'date': actual['publication_year'],
                  'isbn': [i['value'] for i in actual['identifiers'] if i['source'] == 'isbn'],
                  'languages': [actual['language']] if actual['language'] else [], 'label': actual['series'],
                  'ndc': actual['extra'].get('ndc_codes', [])}
        if any(record.get(k) != v for k, v in fields.items()):
            raise ValueError('NDL edition fields differ from raw')
        if not fields['title']:
            raise ValueError('NDL edition title absent')
        if record.get('admission', {}).get('light_novel_confirmed') is not False:
            raise ValueError('label alone cannot confirm a light novel')
        label_candidate = any(actual['series'] and e.get('query_term', '') and e['query_term'] in actual['series']
                              for e in record.get('label_evidence', []))
        if record.get('label_candidate') != label_candidate:
            raise ValueError('label candidate not reproduced by returned series field')
        entry_id = 'lne:ndl:' + source_id
        language = {'jpn': 'ja', 'eng': 'en'}.get(actual['language'], actual['language'])
        return {'entry_id': entry_id, 'entry_kind': 'edition', 'work_id': entry_id, 'qid': None,
                'family': 'light_novel', 'title': fields['title'], 'aliases': actual['extra'].get('title_readings', []),
                'authors': [{'qid': None, 'name': name} for name in fields['authors']], 'genres': [], 'subjects': [],
                'publishers': [{'qid': None, 'label': fields['publisher']}] if fields['publisher'] else [],
                'original_language': None, 'language': language, 'year': fields['date'], 'volume': fields['volume'],
                'isbn': fields['isbn'], 'publication_label': fields['label'], 'label_candidate': label_candidate,
                'light_novel_confirmed': False, 'admission_status': 'bibliographic_candidate_only',
                'relation_resolution': 'original_work_and_story_series_unresolved',
                'source_url': wanted_url, 'relations': {p: [] for p in RELATIONS}, 'read_status': 'unknown',
                'recommendable': False, 'llm_features': [], 'native_features': [], 'strict72_features': [],
                'content_sources': [], 'source_attributions': [{'source': 'ndl', 'provider': provider,
                    'source_url': wanted_url, 'scope': 'edition', 'credit': 'NDLサーチAPIの国立国会図書館書誌データを加工して作成',
                    'license_url': 'https://www.ndl.go.jp/data/data_service/quickguide'}],
                'classification_scope': 'edition', 'content_features_adopted': False,
                'recommendation_mode': 'insufficient_evidence'}

    def ingest(self, records, *, _cache=None, _projected=None):
        result = {'inserted': 0, 'updated': 0, 'unchanged': 0, 'failed': 0, 'failures': []}
        cache = {} if _cache is None else _cache
        with self._connect() as db:
            for record in records:
                try:
                    row = self._project(record, cache)
                    if row['entry_kind'] in {'publisher_group', 'publisher_reference'}:
                        raise ValueError('publisher source references require their dedicated importer')
                    encoded = _json(record)
                    digest = hashlib.sha256(encoded.encode()).hexdigest()
                    if record.get('schema_version') in {'light-novel-metadata-v3', 'light-novel-work-metadata-v3'} and not db.in_transaction:
                        db.execute('BEGIN IMMEDIATE')
                    previous = db.execute('SELECT record_sha256 FROM entries WHERE entry_id=?', (row['entry_id'],)).fetchone()
                    if record.get('schema_version') in {'light-novel-metadata-v3', 'light-novel-work-metadata-v3'} and previous is None:
                        if record.get('schema_version') == 'light-novel-work-metadata-v3':
                            from .light_novel_work_admission_v3 import check_duplicate_titles
                        else:
                            from .light_novel_admission_v3 import check_duplicate_titles
                        occupied = []
                        for identity, kind, saved, saved_sha in db.execute(
                                "SELECT entry_id,kind,record_json,record_sha256 FROM entries WHERE kind IN ('series','work','publisher_group') OR entry_id=?",
                                ('lnw:wd:' + record['id'],)):
                            if hashlib.sha256(saved.encode()).hexdigest() != saved_sha:
                                raise ValueError('v3 duplicate-title witness metadata SHA differs')
                            witness = json.loads(saved)
                            occupied.append({'entry_id': identity, 'entry_kind': kind,
                                'record': witness})
                        check_duplicate_titles(record, occupied)
                    if _projected is not None:
                        _projected[row['entry_id']] = row
                    if previous and previous['record_sha256'] == digest:
                        result['unchanged'] += 1
                        continue
                    db.execute('''INSERT INTO entries VALUES(?,?,?,?) ON CONFLICT(entry_id) DO UPDATE SET
                        kind=excluded.kind,record_json=excluded.record_json,record_sha256=excluded.record_sha256''',
                        (row['entry_id'], row['entry_kind'], encoded, digest))
                    result['updated' if previous else 'inserted'] += 1
                except (KeyError, ValueError, OSError, TypeError) as exc:
                    # Local diagnostics are explicit but never exported in API rows.
                    reason = str(exc)
                    result['failed'] += 1
                    result['failures'].append({'source_id': record.get('id'), 'reason': reason})
                    db.execute('INSERT INTO ingestion_failures VALUES(?,?,?)', (record.get('id'), reason, datetime.now(timezone.utc).isoformat()))
        return result

    def import_metadata(self, metadata_path):
        with Path(metadata_path).open(encoding='utf-8') as handle:
            return self.ingest(json.loads(line) for line in handle if line.strip())

    def import_editions(self, records):
        return self.ingest(records)

    def import_ndl_bulk(self, receipt_path, expected_sha256):
        """Add new own-NDL edition identities, preserving existing SRU bindings."""
        from .light_novel_ndl_bulk_v1 import verify_bulk_receipt, bulk_metadata_records
        cache = {}
        receipt, documents = verify_bulk_receipt(receipt_path, expected_sha256, self._artifact, cache)
        self._conditions(receipt, 'ndl', cache, provider='iss-ndl-opac-national')
        records = bulk_metadata_records(receipt_path, expected_sha256, receipt, documents)
        with self._connect() as db:
            existing = {row[0] for row in db.execute('SELECT entry_id FROM entries')}
        new = [record for record in records if 'lne:ndl:' + record['id'] not in existing]
        result = self.ingest(new)
        result.update(downloaded_own_editions=len(records), existing_identities_preserved=len(records) - len(new))
        return result

    def _project_ndl_series(self, record, cache):
        if record.get('publisher_series_proof', {}).get('kind') == 'futaba_mnovels_workgroup':
            return self._project_futaba_workgroup(record, cache)
        from .light_novel_ndl_series_v1 import publisher_assertions, match_bibliography
        if record.get('source') != 'ndl':
            raise ValueError('NDL series source differs')
        anchor = record['anchor_edition_record']
        base = self._project_edition(anchor, cache)
        if record['id'] != anchor['id']:
            raise ValueError('NDL series anchor identity differs')
        proof = record['publisher_series_proof']
        assertion = publisher_assertions(proof, self._artifact, cache).get(proof['reference_id'])
        if assertion is None:
            raise ValueError('official novel series assertion absent')
        match = self._publisher_series_match(assertion, anchor, cache, allow_unnumbered=True)
        if match is None:
            raise ValueError('NDL anchor does not reproduce publisher series identity')
        cobalt = assertion.get('index_id') == 'cobalt-explicit-series'
        earthstar = assertion.get('index_id') == 'earthstar-explicit-work-group'
        dre_dual = assertion.get('index_id') == 'dre-novels-series-v2'
        explicit = cobalt or earthstar or dre_dual
        if explicit and record.get('title') != assertion['title']:
            raise ValueError('explicit series search title differs from official source')
        entry_id = 'lns:ndl:' + anchor['id']
        return {**base, 'entry_id': entry_id, 'work_id': entry_id, 'entry_kind': 'series',
                'title': match['title'], 'aliases': [] if explicit else anchor['source_record']['extra'].get('title_readings', []),
                'title_origin': match['title_origin'],
                'authors': [{'qid': None, 'name': a['name'], 'ndl_authorities': a['authority_ids']} for a in match['authors']],
                'original_language': None, 'language': None, 'original_language_status': 'unknown',
                'year': None, 'anchor_edition_year': base['year'],
                'volume': None, 'isbn': [], 'classification_scope': 'series',
                'light_novel_confirmed': True, 'label_candidate': False,
                'admission_status': 'ndl_series_identity_verified', 'relation_resolution': 'series_unit_and_anchor_verified',
                'series_identity_basis': 'publisher_series_and_ndl_bibliography',
                'series_assertion_source_url': assertion['source_url'],
                'publisher_series_reference_id': assertion['reference_id'],
                'source_attributions': [{**s, 'scope': 'publisher_work_group_member_bibliographic_anchor' if earthstar else
                                        'series_member_bibliographic_anchor' if explicit else 'series_bibliographic_metadata'}
                                        for s in base['source_attributions']],
                **({'series_title_source': 'publisher_explicit_work_group' if earthstar else 'publisher_explicit_group',
                    'publication_label': None, 'publishers': [],
                    'content_sources': [
                        {'source_url': assertion['source_url'], 'attribution': '作品名・著者・収録参照: アース・スター公式資料' if earthstar else 'シリーズ名・著者・収録参照: DREノベルス公式資料' if dre_dual else 'シリーズ名・著者・収録参照: 集英社公式資料'},
                        {'source_url': base['source_url'], 'attribution': base['source_attributions'][0]['credit'],
                         'license_url': base['source_attributions'][0]['license_url']}]}
                   if explicit else {}),
                **({'series_identity_basis': 'publisher_work_group_and_ndl_bibliography',
                    'admission_status': 'publisher_work_group_verified',
                    'relation_resolution': 'publisher_work_group_and_anchor_verified',
                    'story_series_identity_asserted': False, 'original_work_identity_asserted': False}
                   if earthstar else {})}

    def _futaba_workgroups(self, path, sha256, cache):
        return self._publisher_source_groups('futaba', path, sha256, cache)

    def _orange_workgroups(self, path, sha256, cache):
        return self._publisher_source_groups('orange', path, sha256, cache)

    def _ecobalt_workgroups(self, path, sha256, cache):
        return self._publisher_source_groups('ecobalt', path, sha256, cache)

    def _sbcr_workgroups(self, path, sha256, cache):
        return self._publisher_source_groups('sbcr', path, sha256, cache)

    def _hj_index_workgroups(self, path, sha256, cache):
        return self._publisher_source_groups('hj', path, sha256, cache)

    def _arian_index_workgroups(self, path, sha256, cache):
        return self._publisher_source_groups('arian', path, sha256, cache)

    def _hjnovels_index_workgroups(self, path, sha256, cache):
        return self._publisher_source_groups('hjnovels', path, sha256, cache)

    def _sbcr_index_workgroups(self, path, sha256, cache):
        return self._publisher_source_groups('sbcrindex', path, sha256, cache)

    def _ecobalt_source_frame(self, path, sha256, cache):
        from .light_novel_ecobalt_groups_v1 import verify_ecobalt_frame
        key = ('ecobalt_source_frame', str(_resolved_path(path, cache)), sha256)
        if key not in cache:
            cache[key] = verify_ecobalt_frame(path, sha256)
        return cache[key]

    def _publisher_source_groups(self, provider, path, sha256, cache):
        profile = SOURCE_GROUP_PROVIDERS[provider]
        key = ('publisher_source_groups', provider, str(_resolved_path(path, cache)), sha256)
        if key not in cache:
            groups = {}
            verifier = getattr(importlib.import_module('.' + profile['module'], __package__), profile['verifier'])
            source_groups = self._ecobalt_source_frame(path, sha256, cache)['groups'] if provider == 'ecobalt' else verifier(path, sha256)
            for group in source_groups:
                reference = group['reference_id']
                if (not re.fullmatch(profile['reference_pattern'], reference)
                        or reference in groups or group['source_unit'] != 'publisher_work_group'
                        or group['kind'] != profile['source_kind'] or not group['title']
                        or (provider == 'futaba' and group['title'] != group['grouping_title'])
                        or any(group.get(field) is not None for field in
                               ('authors', 'publisher', 'label', 'form', 'isbn', 'official_lightnovel_tag'))
                        or group['frame_member_count'] != len(group['members'])
                        or group[profile['complete_field']] is not True
                        or group['global_membership_complete'] is not None
                        or group['story_series_identity_asserted'] is not False
                        or group['original_work_identity_asserted'] is not False):
                    raise ValueError('publisher source workgroup contract differs')
                ids = [member['publisher_reference_id'] for member in group['members']]
                if profile.get('index_only'):
                    if (group['members'] != [] or group['frame_membership_complete'] is not None
                            or group['total_product_count'] is not None or group['index_frame_pages_complete'] is not True):
                        raise ValueError('publisher native index card contract differs')
                elif not ids or len(ids) != len(set(ids)) or any(not re.fullmatch(profile['member_pattern'], identity) for identity in ids):
                    raise ValueError('publisher source workgroup member identity differs')
                groups[reference] = group
            cache[key] = groups
        return cache[key]

    @staticmethod
    def _futaba_source_key(assertion):
        return LightNovelCatalogue._publisher_source_key(assertion)

    @staticmethod
    def _publisher_source_key(assertion):
        return {key: value for key, value in assertion['local_reference_key'].items()
                if key != 'source_snapshot_sha256'}

    @staticmethod
    def _futaba_source_assertion(path, sha256, group):
        return LightNovelCatalogue._publisher_source_assertion('futaba', path, sha256, group)

    @staticmethod
    def _publisher_source_assertion(provider, path, sha256, group):
        local_key = group['local_reference_key']
        if provider == 'orange':
            # The source's taxonomy URL key is stable across renames; local
            # UUID continuity additionally requires the complete literal title.
            local_key = {**local_key, 'literal': group['title']}
        assertion = {'manifest_path': path, 'manifest_sha256': sha256,
                'reference_id': group['reference_id'], 'local_reference_key': local_key,
                'member_reference_ids': sorted(m['publisher_reference_id'] for m in group['members'])}
        if SOURCE_GROUP_PROVIDERS[provider].get('index_only'):
            assertion['title'] = group['title']
        return assertion

    @classmethod
    def _futaba_source_continuity(cls, previous, following):
        return cls._publisher_source_continuity(previous, following)

    @classmethod
    def _publisher_source_continuity(cls, previous, following, *, provider=None):
        return (cls._publisher_source_key(previous) == cls._publisher_source_key(following)
                and ((SOURCE_GROUP_PROVIDERS.get(provider, {}).get('index_only') and previous['member_reference_ids'] == following['member_reference_ids'] == []
                      and previous['title'] == following['title'])
                     or bool(set(previous['member_reference_ids']) & set(following['member_reference_ids']))))

    @staticmethod
    def _futaba_product_references(group):
        return LightNovelCatalogue._publisher_product_references('futaba', group)

    @staticmethod
    def _publisher_product_references(provider, group):
        roles = {'author', 'illustrator', 'original_author', 'original_concept', 'supervisor', 'editor'}
        if provider == 'orange':
            roles |= {'cover_illustrator', 'commentary_author', 'screenplay_author', 'unknown'}
        elif provider == 'ecobalt':
            roles.add('unknown')
        rows = [{'publisher_reference_id': member['publisher_reference_id'], 'title': member['title'],
                 'source_url': member['source_url'], 'label': member['label'], 'form': member['form'],
                 'credits': [{'role': c['role'] if c['role'] in roles else None, 'name': c['name']}
                             for c in member['credits']],
                 'media_references': [{key: media[key] for key in
                     ('media_type_source_literal', 'media_id_source_literal', 'source_url')}
                     for media in member['media_references'] or []]} for member in group['members']]
        if provider == 'orange':
            for row, member in zip(rows, group['members'], strict=True):
                row['credit_literals'] = [{'role_literal': c['role_literal'], 'name': c['name']}
                                          for c in member['credit_literals']]
        elif provider == 'ecobalt':
            for row, member in zip(rows, group['members'], strict=True):
                row['author_display'] = member['author_display']
                row['credit_literals'] = [{key: c[key] for key in
                    ('name', 'entry_role_literal', 'role_context_literal', 'role_scope')}
                    for c in member['credit_literals']]
        return rows

    @staticmethod
    def _publisher_source_display(provider, group):
        if SOURCE_GROUP_PROVIDERS[provider].get('index_only'):
            display = {'robots_evidence': dict(group['robots_evidence']), 'usage_permissions': dict(group['usage_permissions']),
                'native_index_card_complete': group['native_index_card_complete'],
                'index_frame_pages_complete': group['index_frame_pages_complete'],
                'frame_membership_complete': group['frame_membership_complete'], 'total_product_count': group['total_product_count'],
                'index_credit_literals': [{key: credit[key] for key in ('role_literal', 'name', 'role_scope')}
                    for credit in group['index_credit_literals']]}
            if provider == 'sbcrindex':
                display['index_name_cautions'] = list(group['index_name_cautions'])
            return display
        if provider == 'sbcr':
            return {'robots_evidence': dict(group['robots_evidence']),
                    'usage_permissions': dict(group['usage_permissions'])}
        if provider != 'ecobalt':
            return {}
        return {'heading_author_display': group['heading_author_display'],
                'role_context': {key: group['role_context'][key] for key in ('literal', 'scope')}}

    def _project_futaba_source_group(self, record, cache):
        return self._project_publisher_source_group('futaba', record, cache)

    def _project_publisher_source_group(self, provider, record, cache):
        profile = SOURCE_GROUP_PROVIDERS[provider]
        identity = _entry('lng:pub:' + provider + ':' + record['id'])
        if (record.get('schema_version') != profile['schema']
                or record.get('source') != 'publisher' or record.get('provider') != profile['record_provider']
                or record.get('kind') != 'publisher_group'):
            raise ValueError('publisher source group typed scope differs')
        assertions = record['source_group_assertions']
        if not isinstance(assertions, list) or not assertions:
            raise ValueError('publisher source group has no versioned assertion')
        seen = set()
        for index, assertion in enumerate(assertions):
            key = (assertion['manifest_path'], assertion['manifest_sha256'], assertion['reference_id'])
            members = assertion['member_reference_ids']
            if (key in seen or not re.fullmatch(r'[0-9a-f]{64}', assertion['manifest_sha256'])
                    or not re.fullmatch(profile['reference_pattern'], assertion['reference_id'])
                    or (members != [] if profile.get('index_only') else not members) or members != sorted(set(members))
                    or any(not re.fullmatch(profile['member_pattern'], member) for member in members)
                    or index and not self._publisher_source_continuity(assertions[index - 1], assertion, provider=provider)):
                raise ValueError('publisher source group assertion continuity differs')
            seen.add(key)
        active = assertions[-1]
        group = getattr(self, profile['loader'])(active['manifest_path'], active['manifest_sha256'], cache).get(active['reference_id'])
        if (group is None or active != self._publisher_source_assertion(provider, active['manifest_path'], active['manifest_sha256'], group)
                or record['title'] != group['title']
                or any(record.get(key) != value for key, value in self._publisher_source_display(provider, group).items())
                or record['publisher_product_references'] != self._publisher_product_references(provider, group)):
            raise ValueError('publisher source group fields do not replay')
        return {**self._publisher_source_display(provider, group),
            'entry_id': identity, 'work_id': identity, 'entry_kind': 'publisher_group', 'qid': None,
            'family': 'light_novel', 'title': group['title'], 'aliases': [], 'authors': [], 'publishers': [],
            'genres': [], 'subjects': [], 'original_language': None, 'language': None, 'year': None,
            'volume': None, 'isbn': [], 'publication_label': None, 'read_status': 'unknown',
            'light_novel_confirmed': False, 'label_candidate': False, 'classification_scope': 'publisher_group',
            'admission_status': 'publisher_source_group_verified', 'relation_resolution': 'publisher_source_group_verified',
            'series_identity_basis': 'publisher_source_group', 'series_title_source': 'publisher_explicit_work_group',
            'publisher_work_group_kind': profile['public_kind'], 'series_assertion_source_url': group['source_url'],
            'source_url': group['source_url'], 'publisher_series_reference_id': group['reference_id'],
            'publisher_product_references': self._publisher_product_references(provider, group),
            'source_frame_member_count': group['frame_member_count'], 'verified_bibliographic_member_count': 0,
            'frame_membership_complete': None if profile.get('index_only') else True,
            'exact_grouping_filter_complete': group.get('exact_grouping_filter_complete'),
            'global_membership_complete': None, 'story_series_identity_asserted': False,
            'original_work_identity_asserted': False, 'paper_electronic_same_edition_asserted': False,
            'source_attributions': [{'source': 'publisher', 'provider': profile['record_provider'],
                'source_url': group['source_url'], 'scope': 'publisher_work_group_source_frames',
                'credit': profile['credit']}],
            'content_sources': [], 'relations': {prop: [] for prop in RELATIONS}, 'recommendable': False,
            'llm_features': [], 'native_features': [], 'strict72_features': [],
            'content_features_adopted': False, 'recommendation_mode': 'insufficient_evidence'}

    def import_futaba_source_groups(self, manifest_path, expected_sha256, *, reference_ids=None):
        return self._import_publisher_source_groups('futaba', manifest_path, expected_sha256, reference_ids=reference_ids)

    def import_orange_source_groups(self, manifest_path, expected_sha256, *, reference_ids=None):
        return self._import_publisher_source_groups('orange', manifest_path, expected_sha256, reference_ids=reference_ids)

    def import_ecobalt_source_groups(self, manifest_path, expected_sha256, *, reference_ids=None):
        return self._import_publisher_source_groups('ecobalt', manifest_path, expected_sha256, reference_ids=reference_ids)

    def import_sbcr_source_groups(self, manifest_path, expected_sha256, *, reference_ids=None):
        return self._import_publisher_source_groups('sbcr', manifest_path, expected_sha256, reference_ids=reference_ids)

    def import_hj_index_source_groups(self, manifest_path, expected_sha256, *, reference_ids=None):
        return self._import_publisher_source_groups('hj', manifest_path, expected_sha256, reference_ids=reference_ids)

    def import_arian_index_source_groups(self, manifest_path, expected_sha256, *, reference_ids=None):
        return self._import_publisher_source_groups('arian', manifest_path, expected_sha256, reference_ids=reference_ids)

    def import_hjnovels_index_source_groups(self, manifest_path, expected_sha256, *, reference_ids=None):
        return self._import_publisher_source_groups('hjnovels', manifest_path, expected_sha256, reference_ids=reference_ids)

    def import_sbcr_index_source_groups(self, manifest_path, expected_sha256, *, reference_ids=None):
        return self._import_publisher_source_groups('sbcrindex', manifest_path, expected_sha256, reference_ids=reference_ids)

    def _ecobalt_publisher_references(self, path, sha256, cache):
        result = {}
        for reference in self._ecobalt_source_frame(path, sha256, cache)['ungrouped_references']:
            identity = reference['reference_id']
            if (not re.fullmatch(r'ecobalt-product-local:[0-9a-f]{64}', identity)
                    or identity in result or reference['kind'] != 'official_publisher_product_reference'
                    or reference['source_unit'] != 'publisher_product_reference'
                    or reference['membership_status'] != 'ungrouped'
                    or reference['actual_work_identity_asserted'] is not False
                    or reference['edition_identity_asserted'] is not False):
                raise ValueError('eCobalt product reference contract differs')
            result[identity] = reference
        return result

    @staticmethod
    def _ecobalt_reference_assertion(path, sha256, reference):
        return {'manifest_path': path, 'manifest_sha256': sha256,
                'reference_id': reference['reference_id'], 'local_reference_key': reference['local_reference_key']}

    @staticmethod
    def _publisher_reference_assertion(provider, path, sha256, reference):
        assertion = LightNovelCatalogue._ecobalt_reference_assertion(path, sha256, reference)
        if provider in {'futaba', 'kadokawa', 'kadokawaprint', 'kadokawatrade', 'tobooks', 'gagaga', 'kodansha', 'overlapdetails', 'overlapbunkodetails', 'shueishadx', 'gc', 'overlap', 'alphapolis', 'hifumi', 'ichijinshairis', 'ichijinshairisspecial', 'earthstar', 'dre'}:
            assertion['title'] = reference['title']
        return assertion

    @staticmethod
    def _ecobalt_reference_fields(reference):
        return {'title': reference['title'], 'author_display': reference['author_display'],
                'role_context': {key: reference['role_context'][key] for key in ('literal', 'scope')},
                'publisher_product_reference': LightNovelCatalogue._publisher_product_references(
                    'ecobalt', {'members': [reference]})[0]}

    def _futaba_publisher_references(self, path, sha256, cache):
        from .light_novel_futaba_references_v1 import verify_futaba_mnovels_ungrouped_references
        key = ('futaba_ungrouped_references', str(_resolved_path(path, cache)), sha256)
        if key not in cache:
            references = {}
            for reference in verify_futaba_mnovels_ungrouped_references(path, sha256):
                identity = reference['publisher_reference_id']
                if (not re.fullmatch(PUBLISHER_REFERENCE_PROVIDERS['futaba']['reference_pattern'], identity)
                        or identity in references or reference['source_unit'] != 'publisher_product_reference'
                        or reference['grouping_status'] != 'unknown_no_nonempty_publisher_grouping'
                        or reference['story_series_asserted'] is not False):
                    raise ValueError('Futaba ungrouped product reference contract differs')
                references[identity] = {**reference, 'reference_id': identity, 'local_reference_key': {
                    'provider': 'futaba-mnovels', 'source_scope': 'mnovels_ungrouped_product_reference',
                    'publisher_reference_id': identity, 'official_product_url': reference['source_url']}}
            cache[key] = references
        return cache[key]

    @staticmethod
    def _futaba_reference_fields(reference):
        product = {'publisher_reference_id': reference['publisher_reference_id'], 'title': reference['title'],
            'source_url': reference['source_url'], 'label': reference['label'], 'form': reference['form'],
            'credits': [{key: credit[key] for key in ('role', 'name')} for credit in reference['credits']],
            'credit_literals': [{'role_literal': credit['role'], 'name': credit['name']}
                for credit in reference['credit_literals']], 'media_references': []}
        return {'title': reference['title'], 'publisher_product_reference': product,
                'isbn': [reference['isbn']] if reference['isbn'] is not None else [],
                'publication_label': reference['label'], 'grouping_status': reference['grouping_status']}

    def _kadokawa_publisher_references(self, path, sha256, cache, *, reference_ids):
        """Authenticate only selected source IDs/pages, sharing an operation-local index."""
        from .light_novel_kadokawa_references_v1 import verify_kadokawa_references
        if reference_ids is None or isinstance(reference_ids, (str, bytes)):
            raise ValueError('an explicit Kadokawa product reference selection is required')
        requested = list(reference_ids)
        pattern = PUBLISHER_REFERENCE_PROVIDERS['kadokawa']['reference_pattern']
        if (not requested or len(requested) != len(set(requested))
                or any(not isinstance(value, str) or not re.fullmatch(pattern, value) for value in requested)):
            raise ValueError('unique explicit Kadokawa product reference IDs are required')
        key = ('kadokawa_product_references', str(_resolved_path(path, cache)), sha256)
        references = cache.setdefault(key, {})
        missing = [value for value in requested if value not in references]
        if missing:
            verified = {}
            for reference in verify_kadokawa_references(path, sha256, reference_ids=missing, cache=cache):
                identity = reference['publisher_reference_id']
                if (identity not in missing or identity in verified or not re.fullmatch(pattern, identity)
                        or reference['official_product_id'] != identity.split(':', 1)[1]
                        or reference['publisher_book_id'] != 'kadokawa:' + reference['official_product_id']
                        or reference['source_unit'] != 'publisher_product_reference'
                        or reference['reference_scope'] != 'private_bibliographic_discovery'
                        or reference['product_form_id'] != 13 or reference['form'] != '電子専売'
                        or reference['isbn'] is not None or reference['publisher'] is not None
                        or reference['official_lightnovel_tag'] is not None
                        or reference['story_series_asserted'] is not False):
                    raise ValueError('Kadokawa private product reference contract differs')
                permissions = reference['usage_permissions']
                if permissions != {'purpose': 'private_bibliographic_discovery',
                        'public_display': 'unknown', 'commercial_reuse': 'unknown',
                        'prose_ai_permission': False, 'images_permission': False,
                        'raw_redistribution_permission': False}:
                    raise ValueError('Kadokawa private discovery usage boundary differs')
                verified[identity] = {**reference, 'reference_id': identity, 'local_reference_key': {
                    'provider': 'kadokawa', 'source_scope': 'form13_isbn_null_product_reference',
                    'publisher_reference_id': identity, 'official_product_url': reference['source_url'],
                    'product_form_id': 13, 'label': reference['label']}}
            if set(verified) != set(missing):
                raise ValueError('selected Kadokawa product reference is absent')
            references.update(verified)
        return {identity: references[identity] for identity in requested}

    @staticmethod
    def _kadokawa_reference_fields(reference):
        return {'title': reference['title'], 'isbn': [], 'publication_label': reference['label'],
            'reference_scope': reference['reference_scope'],
            'usage_permissions': dict(reference['usage_permissions']),
            'publisher_product_reference': {
                'publisher_reference_id': reference['publisher_reference_id'],
                'title': reference['title'], 'source_url': reference['source_url'],
                'label': reference['label'], 'form': reference['form'],
                'credits': [{key: credit[key] for key in ('role', 'name')} for credit in reference['credits']],
                'credit_literals': [{key: credit[key] for key in ('role_literal', 'name')}
                    for credit in reference['credit_literals']], 'media_references': []}}

    def _kadokawa_print_publisher_references(self, path, sha256, cache, *, reference_ids):
        return self._kadokawa_paper_publisher_references('kadokawaprint', path, sha256, cache, reference_ids=reference_ids)

    def _kadokawa_trade_publisher_references(self, path, sha256, cache, *, reference_ids):
        return self._kadokawa_paper_publisher_references('kadokawatrade', path, sha256, cache, reference_ids=reference_ids)

    def _kadokawa_paper_publisher_references(self, provider, path, sha256, cache, *, reference_ids):
        """Share paper validation without changing either format's scoped identity."""
        from . import light_novel_kadokawa_print_references_v1 as source
        from .normalization import normalize_isbn
        profile = PUBLISHER_REFERENCE_PROVIDERS[provider]
        form_id, form_name = profile['paper_form_id'], profile['paper_form_name']
        if reference_ids is None or isinstance(reference_ids, (str, bytes)):
            raise ValueError('an explicit Kadokawa print reference selection is required')
        requested = list(reference_ids)
        pattern = profile['reference_pattern']
        if (not requested or len(requested) > 200 or any(not isinstance(value, str) for value in requested)
                or len(requested) != len(set(requested)) or any(not re.fullmatch(pattern, value) for value in requested)):
            raise ValueError('unique explicit Kadokawa print reference IDs are required, at most 200')
        key = (profile['paper_cache_tag'], str(_resolved_path(path, cache)), sha256)
        references = cache.setdefault(key, {})
        missing = [value for value in requested if value not in references]
        if missing:
            verified = {}
            verifier = getattr(source, profile['paper_verifier'])
            for reference in verifier(path, sha256, reference_ids=missing, cache=cache):
                identity = reference['publisher_reference_id']
                isbn = reference['isbn']
                normalized = normalize_isbn(isbn) if isinstance(isbn, str) else None
                local_key = {'provider': 'kadokawa', 'source_scope': profile['paper_source_scope'],
                    'publisher_reference_id': identity, 'official_product_url': reference['source_url'],
                    'product_form_id': form_id, 'label': reference['label']}
                if (identity not in missing or identity in verified or not re.fullmatch(pattern, identity)
                        or reference['official_product_id'] != identity.split(':', 1)[1]
                        or reference['publisher_book_id'] != 'kadokawa:' + reference['official_product_id']
                        or reference['source_url'] != 'https://www.kadokawa.co.jp/product/' + reference['official_product_id'] + '/'
                        or reference['source_unit'] != 'publisher_product_reference'
                        or reference['kind'] != 'official_publisher_product_reference'
                        or reference['reference_scope'] != 'private_bibliographic_discovery'
                        or reference['source_scope'] != local_key['source_scope']
                        or reference['local_reference_key'] != local_key
                        or type(reference['product_form_id']) is not int or reference['product_form_id'] != form_id
                        or reference['form'] != form_name or not normalized or reference['isbn_literal'] != isbn
                        or reference['normalized_isbn13_lookup_key'] != normalized
                        or reference['isbn_lookup_key_basis'] != 'explicit_source_literal_only_not_identity'
                        or reference['provider'] != 'kadokawa' or reference['catalogue_owner'] != 'KADOKAWA'
                        or reference['publisher_field_basis'] != 'catalogue_owner_context'
                        or reference['publisher'] is not None or reference['individual_publisher'] is not None
                        or reference['official_lightnovel_tag'] is not None
                        or any(reference[field] is not False for field in ('story_series_asserted',
                            'actual_work_identity_asserted', 'edition_identity_asserted', 'story_series_identity_asserted',
                            'original_work_identity_asserted', 'light_novel_confirmed', 'same_edition_identity_asserted',
                            'product_sku_identity_asserted', 'paper_electronic_same_edition_asserted',
                            'global_author_identity_asserted', 'global_author_alias_asserted',
                            'independent_volume_numbers_inferred'))):
                    raise ValueError('Kadokawa print private product reference contract differs')
                if reference['usage_permissions'] != {'purpose': 'private_bibliographic_discovery',
                        'public_display': 'unknown', 'commercial_reuse': 'unknown', 'prose_ai_permission': False,
                        'images_permission': False, 'raw_redistribution_permission': False}:
                    raise ValueError('Kadokawa print private discovery usage boundary differs')
                if any(reference['usage_permissions'][field] is not False for field in
                        ('prose_ai_permission', 'images_permission', 'raw_redistribution_permission')):
                    raise ValueError('Kadokawa print private discovery usage boundary differs')
                verified[identity] = {**reference, 'reference_id': identity, 'local_reference_key': local_key}
            if set(verified) != set(missing):
                raise ValueError('selected Kadokawa print product reference is absent')
            references.update(verified)
        return {identity: references[identity] for identity in requested}

    @staticmethod
    def _kadokawa_print_reference_fields(reference):
        return {**LightNovelCatalogue._kadokawa_reference_fields(reference), 'isbn': [reference['isbn_literal']]}

    def _tobooks_publisher_references(self, path, sha256, cache):
        """Reuse the whole finite TO verifier once per pinned frame/operation."""
        from .light_novel_publisher_references_v1 import verify_publisher_references
        from .normalization import normalize_isbn
        key = ('tobooks_product_references', str(_resolved_path(path, cache)), sha256)
        if key not in cache:
            references = {}
            for reference in verify_publisher_references('tobooks', path, sha256):
                identity, isbn, labels = reference['publisher_reference_id'], reference['isbn'], reference['label_literals']
                if (not isinstance(identity, str) or not re.fullmatch(PUBLISHER_REFERENCE_PROVIDERS['tobooks']['reference_pattern'], identity)
                        or identity in references or reference['source_unit'] != 'publisher_product_reference'
                        or reference['source_url'] != 'https://www.tobooks.jp/contents/' + identity.split(':')[1]
                        or not isinstance(reference['title'], str) or not reference['title'].strip()
                        or reference['publisher'] != 'TOブックス'
                        or isbn is not None and (not isinstance(isbn, str) or not re.fullmatch(r'97[89][0-9]{10}', isbn)
                                                 or normalize_isbn(isbn) != isbn)
                        or not isinstance(labels, list) or any(not isinstance(value, str) or not value for value in labels)
                        or reference['label'] != (labels[0] if len(labels) == 1 else None)
                        or reference['form'] is not None and (not isinstance(reference['form'], str) or not reference['form'])
                        or not isinstance(reference['credits'], list)
                        or any(credit.get('role') != 'unknown' or not isinstance(credit.get('name'), str)
                               or not credit['name'] for credit in reference['credits'])
                        or reference['story_series_asserted'] is not False or reference['metadata_conflict'] is not False
                        or reference['bibliographic_variants'] != []
                        or reference['official_lightnovel_tag'] is not None and type(reference['official_lightnovel_tag']) is not bool
                        or not isinstance(reference['source_evidence'], list) or not reference['source_evidence']
                        or any(not re.fullmatch(r'[0-9a-f]{64}', evidence['raw_sha256'])
                               or not isinstance(evidence['locator'], str) or not evidence['locator']
                               for evidence in reference['source_evidence'])):
                    raise ValueError('TO product reference contract differs')
                references[identity] = {**reference, 'reference_id': identity,
                    'reference_scope': 'private_bibliographic_discovery',
                    # A local discovery boundary, not an upstream reuse grant.
                    'usage_permissions': {'purpose': 'private_bibliographic_discovery',
                        'public_display': 'unknown', 'commercial_reuse': 'unknown',
                        'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False},
                    'credit_literals': [{'role_literal': None, 'name': credit['name']} for credit in reference['credits']],
                    'local_reference_key': {'provider': 'tobooks', 'source_scope': 'official_product_listing_reference',
                        'publisher_reference_id': identity, 'official_product_url': reference['source_url']}}
            cache[key] = references
        return cache[key]

    @staticmethod
    def _tobooks_reference_fields(reference):
        return {**LightNovelCatalogue._kadokawa_reference_fields(reference),
            'isbn': [reference['isbn']] if reference['isbn'] is not None else [],
            'isbn_basis': 'source_normalized_isbn13_not_raw_literal'}

    def _gagaga_publisher_references(self, path, sha256, cache):
        return self._finite_listing_publisher_references('gagaga', path, sha256, cache)

    def _kodansha_publisher_references(self, path, sha256, cache):
        return self._finite_listing_publisher_references('kodansha', path, sha256, cache)

    def _gc_publisher_references(self, path, sha256, cache):
        return self._finite_listing_publisher_references('gc', path, sha256, cache)

    def _overlap_bunko_publisher_references(self, path, sha256, cache):
        return self._finite_listing_publisher_references('overlap', path, sha256, cache)

    def _alphapolis_publisher_references(self, path, sha256, cache):
        return self._finite_listing_publisher_references('alphapolis', path, sha256, cache)

    def _earthstar_publisher_references(self, path, sha256, cache):
        return self._finite_listing_publisher_references('earthstar', path, sha256, cache)

    def _dre_publisher_references(self, path, sha256, cache):
        return self._finite_listing_publisher_references('dre', path, sha256, cache)

    def _ichijinsha_iris_special_publisher_references(self, path, sha256, cache):
        return self._finite_listing_publisher_references('ichijinshairisspecial', path, sha256, cache)

    def _ichijinsha_iris_publisher_references(self, path, sha256, cache):
        return self._finite_listing_publisher_references('ichijinshairis', path, sha256, cache)

    def _hifumi_publisher_references(self, path, sha256, cache):
        return self._finite_listing_publisher_references('hifumi', path, sha256, cache)

    def _gc_native_references(self, path, sha256, cache):
        key = ('gc_native_references', str(_resolved_path(path, cache)), sha256)
        if key not in cache:
            cache[key] = self._publisher_bibliography_references('gc', path, sha256)
        return cache[key]

    def _alphapolis_native_references(self, path, sha256, cache):
        key = ('alphapolis_native_references', str(_resolved_path(path, cache)), sha256)
        if key not in cache:
            cache[key] = self._publisher_bibliography_references('alphapolis', path, sha256)
        return cache[key]

    def _hifumi_native_references(self, path, sha256, cache):
        key = ('hifumi_native_references', str(_resolved_path(path, cache)), sha256)
        if key not in cache:
            cache[key] = self._publisher_bibliography_references('hifumi', path, sha256)
        return cache[key]

    def _finite_listing_publisher_references(self, provider, path, sha256, cache):
        """Adapt existing finite verifiers, without inferring missing bibliographic fields."""
        from .light_novel_publisher_references_v1 import verify_publisher_references
        from .normalization import normalize_isbn
        profile = PUBLISHER_REFERENCE_PROVIDERS[provider]
        key = (provider + '_product_references', str(_resolved_path(path, cache)), sha256)
        if key not in cache:
            references = {}
            native = (self._gc_native_references(path, sha256, cache) if provider == 'gc'
                      else self._alphapolis_native_references(path, sha256, cache) if provider == 'alphapolis'
                      else self._hifumi_native_references(path, sha256, cache) if provider == 'hifumi'
                      else verify_publisher_references(provider, path, sha256))
            for reference in native:
                identity, isbn, labels = reference['publisher_reference_id'], reference['isbn'], reference['label_literals']
                if (not isinstance(identity, str) or not re.fullmatch(profile['reference_pattern'], identity)
                        or identity in references or reference['source_unit'] != profile['source_unit']
                        or not isinstance(reference['source_url'], str)
                        or not re.fullmatch(profile['source_url_pattern'], reference['source_url'])
                        or not isinstance(reference['title'], str) or not reference['title'].strip()
                        or reference['publisher'] != profile['source_publisher']
                        or isbn is not None and (not isinstance(isbn, str) or not re.fullmatch(r'97[89][0-9]{10}', isbn)
                                                 or normalize_isbn(isbn) != isbn)
                        or not isinstance(labels, list) or any(not isinstance(value, str) or not value for value in labels)
                        or reference['label'] != (labels[0] if len(labels) == 1 else None)
                        or reference['form'] is not None and (not isinstance(reference['form'], str) or not reference['form'])
                        or not isinstance(reference['credits'], list)
                        or any(credit.get('role') not in profile['source_roles'] or not isinstance(credit.get('name'), str)
                               or not credit['name'] for credit in reference['credits'])
                        or reference['story_series_asserted'] is not False
                        or type(reference['metadata_conflict']) is not bool or not isinstance(reference['bibliographic_variants'], list)
                        or not reference['metadata_conflict'] and reference['bibliographic_variants'] != []
                        or reference['official_lightnovel_tag'] is not None and type(reference['official_lightnovel_tag']) is not bool
                        or not isinstance(reference['source_evidence'], list) or not reference['source_evidence']
                        or any(not re.fullmatch(r'[0-9a-f]{64}', evidence['raw_sha256'])
                               or not isinstance(evidence['locator'], str) or not evidence['locator']
                               for evidence in reference['source_evidence'])):
                    raise ValueError(provider + ' finite listing reference contract differs')
                if provider == 'kodansha' and (reference['source_url'] !=
                        'https://www.kodansha.co.jp/comic/products/' + identity.split(':')[1]
                        or isbn is not None or labels != ['講談社ラノベ文庫'] or reference['form'] is not None):
                    raise ValueError('Kodansha finite listing scope differs')
                if provider == 'gagaga':
                    snapshot = identity.startswith('publisher-lineup-snapshot:')
                    if snapshot != reference['source_url'].startswith('https://gagagabunko.jp/release/index.html#'):
                        raise ValueError('Gagaga listing identity scope differs')
                    if not snapshot and identity != 'publisher-lineup:' + hashlib.sha256(reference['source_url'].encode()).hexdigest()[:24]:
                        raise ValueError('Gagaga archive listing identity differs')
                    if snapshot and not any(identity == 'publisher-lineup-snapshot:' + hashlib.sha256(
                            (reference['source_url'] + '\n' + evidence['raw_sha256']).encode()).hexdigest()[:24]
                            for evidence in reference['source_evidence']):
                        raise ValueError('Gagaga release snapshot identity differs')
                if provider == 'gc':
                    from .light_novel_gc_references_v1 import _ROLES, _SCOPE
                    if (reference['official_product_id'] != identity.split(':')[1]
                            or reference['source_url'] != 'https://gcnovels.jp/book/' + reference['official_product_id']
                            or isbn is not None or labels != [] or reference['form'] is not None
                            or reference['volume'] is not None or reference['official_lightnovel_tag'] is not None
                            or reference['source_frame_scope'] != _SCOPE
                            or reference['source_frame_scope']['index_pages_complete'] is not False
                            or any(not isinstance(c.get('role_literal'), str)
                                   or c['role'] != _ROLES.get(c['role_literal'], 'unknown') for c in reference['credits'])):
                        raise ValueError('GC partial listing scope or unknown fields differ')
                if provider == 'overlap':
                    from .light_novel_overlap_details_v1 import _product
                    if (_product(reference['source_url'], 'BNK') != identity.split(':')[1]
                            or isbn is not None or reference['form'] is not None or reference['credits'] != []
                            or labels != ['オーバーラップ文庫'] or reference['official_lightnovel_tag'] is not None):
                        raise ValueError('Overlap BNK card scope or unknown fields differ')
                if provider == 'alphapolis':
                    from .light_novel_alphapolis_references_v1 import _ROLES, _SPECS, _DUPLICATE
                    spec = next((s for s in _SPECS if s[1] == reference['label']), None)
                    main, sub = reference['title_literal'], reference['subtitle_literal']
                    membership = reference['source_scope_membership']
                    expected_membership = ({'source_unit': 'publisher_commercial_book_index_membership',
                        'brand': spec[0], 'entry_url': spec[2],
                        'index_scope': 'alphapolis_novel_tankou_category_100' if spec[0] == 'alphapolis' else 'regina_tankou',
                        'membership_basis': 'explicit_book_card_and_own_label_tag_in_fixed_index',
                        'label_context_literal': spec[1], 'individual_lightnovel_classification': 'unknown',
                        'full_historical_catalogue_complete': False} if spec else None)
                    if (spec is None or reference['source_url'] != 'https://www.alphapolis.co.jp/book/detail/' + identity.split(':')[1]
                            or reference['opaque_url_ids'] != identity.split(':')[1].split('/')
                            or not isinstance(main, str) or not main or not isinstance(sub, str)
                            or reference['title'] != main + ('\n' + sub if sub else '')
                            or reference['complete_display_title'] != reference['title']
                            or isbn is not None or reference['form'] is not None or reference['volume'] is not None
                            or reference['official_lightnovel_tag'] is not None or membership != expected_membership
                            or membership['full_historical_catalogue_complete'] is not False
                            or any(not isinstance(c.get('role_display'), str)
                                   or c['role'] != _ROLES.get(c['role_display'], 'unknown') for c in reference['credits'])
                            or any(reference[field] is not False for field in ('edition_identity_asserted',
                                'original_work_identity_asserted', 'story_series_identity_asserted'))
                            or type(reference['occurrence_count']) is not int
                            or reference['occurrence_count'] != (2 if identity == _DUPLICATE else 1)
                            or len(reference['source_evidence']) != reference['occurrence_count']):
                        raise ValueError('Alpha complete title, sparse facts or card membership differs')
                if provider == 'hifumi':
                    from .light_novel_hifumi_references_v1 import _LABELS
                    if (reference['source_url'] != 'https://hifumi.co.jp/lineup/' + identity.split(':')[1] + '/'
                            or reference['label'] not in {label for label, _ in _LABELS.values()}
                            or isbn is not None or reference['form'] is not None
                            or reference['official_lightnovel_tag'] is not True
                            or len(reference['source_evidence']) != 3
                            or reference['source_evidence'][-1]['source_url'] != 'https://hifumi.co.jp/lineup_all/'):
                        raise ValueError('Hifumi three-label card scope or unknown fields differ')
                if provider == 'ichijinshairis':
                    from .light_novel_ichijinsha_references_v1 import _ROLES
                    if (identity != 'publisher-book:' + hashlib.sha256(reference['source_url'].encode()).hexdigest()[:24]
                            or labels not in [['一迅社文庫アイリス'], ['アイリスNEO']]
                            or reference['form'] is not None or reference['official_lightnovel_tag'] is not None
                            or any(not isinstance(c.get('role_display'), str)
                                   or c['role'] != _ROLES.get(c['role_display'], 'unknown') for c in reference['credits'])):
                        raise ValueError('Iris selected card scope or role differs')
                if provider == 'ichijinshairisspecial':
                    from .light_novel_ichijinsha_promotions_v1 import ENTRY, _LOCATOR
                    evidence = reference['source_evidence']
                    locator = _LOCATOR.fullmatch(evidence[-1]['locator'])
                    if (labels != [] or reference['form'] is not None or reference['official_lightnovel_tag'] is not None
                            or len(evidence) != 2 or evidence[0]['source_url'] != ENTRY
                            or evidence[-1]['source_url'] != reference['source_url'] or locator is None
                            or identity != 'publisher-promotion-book:' + hashlib.sha256(
                                (reference['source_url'] + '#' + locator[1]).encode()).hexdigest()[:24]):
                        raise ValueError('Iris promotion scope or own bibliography differs')
                if provider == 'earthstar' and (
                        reference['source_url'] != 'https://earthstar-novel.com/book/detail/' + identity.split(':')[1] + '/'
                        or isbn is not None or labels != []
                        or reference['form'] not in (None, '小説', 'ノベル', '漫画', 'コミック')
                        or reference['official_lightnovel_tag'] is not None):
                    raise ValueError('EarthStar card scope or unknown fields differ')
                # Gagaga roles are original literals; Kodansha roles are already
                # normalized by its source verifier and are not relabelled as literals.
                credits = ([{'role': 'unknown', 'name': c['name']} for c in reference['credits']]
                           if provider == 'gagaga' else reference['credits'])
                literals = ([{'role_literal': c['role'], 'name': c['name']} for c in reference['credits']]
                            if provider == 'gagaga' else [])
                if provider == 'gc':
                    literals = [{'role_literal': c['role_literal'], 'name': c['name']} for c in reference['credits']]
                elif provider in {'alphapolis', 'ichijinshairis'}:
                    literals = [{'role_literal': c['role_display'], 'name': c['name']} for c in reference['credits']]
                references[identity] = {**reference, 'credits': credits, 'credit_literals': literals,
                    'reference_id': identity, 'reference_scope': 'private_bibliographic_discovery',
                    'usage_permissions': {'purpose': 'private_bibliographic_discovery',
                        'public_display': 'unknown', 'commercial_reuse': 'unknown', 'prose_ai_permission': False,
                        'images_permission': False, 'raw_redistribution_permission': False},
                    'local_reference_key': {'provider': 'overlap-bunko' if provider == 'overlap' else provider,
                        'source_scope': ('official_partial_shared_GC_GCN_index_reference' if provider == 'gc'
                                         else 'official_BNK_card_index_product_reference' if provider == 'overlap'
                                         else 'official_two_commercial_book_card_frames' if provider == 'alphapolis'
                                         else 'official_three_label_card_index_reference' if provider == 'hifumi'
                                         else 'official_product_listing_reference'),
                        'publisher_reference_id': identity, 'official_product_url': reference['source_url']}}
            cache[key] = references
        return cache[key]

    @staticmethod
    def _finite_listing_reference_fields(reference):
        # A conflict elsewhere in the finite frame does not invalidate a selected
        # safe listing, but a conflicting listing itself must never be admitted.
        if reference['metadata_conflict'] is not False or reference['bibliographic_variants'] != []:
            raise ValueError('selected finite listing metadata conflicts')
        return LightNovelCatalogue._tobooks_reference_fields(reference)

    @staticmethod
    def _gc_reference_fields(reference):
        if reference['metadata_conflict'] is not False or reference['bibliographic_variants'] != []:
            raise ValueError('selected GC listing metadata conflicts')
        return {**LightNovelCatalogue._kadokawa_reference_fields(reference),
            'source_frame_scope': dict(reference['source_frame_scope']), 'isbn_basis': 'unknown_no_explicit_ISBN_field'}

    @staticmethod
    def _overlap_bunko_reference_fields(reference):
        if reference['metadata_conflict'] is not False or reference['bibliographic_variants'] != []:
            raise ValueError('selected Overlap BNK listing metadata conflicts')
        return {**LightNovelCatalogue._kadokawa_reference_fields(reference), 'isbn_basis': 'unknown_no_explicit_ISBN_field'}

    @staticmethod
    def _alphapolis_reference_fields(reference):
        if reference['metadata_conflict'] is not False or reference['bibliographic_variants'] != []:
            raise ValueError('selected Alpha listing metadata conflicts')
        fields = LightNovelCatalogue._kadokawa_reference_fields(reference)
        titles = {key: reference[key] for key in ('title_literal', 'subtitle_literal', 'complete_display_title')}
        fields['publisher_product_reference'].update(titles)
        return {**fields, **titles, 'source_scope_membership': dict(reference['source_scope_membership']),
            'occurrence_count': reference['occurrence_count'], 'isbn_basis': 'unknown_no_explicit_ISBN_field'}

    @staticmethod
    def _hifumi_reference_fields(reference):
        if reference['metadata_conflict'] is not False or reference['bibliographic_variants'] != []:
            raise ValueError('selected Hifumi listing metadata conflicts')
        return {**LightNovelCatalogue._kadokawa_reference_fields(reference), 'isbn_basis': 'unknown_no_explicit_ISBN_field'}

    def _overlap_detail_publisher_references(self, path, sha256, cache):
        return self._overlap_scoped_detail_publisher_references(path, sha256, cache, provider='overlapdetails')

    def _overlap_bunko_detail_publisher_references(self, path, sha256, cache):
        return self._overlap_scoped_detail_publisher_references(path, sha256, cache, provider='overlapbunkodetails')

    def _overlap_scoped_detail_publisher_references(self, path, sha256, cache, *, provider):
        """Consume the existing selected own-card/detail verifier, not its native roster."""
        from . import light_novel_overlap_details_v1 as source
        from .normalization import normalize_isbn
        key = ('overlap_detail_product_references', provider, str(_resolved_path(path, cache)), sha256)
        if key not in cache:
            references = {}
            for reference in source.verify_overlap_product_details(path, sha256):
                native_identity = reference['publisher_product_reference_id']
                if provider == 'overlapbunkodetails':
                    if not re.fullmatch(r'overlap-product:[A-Za-z0-9_-]+', native_identity):
                        raise ValueError('Overlap BNK detail requires an own BNK card')
                    typed_identity = native_identity.replace('overlap-product:', 'overlap-bnk-detail-product:', 1)
                    reference = {**reference, 'publisher_reference_id': typed_identity,
                                 'publisher_product_reference_id': typed_identity}
                identity, isbn = reference['publisher_reference_id'], reference['isbn']
                literal, credits = reference['isbn_source_literal'], reference['credits']
                matched = re.fullmatch(PUBLISHER_REFERENCE_PROVIDERS[provider]['reference_pattern'], identity)
                category = identity.split('-')[1].upper() if matched else None
                if (not matched or identity in references or reference['publisher_product_reference_id'] != identity
                        or source._product(reference['source_url'], category) != identity.split(':', 1)[1]
                        or reference['source_unit'] != 'publisher_product_detail_bibliographic_reference_not_confirmed_edition'
                        or not isinstance(reference['title'], str) or not reference['title'].strip()
                        or reference['publisher'] is not None or reference['detail_label'] is not None
                        or reference['label'] != source._BRANDS[category][0]
                        or reference['label_source_scope'] != 'own_card_label_only_detail_own_label_not_observed'
                        or reference['isbn_source_basis'] != 'explicit_ISBN_label_value_not_URL'
                        or literal is None and isbn is not None
                        or literal is not None and (not isinstance(literal, str)
                            or not re.fullmatch(r'[0-9Xx][0-9Xx -]*', literal)
                            or normalize_isbn(literal) is None or normalize_isbn(literal) != isbn)
                        or reference['form'] is not None and (not isinstance(reference['form'], str) or not reference['form'])
                        or not isinstance(credits, list) or len(credits) != 2
                        or credits[0].get('role') != 'unknown' or credits[0].get('role_literal') is not None
                        or credits[1].get('role') != 'illustrator' or credits[1].get('role_literal') != 'イラスト'
                        or credits[1].get('role_marker_literal') not in {'イラスト／', 'イラスト/'}
                        or any(not isinstance(c.get('name'), str) or not c['name'] for c in credits)
                        or any(reference[field] is not False for field in ('edition_identity_asserted',
                            'original_work_identity_asserted', 'story_series_identity_asserted'))):
                    raise ValueError('Overlap own card/detail product reference contract differs')
                references[identity] = {**reference, 'reference_id': identity,
                    'reference_scope': 'private_bibliographic_discovery',
                    'usage_permissions': {'purpose': 'private_bibliographic_discovery',
                        'public_display': 'unknown', 'commercial_reuse': 'unknown', 'prose_ai_permission': False,
                        'images_permission': False, 'raw_redistribution_permission': False},
                    'credit_literals': [{'role_literal': None, 'name': credits[0]['name']},
                        {'role_literal': credits[1]['role_literal'], 'role_marker_literal': credits[1]['role_marker_literal'],
                         'name': credits[1]['name']}],
                    'local_reference_key': {'provider': PUBLISHER_REFERENCE_PROVIDERS[provider]['record_provider'],
                        **({'native_publisher_product_reference_id': native_identity} if provider == 'overlapbunkodetails' else {}),
                        'source_scope': 'official_own_card_product_detail_reference',
                        'publisher_reference_id': identity, 'official_product_url': reference['source_url']}}
            cache[key] = references
        return cache[key]

    @staticmethod
    def _overlap_detail_reference_fields(reference):
        fields = LightNovelCatalogue._kadokawa_reference_fields(reference)
        fields['publisher_product_reference'].update(
            credit_literals=[dict(c) for c in reference['credit_literals']],
            label_source_scope=reference['label_source_scope'])
        return {**fields, 'isbn': [reference['isbn_source_literal']] if reference['isbn_source_literal'] is not None else [],
                'isbn_basis': 'explicit_source_ISBN_label_value_not_product_ID'}

    @staticmethod
    def _shueisha_dx_books(path, sha256, cache):
        """Keep native formal-book fields intact when sharing source replay."""
        from .light_novel_shueisha_books_v1 import verify_shueisha_books
        key = ('shueisha_dx_native_books', str(_resolved_path(path, cache)), sha256)
        if key not in cache:
            cache[key] = verify_shueisha_books(path, sha256)
        return cache[key]

    def _shueisha_dx_publisher_references(self, path, sha256, cache):
        """Replay the complete existing frame once; URL IDs never become standalone ISBNs."""
        key = ('shueisha_dx_product_references', str(_resolved_path(path, cache)), sha256)
        if key not in cache:
            references = {}
            for book in self._shueisha_dx_books(path, sha256, cache):
                identity = book['publisher_book_id']
                if (not isinstance(identity, str)
                        or not re.fullmatch(PUBLISHER_REFERENCE_PROVIDERS['shueishadx']['reference_pattern'], identity)
                        or identity in references or book['kind'] != 'book'
                        or book['source_url'] != 'https://dash.shueisha.co.jp/bookDetail/index/' + identity
                        or book['isbn_as_publisher_url'] != identity or book['publisher'] != '集英社'
                        or not isinstance(book['title'], str) or not book['title'].strip()
                        or not isinstance(book['label'], str) or not book['label'].strip()
                        or not isinstance(book['credits'], list)
                        or any(not isinstance(c.get('role'), str) or not c['role'].strip()
                               or not isinstance(c.get('name'), str) or not c['name'].strip() for c in book['credits'])
                        or book['story_series_identity'] != 'unassessed_no_title_stripping'
                        or not re.fullmatch(r'[0-9a-f]{64}', book['source_evidence']['source_sha256'])
                        or not isinstance(book['source_evidence']['selector'], str) or not book['source_evidence']['selector']):
                    raise ValueError('Shueisha DX finite book reference contract differs')
                references[identity] = {**book, 'reference_id': identity, 'publisher_reference_id': identity,
                    # Existing book.isbn is derived from the URL path; the source
                    # contains no independent ISBN column. Deliberately ignore it.
                    'isbn': None, 'form': None, 'reference_scope': 'private_bibliographic_discovery',
                    'usage_permissions': {'purpose': 'private_bibliographic_discovery',
                        'public_display': 'unknown', 'commercial_reuse': 'unknown', 'prose_ai_permission': False,
                        'images_permission': False, 'raw_redistribution_permission': False},
                    'credits': [{'role': 'unknown', 'name': c['name']} for c in book['credits']],
                    'credit_literals': [{'role_literal': c['role'], 'name': c['name']} for c in book['credits']],
                    'local_reference_key': {'provider': 'shueisha-dx',
                        'source_scope': 'official_DX_library_book_listing_reference',
                        'publisher_book_id': identity, 'official_book_url': book['source_url']}}
            cache[key] = references
        return cache[key]

    @staticmethod
    def _shueisha_dx_reference_fields(reference):
        # The finite source also contains comics. Authenticate the whole source
        # contract, but constrain only the selected/stored reference's context.
        if reference['label'] not in PUBLISHER_REFERENCE_PROVIDERS['shueishadx']['novel_labels']:
            raise ValueError('selected Shueisha DX reference lacks approved novel imprint context')
        return {**LightNovelCatalogue._kadokawa_reference_fields(reference),
                'isbn_basis': 'unknown_no_independent_ISBN_field_source_URL_identifier_only'}

    def _project_ecobalt_publisher_reference(self, record, cache):
        return self._project_publisher_reference('ecobalt', record, cache)

    def _project_publisher_reference(self, provider, record, cache):
        profile = PUBLISHER_REFERENCE_PROVIDERS[provider]
        provider_name = {'ecobalt': 'eCobalt', 'futaba': 'Futaba', 'kadokawa': 'Kadokawa', 'kadokawaprint': 'Kadokawa print', 'kadokawatrade': 'Kadokawa trade', 'tobooks': 'TO', 'gagaga': 'Gagaga', 'kodansha': 'Kodansha', 'overlapdetails': 'Overlap detail', 'overlapbunkodetails': 'Overlap BNK detail', 'shueishadx': 'Shueisha DX', 'gc': 'GC', 'overlap': 'Overlap BNK', 'alphapolis': 'Alpha', 'hifumi': 'Hifumi', 'ichijinshairis': 'Iris', 'ichijinshairisspecial': 'Iris promotion', 'earthstar': 'EarthStar', 'dre': 'DRE'}[provider]
        identity = _entry('lnr:pub:' + provider + ':' + record['id'])
        if (record.get('schema_version') != profile['schema'] or record.get('source') != 'publisher'
                or record.get('provider') != profile['record_provider'] or record.get('kind') != 'publisher_reference'):
            raise ValueError(provider_name + ' product reference typed scope differs')
        assertions = record['source_reference_assertions']
        if not isinstance(assertions, list) or not assertions:
            raise ValueError(provider_name + ' product reference has no versioned assertion')
        seen, previous_key = set(), None
        for assertion in assertions:
            pin = (assertion['manifest_path'], assertion['manifest_sha256'], assertion['reference_id'])
            key = assertion['local_reference_key']
            if (pin in seen or not re.fullmatch(r'[0-9a-f]{64}', assertion['manifest_sha256'])
                    or not re.fullmatch(profile['reference_pattern'], assertion['reference_id'])
                    or provider in {'futaba', 'kadokawa', 'kadokawaprint', 'kadokawatrade', 'tobooks', 'gagaga', 'kodansha', 'overlapdetails', 'overlapbunkodetails', 'shueishadx', 'gc', 'overlap', 'alphapolis', 'hifumi', 'ichijinshairis', 'ichijinshairisspecial', 'earthstar', 'dre'} and assertion['title'] != record['title']
                    or previous_key is not None and key != previous_key):
                raise ValueError(provider_name + ' product reference assertion continuity differs')
            seen.add(pin)
            previous_key = key
        active = assertions[-1]
        if provider in {'kadokawa', 'kadokawaprint', 'kadokawatrade'}:
            reference = getattr(self, profile['loader'])(active['manifest_path'], active['manifest_sha256'],
                cache, reference_ids=[active['reference_id']]).get(active['reference_id'])
        else:
            reference = getattr(self, profile['loader'])(active['manifest_path'], active['manifest_sha256'], cache).get(active['reference_id'])
        if (reference is None or active != self._publisher_reference_assertion(provider, active['manifest_path'], active['manifest_sha256'], reference)
                or any(record.get(key) != value for key, value in getattr(self, profile['fields'])(reference).items())):
            raise ValueError(provider_name + ' product reference fields do not replay')
        row = {**getattr(self, profile['fields'])(reference),
            'entry_id': identity, 'work_id': identity, 'entry_kind': 'publisher_reference', 'qid': None,
            'family': 'light_novel', 'aliases': [], 'authors': [], 'publishers': [], 'genres': [], 'subjects': [],
            'original_language': None, 'language': None, 'year': None, 'volume': None, 'isbn': [],
            'publication_label': None, 'read_status': 'unknown', 'light_novel_confirmed': False, 'label_candidate': False,
            'classification_scope': 'publisher_reference', 'reference_identity_basis': 'publisher_product_reference',
            'reference_title_source': 'publisher_product_title', 'publisher_reference_kind': profile['public_kind'],
            'admission_status': 'publisher_product_reference_verified', 'relation_resolution': 'publisher_product_reference_verified',
            'source_url': reference['source_url'], 'publisher_reference_id': reference['publisher_reference_id'],
            'actual_work_identity_asserted': False, 'edition_identity_asserted': False,
            'story_series_identity_asserted': False, 'original_work_identity_asserted': False,
            'paper_electronic_same_edition_asserted': False, 'global_membership_complete': None,
            'source_attributions': [{'source': 'publisher', 'provider': profile['record_provider'], 'source_url': reference['source_url'],
                'scope': 'publisher_product_reference', 'credit': profile['credit']}],
            'content_sources': [], 'relations': {prop: [] for prop in RELATIONS}, 'recommendable': False,
            'llm_features': [], 'native_features': [], 'strict72_features': [],
            'content_features_adopted': False, 'recommendation_mode': 'insufficient_evidence'}
        if provider == 'futaba':
            row.update(isbn=record['isbn'], publication_label=record['publication_label'],
                usage_permissions={'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
                    'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False})
        elif provider in {'kadokawa', 'kadokawaprint', 'kadokawatrade', 'tobooks', 'gagaga', 'kodansha', 'overlapdetails', 'overlapbunkodetails', 'shueishadx', 'gc', 'overlap', 'alphapolis', 'hifumi', 'ichijinshairis', 'ichijinshairisspecial', 'earthstar', 'dre'}:
            row.update(publication_label=record['publication_label'],
                reference_scope=record['reference_scope'], usage_permissions=record['usage_permissions'],
                light_novel_identity_asserted=False)
            if provider in {'kadokawaprint', 'kadokawatrade', 'tobooks', 'gagaga', 'kodansha', 'overlapdetails', 'overlapbunkodetails', 'shueishadx', 'gc', 'overlap', 'alphapolis', 'hifumi', 'ichijinshairis', 'ichijinshairisspecial', 'earthstar', 'dre'}:
                row['isbn'] = record['isbn']
        return row

    def import_ecobalt_publisher_references(self, manifest_path, expected_sha256, *, reference_ids=None):
        return self._import_publisher_references('ecobalt', manifest_path, expected_sha256, reference_ids=reference_ids)

    def import_futaba_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        if reference_ids is None:
            raise ValueError('an explicit Futaba product reference selection is required')
        return self._import_publisher_references('futaba', manifest_path, expected_sha256, reference_ids=reference_ids)

    def import_kadokawa_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_publisher_references('kadokawa', manifest_path, expected_sha256, reference_ids=reference_ids)

    def import_kadokawa_print_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_publisher_references('kadokawaprint', manifest_path, expected_sha256, reference_ids=reference_ids)

    def import_kadokawa_trade_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_publisher_references('kadokawatrade', manifest_path, expected_sha256, reference_ids=reference_ids)

    def import_tobooks_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        if reference_ids is None or isinstance(reference_ids, (str, bytes)):
            raise ValueError('an explicit TO product reference selection is required')
        requested = list(reference_ids)
        if (not requested or any(not isinstance(identity, str) or not re.fullmatch(
                PUBLISHER_REFERENCE_PROVIDERS['tobooks']['reference_pattern'], identity) for identity in requested)
                or len(requested) != len(set(requested))):
            raise ValueError('unique explicit TO product reference IDs are required')
        return self._import_publisher_references('tobooks', manifest_path, expected_sha256, reference_ids=requested)

    def import_gagaga_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('gagaga', manifest_path, expected_sha256, reference_ids)

    def import_kodansha_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('kodansha', manifest_path, expected_sha256, reference_ids)

    def import_overlap_detail_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('overlapdetails', manifest_path, expected_sha256, reference_ids)

    def import_overlap_bunko_detail_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('overlapbunkodetails', manifest_path, expected_sha256, reference_ids)

    def import_shueisha_dx_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('shueishadx', manifest_path, expected_sha256, reference_ids)

    def import_gc_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('gc', manifest_path, expected_sha256, reference_ids)

    def import_overlap_bunko_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('overlap', manifest_path, expected_sha256, reference_ids)

    def import_alphapolis_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('alphapolis', manifest_path, expected_sha256, reference_ids)

    def import_earthstar_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('earthstar', manifest_path, expected_sha256, reference_ids)

    def import_dre_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('dre', manifest_path, expected_sha256, reference_ids)

    def import_ichijinsha_iris_special_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('ichijinshairisspecial', manifest_path, expected_sha256, reference_ids)

    def import_ichijinsha_iris_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('ichijinshairis', manifest_path, expected_sha256, reference_ids)

    def import_hifumi_publisher_references(self, manifest_path, expected_sha256, *, reference_ids):
        return self._import_explicit_finite_listing('hifumi', manifest_path, expected_sha256, reference_ids)

    def _import_explicit_finite_listing(self, provider, manifest_path, sha256, reference_ids):
        if reference_ids is None or isinstance(reference_ids, (str, bytes)):
            raise ValueError('an explicit finite product reference selection is required')
        requested = list(reference_ids)
        if (not requested or any(not isinstance(identity, str) or not re.fullmatch(
                PUBLISHER_REFERENCE_PROVIDERS[provider]['reference_pattern'], identity) for identity in requested)
                or len(requested) != len(set(requested))):
            raise ValueError('unique explicit finite product reference IDs are required')
        return self._import_publisher_references(provider, manifest_path, sha256, reference_ids=requested)

    def _import_publisher_references(self, provider, manifest_path, expected_sha256, *, reference_ids):
        """Store ungrouped listings with local identity, without asserting a work or edition."""
        cache = {}
        profile = PUBLISHER_REFERENCE_PROVIDERS[provider]
        provider_name = {'ecobalt': 'eCobalt', 'futaba': 'Futaba', 'kadokawa': 'Kadokawa', 'kadokawaprint': 'Kadokawa print', 'kadokawatrade': 'Kadokawa trade', 'tobooks': 'TO', 'gagaga': 'Gagaga', 'kodansha': 'Kodansha', 'overlapdetails': 'Overlap detail', 'overlapbunkodetails': 'Overlap BNK detail', 'shueishadx': 'Shueisha DX', 'gc': 'GC', 'overlap': 'Overlap BNK', 'alphapolis': 'Alpha', 'hifumi': 'Hifumi', 'ichijinshairis': 'Iris', 'ichijinshairisspecial': 'Iris promotion', 'earthstar': 'EarthStar', 'dre': 'DRE'}[provider]
        path = str(_resolved_path(manifest_path, cache))
        if provider in {'kadokawa', 'kadokawaprint', 'kadokawatrade'}:
            reference_ids = list(reference_ids) if reference_ids is not None and not isinstance(reference_ids, (str, bytes)) else reference_ids
            references = getattr(self, profile['loader'])(path, expected_sha256, cache, reference_ids=reference_ids)
        else:
            references = getattr(self, profile['loader'])(path, expected_sha256, cache)
        if isinstance(reference_ids, (str, bytes)):
            raise ValueError('an explicit product reference collection is required')
        requested = list(references) if reference_ids is None else list(reference_ids)
        if len(requested) != len(set(requested)) or set(requested) - references.keys():
            raise ValueError('unknown or duplicate publisher product reference')
        result = {'source_references': len(references), 'requested_references': len(requested),
            'inserted': 0, 'updated': 0, 'unchanged': 0, 'held': 0, 'held_reasons': {}, 'reference_entries': {}}
        target_where = ("entry_id LIKE ? OR json_extract(record_json,'$.schema_version')=? OR "
                        "(kind='publisher_reference' AND json_extract(record_json,'$.provider')=?)")
        target_parameters = ('lnr:pub:' + provider + ':%', profile['schema'], profile['record_provider'])
        with self._connect() as db:
            stored = {r['entry_id']: dict(r) for r in db.execute('SELECT * FROM entries WHERE ' + target_where, target_parameters)}
        keys, records, stable_references = {}, {}, set()
        for identity, entry in stored.items():
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored ' + provider_name + ' product reference SHA mismatch')
            record = json.loads(entry['record_json'])
            if provider in {'kadokawa', 'kadokawaprint', 'kadokawatrade'}:
                # Snapshot every local row for the transaction/race guard, but
                # never replay an unrelated product's source pages on import.
                if (record.get('schema_version') != profile['schema'] or record.get('source') != 'publisher'
                        or record.get('provider') != profile['record_provider']
                        or record.get('kind') != 'publisher_reference' or entry['kind'] != 'publisher_reference'
                        or _entry('lnr:pub:' + provider + ':' + record['id']) != identity):
                    raise ValueError('stored Kadokawa product reference typed identity differs')
                claimed = {a['reference_id'] for a in record['source_reference_assertions']}
                claimed.add(record['publisher_product_reference']['publisher_reference_id'])
                if not claimed.intersection(requested):
                    continue
            row = self._project_publisher_reference(provider, record, cache)
            if row['entry_id'] != identity or row['entry_kind'] != entry['kind']:
                raise ValueError('stored ' + provider_name + ' product reference typed identity differs')
            records[identity] = record
            stable_references.add(record['source_reference_assertions'][-1]['reference_id'])
            key = _json(record['source_reference_assertions'][-1]['local_reference_key'])
            keys.setdefault(key, []).append(identity)
        proposals = []
        for reference_id in requested:
            reference = references[reference_id]
            assertion = self._publisher_reference_assertion(provider, path, expected_sha256, reference)
            previous_ids = keys.get(_json(assertion['local_reference_key']), [])
            if provider in {'futaba', 'kadokawa', 'kadokawaprint', 'kadokawatrade', 'tobooks', 'gagaga', 'kodansha', 'overlapdetails', 'overlapbunkodetails', 'shueishadx', 'gc', 'overlap', 'alphapolis', 'hifumi', 'ichijinshairis', 'ichijinshairisspecial', 'earthstar', 'dre'} and (not previous_ids and reference_id in stable_references
                    or previous_ids and any(records[identity]['title'] != reference['title'] for identity in previous_ids)):
                result['held'] += 1
                reason = 'source_reference_scope_or_title_changed'
                result['held_reasons'][reason] = result['held_reasons'].get(reason, 0) + 1
                continue
            if len(previous_ids) > 1:
                result['held'] += 1
                reason = 'ambiguous_existing_product_reference'
                result['held_reasons'][reason] = result['held_reasons'].get(reason, 0) + 1
                continue
            previous = None
            if previous_ids:
                identity = previous_ids[0]
                previous, record = stored[identity], records[identity]
                if record['source_reference_assertions'][-1] == assertion:
                    result['unchanged'] += 1
                    result['reference_entries'][reference_id] = identity
                    continue
                record = {**record, **getattr(self, profile['fields'])(reference),
                    'source_reference_assertions': [*record['source_reference_assertions'], assertion]}
            else:
                record = {'schema_version': profile['schema'], 'source': 'publisher', 'provider': profile['record_provider'],
                    'kind': 'publisher_reference', 'id': uuid.uuid4().hex,
                    'source_reference_assertions': [assertion], **getattr(self, profile['fields'])(reference)}
                identity = 'lnr:pub:' + provider + ':' + record['id']
            self._project_publisher_reference(provider, record, cache)
            encoded = _json(record)
            proposals.append((identity, reference_id, previous, encoded, hashlib.sha256(encoded.encode()).hexdigest()))
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if {r['entry_id'] for r in db.execute('SELECT entry_id FROM entries WHERE ' + target_where, target_parameters)} != set(stored):
                raise ValueError('publisher reference target set changed during import')
            for identity, previous in stored.items():
                current = db.execute('SELECT kind,record_sha256 FROM entries WHERE entry_id=?', (identity,)).fetchone()
                if current is None or tuple(current) != (previous['kind'], previous['record_sha256']):
                    raise ValueError('publisher reference metadata changed during import')
            for identity, reference_id, previous, encoded, digest in proposals:
                if previous is None:
                    db.execute('INSERT INTO entries VALUES(?,?,?,?)', (identity, 'publisher_reference', encoded, digest))
                    result['inserted'] += 1
                else:
                    db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?', (encoded, digest, identity))
                    result['updated'] += 1
                result['reference_entries'][reference_id] = identity
        return result

    def _import_publisher_source_groups(self, provider, manifest_path, expected_sha256, *, reference_ids=None):
        """Admit one provider's source groups without an NDL/story identity.

        Local UUIDs survive only unambiguous, exact scoped-key continuations
        with a shared product reference. Existing anchored groups stay intact.
        """
        cache = {}
        profile = SOURCE_GROUP_PROVIDERS[provider]
        path = str(_resolved_path(manifest_path, cache))
        groups = getattr(self, profile['loader'])(path, expected_sha256, cache)
        if isinstance(reference_ids, (str, bytes)):
            raise ValueError('an explicit workgroup reference collection is required')
        requested = list(groups) if reference_ids is None else list(reference_ids)
        if len(requested) != len(set(requested)) or set(requested) - groups.keys():
            raise ValueError('unknown or duplicate publisher workgroup reference')
        result = {'source_groups': len(groups), 'requested_groups': len(requested), 'inserted': 0,
                  'updated': 0, 'unchanged': 0, 'protected_existing': 0, 'held': 0,
                  'held_reasons': {}, 'reference_entries': {}}
        target_where = ("entry_id LIKE ? OR json_extract(record_json,'$.schema_version')=? OR "
                        "(kind='publisher_group' AND json_extract(record_json,'$.provider')=?)")
        target_parameters = ('lng:pub:' + provider + ':%', profile['schema'], profile['record_provider'])
        if provider == 'futaba':
            target_where += " OR json_extract(record_json,'$.publisher_series_proof.kind')='futaba_mnovels_workgroup'"
        with self._connect() as db:
            stored = {r['entry_id']: dict(r) for r in db.execute('SELECT * FROM entries WHERE ' + target_where, target_parameters)}
        keys, products, stable_references = {}, {}, set()
        index_urls, index_titles = set(), set()
        records = {}
        for identity, entry in stored.items():
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored publisher source group SHA mismatch')
            record = json.loads(entry['record_json'])
            records[identity] = record
            if record.get('schema_version') == profile['schema']:
                row = self._project_publisher_source_group(provider, record, cache)
                if row['entry_id'] != identity or row['entry_kind'] != entry['kind']:
                    raise ValueError('stored publisher source group typed identity differs')
                assertion = record['source_group_assertions'][-1]
                if profile.get('index_only'):
                    index_urls.add(row['source_url'])
                    index_titles.add(row['title'])
            else:
                if provider != 'futaba':
                    raise ValueError('stored publisher source group typed identity differs')
                proof = record['publisher_series_proof']
                old_group = self._futaba_workgroups(proof['manifest_path'], proof['manifest_sha256'], cache).get(proof['reference_id'])
                if old_group is None or identity != 'lns:ndl:' + record['id'] or entry['kind'] != 'series':
                    raise ValueError('preserved Futaba anchor identity differs')
                assertion = self._futaba_source_assertion(proof['manifest_path'], proof['manifest_sha256'], old_group)
            if provider == 'orange':
                stable_references.add(assertion['reference_id'])
            keys.setdefault(_json(self._publisher_source_key(assertion)), []).append(identity)
            for member in assertion['member_reference_ids']:
                products.setdefault(member, set()).add(identity)
        proposals = []
        for reference in requested:
            group = groups[reference]
            assertion = self._publisher_source_assertion(provider, path, expected_sha256, group)
            previous_ids = keys.get(_json(self._publisher_source_key(assertion)), [])
            reason = None
            previous = None
            if provider == 'orange' and reference in stable_references and not previous_ids:
                reason = 'source_group_scope_or_title_changed'
            elif profile.get('index_only') and not previous_ids and (group['source_url'] in index_urls or group['title'] in index_titles):
                reason = 'source_group_scope_or_title_changed'
            elif len(previous_ids) > 1:
                reason = 'ambiguous_existing_source_group'
            elif previous_ids:
                identity = previous_ids[0]
                previous = stored[identity]
                record = records[identity]
                if record.get('schema_version') != profile['schema']:
                    result['protected_existing'] += 1
                    result['reference_entries'][reference] = identity
                    continue
                old = record['source_group_assertions'][-1]
                competing = set().union(*(products.get(m, set()) for m in assertion['member_reference_ids'])) - {identity}
                if (provider == 'sbcr' or profile.get('index_only')) and record['title'] != group['title']:
                    reason = 'source_group_scope_or_title_changed'
                elif competing or not self._publisher_source_continuity(old, assertion, provider=provider):
                    reason = 'ambiguous_source_group_continuation'
                elif old == assertion:
                    result['unchanged'] += 1
                    result['reference_entries'][reference] = identity
                    continue
                else:
                    record = {**record, 'source_group_assertions': [*record['source_group_assertions'], assertion],
                              'publisher_product_references': self._publisher_product_references(provider, group)}
                    if profile.get('index_only'):
                        record.update(self._publisher_source_display(provider, group))
            else:
                if any(products.get(m) for m in assertion['member_reference_ids']):
                    reason = 'source_group_scope_or_title_changed'
                else:
                    record = {'schema_version': profile['schema'], 'source': 'publisher',
                        'provider': profile['record_provider'], 'kind': 'publisher_group', 'id': uuid.uuid4().hex,
                        'title': group['title'], 'source_group_assertions': [assertion],
                        'publisher_product_references': self._publisher_product_references(provider, group),
                        **self._publisher_source_display(provider, group)}
                    identity = 'lng:pub:' + provider + ':' + record['id']
            if reason:
                result['held'] += 1
                result['held_reasons'][reason] = result['held_reasons'].get(reason, 0) + 1
                continue
            self._project_publisher_source_group(provider, record, cache)
            encoded = _json(record)
            proposals.append((identity, reference, previous, encoded, hashlib.sha256(encoded.encode()).hexdigest()))
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if {r['entry_id'] for r in db.execute('SELECT entry_id FROM entries WHERE ' + target_where, target_parameters)} != set(stored):
                raise ValueError('publisher group target set changed during import')
            for identity, previous in stored.items():
                current = db.execute('SELECT kind,record_sha256 FROM entries WHERE entry_id=?', (identity,)).fetchone()
                if current is None or tuple(current) != (previous['kind'], previous['record_sha256']):
                    raise ValueError('publisher group metadata changed during import')
            for identity, reference, previous, encoded, digest in proposals:
                if previous is None:
                    db.execute('INSERT INTO entries VALUES(?,?,?,?)', (identity, 'publisher_group', encoded, digest))
                    result['inserted'] += 1
                else:
                    db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?', (encoded, digest, identity))
                    result['updated'] += 1
                result['reference_entries'][reference] = identity
        return result

    def _futaba_correspondence_state(self, cache):
        key = ('futaba_workgroup_correspondence_state',)
        if key not in cache:
            with self._connect() as db:
                bindings = [dict(row) for row in db.execute(
                    'SELECT * FROM publisher_bibliographic_correspondences WHERE provider=?', ('futaba-mnovels',))]
                stored = {}
                identities = [row['edition_id'] for row in bindings]
                for start in range(0, len(identities), 400):
                    batch = identities[start:start + 400]
                    stored.update((row['entry_id'], dict(row)) for row in db.execute(
                        'SELECT * FROM entries WHERE entry_id IN (' + ','.join('?' for _ in batch) + ')', batch))
            indexed, proofs = {}, {}
            for binding in bindings:
                if hashlib.sha256(binding['proof_json'].encode()).hexdigest() != binding['proof_sha256']:
                    continue
                try:
                    proof = json.loads(binding['proof_json'])
                    references = proof['reference_ids']
                    if (not references or not isinstance(references, list)
                            or references != sorted(set(references))):
                        continue
                except (ValueError, KeyError, TypeError):
                    continue
                proofs[binding['edition_id']] = (binding, proof)
                for reference in references:
                    indexed.setdefault(reference, []).append(binding['edition_id'])
            cache[key] = {'stored': stored, 'proofs': proofs, 'index': indexed}
        return cache[key]

    def _futaba_verified_correspondence(self, identity, group, cache):
        from .light_novel_bibliographic_match_v1 import SCOPE
        snapshot = group['source_scope']['manifest_sha256']
        key = ('futaba_workgroup_verified_correspondence', identity, snapshot)
        if key in cache:
            return cache[key]
        state = self._futaba_correspondence_state(cache)
        entry = state['stored'].get(identity)
        saved = state['proofs'].get(identity)
        if entry is None or saved is None:
            return None
        binding, proof = saved
        if (proof.get('provider') != 'futaba-mnovels' or proof.get('scope') != SCOPE
                or proof.get('manifest_sha256') != snapshot
                or proof.get('edition_record_sha256') != entry['record_sha256']):
            return None
        if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
            raise ValueError('stored Futaba member metadata SHA mismatch')
        record = json.loads(entry['record_json'])
        row = self._project_edition(record, cache)
        if row['entry_id'] != identity or row['entry_kind'] != entry['kind']:
            raise ValueError('stored Futaba member typed identity differs')
        source_key = ('futaba_workgroup_formal_references', str(_resolved_path(proof['manifest_path'], cache)), snapshot)
        if source_key not in cache:
            references = self._publisher_bibliography_references('futaba-mnovels', proof['manifest_path'], snapshot)
            document = {ref['publisher_reference_id']: ref for ref in references}
            if len(document) != len(references):
                raise ValueError('duplicate formal Futaba reference')
            cache[source_key] = document
        document = cache[source_key]
        if any(reference not in document for reference in proof['reference_ids']):
            return None
        parts = self._edition_primary_bibliographic_parts(record, cache)
        matches = [self._publisher_bibliographic_match('futaba-mnovels', document[reference], parts, cache)
                   for reference in proof['reference_ids']]
        if any(match is None for match in matches) or matches != proof.get('matches'):
            return None
        result = {'entry': entry, 'record': record, 'row': row, 'binding': binding, 'references': document}
        cache[key] = result
        return result

    def _futaba_group_members(self, group, cache):
        key = ('futaba_workgroup_members', group['reference_id'], group['source_scope']['manifest_sha256'])
        if key not in cache:
            state = self._futaba_correspondence_state(cache)
            result = {}
            for member in group['members']:
                reference = member['publisher_reference_id']
                for identity in state['index'].get(reference, ()):
                    verified = self._futaba_verified_correspondence(identity, group, cache)
                    if verified is None:
                        continue
                    formal = verified['references'][reference]
                    if any(member.get(field) != value for field, value in formal.items()):
                        continue
                    result.setdefault(identity, {**verified, 'publisher_reference_ids': []})['publisher_reference_ids'].append(reference)
            for member in result.values():
                member['publisher_reference_ids'] = sorted(set(member['publisher_reference_ids']))
            cache[key] = result
        return cache[key]

    def _project_futaba_workgroup(self, record, cache):
        proof = record['publisher_series_proof']
        group = self._futaba_workgroups(proof['manifest_path'], proof['manifest_sha256'], cache).get(proof['reference_id'])
        if group is None or record.get('title') != group['title']:
            raise ValueError('Futaba workgroup title/source reference differs')
        members = self._futaba_group_members(group, cache)
        identities = record['member_edition_ids']
        anchor_id = 'lne:ndl:' + record['id']
        if (record.get('source') != 'ndl' or not identities or identities != sorted(set(identities))
                or anchor_id not in identities or any(identity not in members for identity in identities)):
            raise ValueError('Futaba workgroup has no authenticated NDL anchor/member')
        anchor = members[anchor_id]
        if (record['anchor_edition_record'] != anchor['record']
                or proof['anchor_record_sha256'] != anchor['entry']['record_sha256']
                or proof['anchor_correspondence_sha256'] != anchor['binding']['proof_sha256']):
            raise ValueError('Futaba workgroup anchor proof differs')
        base = anchor['row']
        identity = _entry('lns:ndl:' + record['id'])
        return {**base, 'entry_id': identity, 'work_id': identity, 'entry_kind': 'series',
            'title': group['title'], 'aliases': [], 'authors': [], 'publishers': [], 'genres': [], 'subjects': [],
            'original_language': None, 'language': None, 'year': None, 'volume': None, 'isbn': [], 'publication_label': None,
            'light_novel_confirmed': False, 'label_candidate': False, 'classification_scope': 'series',
            'admission_status': 'publisher_work_group_verified', 'relation_resolution': 'publisher_work_group_and_anchor_verified',
            'series_identity_basis': 'publisher_work_group_and_ndl_bibliography', 'series_title_source': 'publisher_explicit_work_group',
            'publisher_work_group_kind': 'futaba_mnovels_grouping', 'series_assertion_source_url': group['source_url'],
            'source_url': group['source_url'], 'publisher_series_reference_id': group['reference_id'],
            'publisher_product_references': self._futaba_product_references(group),
            'title_origin': {'kind': 'publisher_explicit_work_group', 'source_url': group['source_url'], 'locator': 'own grouping field'},
            'source_frame_member_count': group['frame_member_count'], 'verified_bibliographic_member_count': len(identities),
            'frame_membership_complete': group['frame_membership_complete'],
            'exact_grouping_filter_complete': group['exact_grouping_filter_complete'], 'global_membership_complete': None,
            'story_series_identity_asserted': False, 'original_work_identity_asserted': False,
            'source_attributions': [{'source': 'publisher', 'provider': 'futaba-mnovels', 'source_url': group['source_url'],
                'scope': 'publisher_work_group_source_frames', 'credit': '作品グループ名・保存枠内の掲載参照: 双葉社の書籍一覧'},
                *[{**source, 'scope': 'publisher_work_group_member_bibliographic_anchor'} for source in base['source_attributions']]],
            'content_sources': [], 'relations': {prop: [] for prop in RELATIONS}, 'recommendable': False}

    @staticmethod
    def _futaba_workgroup_membership(record, record_sha256, identity, member):
        proof = record['publisher_series_proof']
        return {'kind': 'futaba_mnovels_workgroup_membership', 'scope': 'publisher_work_group_source_frame_only',
            'edition_record_sha256': member['entry']['record_sha256'], 'series_record_sha256': record_sha256,
            'correspondence_proof_sha256': member['binding']['proof_sha256'],
            'edition_id': identity, 'series_entry_id': 'lns:ndl:' + record['id'],
            'reference_id': proof['reference_id'], 'manifest_path': proof['manifest_path'],
            'manifest_sha256': proof['manifest_sha256'], 'publisher_reference_ids': member['publisher_reference_ids'],
            'story_series_identity_asserted': False, 'original_work_identity_asserted': False}

    def import_futaba_mnovels_workgroups(self, manifest_path, expected_sha256, *, reference_ids=None):
        """Admit source-scoped M groups through existing exact product correspondences.

        Existing group records and single-parent relations are immutable here;
        a changed proof or occupied anchor is held for separate review.
        """
        cache = {}
        path = str(_resolved_path(manifest_path, cache))
        groups = self._futaba_workgroups(path, expected_sha256, cache)
        if isinstance(reference_ids, (str, bytes)):
            raise ValueError('an explicit workgroup reference collection is required')
        requested = list(groups) if reference_ids is None else list(reference_ids)
        if len(requested) != len(set(requested)) or set(requested) - groups.keys():
            raise ValueError('unknown or duplicate Futaba workgroup reference')
        result = {'source_groups': len(groups), 'requested_groups': len(requested), 'inserted': 0, 'unchanged': 0,
                  'linked': 0, 'unchanged_memberships': 0, 'held': 0, 'held_reasons': {}}
        def hold(reason):
            result['held'] += 1
            result['held_reasons'][reason] = result['held_reasons'].get(reason, 0) + 1
        with self._connect() as db:
            parents = {row['edition_id']: dict(row) for row in db.execute('SELECT * FROM edition_series_memberships')}
            occupied = {row['entry_id']: dict(row) for row in db.execute(
                "SELECT * FROM entries WHERE kind='series' OR entry_id LIKE 'lns:ndl:%'")}
        reference_groups = {}
        for reference, group in groups.items():
            for member in group['members']:
                reference_groups.setdefault(member['publisher_reference_id'], set()).add(reference)
        claims = {}
        for identity, (_, proof) in self._futaba_correspondence_state(cache)['proofs'].items():
            claims[identity] = set().union(*(reference_groups.get(reference, set()) for reference in proof['reference_ids']))
        existing = {}
        for identity, entry in occupied.items():
            record = json.loads(entry['record_json'])
            proof = record.get('publisher_series_proof', {})
            if proof.get('kind') == 'futaba_mnovels_workgroup':
                if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                    raise ValueError('stored Futaba group SHA mismatch')
                existing.setdefault(proof['reference_id'], []).append((identity, entry, record))
        proposals = []
        for reference in requested:
            group = groups[reference]
            members = self._futaba_group_members(group, cache)
            previous = existing.get(reference, [])
            if len(previous) > 1:
                hold('ambiguous_existing_group_anchor')
                continue
            prior_id = previous[0][0] if previous else None
            available = {identity: member for identity, member in members.items()
                         if len(claims.get(identity, ())) == 1
                         and (identity not in parents or parents[identity]['series_id'] == prior_id)}
            if not available:
                hold('no_unconflicted_verified_ndl_member')
                continue
            if previous:
                group_id, old_entry, record = previous[0]
                proof = record['publisher_series_proof']
                if (proof['manifest_path'] != path or proof['manifest_sha256'] != expected_sha256
                        or any(identity not in available for identity in record['member_edition_ids'])):
                    hold('existing_group_proof_conflict')
                    continue
            else:
                anchors = [identity for identity in available if 'lns:ndl:' + identity.removeprefix('lne:ndl:') not in occupied]
                if not anchors:
                    hold('existing_anchor_conflict')
                    continue
                anchor_id = min(anchors)
                anchor = available[anchor_id]
                record = {'schema_version': 'light-novel-ndl-series-v1', 'source': 'ndl', 'id': anchor['record']['id'],
                    'title': group['title'], 'anchor_edition_record': anchor['record'], 'member_edition_ids': sorted(available),
                    'publisher_series_proof': {'kind': 'futaba_mnovels_workgroup', 'manifest_path': path,
                        'manifest_sha256': expected_sha256, 'reference_id': reference,
                        'anchor_record_sha256': anchor['entry']['record_sha256'],
                        'anchor_correspondence_sha256': anchor['binding']['proof_sha256']}}
                group_id, old_entry = 'lns:ndl:' + record['id'], None
            self._project_futaba_workgroup(record, cache)
            encoded = _json(record)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            if old_entry is not None and old_entry['record_sha256'] != digest:
                hold('existing_group_proof_conflict')
                continue
            selected = {identity: available[identity] for identity in record['member_edition_ids']}
            links = {identity: _json(self._futaba_workgroup_membership(record, digest, identity, member))
                     for identity, member in selected.items()}
            if any(identity in parents and (parents[identity]['series_id'] != group_id
                    or parents[identity]['proof_json'] != text
                    or parents[identity]['proof_sha256'] != hashlib.sha256(text.encode()).hexdigest())
                   for identity, text in links.items()):
                hold('existing_member_proof_conflict')
                continue
            proposals.append((group_id, old_entry, encoded, digest, selected, links))
            occupied[group_id] = {'entry_id': group_id}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for group_id, previous, encoded, digest, selected, links in proposals:
                current = db.execute('SELECT kind,record_sha256 FROM entries WHERE entry_id=?', (group_id,)).fetchone()
                if (previous is None and current is not None or previous is not None and
                        (current is None or tuple(current) != (previous['kind'], previous['record_sha256']))):
                    raise ValueError('Futaba group metadata changed during import')
                for identity, member in selected.items():
                    current = db.execute('SELECT kind,record_sha256 FROM entries WHERE entry_id=?', (identity,)).fetchone()
                    binding = db.execute('SELECT proof_sha256 FROM publisher_bibliographic_correspondences '
                        'WHERE edition_id=? AND provider=?', (identity, 'futaba-mnovels')).fetchone()
                    parent = db.execute('SELECT * FROM edition_series_memberships WHERE edition_id=?', (identity,)).fetchone()
                    if (current is None or tuple(current) != (member['entry']['kind'], member['entry']['record_sha256'])
                            or binding is None or binding[0] != member['binding']['proof_sha256']
                            or (dict(parent) if parent else None) != parents.get(identity)):
                        raise ValueError('Futaba member evidence or parent changed during import')
                if previous is None:
                    db.execute('INSERT INTO entries VALUES(?,?,?,?)', (group_id, 'series', encoded, digest))
                    result['inserted'] += 1
                else:
                    result['unchanged'] += 1
                for identity, text in links.items():
                    if identity in parents:
                        result['unchanged_memberships'] += 1
                    else:
                        db.execute('INSERT INTO edition_series_memberships VALUES(?,?,?,?)',
                            (identity, group_id, text, hashlib.sha256(text.encode()).hexdigest()))
                        result['linked'] += 1
        return result

    def _source_with_title_parts(self, edition, cache):
        """Parse only explicit ISBD separators in reproduced RDF title fields."""
        from .ingestion.ndl import DC, DCTERMS, _value_text
        source = edition['source_record']
        proof = edition['provenance']
        key = ('ndl_nodes', proof['raw_path'], proof['raw_sha256'])
        if key not in cache:
            self._project_edition(edition, cache)
        node, index = cache[key][source['source_id']]
        originals = {title['value'] for title in source['titles']}
        parts = []
        for title_node in node.iter():
            if title_node.tag not in {f'{{{DC}}}title', f'{{{DCTERMS}}}title'}:
                continue
            value = _value_text(title_node)
            if value not in originals:
                continue
            main = re.split(r' (?:=|:) ', value, maxsplit=1)[0].strip()
            if main and main != value:
                parts.append({'value': main, 'locator': f'records[{index}]/recordData/{title_node.tag}',
                              'kind': 'rdf_title_isbd_main', 'source_value': value})
        return {**source, '_cataloguing_main_titles': parts}

    @staticmethod
    def _dre_label_assertion(assertion, record):
        """Bind a literal English imprint only to its explicit own member ISBN."""
        from .normalization import normalize_isbn
        from .light_novel_ndl_series_v1 import person_key
        source = record['source_record']
        imprint = unicodedata.normalize('NFKC',source.get('series') or '').split(';')[0].strip()
        if imprint != 'DRE NOVELS':
            return assertion, None
        isbns = {normalize_isbn(i['value']) for i in source['identifiers'] if i['source']=='isbn'} - {None}
        members = [m for m in assertion.get('explicit_book_memberships',[]) if m['isbn'] in isbns
            and {person_key(n) for n in m['authors']}=={person_key(n) for n in assertion['authors']}]
        if len(members)!=1:
            return None
        key=normalize_search(assertion['title']);member_key=normalize_search(members[0]['title'])
        suffix=member_key[len(key):] if member_key.startswith(key) else ''
        number=re.match(r'^([0-9]{1,4})(?:$|[^0-9])',suffix)
        volume=unicodedata.normalize('NFKC',source.get('volume') or '').strip()
        if not number or not volume.isdecimal() or int(number[1])!=int(volume):
            return None
        witness={'official_label':'DREノベルス','ndl_literal_imprint':'DRE NOVELS',
            'explicit_member_isbn':members[0]['isbn'],'official_member_url':members[0]['source_url'],
            'edition_scoped_only':True}
        return {**assertion,'label_aliases':['DRE NOVELS']}, witness

    def _publisher_series_match(self, assertion, edition_record, cache, *, allow_unnumbered=False):
        if assertion.get('index_id') == 'dre-novels-series-v2':
            from .light_novel_dre_dual_series_v2 import match_member, match_member_v4
            parts = self._edition_primary_bibliographic_parts(edition_record, cache)
            if not parts:
                return None
            matched = match_member(assertion, parts)
            if matched is not None:
                return matched
            own = self._authenticated_own_primary_resource(edition_record, parts, cache)
            return match_member_v4(assertion, parts, own, self._artifact, cache)
        if assertion.get('index_id') == 'dre-novels-series':
            scoped = self._dre_label_assertion(assertion, edition_record)
            if scoped is None:
                return None
            assertion, _ = scoped
        if assertion.get('index_id') == 'earthstar-explicit-work-group':
            from .light_novel_earthstar_match_v1 import match_earthstar_member
            parts = self._edition_primary_bibliographic_parts(edition_record, cache)
            return match_earthstar_member(assertion, parts) if parts else None
        if assertion.get('index_id') == 'cobalt-explicit-series':
            from .light_novel_explicit_series_match_v1 import match_cobalt_member
            parts = self._edition_primary_bibliographic_parts(edition_record, cache)
            return match_cobalt_member(assertion, parts) if parts else None
        from .light_novel_ndl_series_v1 import match_bibliography
        strict = match_bibliography(assertion, self._source_with_title_parts(edition_record, cache),
                                   normalize_search, allow_unnumbered=allow_unnumbered)
        if strict is not None:
            return strict
        return self._publisher_series_match_dated_author(assertion, edition_record, cache,
                                                        allow_unnumbered=allow_unnumbered)

    def _publisher_series_match_dated_author(self, assertion, edition_record, cache, *, allow_unnumbered=False):
        """Apply an own-record author binding without changing existing names.

        All other series gates still run through the legacy matcher, using
        direct fields of the sole own resource rather than merged metadata.
        """
        from .light_novel_explicit_series_match_v1 import dated_own_author_bindings
        from .light_novel_ndl_series_v1 import match_bibliography, person_key
        from .light_novel_reference_match_v1 import _parent
        parts = self._edition_primary_bibliographic_parts(edition_record, cache)
        if parts is None:
            return None
        source = parts['source']
        agents = parts['publisher_agents']
        publishers = [row for row in agents if row['description_values'] == []]
        distributors = [row for row in agents if row['description_values'] == ['頒布']]
        if (len(publishers) != 1 or normalize_search(publishers[0]['name']) != normalize_search(assertion['publisher'])
                or len(publishers) + len(distributors) != len(agents)
                or any(not row['name'] or not row['locator'] for row in agents)
                or len({_parent(row['locator']) for row in agents}) != 1):
            return None
        if distributors and (any(not row['structured_agent'] for row in agents)
                or any(len(row['description_locators']) != 1 or not row['description_locators'][0] for row in distributors)):
            return None
        ndc = source.get('extra', {}).get('ndc_codes', [])
        if source.get('language') not in ('ja', 'jpn') or not ndc or any(not value.startswith('913') for value in ndc):
            return None
        own = _parent(publishers[0]['locator'])
        titles = [row for row in parts['titles'] if '/{http://purl.org/dc/terms/}title[' in row['locator']]
        if not titles or any(not row['value'] or _parent(row['locator']) != own for row in titles):
            return None
        volume = parts['volume']
        if volume and (not volume['value'] or _parent(volume['locator']) != own):
            return None
        main_titles = []
        for row in titles:
            main = re.split(r' (?:=|:) ', row['value'], maxsplit=1)[0].strip()
            if normalize_search(assertion['title']) not in {normalize_search(row['value']), normalize_search(main)}:
                return None
            if main and main != row['value']:
                main_titles.append({'value': main, 'kind': 'own_primary_rdf_title_isbd_main',
                                    'locator': row['locator'], 'source_value': row['value']})
        names = list(assertion['authors'])
        if not names:
            # A mixed shop display cannot supply an author role. Only an own
            # explicit writer literal may intersect that provider's display.
            literals = {person_key(row['name']) for row in parts['literal_authors']}
            names = list(dict.fromkeys(name for name in assertion.get('creator_display_names', [])
                                      if person_key(name) in literals))
        if not names:
            return None
        bindings, contributors = [], []
        for name in names:
            direct = [row for row in source['contributors'] if row['role'] == 'author'
                and person_key(row['name']) == person_key(name) and any(
                    item['source'] == 'ndl-authority' and re.fullmatch(
                        r'https?://id\.ndl\.go\.jp/auth/(?:entity|ndlna)/[0-9]{6,12}', item['value'])
                    for item in row['identifiers'])]
            if direct:
                contributors.extend(direct)
                continue
            bound = dated_own_author_bindings(parts, name)
            if not bound:
                return None
            bindings.extend(bound)
            contributors.append({'name': name, 'role': 'author', 'identifiers': [
                {'source': 'ndl-authority', 'value': uri} for row in bound for uri in row['authority_ids']]})
        if not bindings:
            return None
        adapted = {**source, 'titles': [{'value': row['value']} for row in titles],
                   '_cataloguing_main_titles': main_titles,
                   'volume': volume['value'] if volume else None, 'contributors': contributors}
        matched = match_bibliography({**assertion, 'authors': names}, adapted, normalize_search,
                                     allow_unnumbered=allow_unnumbered)
        if matched is None:
            return None
        origin = next(({'kind': 'own_primary_dcterms_title', 'locator': row['locator']} for row in titles
                       if normalize_search(row['value']) == normalize_search(assertion['title'])), None)
        if origin is None:
            origin = {key: main_titles[0][key] for key in ('kind', 'locator')}
        return {**matched, 'rule_version': 'publisher-ndl-dated-own-author-binding-v2',
                'title_origin': origin, 'source_raw_sha256': parts['source_raw_sha256'],
                'dated_own_author_bindings': bindings,
                'own_bibliographic_locators': {'titles': [row['locator'] for row in titles],
                    'volume': volume['locator'] if volume else None,
                    'publisher': publishers[0]['locator'],
                    'distribution': [row['description_locators'][0] for row in distributors]}}

    def _ndl_membership_match(self, series, series_record, edition_record, cache):
        from .light_novel_ndl_series_v1 import publisher_assertions, match_bibliography
        proof = series_record['publisher_series_proof']
        assertion = publisher_assertions(proof, self._artifact, cache).get(proof['reference_id'])
        source = self._source_with_title_parts(edition_record, cache)
        anchor = self._source_with_title_parts(series_record['anchor_edition_record'], cache)
        match = self._publisher_series_match(assertion, edition_record, cache,
            allow_unnumbered=source['source_id'] == anchor['source_id']) if assertion else None
        anchor_match = self._publisher_series_match(assertion, series_record['anchor_edition_record'], cache,
            allow_unnumbered=True) if assertion else None
        if not match or not anchor_match:
            return None
        def ids(value):
            return {authority.rsplit('/', 1)[-1] for a in value['authors'] for authority in a['authority_ids']}
        if ids(match) != ids(anchor_match):
            return None
        if assertion.get('index_id') == 'earthstar-explicit-work-group':
            return {'rule_version': 'earthstar-explicit-ndl-workgroup-membership-v1',
                'scope': 'edition_to_publisher_work_group_only',
                'edition_id': 'lne:ndl:' + source['source_id'], 'series_entry_id': series['entry_id'],
                'matched_title': match['matched_ndl_title'], 'official_work_group_title': assertion['title'],
                'volume': match['volume'], 'volume_number': match['volume_number'],
                'author_authority_ids': sorted(ids(match)), 'imprint': match['imprint'], 'ndc_codes': match['ndc_codes'],
                'sources': [source['source_url'], assertion['source_url']], 'explicit_member_match': match,
                'story_series_identity_asserted': False, 'original_work_identity_asserted': False}
        if assertion.get('index_id') == 'dre-novels-series-v2':
            return {'rule_version': ('dre-explicit-dual-source-ndl-series-membership-v4'
                    if any(m.get('rule_version') == 'dre-dual-source-explicit-ndl-member-match-v4' for m in (match, anchor_match))
                    else 'dre-explicit-dual-source-ndl-series-membership-v3'
                    if any(m.get('rule_version') == 'dre-dual-source-explicit-ndl-member-match-v3' for m in (match, anchor_match))
                    else 'dre-explicit-dual-source-ndl-series-membership-v2'),
                'scope': 'edition_membership_only',
                'edition_id': 'lne:ndl:' + source['source_id'], 'series_entry_id': series['entry_id'],
                'matched_title': match['matched_ndl_title'], 'official_series_title': assertion['title'],
                'volume': match['volume'], 'volume_number': match['volume_number'],
                'author_authority_ids': sorted(ids(match)), 'imprint': match['imprint'], 'ndc_codes': match['ndc_codes'],
                'sources': [source['source_url'], assertion['source_url']], 'explicit_member_match': match,
                'original_work_identity_asserted': False}
        if assertion.get('index_id') == 'cobalt-explicit-series':
            return {'rule_version': 'cobalt-explicit-ndl-series-membership-v1', 'scope': 'edition_membership_only',
                'edition_id': 'lne:ndl:' + source['source_id'], 'series_entry_id': series['entry_id'],
                'matched_title': match['matched_ndl_title'], 'official_series_title': assertion['title'],
                'volume': match['volume'], 'volume_number': match['volume_number'],
                'author_authority_ids': sorted(ids(match)), 'imprint': match['imprint'], 'ndc_codes': match['ndc_codes'],
                'sources': [source['source_url'], assertion['source_url']], 'explicit_member_match': match}
        result = {'rule_version': 'publisher-ndl-series-membership-v1', 'scope': 'edition_membership_only',
                'edition_id': 'lne:ndl:' + source['source_id'], 'series_entry_id': series['entry_id'],
                'matched_title': match['title'], 'volume': match['volume'], 'volume_number': match['volume_number'],
                'author_authority_ids': sorted(ids(match)), 'imprint': match['imprint'], 'ndc_codes': match['ndc_codes'],
                'sources': [source['source_url'], assertion['source_url']],
                'field_locators': {'title': 'recordData/dcterms:title;official.series_card.title',
                    'author_authority': 'recordData/dcterms:creator/rdf:about;official.series_card.author',
                    'imprint': 'recordData/dcndl:seriesTitle;official.series_index.label',
                    'publisher': 'recordData/dcterms:publisher;official.publisher',
                    'volume': 'recordData/dcndl:volume', 'novel_class': 'recordData/subject/NDC913'}}
        if match.get('dated_own_author_bindings') or anchor_match.get('dated_own_author_bindings'):
            result.update(rule_version='publisher-ndl-series-membership-v2',
                          edition_match=match, anchor_match=anchor_match)
        return result

    def _ndl_membership_candidates(self, rows, stored, cache):
        records = {key: json.loads(value['record_json']) for key, value in stored.items()}
        series_rows = [r for r in rows if r['entry_id'].startswith('lns:ndl:')
                       and records[r['entry_id']].get('publisher_series_proof', {}).get('kind') != 'futaba_mnovels_workgroup']
        index, explicit_index, dre_v2_isbn_index = {}, {}, {}
        for series in series_rows:
            index.setdefault(normalize_search(series['title']), []).append(series)
            proof = records[series['entry_id']]['publisher_series_proof']
            if proof.get('kind') == 'dre-novels-series-v2':
                from .light_novel_ndl_series_v1 import publisher_assertions
                assertion = publisher_assertions(proof, self._artifact, cache).get(proof['reference_id'])
                if assertion and assertion.get('index_id') == 'dre-novels-series-v2':
                    for member in assertion['explicit_book_memberships']:
                        dre_v2_isbn_index.setdefault(member['isbn'], []).append(series)
            if proof.get('cobalt_series_path') or proof.get('earthstar_series_path'):
                from .light_novel_ndl_series_v1 import publisher_assertions
                from .light_novel_explicit_series_match_v1 import candidate_title_keys
                assertion = publisher_assertions(proof, self._artifact, cache).get(proof['reference_id'])
                if assertion and assertion.get('index_id') in ('cobalt-explicit-series', 'earthstar-explicit-work-group'):
                    for key in candidate_title_keys(assertion):
                        explicit_index.setdefault(key, []).append(series)
        result = {}
        for edition in rows:
            if edition['entry_kind'] != 'edition':
                continue
            record = records[edition['entry_id']]
            source = self._source_with_title_parts(record, cache)
            candidates = {s['entry_id']: s for t in [*source['titles'], *source.get('_cataloguing_main_titles', [])]
                          for s in index.get(normalize_search(t['value']), [])}
            if dre_v2_isbn_index:
                from .normalization import normalize_isbn
                candidates.update({s['entry_id']: s for identifier in source['identifiers']
                    if identifier['source'] == 'isbn'
                    for s in dre_v2_isbn_index.get(normalize_isbn(identifier['value']), [])})
            if explicit_index:
                from .light_novel_explicit_series_match_v1 import candidate_ndl_title_keys
                candidates.update({s['entry_id']: s for key in candidate_ndl_title_keys(source)
                                   for s in explicit_index.get(key, [])})
            matched = [match for series in candidates.values()
                       if (match := self._ndl_membership_match(series, records[series['entry_id']], record, cache))]
            if len(matched) == 1:
                result[edition['entry_id']] = matched[0]
        return result

    def import_ndl_series_from_publishers(self, frames_path, expected_sha256, *, source_root,
                                         sbcr_series_path=None, sbcr_series_sha256=None,
                                         label_aliases_path=None, label_aliases_sha256=None,
                                         cobalt_series_path=None, cobalt_series_sha256=None,
                                         earthstar_series_path=None, earthstar_series_sha256=None,
                                         reference_ids=None, refresh_memberships=False):
        """Admit uniquely cross-checked series; retain a stable NDL anchor ID."""
        if type(refresh_memberships) is not bool or refresh_memberships and reference_ids is None:
            raise ValueError('membership refresh requires a boolean and explicit series references')
        from .light_novel_ndl_series_v1 import publisher_assertions, match_bibliography
        cache = {}
        # Freeze the small receipt document; later collection may extend its source.
        raw_frames = self._artifact(frames_path, expected_sha256, cache)
        frozen = self.db_path.parent / 'publisher-series-sourceframes' / (expected_sha256 + '.json')
        frozen.parent.mkdir(parents=True, exist_ok=True)
        try:
            with frozen.open('xb') as handle:
                handle.write(raw_frames)
        except FileExistsError:
            self._artifact(frozen, expected_sha256, cache)
        source_proof = {'frames_path': str(frozen.resolve()), 'frames_sha256': expected_sha256,
                        'source_root': str(Path(source_root).resolve())}
        for name, path, digest in [('sbcr_series', sbcr_series_path, sbcr_series_sha256),
                                   ('label_aliases', label_aliases_path, label_aliases_sha256)]:
            if bool(path) != bool(digest):
                raise ValueError('publisher supplemental path and SHA are required together')
            if path:
                content = self._artifact(path, digest, cache)
                snapshot = frozen.parent / (digest + '.json')
                try:
                    with snapshot.open('xb') as handle:
                        handle.write(content)
                except FileExistsError:
                    self._artifact(snapshot, digest, cache)
                source_proof[name + '_path'] = str(snapshot.resolve())
                source_proof[name + '_sha256'] = digest
        if bool(cobalt_series_path) != bool(cobalt_series_sha256):
            raise ValueError('Cobalt supplemental path and SHA are required together')
        if cobalt_series_path:
            self._artifact(cobalt_series_path, cobalt_series_sha256, cache)
            # This manifest's raw paths are relative to its own source directory.
            # Preserve that hash-pinned location rather than rebasing its paths.
            source_proof.update(cobalt_series_path=str(Path(cobalt_series_path).resolve()),
                                cobalt_series_sha256=cobalt_series_sha256)
        if bool(earthstar_series_path) != bool(earthstar_series_sha256):
            raise ValueError('EarthStar supplemental path and SHA are required together')
        if earthstar_series_path:
            self._artifact(earthstar_series_path, earthstar_series_sha256, cache)
            source_proof.update(earthstar_series_path=str(Path(earthstar_series_path).resolve()),
                                earthstar_series_sha256=earthstar_series_sha256)
        assertions = publisher_assertions(source_proof, self._artifact, cache)
        with self._connect() as db:
            stored = {r['entry_id']: dict(r) for r in db.execute('SELECT * FROM entries')}
        records = {}
        for key, value in stored.items():
            if hashlib.sha256(value['record_json'].encode()).hexdigest() != value['record_sha256']:
                raise ValueError('stored metadata SHA mismatch')
            records[key] = json.loads(value['record_json'])
        existing = {record['publisher_series_proof']['reference_id']: record
                    for record in records.values() if record.get('schema_version') == 'light-novel-ndl-series-v1'}
        scoped = reference_ids is not None
        requested = set(assertions) if not scoped else set(reference_ids)
        if requested - assertions.keys():
            raise ValueError('unknown publisher series reference')
        completed = requested & existing.keys() if scoped else set()
        active = requested - completed
        matching_references = requested if refresh_memberships else active
        target_titles = {normalize_search(assertions[key]['title']) for key in matching_references}
        from .normalization import normalize_isbn
        dre_v2_isbns = {m['isbn'] for ref in matching_references
            if assertions[ref].get('index_id') == 'dre-novels-series-v2'
            for m in assertions[ref]['explicit_book_memberships']}
        explicit_titles = set()
        if cobalt_series_path or earthstar_series_path:
            from .light_novel_explicit_series_match_v1 import candidate_title_keys, candidate_ndl_title_keys
            for key in matching_references:
                if assertions[key].get('index_id') in ('cobalt-explicit-series', 'earthstar-explicit-work-group'):
                    explicit_titles.update(candidate_title_keys(assertions[key]))
        selected = set(stored) if not scoped else set()
        if scoped:
            # "Unchanged" still authenticates the explicitly requested existing
            # group and its anchor; unrelated completed groups stay unopened.
            selected.update(key for key, record in records.items()
                if record.get('schema_version') == 'light-novel-ndl-series-v1'
                and record['publisher_series_proof']['reference_id'] in completed)
            for key, record in records.items():
                if stored[key]['kind'] == 'edition' or record.get('schema_version') in {
                        'light-novel-edition-v1', 'light-novel-edition-bulk-v1'}:
                    if dre_v2_isbns & {normalize_isbn(v) for v in record.get('isbn', [])}:
                        selected.add(key)
                    titles = [row['value'] for row in record['source_record']['titles']]
                    title_keys = {normalize_search(value) for title in titles
                        for value in (title, re.split(r' (?:=|:) ', title, maxsplit=1)[0])}
                    if explicit_titles and explicit_titles & candidate_ndl_title_keys(record['source_record']):
                        selected.add(key)
                elif key.startswith('lns:wd:'):
                    title_keys = {normalize_search(value) for value in [record['title'], *record.get('aliases', [])]}
                elif record.get('schema_version') == 'light-novel-ndl-series-v1':
                    assertion = assertions.get(record['publisher_series_proof']['reference_id'])
                    title_keys = {normalize_search(assertion['title'])} if assertion else set()
                else:
                    continue
                if title_keys & target_titles:
                    selected.add(key)
        projected = {}
        for key in selected:
            row = self._project(records[key], cache)
            if row['entry_id'] != key or row['entry_kind'] != stored[key]['kind']:
                raise ValueError('stored typed identity differs')
            projected[key] = row
        rows = list(projected.values())
        editions, explicit_editions, dre_v2_editions = {}, {}, {}
        for row in rows:
            if row['entry_kind'] == 'edition':
                source = self._source_with_title_parts(records[row['entry_id']], cache)
                for identifier in source['identifiers']:
                    if identifier['source'] == 'isbn':
                        isbn = normalize_isbn(identifier['value'])
                        if isbn in dre_v2_isbns:
                            dre_v2_editions.setdefault(isbn, {})[row['entry_id']] = records[row['entry_id']]
                for title in [*source['titles'], *source.get('_cataloguing_main_titles', [])]:
                    editions.setdefault(normalize_search(title['value']), {})[row['entry_id']] = records[row['entry_id']]
                if explicit_titles:
                    for title_key in candidate_ndl_title_keys(source):
                        explicit_editions.setdefault(title_key, {})[row['entry_id']] = records[row['entry_id']]
        wd_titles = {normalize_search(title) for row in rows if row['entry_kind'] == 'series' and row['qid']
                     for title in [row['title'], *row['aliases']]}
        result = {'inserted': 0, 'updated': 0, 'unchanged': len(completed), 'held': 0, 'held_reasons': {}, 'failures': [],
                  'requested_series_references': len(requested), 'proposal_references': len(active),
                  'candidate_entries_verified': len(selected)}
        def hold(reason):
            result['held'] += 1
            result['held_reasons'][reason] = result['held_reasons'].get(reason, 0) + 1
        proposals = []
        for reference, assertion in assertions.items():
            if reference not in active:
                continue
            key = normalize_search(assertion['title'])
            if key in wd_titles:
                hold('potential_wikidata_series_overlap_requires_authority_review')
                continue
            qualified = []
            if assertion.get('index_id') == 'dre-novels-series-v2':
                matching_editions = {}
                for member in assertion['explicit_book_memberships']:
                    matching_editions.update(dre_v2_editions.get(member['isbn'], {}))
                candidates = matching_editions.values()
            elif assertion.get('index_id') in ('cobalt-explicit-series', 'earthstar-explicit-work-group'):
                matching_editions = {}
                for title_key in candidate_title_keys(assertion):
                    matching_editions.update(explicit_editions.get(title_key, {}))
                candidates = matching_editions.values()
            else:
                candidates = editions.get(key, {}).values()
            for edition in candidates:
                match = self._publisher_series_match(assertion, edition, cache, allow_unnumbered=True)
                if match:
                    qualified.append((edition, match))
            if not qualified:
                hold('no_qualified_ndl_anchor')
                continue
            author_groups = {tuple(sorted({value.rsplit('/', 1)[-1] for a in match['authors'] for value in a['authority_ids']})) for _, match in qualified}
            if len(author_groups) != 1:
                hold('ambiguous_ndl_author_authorities')
                continue
            previous = existing.get(reference)
            anchor = previous['anchor_edition_record'] if previous else min(qualified, key=lambda pair: (
                pair[1]['volume_number'] is None, pair[1]['volume_number'] or 0,
                pair[0]['source_record']['publication_year'] or 9999, pair[0]['id']))[0]
            if assertion.get('index_id') == 'dre-novels-series-v2':
                from .light_novel_dre_dual_series_v2 import admission_conflict
                with self._connect() as db:
                    existing_memberships = db.execute('SELECT edition_id,series_id FROM edition_series_memberships').fetchall()
                conflict = admission_conflict(assertion, records, anchor['id'], existing_memberships)
                if conflict:
                    hold(conflict)
                    continue
            if any(record['id'] == anchor['id'] and owner != reference for owner, record in existing.items()):
                hold('ambiguous_publisher_series_for_same_ndl_anchor')
                continue
            proposals.append({'schema_version': 'light-novel-ndl-series-v1', 'source': 'ndl', 'id': anchor['id'],
                              'anchor_edition_record': anchor, 'publisher_series_proof': {**source_proof, 'reference_id': reference}})
            if assertion.get('index_id') in ('cobalt-explicit-series', 'earthstar-explicit-work-group', 'dre-novels-series-v2'):
                proposals[-1]['title'] = assertion['title']
            if assertion.get('index_id') == 'dre-novels-series-v2':
                proposals[-1]['publisher_series_proof']['kind'] = 'dre-novels-series-v2'
        anchors = {}
        for proposal in proposals:
            anchors.setdefault(proposal['id'], []).append(proposal)
        admitted = []
        for group in anchors.values():
            if len(group) != 1:
                for _ in group:
                    hold('ambiguous_publisher_series_for_same_ndl_anchor')
                continue
            admitted.extend(group)
        added_rows = {}
        imported = self.ingest(admitted, _cache=cache, _projected=added_rows) if admitted else {
            'inserted': 0, 'updated': 0, 'unchanged': 0, 'failed': 0, 'failures': []}
        for key in ('inserted', 'updated', 'unchanged'):
            result[key] += imported[key]
        result['failures'].extend(imported['failures'])
        for _ in range(imported['failed']):
            hold('projection_validation_failed')
        # Persist edition membership separately; it never creates an original work.
        for record in admitted:
            key = 'lns:ndl:' + record['id']
            if key not in added_rows:
                continue
            encoded = _json(record)
            stored[key] = {'entry_id': key, 'kind': 'series', 'record_json': encoded,
                           'record_sha256': hashlib.sha256(encoded.encode()).hexdigest()}
            projected[key] = added_rows[key]
        membership_stored = {key: stored[key] for key in projected}
        memberships = self._ndl_membership_candidates(list(projected.values()), membership_stored, cache)
        new_memberships = unchanged_memberships = conflicts = 0
        with self._connect() as db:
            for edition_id, membership in memberships.items():
                series_id = membership['series_entry_id']
                proof = {'kind': 'publisher_ndl_series_membership', 'edition_record_sha256': stored[edition_id]['record_sha256'],
                         'series_record_sha256': stored[series_id]['record_sha256'], 'membership': membership}
                encoded = _json(proof)
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                previous = db.execute('SELECT proof_sha256 FROM edition_series_memberships WHERE edition_id=?', (edition_id,)).fetchone()
                if previous and previous['proof_sha256'] == digest:
                    unchanged_memberships += 1
                    continue
                if scoped and previous:
                    conflicts += 1
                    hold('existing_edition_membership_conflict')
                    continue
                db.execute('INSERT OR REPLACE INTO edition_series_memberships VALUES(?,?,?,?)',
                           (edition_id, series_id, encoded, digest))
                new_memberships += 1
        result['verified_edition_memberships'] = len(memberships) - conflicts
        result['new_edition_memberships'] = new_memberships
        result['unchanged_edition_memberships'] = unchanged_memberships
        return result

    def _author_authorities(self, manifest_path, expected_sha256, cache):
        key = ('author_authorities', str(_resolved_path(manifest_path, cache)), expected_sha256)
        if key in cache:
            return cache[key]
        manifest = self._payload(manifest_path, expected_sha256, cache)
        if manifest.get('version') != 'light-novel-author-authorities-v1':
            raise ValueError('unsupported author authority manifest')
        result = {}
        for row in manifest['authors']:
            qid = _qid(row['author_qid'])
            if qid in result:
                raise ValueError('duplicate author authority identity')
            entity = self._payload(row['raw_path'], row['raw_sha256'], cache).get('entities', {}).get(qid, {})
            if entity.get('id') != qid or 'missing' in entity:
                raise ValueError('author authority raw identity differs')
            ids = set()
            for statement in entity.get('claims', {}).get('P349', []):
                snak = statement.get('mainsnak', {})
                value = snak.get('datavalue', {}).get('value')
                if (statement.get('rank') != 'deprecated' and snak.get('snaktype') == 'value'
                        and isinstance(value, str) and re.fullmatch(r'[0-9]{6,12}', value)):
                    ids.add(value)
            result[qid] = ids
        cache[key] = result
        return result

    @staticmethod
    def _publisher_books(provider, manifest_path, expected_sha256):
        if provider == 'kadokawa':
            from .light_novel_publisher_books_v1 import verify_kadokawa_books
            return verify_kadokawa_books(manifest_path, expected_sha256)
        if provider == 'shueisha-dx':
            from .light_novel_shueisha_books_v1 import verify_shueisha_books
            return verify_shueisha_books(manifest_path, expected_sha256)
        if provider == 'ichijinsha-iris':
            from .light_novel_ichijinsha_books_v1 import verify_ichijinsha_books
            return verify_ichijinsha_books(manifest_path, expected_sha256)
        if provider == 'hifumi':
            from .light_novel_hifumi_details_v1 import verify_hifumi_product_details
            return verify_hifumi_product_details(manifest_path, expected_sha256)
        if provider in {'tobooks', 'futaba'}:
            from .light_novel_publisher_references_v1 import verify_publisher_references
            return [{**row, 'publisher_book_id': row['publisher_reference_id']}
                    for row in verify_publisher_references(provider, manifest_path, expected_sha256)]
        raise ValueError('unknown bibliographic publisher provider')

    def _publisher_edition_match(self, provider, publisher, edition, cache):
        if provider in {'hifumi', 'futaba'}:
            parts = self._edition_primary_bibliographic_parts(edition, cache)
            if parts is None:
                return None
            if provider == 'hifumi':
                from .light_novel_hifumi_details_v1 import match_hifumi_product_detail
                return match_hifumi_product_detail(publisher, parts)
            from .light_novel_futaba_match_v1 import match_futaba_reference
            return match_futaba_reference(publisher, parts)
        if provider == 'tobooks':
            from .normalization import normalize_isbn
            from .light_novel_reference_match_v1 import match_tobooks_edition
            isbn = normalize_isbn(str(publisher.get('isbn') or ''))
            if not isbn:
                return None
            parts = self._edition_primary_bibliographic_parts(edition, cache)
            if parts is None:
                return None
            return match_tobooks_edition(publisher, parts,
                same_isbn_parts=self._reference_same_isbn_parts(isbn, cache))
        # Keep every previously accepted v1 proof byte-for-byte reproducible.
        match = self._publisher_edition_match_v1(provider, publisher, edition, cache)
        if match is not None:
            return match
        if provider == 'shueisha-dx':
            from .light_novel_shueisha_match_v1 import _LABELS, match_shueisha_bibliographic_edition
            if publisher.get('label') not in _LABELS:
                return None
            parts = self._edition_primary_bibliographic_parts(edition, cache)
            return match_shueisha_bibliographic_edition(publisher, parts) if parts is not None else None
        match = self._publisher_edition_match_v2(provider, publisher, edition, cache)
        if match is not None:
            return match
        if provider == 'kadokawa':
            match = self._publisher_edition_match_v2(provider, publisher, edition, cache, _part_information=True)
            return match if match is not None else self._publisher_edition_match_v2(
                provider, publisher, edition, cache, _publisher_context=True)
        match = self._publisher_edition_match_iris_neo(provider, publisher, edition, cache)
        return match if match is not None else self._publisher_edition_match_iris_neo(provider, publisher, edition, cache, _extended=True)

    def _reference_same_isbn_parts(self, isbn, cache):
        """Authenticate every same-ISBN own record, including outside a search.

        Metadata only selects candidates. Each selected stored record must still
        reproduce its own raw fields and typed identity within this operation.
        """
        from .normalization import normalize_isbn
        index_key = ('reference_edition_isbn_index',)
        if index_key not in cache:
            stored = cache.get(('reference_all_stored',))
            if stored is None:
                with self._connect() as db:
                    stored = db.execute('SELECT * FROM entries').fetchall()
            parsed = cache.get(('reference_all_records',), {})
            index = {}
            for entry in stored:
                record = parsed.get(entry['entry_id'])
                if record is None:
                    record = json.loads(entry['record_json'])
                if (entry['kind'] != 'edition' and record.get('schema_version') not in {
                        'light-novel-edition-v1', 'light-novel-edition-bulk-v1'}):
                    continue
                for identifier in record['source_record']['identifiers']:
                    value = normalize_isbn(identifier['value']) if identifier['source'] == 'isbn' else None
                    if value:
                        index.setdefault(value, {})[entry['entry_id']] = (entry, record)
            cache[index_key] = index
        parts_key = ('reference_same_isbn_parts', isbn)
        if parts_key not in cache:
            parts = []
            for entry, record in cache[index_key].get(isbn, {}).values():
                if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                    raise ValueError('stored metadata SHA mismatch')
                projected = self._project_edition(record, cache)
                if projected['entry_id'] != entry['entry_id'] or projected['entry_kind'] != entry['kind']:
                    raise ValueError('stored typed identity differs')
                primary = self._edition_primary_bibliographic_parts(record, cache)
                if primary is None:
                    # An ambiguous same-ISBN record invalidates confirmation;
                    # do not silently omit it from the candidate population.
                    parts.append(None)
                else:
                    parts.append(primary)
            cache[parts_key] = parts
        return cache[parts_key]

    def _publisher_edition_match_v1(self, provider, publisher, edition, cache):
        from .normalization import normalize_isbn
        from .light_novel_ndl_series_v1 import person_key
        from .light_novel_publisher_books_v1 import KADOKAWA_LABELS
        scopes = {'kadokawa': set(KADOKAWA_LABELS.values()),
                  'shueisha-dx': {'ダッシュエックス文庫', 'スーパーダッシュ文庫', 'ダッシュエックス文庫DIGITAL',
                                  'Dノベルf／ダッシュエックスノベルエフ', 'Dノベル／ダッシュエックスノベル'},
                  'ichijinsha-iris': {'一迅社文庫アイリス', 'アイリスNEO', '一迅社文庫アイリスNEO'}}
        if publisher.get('label') not in scopes.get(provider, set()):
            return None
        source = self._source_with_title_parts(edition, cache)
        if (source['language'] not in {'ja', 'jpn'} or not source.get('publisher')
                or not any(code.startswith('913') for code in source.get('extra', {}).get('ndc_codes', []))):
            return None
        isbn = normalize_isbn(str(publisher.get('isbn') or ''))
        if not isbn or isbn not in {normalize_isbn(row['value']) for row in source['identifiers'] if row['source'] == 'isbn'}:
            return None
        def title_key(value):
            return ''.join(c for c in normalize_search(value) if not unicodedata.category(c).startswith('P'))
        ndl_titles = [*source['titles'], *source.get('_cataloguing_main_titles', [])]
        matched_title = next((row['value'] for row in ndl_titles if title_key(row['value']) == title_key(publisher['title'])), None)
        if matched_title is None:
            return None
        imprint = unicodedata.normalize('NFKC', source.get('series') or '').split(';')[0].strip()
        official_labels = [publisher['label']]
        if provider == 'shueisha-dx' and '／' in publisher['label']:
            official_labels += publisher['label'].split('／')
        if not {normalize_search(label) for label in official_labels} & {normalize_search(value) for value in imprint.split(' = ')}:
            return None
        official_authors = [credit['name'] for credit in publisher.get('credits', [])
                            if credit.get('role') in {'author', '著', '著者', '原作', '著者・イラスト', '著・イラスト'}]
        if not official_authors:
            return None
        matched_authors = []
        for name in official_authors:
            candidates = [author for author in source['contributors']
                          if author['role'] == 'author' and person_key(author['name']) == person_key(name)]
            authorities = sorted({identifier['value'] for author in candidates for identifier in author['identifiers']
                if identifier['source'] == 'ndl-authority' and re.fullmatch(r'https?://id\.ndl\.go\.jp/auth/(?:entity|ndlna)/[0-9]{6,12}', identifier['value'])})
            if not authorities:
                return None
            matched_authors.append({'official_credit': name, 'ndl_authorities': authorities})
        return {'rule_version': 'publisher-ndl-edition-confirmation-v1', 'scope': 'edition_only',
                'isbn13': isbn, 'matched_ndl_title': matched_title, 'authors': matched_authors,
                'ndl_imprint': imprint, 'official_label': publisher['label'],
                'ndl_publisher': source['publisher'], 'original_work_identity_asserted': False,
                'story_series_identity_asserted': False,
                'sources': [source['source_url'], publisher['source_url']]}

    def _publisher_edition_match_iris_neo(self, provider, publisher, edition, cache, *, _extended=False):
        """An edition-bound label witness, never a series alias or identity."""
        from .normalization import normalize_isbn
        from .light_novel_ndl_series_v1 import person_key
        if (provider != 'ichijinsha-iris' or publisher.get('label') != 'アイリスNEO'
                or publisher.get('publisher') != '一迅社'):
            return None
        parts = self._edition_primary_bibliographic_parts(edition, cache)
        if parts is None:
            return None
        source = parts['source']
        imprint = unicodedata.normalize('NFKC', source.get('series') or '').split(';')[0].strip()
        publisher_match = {'rule': 'single_exact_publisher', 'publisher': '一迅社'}
        agents = parts['publisher_agents']
        if (source.get('publisher') != '一迅社' or _extended and
                (len(agents) != 1 or any(row['description_values'] for row in agents))):
            if not _extended:
                return None
            if not agents or not all(row['structured_agent'] and row['name'] for row in agents):
                return None
            publishers = [row for row in agents if row['description_values'] == []]
            distributors = [row for row in agents if row['description_values'] == ['頒布']]
            if (len(publishers) != 1 or publishers[0]['name'] != '一迅社'
                    or len(publishers) + len(distributors) != len(agents) or not distributors):
                return None
            publisher_match = {'rule': 'unique_publisher_and_explicit_distribution_agents',
                'publisher_agent': {'name': publishers[0]['name'], 'locator': publishers[0]['locator']},
                'distribution_agents': [{'name': row['name'], 'description': '頒布', 'locator': row['locator'],
                    'description_locator': row['description_locators'][0]} for row in distributors]}
        if (imprint != 'IRIS NEO' or source['language'] not in {'ja', 'jpn'}
                or not any(code.startswith('913') for code in source['extra']['ndc_codes'])):
            return None
        isbn = normalize_isbn(str(publisher.get('isbn') or ''))
        if not isbn or isbn not in {normalize_isbn(value['value']) for value in source['identifiers'] if value['source'] == 'isbn'}:
            return None
        def title_key(value):
            return ''.join(c for c in normalize_search(value) if not unicodedata.category(c).startswith('P'))
        # dcterms:title is the own complete display title, including its volume.
        # Do not substitute the dc:title main field, contents or omitted words.
        title = next((row for row in parts['titles']
            if '/{http://purl.org/dc/terms/}title[' in row['locator']
            and title_key(row['value']) == title_key(publisher['title'])), None)
        title_match = {'rule': 'direct_own_complete_title'}
        if title is None and _extended:
            volume = parts['volume']['value'] if parts['volume'] else None
            def extended_key(value):
                return ''.join(c for c in normalize_search(value).replace('〜', '~')
                               if not unicodedata.category(c).startswith('P'))
            for row in parts['titles']:
                if '/{http://purl.org/dc/terms/}title[' not in row['locator']:
                    continue
                value = row['value']
                if volume and value.endswith('. ' + volume):
                    value = value[:-len('. ' + volume)]
                split = value.split(' : ', 1)
                main, subtitle = split[0], split[1] if len(split) == 2 else None
                if not main or subtitle == '':
                    continue
                variants = [main + (volume or '') + (subtitle or ''), main + (subtitle or '') + (volume or '')]
                if subtitle:
                    variants += [main + (volume or '') + '~' + subtitle + '~', main + '~' + subtitle + '~' + (volume or '')]
                if any(extended_key(candidate) == extended_key(publisher['title']) for candidate in variants):
                    title = row
                    title_match = {'rule': 'own_complete_title_all_parts_v1', 'source_value': row['value'],
                        'main': main, 'subtitle': subtitle, 'volume': volume,
                        'locators': [row['locator']] + ([parts['volume']['locator']] if volume else [])}
                    break
        if title is None:
            return None
        writers = [credit['name'] for credit in publisher.get('credits', [])
            if credit.get('role') in {'author', '著', '著者', '著者・イラスト', '著・イラスト'}]
        if not writers:
            return None
        authors = []
        for name in writers:
            authorities = sorted({identifier['value'] for author in source['contributors']
                if author['role'] == 'author' and person_key(author['name']) == person_key(name)
                for identifier in author['identifiers'] if identifier['source'] == 'ndl-authority'
                and re.fullmatch(r'https?://id\.ndl\.go\.jp/auth/(?:entity|ndlna)/[0-9]{6,12}', identifier['value'])})
            if not authorities:
                return None
            authors.append({'official_credit': name, 'ndl_authorities': authorities})
        proof = {'rule_version': 'publisher-ndl-edition-confirmation-iris-neo-v1', 'scope': 'edition_only',
            'isbn13': isbn, 'matched_ndl_title': title['value'], 'primary_title_locator': title['locator'],
            'source_raw_sha256': parts['source_raw_sha256'], 'authors': authors,
            'ndl_imprint': imprint, 'official_label': publisher['label'], 'ndl_publisher': source['publisher'],
            'imprint_match': {'rule': 'same_edition_ichijinsha_iris_neo_pair', 'official_label': publisher['label'],
                'ndl_imprint': imprint, 'scope': 'this_isbn_edition_only'},
            'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
            'sources': [source['source_url'], publisher['source_url']]}
        if _extended:
            proof.update(rule_version='publisher-ndl-edition-confirmation-iris-neo-v2',
                         publisher_match=publisher_match, title_match=title_match)
        return proof

    def _edition_primary_bibliographic_parts(self, edition, cache):
        """Read direct fields of this edition's sole own title-bearing resource.

        The outer record has already passed its original provider/raw replay.
        Contents, related records, readings and holding resources supply no
        primary title variants or literal writer assertions. Optional own
        partInformation witnesses remain separate for a bounded KAD path.
        """
        from .ingestion.ndl import DC, DCTERMS, DCNDL, RDF, FOAF, _value_text, _contributor
        source = self._source_with_title_parts(edition, cache)
        provenance = edition['provenance']
        node, _ = cache[('ndl_nodes', provenance['raw_path'], provenance['raw_sha256'])][source['source_id']]
        resources = [value for value in node.iter(f'{{{DCNDL}}}BibResource')
            if value.get(f'{{{RDF}}}about', '').split('#')[0] == source['source_url']
            and any(child.tag in {f'{{{DC}}}title', f'{{{DCTERMS}}}title'} for child in value)]
        if len(resources) != 1:
            return None
        primary = resources[0]
        base = source['extra']['raw_locator'] + '/dcndl:BibResource[@rdf:about=' + primary.get(f'{{{RDF}}}about') + ']'
        titles, volumes, literal_authors, contributors = [], [], [], []
        edition_statements = []
        identifiers, publishers, languages, imprints, ndc = [], [], [], [], []
        publisher_agents = []
        own_part_information_titles, part_information_count = [], 0
        part_information_valid = True
        for position, child in enumerate(primary):
            locator = base + '/' + child.tag + f'[{position + 1}]'
            value = _value_text(child)
            if child.tag in {f'{{{DC}}}title', f'{{{DCTERMS}}}title'} and value:
                titles.append({'value': value, 'locator': locator})
            elif child.tag == f'{{{DCNDL}}}volume' and value:
                volumes.append({'value': value, 'locator': locator})
            elif child.tag == f'{{{DCNDL}}}edition' and value:
                edition_statements.append({'value': value, 'locator': locator})
            elif child.tag == f'{{{DCNDL}}}partInformation':
                part_information_count += 1
                if (child.attrib or len(child) != 1 or child[0].tag != f'{{{RDF}}}Description'
                        or child[0].attrib or len(child[0]) != 1
                        or child[0][0].tag != f'{{{DCTERMS}}}title'
                        or child[0][0].attrib or len(child[0][0]) != 0 or not _value_text(child[0][0])
                        or any((value or '').strip() for value in
                            (child.text, child[0].text, child[0].tail, child[0][0].tail))):
                    part_information_valid = False
                    continue
                description_locator = locator + f'/{{{RDF}}}Description[1]'
                own_part_information_titles.append({'value': _value_text(child[0][0]),
                    'part_information_locator': locator, 'description_locator': description_locator,
                    'locator': description_locator + f'/{{{DCTERMS}}}title[1]'})
            elif child.tag in {f'{{{DC}}}creator', f'{{{DCTERMS}}}creator'} and value:
                # A default creator role is insufficient when no authority exists.
                marked = re.fullmatch(r'(.+?)\s*[\[（(]?\s*(著者|著|原作|作)\s*[\]）)]?', value)
                contributor = _contributor(child)
                if contributor:
                    contributors.append(contributor.model_dump(mode='json'))
                if marked and contributor and contributor.role == 'author':
                    literal_authors.append({'name': contributor.name, 'literal': value, 'locator': locator})
            elif child.tag == f'{{{DCTERMS}}}identifier' and child.get(f'{{{RDF}}}datatype', '').rsplit('/', 1)[-1] == 'ISBN':
                identifiers.append({'source': 'isbn', 'value': value})
            elif child.tag == f'{{{DCTERMS}}}publisher':
                if value:
                    publishers.append(value)
                agents = child.findall(f'{{{FOAF}}}Agent')
                descriptions = [(index, element) for index, element in enumerate(agents[0], 1)
                    if element.tag == f'{{{DCTERMS}}}description'] if len(agents) == 1 else []
                publisher_agents.append({'name': value, 'locator': locator,
                    'structured_agent': len(agents) == 1 and len(child) == 1,
                    'description_values': [_value_text(element) for _, element in descriptions],
                    'description_locators': [locator + f'/{{{FOAF}}}Agent[1]/{{{DCTERMS}}}description[{index}]'
                        for index, _ in descriptions]})
            elif child.tag == f'{{{DCTERMS}}}language' and value:
                languages.append(value)
            elif child.tag == f'{{{DCNDL}}}seriesTitle' and value:
                imprints.append(value)
            elif child.tag == f'{{{DCTERMS}}}subject':
                resource = child.get(f'{{{RDF}}}resource', '')
                if re.fullmatch(r'https?://id\.ndl\.go\.jp/class/ndc[0-9]*/[0-9.]+', resource):
                    ndc.append(resource.rsplit('/', 1)[-1])
        if len({row['value'] for row in volumes}) > 1:
            return None
        direct_source = {**source, 'contributors': contributors, 'identifiers': identifiers,
            'publisher': '; '.join(dict.fromkeys(publishers)) or None,
            'language': languages[0] if len(set(languages)) == 1 else None,
            'series': '; '.join(dict.fromkeys(imprints)) or None,
            'extra': {**source['extra'], 'ndc_codes': ndc}}
        parts = {'source': direct_source, 'titles': titles, 'volume': volumes[0] if volumes else None,
                'literal_authors': literal_authors, 'publisher_agents': publisher_agents,
                'edition_statements': edition_statements,
                'source_raw_sha256': provenance['raw_sha256']}
        if part_information_count:
            parts.update(own_part_information_titles=own_part_information_titles,
                own_part_information_count=part_information_count,
                own_part_information_structure_valid=part_information_valid,
                own_volume_count=len(volumes))
        return parts

    def _authenticated_own_primary_resource(self, edition, parts, cache):
        """Select an own node already authenticated into this operation's cache."""
        from .ingestion.ndl import DC, DCTERMS, DCNDL, RDF
        source, provenance = parts['source'], edition['provenance']
        node, _ = cache[('ndl_nodes', provenance['raw_path'], provenance['raw_sha256'])][source['source_id']]
        resources = [value for value in node.iter('{' + DCNDL + '}BibResource')
            if value.get('{' + RDF + '}about', '').split('#')[0] == source['source_url']
            and any(child.tag in {'{' + DC + '}title', '{' + DCTERMS + '}title'} for child in value)]
        return resources[0] if len(resources) == 1 else None

    def _publisher_edition_match_v2(self, provider, publisher, edition, cache, *, _part_information=False, _publisher_context=False):
        from .normalization import normalize_isbn
        from .light_novel_ndl_series_v1 import person_key
        from .light_novel_publisher_books_v1 import KADOKAWA_LABELS
        # This extension is backed by the finite KADOKAWA book frame only.
        product_form = publisher.get('product_form_id')
        paper_book = product_form == 1 and publisher.get('official_product_form_name') == '単行本'
        if ((_part_information and _publisher_context) or provider != 'kadokawa'
                or type(product_form) is not int or (product_form != 3 and not paper_book)
                or publisher.get('label') not in KADOKAWA_LABELS.values()):
            return None
        parts = self._edition_primary_bibliographic_parts(edition, cache)
        if parts is None:
            return None
        source = parts['source']
        publisher_context = None
        if _publisher_context:
            from .light_novel_kadokawa_publisher_context_v1 import match_publisher_context
            publisher_context = match_publisher_context(publisher, parts)
            if publisher_context is None:
                return None
        if (source['language'] not in {'ja', 'jpn'}
                or (not _publisher_context and (source.get('publisher') != 'KADOKAWA' or publisher.get('publisher') != 'KADOKAWA'))
                or not any(code.startswith('913') for code in source.get('extra', {}).get('ndc_codes', []))):
            return None
        isbn = normalize_isbn(str(publisher.get('isbn') or ''))
        if not isbn or isbn not in {normalize_isbn(row['value']) for row in source['identifiers'] if row['source'] == 'isbn'}:
            return None

        def title_key(value):
            return ''.join(c for c in normalize_search(value).replace('〜', '~')
                           if not unicodedata.category(c).startswith('P'))

        volume = parts['volume']['value'] if parts['volume'] else None
        candidates = []
        reverse_candidates = []
        for title in parts['titles']:
            raw_title = title['value']
            candidates.append((raw_title, {'rule': 'direct_primary_title', 'source_value': raw_title,
                'main': raw_title, 'subtitle': None, 'volume': volume, 'locators': [title['locator']]}))
            value = raw_title
            if volume and value.endswith('. ' + volume):
                value = value[:-len('. ' + volume)]
            split = value.split(' : ', 1)
            main, subtitle = split[0], split[1] if len(split) == 2 else None
            if not main or subtitle == '':
                continue
            locators = [title['locator']] + ([parts['volume']['locator']] if volume else [])
            variants = [main + (volume or '') + (subtitle or ''), main + (subtitle or '') + (volume or '')]
            if subtitle:
                variants += [main + (volume or '') + '~' + subtitle + '~',
                             main + '~' + subtitle + '~' + (volume or '')]
            candidates.extend((variant, {'rule': 'primary_isbd_title_and_explicit_volume', 'source_value': raw_title,
                'main': main, 'subtitle': subtitle, 'volume': volume, 'locators': locators}) for variant in variants)
            if subtitle:
                reverse_candidates.extend((variant, {
                    'rule': 'primary_isbd_reverse_title_and_explicit_volume_v1', 'source_value': raw_title,
                    'main': main, 'subtitle': subtitle, 'volume': volume, 'locators': locators})
                    for variant in (subtitle + main + (volume or ''), subtitle + (volume or '') + main))
        # Append after ALL old candidates so every v2 success selects its old proof.
        candidates.extend(reverse_candidates)
        title_match = next((proof for value, proof in candidates if title_key(value) == title_key(publisher['title'])), None)
        if _part_information:
            from .light_novel_kadokawa_parts_match_v1 import match_unique_own_part_information_title
            title_match = match_unique_own_part_information_title(parts, publisher['title'])
        if title_match is None:
            return None
        if _publisher_context:
            from .light_novel_kadokawa_publisher_context_v1 import title_uses_complete_own_dct
            if not title_uses_complete_own_dct(parts, title_match, publisher_context):
                return None
        imprint = unicodedata.normalize('NFKC', source.get('series') or '').split(';')[0].strip()
        exact_imprint = normalize_search(publisher['label']) in {normalize_search(v) for v in imprint.split(' = ')}
        if exact_imprint:
            imprint_match = {'rule': 'exact_label', 'ndl_imprint': imprint, 'official_label': publisher['label']}
        elif (publisher.get('official_label_id') == 344 and publisher['label'] == 'ファンタジア文庫'
              and imprint == '富士見ファンタジア文庫'
              and (_publisher_context or (source['publisher'] == 'KADOKAWA' and publisher.get('publisher') == 'KADOKAWA'))):
            imprint_match = {'rule': 'same_edition_kadokawa_fantasia_344_pair', 'official_label_id': 344,
                'ndl_imprint': imprint, 'official_label': publisher['label'], 'scope': 'this_isbn_edition_only'}
            if _publisher_context:
                imprint_match.update(rule='same_isbn_edition_fantasia_344_pair_with_assessed_catalogue_owner_context',
                    global_alias_asserted=False, label_history_map_asserted=False)
        else:
            return None
        official_authors = [credit['name'] for credit in publisher.get('credits', [])
            if credit.get('role') in {'author', '著', '著者', '原作', '著者・イラスト', '著・イラスト'}]
        if not official_authors:
            return None
        matched_authors = []
        for name in official_authors:
            contributors = [author for author in source['contributors']
                if author['role'] == 'author' and person_key(author['name']) == person_key(name)]
            authorities = sorted({identifier['value'] for author in contributors for identifier in author['identifiers']
                if identifier['source'] == 'ndl-authority' and re.fullmatch(r'https?://id\.ndl\.go\.jp/auth/(?:entity|ndlna)/[0-9]{6,12}', identifier['value'])})
            literal = next((author for author in parts['literal_authors'] if person_key(author['name']) == person_key(name)), None)
            if not authorities and (not contributors or literal is None):
                return None
            author_match = {'official_credit': name, 'ndl_authorities': authorities,
                'authority_status': 'known' if authorities else 'unknown'}
            if literal:
                author_match.update(ndl_literal_credit=literal['literal'], locator=literal['locator'])
            matched_authors.append(author_match)
        version = 'v5' if _publisher_context else 'v4' if _part_information else 'v3' if title_match['rule'] == 'primary_isbd_reverse_title_and_explicit_volume_v1' else 'v2'
        if paper_book:
            version = 'v6'
        proof = {'rule_version': 'publisher-ndl-edition-confirmation-' + version, 'scope': 'edition_only',
                'isbn13': isbn, 'matched_ndl_title': title_match['source_value'], 'title_match': title_match,
                'authors': matched_authors, 'ndl_imprint': imprint, 'official_label': publisher['label'],
                'imprint_match': imprint_match, 'ndl_publisher': source['publisher'],
                'official_product_form_id': product_form,
                'source_raw_sha256': parts['source_raw_sha256'],
                'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
                'sources': [source['source_url'], publisher['source_url']]}
        if paper_book:
            evidence = publisher.get('evidence', {})
            if (evidence.get('source_url') != 'https://www.kadokawa.co.jp/product/search/'
                    or not re.fullmatch(r'[0-9a-f]{64}', evidence.get('source_sha256', ''))):
                return None
            proof.update(official_product_form_name='単行本', product_form_evidence={
                'source_url': evidence['source_url'], 'source_sha256': evidence['source_sha256'],
                'json_pointer': '/product_form/1/name'})
        if _part_information:
            proof.update(confirmed_scope='contained_bibliographic_edition_only',
                part_title_is_volume_title_asserted=False, product_sku_identity_asserted=False)
        if _publisher_context:
            proof.update(confirmed_scope='contained_bibliographic_edition_only', publisher_context_match=publisher_context,
                publisher_identity_asserted=False, global_publisher_alias_asserted=False,
                global_label_alias_asserted=False, label_history_map_asserted=False, product_sku_identity_asserted=False)
        return proof

    def confirm_publisher_editions(self, provider, manifest_path, expected_sha256, *, reference_ids=None):
        """Confirm existing NDL editions, without making a work or series."""
        from .normalization import normalize_isbn
        books = self._publisher_books(provider, manifest_path, expected_sha256)
        if reference_ids is not None:
            indexed = {book['publisher_book_id']: book for book in books}
            if len(indexed) != len(books):
                raise ValueError('duplicate publisher book reference')
            selected = set(reference_ids)
            if selected - indexed.keys():
                raise ValueError('unknown publisher book reference')
            available_count = len(books)
            books = [book for book in books if book['publisher_book_id'] in selected]
        cache = {}
        with self._connect() as db:
            stored = {row['entry_id']: dict(row) for row in db.execute('SELECT * FROM entries WHERE kind=?', ('edition',))}
        if provider == 'tobooks':
            # The stored kind is itself a claim to verify; it must not hide
            # a same-ISBN edition record from this provider's integrity scope.
            with self._connect() as db:
                cache[('reference_all_stored',)] = db.execute('SELECT * FROM entries').fetchall()
        by_isbn = {}
        for book in books:
            isbn = normalize_isbn(str(book.get('isbn') or ''))
            if isbn:
                by_isbn.setdefault(isbn, {})[book['publisher_book_id']] = book
        result = {'publisher_books': len(books), 'publisher_books_with_valid_isbn': sum(len(v) for v in by_isbn.values()),
                  'ndl_editions_considered': len(stored), 'ndl_candidate_editions_verified': 0,
                  'verification_scope': 'matching_isbn_candidates_only', 'confirmed': 0, 'unchanged': 0}
        if reference_ids is not None:
            result.update(available_publisher_books=available_count, requested_publisher_books=len(books))
        matched = set()
        with self._connect() as db:
            for entry in stored.values():
                edition = json.loads(entry['record_json'])
                candidates = {}
                for identifier in edition['source_record']['identifiers']:
                    if identifier['source'] == 'isbn':
                        candidates.update(by_isbn.get(normalize_isbn(identifier['value']), {}))
                if not candidates:
                    continue
                if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                    raise ValueError('stored metadata SHA mismatch')
                # Always validate EACH candidate, including a second record in
                # an already cached raw response. Cached nodes are not a record proof.
                projected = self._project_edition(edition, cache)
                if projected['entry_id'] != entry['entry_id'] or projected['entry_kind'] != entry['kind']:
                    raise ValueError('stored typed identity differs')
                result['ndl_candidate_editions_verified'] += 1
                accepted = [(book, match) for book in candidates.values()
                            if (match := self._publisher_edition_match(provider, book, edition, cache))]
                if not accepted:
                    continue
                book, match = min(accepted, key=lambda pair: pair[0]['publisher_book_id'])
                matched.update(candidate['publisher_book_id'] for candidate, _ in accepted)
                proof = {'provider': provider, 'manifest_path': str(Path(manifest_path).resolve()),
                         'manifest_sha256': expected_sha256, 'publisher_book_id': book['publisher_book_id'],
                         'edition_record_sha256': entry['record_sha256'], 'match': match}
                encoded = _json(proof)
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                previous = db.execute('SELECT proof_sha256 FROM edition_book_confirmations WHERE edition_id=? AND provider=?',
                                      (entry['entry_id'], provider)).fetchone()
                if previous and previous['proof_sha256'] == digest:
                    result['unchanged'] += 1
                else:
                    db.execute('INSERT OR REPLACE INTO edition_book_confirmations VALUES(?,?,?,?)',
                               (entry['entry_id'], provider, encoded, digest))
                    result['confirmed'] += 1
        result['matched_publisher_books'] = len(matched)
        result['unmatched_publisher_books'] = len(books) - len(matched)
        return result

    def _book_confirmation_bindings(self, cache):
        key = ('book_confirmation_bindings',)
        if key not in cache:
            with self._connect() as db:
                cache[key] = db.execute('SELECT * FROM edition_book_confirmations').fetchall()
        return cache[key]

    @staticmethod
    def _confirmed_part_search_title(match):
        """This rule preserves the complete official title, never a guessed alias."""
        if (not isinstance(match, dict) or match.get('scope') != 'edition_only'
                or match.get('rule_version') not in {
                    'publisher-ndl-edition-confirmation-v4', 'publisher-ndl-edition-confirmation-v6'}):
            return None
        title = match.get('title_match')
        if (isinstance(title, dict)
                and title.get('rule') == 'own_primary_title_explicit_volume_and_unique_part_information_v1'
                and isinstance(title.get('official_complete_title'), str)
                and title['official_complete_title'].strip()):
            return title['official_complete_title']
        return None

    @staticmethod
    def _confirmed_kadokawa_composed_search_titles(match):
        """Expose only the literal vocabulary of the saved, older ISBD rule."""
        if (not isinstance(match, dict) or match.get('scope') != 'edition_only'
                or match.get('rule_version') not in {'publisher-ndl-edition-confirmation-v2',
                    'publisher-ndl-edition-confirmation-v5', 'publisher-ndl-edition-confirmation-v6'}):
            return []
        title = match.get('title_match')
        fields = {'rule', 'source_value', 'main', 'subtitle', 'volume', 'locators'}
        if (not isinstance(title, dict) or set(title) != fields
                or title['rule'] != 'primary_isbd_title_and_explicit_volume'):
            return []
        raw, main, subtitle, volume = (title[key] for key in ('source_value', 'main', 'subtitle', 'volume'))
        if (not isinstance(raw, str) or not raw.strip() or not isinstance(main, str) or not main.strip()
                or any(value is not None and (not isinstance(value, str) or not value.strip()) for value in (subtitle, volume))
                or not isinstance(title['locators'], list) or len(title['locators']) != (2 if volume else 1)
                or any(not isinstance(value, str) or not value for value in title['locators'])
                or match.get('matched_ndl_title') != raw):
            return []
        # Exact round trip of _publisher_edition_match_v2's existing field
        # extraction. No punctuation/words/responsibility text is discarded.
        complete = raw[:-len('. ' + volume)] if volume and raw.endswith('. ' + volume) else raw
        split = complete.split(' : ', 1)
        if (main, subtitle) != (split[0], split[1] if len(split) == 2 else None):
            return []
        variants = [main + (volume or '') + (subtitle or ''), main + (subtitle or '') + (volume or '')]
        if subtitle:
            variants += [main + (volume or '') + '~' + subtitle + '~', main + '~' + subtitle + '~' + (volume or '')]
        return list(dict.fromkeys([raw, *variants]))

    @staticmethod
    def _confirmed_tobooks_search_titles(match):
        """Replay only the existing TO rule's exact composition vocabulary."""
        if (not isinstance(match, dict) or match.get('scope') != 'edition_only'
                or match.get('rule_version') != 'publisher-reference-ndl-contained-edition-tobooks-v1'
                or match.get('source_unit') != 'publisher_product_reference'):
            return []
        title = match.get('title_match')
        if not isinstance(title, dict) or title.get('rule') != 'own_complete_title_all_parts_and_volume_v1':
            return []
        raw, main, subtitle, volume = (title.get(key) for key in ('source_value', 'main', 'subtitle', 'volume'))
        locators = title.get('locators')
        if (not isinstance(raw, str) or not raw or not isinstance(main, str) or not main
                or any(value is not None and (not isinstance(value, str) or not value) for value in (subtitle, volume))
                or not isinstance(locators, list) or len(locators) != (2 if volume else 1)
                or any(not isinstance(value, str) or not value for value in locators)
                or match.get('matched_ndl_title') != raw):
            return []
        # These are precisely _titles' existing four composed variants. Round
        # trip their fields through that rule; no general title parser/alias is added.
        from .light_novel_reference_match_v1 import _parent, _titles
        variants = [main + (volume or '') + (subtitle or ''), main + (subtitle or '') + (volume or '')]
        if subtitle:
            variants += [main + (volume or '') + '~' + subtitle + '~', main + '~' + subtitle + '~' + (volume or '')]
        parts = {'titles': [{'value': raw, 'locator': locators[0]}],
                 'volume': {'value': volume, 'locator': locators[1]} if volume else None}
        if _titles(parts, variants[0], _parent(locators[0])) != title:
            return []
        return list(dict.fromkeys([raw, *variants]))

    @staticmethod
    def _confirmed_iris_search_titles(match):
        """Use only the literal compositions of the accepted Iris NEO v2 rule."""
        if (not isinstance(match, dict) or match.get('scope') != 'edition_only'
                or match.get('rule_version') != 'publisher-ndl-edition-confirmation-iris-neo-v2'):
            return []
        title = match.get('title_match')
        if (not isinstance(title, dict)
                or set(title) != {'rule', 'source_value', 'main', 'subtitle', 'volume', 'locators'}
                or title['rule'] != 'own_complete_title_all_parts_v1'):
            return []
        raw, main, subtitle, volume = (title[key] for key in ('source_value', 'main', 'subtitle', 'volume'))
        locators = title['locators']
        if (not isinstance(raw, str) or not raw.strip() or not isinstance(main, str) or not main.strip()
                or any(value is not None and (not isinstance(value, str) or not value.strip()) for value in (subtitle, volume))
                or not isinstance(locators, list) or len(locators) != (2 if volume else 1)
                or any(not isinstance(value, str) or not value for value in locators)
                or match.get('primary_title_locator') != locators[0]
                or match.get('matched_ndl_title') != raw):
            return []
        # Reproduce the existing matcher's exact decomposition, without
        # creating an alias or interpreting a new title/edition identity.
        complete = raw[:-len('. ' + volume)] if volume and raw.endswith('. ' + volume) else raw
        parts = complete.split(' : ', 1)
        if (main, subtitle) != (parts[0], parts[1] if len(parts) == 2 else None):
            return []
        variants = [main + (volume or '') + (subtitle or ''), main + (subtitle or '') + (volume or '')]
        if subtitle:
            variants += [main + (volume or '') + '~' + subtitle + '~', main + '~' + subtitle + '~' + (volume or '')]
        return list(dict.fromkeys([raw, *variants]))

    def _saved_confirmation_search_titles(self, stored, records, cache):
        """Saved proofs widen selection only; attachment must reproduce them raw."""
        entries = {entry['entry_id']: entry for entry in stored}
        titles = {}
        for binding in self._book_confirmation_bindings(cache):
            try:
                if (binding['provider'] not in {'kadokawa', 'tobooks', 'ichijinsha-iris'}
                        or hashlib.sha256(binding['proof_json'].encode()).hexdigest() != binding['proof_sha256']):
                    continue
                proof = json.loads(binding['proof_json'])
                identity = binding['edition_id']
                entry, record = entries.get(identity), records.get(identity)
                if (not entry or not record or entry['kind'] != 'edition'
                        or record.get('source') != 'ndl'
                        or (record.get('schema_version'), record.get('provider')) not in {
                            ('light-novel-edition-v1', 'iss-ndl-opac'),
                            ('light-novel-edition-bulk-v1', 'iss-ndl-opac-national')}
                        or not re.fullmatch(r'R100000002-[A-Za-z0-9._-]+', record.get('id', ''))
                        or identity != 'lne:ndl:' + record['id']
                        or record.get('source_record', {}).get('source_id') != record['id']
                        or proof['provider'] != binding['provider']
                        or proof['edition_record_sha256'] != entry['record_sha256']):
                    continue
                provider = binding['provider']
                if provider == 'kadokawa':
                    if not re.fullmatch(r'kadokawa:[0-9]{12}', proof['publisher_book_id']):
                        continue
                    title = self._confirmed_part_search_title(proof['match'])
                    candidates = ([title] if title else []) + self._confirmed_kadokawa_composed_search_titles(proof['match'])
                    official_url = 'https://www.kadokawa.co.jp/product/' + proof['publisher_book_id'].split(':')[1] + '/'
                elif provider == 'ichijinsha-iris':
                    if not re.fullmatch(r'publisher-book:[0-9a-f]{24}', proof['publisher_book_id']):
                        continue
                    candidates = self._confirmed_iris_search_titles(proof['match'])
                    sources = proof['match'].get('sources') if isinstance(proof['match'], dict) else None
                    if (not candidates or not isinstance(sources, list) or len(sources) != 2
                            or not isinstance(sources[1], str)
                            or not re.fullmatch(r'https://www\.ichijinsha\.co\.jp/iris/title/[^/?#]+/', sources[1])):
                        continue
                    official_url = sources[1]
                    if proof['publisher_book_id'] != 'publisher-book:' + hashlib.sha256(official_url.encode()).hexdigest()[:24]:
                        continue
                else:
                    if not re.fullmatch(r'tobooks-product:[0-9]+', proof['publisher_book_id']):
                        continue
                    candidates = self._confirmed_tobooks_search_titles(proof['match'])
                    if not candidates:
                        continue
                    official_url = 'https://www.tobooks.jp/contents/' + proof['publisher_book_id'].split(':')[1]
                    if (proof['match'].get('publisher_reference_id') != proof['publisher_book_id']
                            or proof['match'].get('official_source_url') != official_url):
                        continue
                if not candidates:
                    continue
                ndl_url = 'https://ndlsearch.ndl.go.jp/books/' + record['id']
                if (record.get('source_url') != ndl_url
                        or record['source_record'].get('source_url') != ndl_url
                        or proof['match'].get('sources') != [ndl_url, official_url]):
                    continue
                titles.setdefault(identity, []).extend(candidates)
            except (ValueError, KeyError, TypeError):
                continue
        return titles

    def _attach_book_confirmations(self, rows, stored, cache):
        bindings = self._book_confirmation_bindings(cache)
        indexed = {row['entry_id']: row for row in rows}
        stored = {row['entry_id']: row for row in stored}
        documents = cache.setdefault(('book_confirmation_documents',), {})
        for binding in bindings:
            try:
                if hashlib.sha256(binding['proof_json'].encode()).hexdigest() != binding['proof_sha256']:
                    continue
                proof = json.loads(binding['proof_json'])
                entry_id = binding['edition_id']
                if (entry_id not in indexed or proof['provider'] != binding['provider']
                        or stored[entry_id]['record_sha256'] != proof['edition_record_sha256']):
                    continue
                key = (proof['provider'], proof['manifest_path'], proof['manifest_sha256'])
                if key not in documents:
                    try:
                        if proof['provider'] == 'tobooks':
                            # Listings and formal confirmations share one finite
                            # source replay, while retaining their separate identities.
                            documents[key] = {identity: {**reference, 'publisher_book_id': identity}
                                for identity, reference in self._tobooks_publisher_references(
                                    proof['manifest_path'], proof['manifest_sha256'], cache).items()}
                        elif proof['provider'] == 'shueisha-dx':
                            documents[key] = {book['publisher_book_id']: book for book in self._shueisha_dx_books(
                                proof['manifest_path'], proof['manifest_sha256'], cache)}
                        else:
                            documents[key] = {book['publisher_book_id']: book for book in self._publisher_books(*key)}
                    except (OSError, ValueError, KeyError, TypeError):
                        # A failed proof remains failed for this operation only.
                        documents[key] = {}
                book = documents[key].get(proof['publisher_book_id'])
                edition = json.loads(stored[entry_id]['record_json'])
                match = self._publisher_edition_match(proof['provider'], book, edition, cache) if book else None
                if match != proof['match']:
                    continue
                row = indexed[entry_id]
                row.update(light_novel_confirmed=True, edition_bibliography_confirmed=True,
                           edition_identity_basis='publisher_isbn_and_ndl_bibliography',
                           confirmation_source_url=book['source_url'], edition_confirmation_evidence=match)
                if row.get('admission_status') not in ('edition_series_membership_verified', 'publisher_work_group_membership_verified'):
                    row['admission_status'] = 'publisher_ndl_edition_verified'
            except (OSError, ValueError, KeyError, TypeError):
                continue

    @staticmethod
    def _publisher_bibliography_references(provider, manifest_path, expected_sha256):
        if provider == 'futaba-mnovels':
            from .light_novel_futaba_references_v1 import verify_futaba_mnovels_references
            return verify_futaba_mnovels_references(manifest_path, expected_sha256)
        if provider == 'overlap-details':
            from .light_novel_overlap_details_v1 import verify_overlap_product_details
            return [{**row, 'publisher_reference_id': row['publisher_product_reference_id']}
                    for row in verify_overlap_product_details(manifest_path, expected_sha256)]
        if provider in {'gc', 'alphapolis', 'gagaga', 'hifumi'}:
            from .light_novel_publisher_references_v1 import verify_publisher_references
            return verify_publisher_references(provider, manifest_path, expected_sha256)
        if provider == 'kodansha':
            from .light_novel_kodansha_references_v1 import verify_kodansha_references
            return verify_kodansha_references(manifest_path, expected_sha256)
        if provider == 'overlap':
            from .light_novel_overlap_references_v1 import verify_overlap_references
            return verify_overlap_references(manifest_path, expected_sha256)
        raise ValueError('unknown publisher bibliographic reference provider')

    def _publisher_bibliographic_match(self, provider, reference, primary_parts, cache):
        """Authenticate the full ISBN inventory before detailed correspondence."""
        from .light_novel_bibliographic_match_v1 import match_bibliographic_reference
        if provider == 'overlap-details':
            from .normalization import normalize_isbn
            isbn = normalize_isbn(str(reference.get('isbn') or ''))
            if not isbn:
                return None
            return match_bibliographic_reference(provider, reference, primary_parts,
                same_isbn_parts=self._reference_same_isbn_parts(isbn, cache))
        return match_bibliographic_reference(provider, reference, primary_parts)

    def correlate_publisher_bibliographies(self, provider, manifest_path, expected_sha256, *, reference_ids=None):
        """Connect cards to own bibliography, without an ISBN-edition assertion."""
        from .light_novel_bibliographic_match_v1 import (
            SCOPE, candidate_title_keys, reference_hold_reason,
        )
        references = self._publisher_bibliography_references(provider, manifest_path, expected_sha256)
        indexed = {row['publisher_reference_id']: row for row in references}
        if len(indexed) != len(references):
            raise ValueError('duplicate publisher bibliographic reference')
        selected = set(indexed) if reference_ids is None else set(reference_ids)
        if selected - indexed.keys():
            raise ValueError('unknown publisher bibliographic reference')
        result = {'publisher_references': len(indexed), 'requested_publisher_references': len(selected),
                  'candidate_editions_verified': 0, 'new_correspondences': 0, 'updated_correspondences': 0,
                  'unchanged_correspondences': 0, 'matched_publisher_references': 0,
                  'held_reference_reasons': {}, 'verification_scope': 'complete_title_candidates_only',
                  'correspondence_scope': SCOPE}
        eligible, held = {}, {}
        for identity in selected:
            reason = reference_hold_reason(provider, indexed[identity])
            if reason:
                held[identity] = reason
            else:
                eligible[identity] = indexed[identity]
        by_title = {}
        from .light_novel_reference_match_v1 import _key
        for identity, row in eligible.items():
            by_title.setdefault(_key(row['title']), set()).add(identity)
        matched, attempted, cache = set(), set(), {}
        if eligible:
            with self._connect() as db:
                stored = db.execute('SELECT * FROM entries').fetchall()
            if provider == 'overlap-details':
                # Candidate title selection must not hide a contradictory
                # same-ISBN record, including one with a changed SQL kind.
                cache[('reference_all_stored',)] = stored
                cache[('reference_all_records',)] = {}
            candidates = []
            for entry in stored:
                if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                    raise ValueError('stored metadata SHA mismatch')
                record = json.loads(entry['record_json'])
                if provider == 'overlap-details':
                    cache[('reference_all_records',)][entry['entry_id']] = record
                # A changed SQL kind must not conceal a same-title edition.
                if entry['kind'] != 'edition' and record.get('schema_version') not in {
                        'light-novel-edition-v1', 'light-novel-edition-bulk-v1'}:
                    continue
                identities = {identity for title in candidate_title_keys(record['source_record'])
                              for identity in by_title.get(title, ())}
                if identities:
                    candidates.append((entry, record, identities))
            with self._connect() as db:
                for entry, record, identities in candidates:
                    # EACH selected record is checked, including records sharing
                    # a cached RDF artifact. A cached raw node is not its proof.
                    row = self._project_edition(record, cache)
                    if row['entry_id'] != entry['entry_id'] or row['entry_kind'] != entry['kind']:
                        raise ValueError('stored typed identity differs')
                    result['candidate_editions_verified'] += 1
                    parts = self._edition_primary_bibliographic_parts(record, cache)
                    attempted.update(identities)
                    matches = [proof for identity in sorted(identities)
                               if (proof := self._publisher_bibliographic_match(provider, eligible[identity], parts, cache))]
                    if not matches:
                        continue
                    matched.update(proof['publisher_reference_id'] for proof in matches)
                    proof = {'provider': provider, 'scope': SCOPE,
                             'manifest_path': str(Path(manifest_path).resolve()),
                             'manifest_sha256': expected_sha256,
                             'edition_record_sha256': entry['record_sha256'],
                             'reference_ids': [match['publisher_reference_id'] for match in matches],
                             'matches': matches}
                    encoded = _json(proof)
                    digest = hashlib.sha256(encoded.encode()).hexdigest()
                    previous = db.execute('SELECT proof_sha256 FROM publisher_bibliographic_correspondences '
                        'WHERE edition_id=? AND provider=?', (entry['entry_id'], provider)).fetchone()
                    if previous and previous['proof_sha256'] == digest:
                        result['unchanged_correspondences'] += 1
                    else:
                        db.execute('INSERT OR REPLACE INTO publisher_bibliographic_correspondences VALUES(?,?,?,?)',
                                   (entry['entry_id'], provider, encoded, digest))
                        result['updated_correspondences' if previous else 'new_correspondences'] += 1
        for identity in eligible.keys() - matched:
            held[identity] = ('strict_bibliographic_correspondence_unproved' if identity in attempted
                              else 'no_complete_title_candidate')
        for reason in held.values():
            result['held_reference_reasons'][reason] = result['held_reference_reasons'].get(reason, 0) + 1
        result.update(matched_publisher_references=len(matched), held_publisher_references=len(held))
        return result

    def _orange_listing_group_entries(self, cache):
        key = ('orange_listing_group_entries',)
        if key not in cache:
            with self._connect() as db:
                entries = db.execute("SELECT * FROM entries WHERE "
                    "json_extract(record_json,'$.schema_version')='light-novel-orange-source-group-v1' "
                    "OR entry_id LIKE 'lng:pub:orange:%'").fetchall()
            result = {}
            for entry in entries:
                record = json.loads(entry['record_json'])
                if (hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']
                        or entry['kind'] != 'publisher_group' or record.get('kind') != 'publisher_group'
                        or record.get('schema_version') != 'light-novel-orange-source-group-v1'
                        or record.get('source') != 'publisher' or record.get('provider') != 'orange'
                        or entry['entry_id'] != _entry('lng:pub:orange:' + record['id'])):
                    raise ValueError('Orange listing group metadata differs')
                result[entry['entry_id']] = dict(entry)
            cache[key] = result
        return cache[key]

    def _orange_listing_candidates(self, editions, manifest_path, expected_sha256, cache):
        from .ingestion.ndl import DC, DCTERMS
        from .light_novel_bibliographic_match_v1 import candidate_title_keys
        from .light_novel_reference_match_v1 import _key
        from .light_novel_orange_groups_v1 import orange_child_candidate_index, verify_orange_child
        from .light_novel_orange_association_v1 import match_orange_association
        path = str(_resolved_path(manifest_path, cache))
        index_key = ('orange_child_candidate_index', path, expected_sha256)
        if index_key not in cache:
            index = {}
            for child in orange_child_candidate_index(path, expected_sha256):
                pair = (child['group_reference_id'], child['child_reference_id'])
                index.setdefault(_key(child['title']), set()).add(pair)
            cache[index_key] = index
        groups = self._orange_listing_group_entries(cache)
        group_index = {}
        for identity, entry in groups.items():
            record = json.loads(entry['record_json'])
            active = record['source_group_assertions'][-1]
            if (active['manifest_sha256'] == expected_sha256
                    and str(_resolved_path(active['manifest_path'], cache)) == path):
                group_index.setdefault(active['reference_id'], []).append((identity, entry, record, active))
        result, ambiguous = {}, set()
        for identity, entry in editions.items():
            record = json.loads(entry['record_json'])
            pairs = set().union(*(cache[index_key].get(title, set())
                for title in candidate_title_keys(record['source_record'])))
            if len(pairs) != 1:
                if len(pairs) > 1:
                    ambiguous.add(identity)
                continue
            group_reference, child_reference = next(iter(pairs))
            group_entries = group_index.get(group_reference, [])
            if len(group_entries) != 1:
                continue
            group_id, group_entry, group_record, active = group_entries[0]
            parent_key = ('orange_selected_parent', path, expected_sha256, group_reference)
            if parent_key not in cache:
                selected = verify_orange_child(path, expected_sha256,
                    group_reference_id=group_reference, child_reference_id=child_reference)
                cache[parent_key] = selected['group']
            group = cache[parent_key]
            if (active != self._publisher_source_assertion('orange', active['manifest_path'], expected_sha256, group)
                    or group_record['title'] != group['title']
                    or group_record['publisher_product_references'] != self._publisher_product_references('orange', group)):
                raise ValueError('Orange selected listing group does not reproduce')
            children = [child for child in group['members'] if child['publisher_reference_id'] == child_reference]
            if len(children) != 1:
                raise ValueError('Orange selected child is absent or ambiguous')
            parts = self._edition_primary_bibliographic_parts(record, cache)
            own = self._authenticated_own_primary_resource(record, parts, cache) if parts else None
            if own is None or any(child.tag in {'{' + DC + '}contributor', '{' + DCTERMS + '}contributor'} for child in own):
                continue
            association = match_orange_association({'group': group, 'child': children[0],
                'source_proof_sha256': expected_sha256}, parts) if parts else None
            if association is not None:
                result[identity] = {'group_entry_id': group_id,
                    'group_record_sha256': group_entry['record_sha256'], 'association': association}
        return result, ambiguous

    def correlate_orange_edition_listings(self, manifest_path, expected_sha256, *, edition_ids):
        """Associate explicit publisher listings, without story or SKU identity."""
        if isinstance(edition_ids, (str, bytes)) or edition_ids is None:
            raise ValueError('an explicit edition ID collection is required')
        requested = list(edition_ids)
        if len(requested) != len(set(requested)) or any(
                not _entry(identity).startswith('lne:ndl:') for identity in requested):
            raise ValueError('unique typed NDL edition IDs are required')
        result = {'scope': 'bibliographic_reference_association', 'requested_editions': len(requested),
            'new_associations': 0, 'unchanged': 0, 'protected_existing': 0,
            'unresolved_editions': 0, 'ambiguous_editions': 0}
        if not requested:
            return result
        with self._connect() as db:
            editions = {row['entry_id']: dict(row) for row in db.execute(
                'SELECT * FROM entries WHERE entry_id IN (' + ','.join('?' for _ in requested) + ')', requested)}
        if set(editions) != set(requested) or any(entry['kind'] != 'edition' for entry in editions.values()):
            raise ValueError('requested edition is missing or has a different kind')
        cache = {}
        self._membership_base_rows(editions, cache)
        matches, ambiguous = self._orange_listing_candidates(editions, manifest_path, expected_sha256, cache)
        result.update(unresolved_editions=len(requested) - len(matches), ambiguous_editions=len(ambiguous))
        groups = self._orange_listing_group_entries(cache)
        expected_groups = {identity: (entry['kind'], entry['record_sha256']) for identity, entry in groups.items()}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current_groups = {}
            for row in db.execute("SELECT * FROM entries WHERE "
                    "json_extract(record_json,'$.schema_version')='light-novel-orange-source-group-v1' "
                    "OR entry_id LIKE 'lng:pub:orange:%'"):
                if hashlib.sha256(row['record_json'].encode()).hexdigest() != row['record_sha256']:
                    raise ValueError('Orange listing group metadata differs')
                current_groups[row['entry_id']] = (row['kind'], row['record_sha256'])
            if current_groups != expected_groups:
                raise ValueError('Orange listing group set changed during resolution')
            for identity, entry in editions.items():
                current = db.execute('SELECT kind,record_sha256,record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()
                if (current is None or (current['kind'], current['record_sha256']) != (entry['kind'], entry['record_sha256'])
                        or hashlib.sha256(current['record_json'].encode()).hexdigest() != current['record_sha256']):
                    raise ValueError('selected edition metadata changed during resolution')
            for identity in requested:
                match = matches.get(identity)
                previous = db.execute('SELECT * FROM publisher_bibliographic_correspondences '
                    'WHERE edition_id=? AND provider=?', (identity, 'orange')).fetchone()
                if match is None:
                    result['protected_existing'] += int(previous is not None)
                    continue
                proof = {'kind': 'orange_child_listing_association_v1', 'provider': 'orange',
                    'scope': result['scope'], 'manifest_path': str(_resolved_path(manifest_path, cache)),
                    'manifest_sha256': expected_sha256, 'edition_record_sha256': editions[identity]['record_sha256'], **match}
                encoded = _json(proof)
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                if previous is not None:
                    result['unchanged' if previous['proof_json'] == encoded and previous['proof_sha256'] == digest
                        else 'protected_existing'] += 1
                    continue
                db.execute('INSERT INTO publisher_bibliographic_correspondences VALUES(?,?,?,?)',
                    (identity, 'orange', encoded, digest))
                result['new_associations'] += 1
        return result

    def _attach_bibliographic_correspondences(self, rows, stored, cache):
        from .light_novel_bibliographic_match_v1 import SCOPE
        with self._connect() as db:
            bindings = db.execute('SELECT * FROM publisher_bibliographic_correspondences').fetchall()
        indexed = {row['entry_id']: row for row in rows}
        stored = {row['entry_id']: row for row in stored}
        documents = {}
        for binding in bindings:
            try:
                identity = binding['edition_id']
                if identity not in indexed or indexed[identity]['entry_kind'] != 'edition':
                    continue
                if hashlib.sha256(binding['proof_json'].encode()).hexdigest() != binding['proof_sha256']:
                    continue
                proof = json.loads(binding['proof_json'])
                if proof.get('kind') == 'orange_child_listing_association_v1':
                    if (proof['provider'] != binding['provider'] or proof['provider'] != 'orange'
                            or proof['scope'] != 'bibliographic_reference_association'
                            or proof['edition_record_sha256'] != stored[identity]['record_sha256']):
                        continue
                    key = ('orange_listing_associations', proof['manifest_path'], proof['manifest_sha256'], identity)
                    if key not in documents:
                        documents[key] = {}  # One failed listing cannot hide another edition's association.
                        documents[key] = self._orange_listing_candidates({identity: stored[identity]},
                            proof['manifest_path'], proof['manifest_sha256'], cache)[0]
                    match = documents[key].get(identity)
                    if (match is None or any(match[field] != proof[field]
                            for field in ('group_entry_id', 'group_record_sha256', 'association'))):
                        continue
                    indexed[identity]['publisher_listing_associations'] = [{
                        **match['association'], 'group_entry_id': match['group_entry_id']}]
                    continue
                if (proof['provider'] != binding['provider'] or proof['scope'] != SCOPE
                        or proof['edition_record_sha256'] != stored[identity]['record_sha256']
                        or not proof['reference_ids'] or proof['reference_ids'] != sorted(set(proof['reference_ids']))):
                    continue
                key = (proof['provider'], proof['manifest_path'], proof['manifest_sha256'])
                if key not in documents:
                    try:
                        references = (self._gc_native_references(key[1], key[2], cache) if key[0] == 'gc'
                                      else self._alphapolis_native_references(key[1], key[2], cache) if key[0] == 'alphapolis'
                                      else self._hifumi_native_references(key[1], key[2], cache) if key[0] == 'hifumi'
                                      else self._publisher_bibliography_references(*key))
                        documents[key] = {row['publisher_reference_id']: row for row in references}
                    except (OSError, ValueError, KeyError, TypeError):
                        documents[key] = {}  # request-local failures are memoized too
                if any(reference not in documents[key] for reference in proof['reference_ids']):
                    continue
                edition = json.loads(stored[identity]['record_json'])
                parts = self._edition_primary_bibliographic_parts(edition, cache)
                matches = [self._publisher_bibliographic_match(proof['provider'], documents[key][reference], parts, cache)
                           for reference in proof['reference_ids']]
                if any(match is None for match in matches) or matches != proof['matches']:
                    continue
                row = indexed[identity]
                row.update(publisher_bibliography_matched=True, bibliographic_correspondence_scope=SCOPE,
                           bibliography_source_url=matches[0]['official_source_url'])
                # A correspondence can authenticate a general novel's bibliography
                # while its individual light-novel classification remains unknown.
                if all(match.get('official_lightnovel_tag') is True
                       or match.get('light_novel_status') == 'confirmed' for match in matches):
                    row['light_novel_confirmed'] = True
                if row.get('admission_status') not in {'edition_series_membership_verified',
                        'publisher_work_group_membership_verified', 'publisher_ndl_edition_verified'}:
                    row['admission_status'] = 'publisher_bibliographic_record_verified'
            except (OSError, ValueError, KeyError, TypeError):
                continue

    @staticmethod
    def _membership_match(series, source_record, authorities):
        """Strict edition membership, never an assertion of original-work identity.

        All six bibliographic checks are mandatory. A publisher's literary
        series field is only an imprint check and cannot supply story identity.
        """
        if source_record['language'] not in {'jpn', 'ja'}:
            return None
        names = {normalize_search(t) for t in [series['title'], *series['aliases']]}
        matched_titles = [t['value'] for t in source_record['titles'] if normalize_search(t['value']) in names]
        if not matched_titles:
            return None
        volume = unicodedata.normalize('NFKC', source_record.get('volume') or '').strip()
        number = re.match(r'^(?:第)?([0-9]+)(?:巻)?(?:$|\s|[(:])', volume)
        if not number or not 1 <= int(number[1]) <= 10000:
            return None
        ndc = source_record.get('extra', {}).get('ndc_codes', [])
        if not any(code.startswith('913') for code in ndc):
            return None
        imprint = unicodedata.normalize('NFKC', source_record.get('series') or '').split(';')[0].strip()
        # NDL explicitly separates parallel imprint titles using '='; compare
        # complete forms, never a substring of a different publication label.
        imprint_forms = {normalize_search(v) for v in imprint.split(' = ')}
        publishers = [p for p in series['publishers'] if normalize_search(p['label']) in imprint_forms]
        if not imprint or len(publishers) != 1:
            return None
        ndl_ids = set()
        for contributor in source_record['contributors']:
            if contributor['role'] != 'author':
                continue
            for identifier in contributor['identifiers']:
                if identifier['source'] == 'ndl-authority':
                    match = re.fullmatch(r'https?://id\.ndl\.go\.jp/auth/(?:entity|ndlna)/([0-9]{6,12})', identifier['value'])
                    if match:
                        ndl_ids.add(match[1])
        author_matches = []
        for author in series['authors']:
            shared = ndl_ids & authorities.get(author['qid'], set())
            if not shared:
                return None
            author_matches.extend({'author_qid': author['qid'], 'ndl_authority_id': value} for value in sorted(shared))
        if not author_matches or ndl_ids - {a['ndl_authority_id'] for a in author_matches}:
            return None
        return {'rule_version': 'ndl-edition-series-membership-v1', 'scope': 'edition_membership_only',
                'edition_id': 'lne:ndl:' + source_record['source_id'], 'series_entry_id': series['entry_id'],
                'matched_title': matched_titles[0], 'volume': source_record['volume'], 'volume_number': int(number[1]),
                'author_matches': author_matches, 'imprint': publishers[0], 'ndc_codes': ndc,
                'sources': [series['source_url'], source_record['source_url']],
                'field_locators': {'title': 'recordData/dcterms:title', 'volume': 'recordData/dcndl:volume',
                    'author_authority': 'recordData/dcterms:creator/rdf:about;wikidata.claims.P349',
                    'imprint': 'recordData/dcndl:seriesTitle;wikidata.claims.P123',
                    'language': 'recordData/dcterms:language', 'novel_class': 'recordData/subject/NDC913'}}

    def _own_role_membership_candidates(self, rows, stored, authorities, cache):
        """Resolve creator roles only inside each authenticated own resource."""
        from .ingestion.ndl import DC, DCTERMS
        from .light_novel_reference_match_v1 import _parent
        from .light_novel_own_role_v1 import bind_own_direct_credit_roles_v2
        index = {}
        for row in rows:
            if row['entry_kind'] == 'series' and row['entry_id'].startswith('lns:wd:'):
                for title in [row['title'], *row['aliases']]:
                    index.setdefault(normalize_search(title), {})[row['entry_id']] = row
        result = {}
        for row in rows:
            if row['entry_kind'] != 'edition':
                continue
            record = json.loads(stored[row['entry_id']]['record_json'])
            parts = self._edition_primary_bibliographic_parts(record, cache)
            if not parts or not parts['publisher_agents'] or not parts['volume']:
                continue
            candidates = {}
            for title in parts['titles']:
                candidates.update(index.get(normalize_search(title['value']), {}))
            if len(candidates) != 1:
                continue
            series = next(iter(candidates.values()))
            expected_authorities = [authorities.get(author['qid'], set()) for author in series['authors']]
            if (not expected_authorities or any(len(values) != 1 for values in expected_authorities)
                    or len(set().union(*expected_authorities)) != len(expected_authorities)):
                continue
            source = parts['source']
            ndc = source['extra']['ndc_codes']
            if not ndc or any(not re.fullmatch(r'913(?:\.[0-9]+)?', value) for value in ndc):
                continue
            own = self._authenticated_own_primary_resource(record, parts, cache)
            if own is None:
                continue
            if any(child.tag in {'{' + DC + '}contributor', '{' + DCTERMS + '}contributor'} for child in own):
                continue
            own_locator = _parent(parts['publisher_agents'][0]['locator'])
            bindings = bind_own_direct_credit_roles_v2(own, own_locator, parts)
            if bindings['status'] != 'bound':
                continue
            # This copy changes only the matching input. Persisted source
            # contributors and their default roles remain immutable.
            local_source = {**source, 'titles': parts['titles'], 'volume': parts['volume']['value'],
                'contributors': bindings['writer_contributors']}
            membership = self._membership_match(series, local_source, authorities)
            if membership is None:
                continue
            result[row['entry_id']] = {**membership,
                'rule_version': 'ndl-edition-series-membership-own-direct-role-v2',
                'role_bindings': bindings, 'author_identity_asserted': False,
                'original_work_identity_asserted': False, 'P50_backfilled': False}
        return result

    def _membership_candidates(self, rows, stored, authorities):
        index = {}
        for series in rows:
            if series['entry_kind'] == 'series':
                for title in {normalize_search(t) for t in [series['title'], *series['aliases']]}:
                    index.setdefault(title, {})[series['entry_id']] = series
        result = {}
        for edition in rows:
            if edition['entry_kind'] != 'edition':
                continue
            source = json.loads(stored[edition['entry_id']]['record_json'])['source_record']
            candidates = {}
            for title in source['titles']:
                candidates.update(index.get(normalize_search(title['value']), {}))
            proven = [proof for series in candidates.values() if (proof := self._membership_match(series, source, authorities))]
            if len(proven) == 1:
                result[edition['entry_id']] = proven[0]
        return result

    def resolve_edition_series_memberships(self, manifest_path, expected_sha256):
        """Resolve only uniquely supported edition membership from pinned raws."""
        authorities = self._author_authorities(manifest_path, expected_sha256, {})
        rows = self._rows(include_features=False)
        with self._connect() as db:
            stored = {r['entry_id']: dict(r) for r in db.execute('SELECT * FROM entries')}
        candidates = self._membership_candidates(rows, stored, authorities)
        result = {'linked': 0, 'unchanged': 0, 'unresolved_editions': sum(r['entry_kind'] == 'edition' for r in rows) - len(candidates)}
        with self._connect() as db:
            for edition_id, membership in candidates.items():
                series_id = membership['series_entry_id']
                proof = {'manifest_path': str(Path(manifest_path).resolve()), 'manifest_sha256': expected_sha256,
                         'edition_record_sha256': stored[edition_id]['record_sha256'],
                         'series_record_sha256': stored[series_id]['record_sha256'], 'membership': membership}
                encoded = _json(proof)
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                previous = db.execute('SELECT proof_sha256 FROM edition_series_memberships WHERE edition_id=?', (edition_id,)).fetchone()
                if previous and previous['proof_sha256'] == digest:
                    result['unchanged'] += 1
                else:
                    db.execute('INSERT OR REPLACE INTO edition_series_memberships VALUES(?,?,?,?)', (edition_id, series_id, encoded, digest))
                    result['linked'] += 1
        return result

    def _membership_base_rows(self, stored, cache):
        """Authenticate only selected base records, without publisher attachments."""
        rows = []
        for identity, entry in stored.items():
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored metadata SHA mismatch')
            row = self._project(json.loads(entry['record_json']), cache)
            expected_kind = 'edition' if identity.startswith('lne:ndl:') else 'series'
            if (row['entry_id'] != identity or row['entry_kind'] != entry['kind']
                    or row['entry_kind'] != expected_kind):
                raise ValueError('stored typed identity differs')
            rows.append(row)
        if len(rows) != len(stored) or {row['entry_id'] for row in rows} != set(stored):
            raise ValueError('selected membership base records are incomplete')
        return rows

    def resolve_wikidata_edition_memberships(self, manifest_path, expected_sha256, *, edition_ids):
        """Create scoped NDL-edition links to v1 Wikidata series only.

        Complete titles/aliases select raw witnesses; the existing six-gate
        matcher and independent author authority verifier decide uniqueness.
        NDL publisher series/work groups use their separate resolver contract.
        Existing bindings are never replaced or removed by this operation.
        """
        return self._resolve_scoped_wikidata_memberships(manifest_path, expected_sha256,
            edition_ids=edition_ids, own_roles=False)

    def resolve_own_role_wikidata_edition_memberships(self, manifest_path, expected_sha256, *, edition_ids):
        """Use explicit own writer/illustrator credits without rewriting metadata."""
        return self._resolve_scoped_wikidata_memberships(manifest_path, expected_sha256,
            edition_ids=edition_ids, own_roles=True)

    def resolve_dre_edition_memberships(self, manifest_path, expected_sha256, *, edition_ids):
        """Use explicit DRE series identity and independently checked NDL volumes."""
        return self._resolve_scoped_wikidata_memberships(manifest_path, expected_sha256,
            edition_ids=edition_ids, own_roles=True, dre_series=True)

    def resolve_kadokawa_promotion_memberships(self, manifest_path, expected_sha256, *, edition_ids):
        """Connect confirmed editions explicitly linked by their official novel page."""
        return self._resolve_scoped_wikidata_memberships(manifest_path, expected_sha256,
            edition_ids=edition_ids, own_roles=True, kadokawa_promotions=True)

    def resolve_kadokawa_named_part_memberships(self, manifest_path, expected_sha256, *, edition_ids):
        """Bind explicit 上/下 editions without asserting a numeric sequence."""
        return self._resolve_scoped_wikidata_memberships(manifest_path, expected_sha256,
            edition_ids=edition_ids, own_roles=True, kadokawa_promotions=True, named_parts=True)

    def _kadokawa_promotion_assertions(self, manifest_path, expected_sha256, cache):
        from .light_novel_kadokawa_promotions_v1 import verify_promotions
        key=('kadokawa_novel_promotions',str(_resolved_path(manifest_path,cache)),expected_sha256)
        if key not in cache:
            cache[key]=verify_promotions(manifest_path,expected_sha256)
        return cache[key]

    def _legacy_kadokawa_paper_form_witness(self, binding, confirmation, cache):
        """Add a medium witness without changing an authenticated legacy match."""
        from .light_novel_publisher_books_v1 import _checked_bytes, _replay_kadokawa_book_pages
        from .normalization import normalize_isbn
        try:
            match = confirmation['match']
            if (binding['provider'] != 'kadokawa' or confirmation['provider'] != 'kadokawa'
                    or hashlib.sha256(binding['proof_json'].encode()).hexdigest() != binding['proof_sha256']
                    or json.loads(binding['proof_json']) != confirmation
                    or match.get('rule_version') != 'publisher-ndl-edition-confirmation-v1'
                    or 'official_product_form_id' in match):
                return None
            key = ('kadokawa_legacy_paper_form_witness', binding['edition_id'], binding['proof_sha256'])
            if key in cache:
                return cache[key]
            path = _resolved_path(confirmation['manifest_path'], cache)
            manifest = json.loads(_checked_bytes(path, confirmation['manifest_sha256']))
            documents = cache.get(('book_confirmation_documents',), {})
            books = documents.get(('kadokawa', confirmation['manifest_path'], confirmation['manifest_sha256']), {})
            book = books.get(confirmation['publisher_book_id'])
            if not book:
                return None
            evidence = book['evidence']
            receipts = [r for r in manifest['pages'] if r.get('sha256') == evidence['source_sha256']
                        and r.get('request_data') == evidence['request_data']]
            if len(receipts) != 1:
                return None
            receipt = receipts[0]
            if any(receipt.get(field) for field in ('location', 'error', 'hold', 'size_cap_exceeded',
                    'redirects_followed', 'http_redirects_followed', 'retry_count', 'http_retry_count')):
                return None
            # Replay the authenticated exact source document, not a saved projection.
            replayed = _replay_kadokawa_book_pages({**manifest, 'pages': receipts}, path.parent)
            selected = [r for r in replayed if r['publisher_book_id'] == confirmation['publisher_book_id']]
            if len(selected) != 1 or selected[0] != book:
                return None
            own = selected[0]
            raw_path = Path(own['evidence']['raw_path'])
            raw = _checked_bytes(raw_path, receipt['sha256'])
            if 'byte_length' in receipt and receipt['byte_length'] != len(raw):
                return None
            payload = json.loads(raw)
            pointer = own['evidence']['json_pointer']
            item = payload['result']['book'][int(pointer.rsplit('/', 1)[1])]
            if (type(item.get('product_form_id')) is not int or item['product_form_id'] != 3
                    or item.get('itemCode') != confirmation['publisher_book_id'].split(':')[1]
                    or not isinstance(item.get('isbn'), str)
                    or normalize_isbn(item['isbn']) != match.get('isbn13')
                    or own['isbn'] != item['isbn']
                    or payload.get('product_form', {}).get('3', {}).get('name') != '文庫'):
                return None
            witness = {'rule_version': 'kadokawa-legacy-explicit-paper-form-witness-v1',
                'publisher_confirmation_proof_sha256': binding['proof_sha256'],
                'publisher_book_id': confirmation['publisher_book_id'], 'isbn13': match['isbn13'],
                'manifest_path': str(path), 'manifest_sha256': confirmation['manifest_sha256'],
                'source_url': own['evidence']['source_url'], 'http_method': 'POST',
                'request_data': receipt['request_data'], 'raw_path': str(raw_path),
                'raw_sha256': receipt['sha256'], 'item_code_locator': pointer + '/itemCode',
                'isbn_locator': pointer + '/isbn', 'product_form_id': 3,
                'product_form_id_locator': pointer + '/product_form_id',
                'product_form_name': '文庫', 'product_form_name_locator': '/product_form/3/name'}
            cache[key] = witness
            return witness
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            return None

    def _kadokawa_promotion_membership_candidates(self, rows, stored, assertions, cache):
        from .light_novel_reference_match_v1 import _parent
        from .light_novel_ndl_series_v1 import person_key
        editions=[dict(r) for r in rows if r['entry_kind']=='edition']
        self._attach_book_confirmations(editions,list(stored.values()),cache)
        bindings={r['edition_id']:r for r in self._book_confirmation_bindings(cache) if r['provider']=='kadokawa'}
        parents=[r for r in rows if r['entry_kind']=='series' and r['entry_id'].startswith('lns:wd:')]
        result={}
        for edition in editions:
            identity=edition['entry_id'];binding=bindings.get(identity)
            if not binding or edition.get('edition_bibliography_confirmed') is not True:
                continue
            confirmation=json.loads(binding['proof_json']);match=edition.get('edition_confirmation_evidence')
            if (match!=confirmation.get('match') or match.get('scope')!='edition_only'
                    or confirmation.get('publisher_book_id') is None):
                continue
            paper_witness = None
            if match.get('official_product_form_id') != 3:
                paper_witness = self._legacy_kadokawa_paper_form_witness(binding, confirmation, cache)
                if paper_witness is None:
                    continue
            record=json.loads(stored[identity]['record_json'])
            parts=self._edition_primary_bibliographic_parts(record,cache)
            if not parts or not parts['volume']:
                continue
            volume=parts['volume'];value=unicodedata.normalize('NFKC',volume['value']).strip()
            number=re.match(r'^(?:第|その)?([0-9]{1,4})(?:巻)?(?:$|\s|[(:])',value)
            if not number or not 1<=int(number[1])<=10000:
                continue
            source=record['source_record']
            if source['language'] not in ('ja','jpn') or not any(c.startswith('913') for c in source['extra'].get('ndc_codes',[])):
                continue
            mains=[]
            for title in parts['titles']:
                if '/{http://purl.org/dc/terms/}title[' not in title['locator'] or _parent(title['locator'])!=_parent(volume['locator']):
                    continue
                text=unicodedata.normalize('NFKC',title['value']).strip()
                if text.endswith('. '+value):text=text[:-len('. '+value)]
                elif re.fullmatch(r'[0-9]{1,4}',value) and text.endswith(' '+value):
                    text=text[:-len(' '+value)]
                main=re.split(r' (?:=|:) ',text,maxsplit=1)[0].strip()
                if main:mains.append({'main':main,'raw_value':title['value'],'locator':title['locator']})
            if not mains:
                continue
            candidates=[p for p in parents if all(normalize_search(t['main']) in
                {normalize_search(n) for n in [p['title'],*p['aliases']]} for t in mains)]
            if len(candidates)!=1:
                continue
            parent=candidates[0];assertion=assertions.get(parent['entry_id'])
            if (not assertion or parent['original_language']!='ja'
                    or normalize_search(assertion['title'])!=normalize_search(parent['title'])
                    or confirmation['publisher_book_id'] not in assertion['books']):
                continue
            origin=json.loads(stored[parent['entry_id']]['record_json'])['provenance']
            entity=self._payload(origin['raw_path'],origin['raw_sha256'],cache)['entities'][parent['qid']]
            urls={s.get('mainsnak',{}).get('datavalue',{}).get('value') for s in entity.get('claims',{}).get('P856',[])
                if s.get('rank')!='deprecated' and s.get('mainsnak',{}).get('snaktype')=='value'
                and isinstance(s.get('mainsnak',{}).get('datavalue',{}).get('value'),str)}
            if assertion.get('wikidata_source_url',assertion['source_url']) not in urls:
                continue
            if parent['authors'] and {person_key(a['name']) for a in parent['authors']}!={person_key(a['official_credit']) for a in match['authors']}:
                continue
            result[identity]={'rule_version':'kadokawa-official-promotion-edition-membership-v1',
                'scope':'edition_membership_only','edition_id':identity,'series_entry_id':parent['entry_id'],
                'volume_number':int(number[1]),'volume':volume,'main_titles':mains,
                'publisher_book_id':confirmation['publisher_book_id'],
                'publisher_confirmation_proof_sha256':binding['proof_sha256'],
                'official_series_url':assertion['source_url'],'promotion_raw_sha256':assertion['raw_sha256'],
                'explicit_book_link_evidence':assertion['books'][confirmation['publisher_book_id']],
                'series_identity_basis':'exact_wikidata_P856_and_explicit_official_novel_book_list',
                'person_identity_asserted':False,'original_work_identity_asserted':False}
            if 'redirect_evidence' in assertion:
                result[identity].update(rule_version='kadokawa-official-promotion-edition-membership-v2',
                    wikidata_source_url=assertion['wikidata_source_url'],
                    redirect_evidence=assertion['redirect_evidence'],
                    series_identity_basis='exact_wikidata_P856_direct_official_relocation_and_explicit_novel_book_list')
            if paper_witness is not None:
                result[identity].update(rule_version='kadokawa-official-promotion-edition-membership-v3',
                    legacy_paper_form_witness=paper_witness)
        return result

    def _kadokawa_named_part_membership_candidates(self, rows, stored, assertions, cache):
        from .light_novel_reference_match_v1 import _parent, _key
        from .light_novel_ndl_series_v1 import person_key
        from .normalization import normalize_isbn
        from .ingestion.ndl import DCNDL
        editions=[dict(r) for r in rows if r['entry_kind']=='edition']
        self._attach_book_confirmations(editions,list(stored.values()),cache)
        bindings={r['edition_id']:r for r in self._book_confirmation_bindings(cache) if r['provider']=='kadokawa'}
        parents=[r for r in rows if r['entry_kind']=='series' and r['entry_id'].startswith('lns:wd:')]
        result={}
        for edition in editions:
            identity=edition['entry_id'];binding=bindings.get(identity)
            if not binding or edition.get('edition_bibliography_confirmed') is not True:
                continue
            confirmation=json.loads(binding['proof_json']);match=edition.get('edition_confirmation_evidence')
            if match!=confirmation.get('match') or match.get('scope')!='edition_only':
                continue
            paper_witness=None
            if type(match.get('official_product_form_id')) is not int or match['official_product_form_id']!=3:
                paper_witness=self._legacy_kadokawa_paper_form_witness(binding,confirmation,cache)
                if paper_witness is None:continue
            record=json.loads(stored[identity]['record_json'])
            parts=self._edition_primary_bibliographic_parts(record,cache)
            if not parts or not parts['volume']:continue
            own=self._authenticated_own_primary_resource(record,parts,cache)
            if own is None or len(own.findall('{'+DCNDL+'}volume'))!=1:continue
            volume=parts['volume'];label=volume['value']
            if label not in {'上','下'}:continue
            source=parts['source'];isbn=match.get('isbn13')
            if (source['language'] not in ('ja','jpn') or not source['extra'].get('ndc_codes')
                    or any(not re.fullmatch(r'913(?:\.[0-9]+)?',n) for n in source['extra']['ndc_codes'])
                    or not isbn or {normalize_isbn(i['value']) for i in source['identifiers'] if i['source']=='isbn'}!={isbn}
                    or source['publisher']!=match.get('ndl_publisher')
                    or unicodedata.normalize('NFKC',source.get('series') or '').split(';')[0].strip()!=match.get('ndl_imprint')):
                continue
            own_ids={i['value'] for a in source['contributors'] if a['role']=='author'
                for i in a['identifiers'] if i['source']=='ndl-authority'}
            if any(not set(a['ndl_authorities'])<=own_ids or not any(
                    person_key(literal['name'])==person_key(a['official_credit'])
                    and _parent(literal['locator'])==_parent(volume['locator'])
                    for literal in parts['literal_authors']) for a in match['authors']):continue
            primary=[t for t in parts['titles'] if '/{http://purl.org/dc/terms/}title[' in t['locator']]
            mains=[];contexts=[]
            for title in primary:
                text=unicodedata.normalize('NFKC',title['value']).strip()
                if _parent(title['locator'])!=_parent(volume['locator']) or not text.endswith('. '+label):break
                text=text[:-len('. '+label)]
                fields=text.split(' : ')
                if len(fields)!=2 or not all(fields) or any(c in fields[1] for c in ':='):
                    break
                mains.append({'main':fields[0],'raw_value':title['value'],'locator':title['locator']})
                contexts.append(fields[1])
            else:
                if not primary or len({_key(c) for c in contexts})!=1 or any(len(c)>200 for c in contexts):continue
                candidates=[p for p in parents if all(normalize_search(t['main']) in
                    {normalize_search(n) for n in [p['title'],*p['aliases']]} for t in mains)]
                if len(candidates)!=1:continue
                parent=candidates[0];assertion=assertions.get(parent['entry_id'])
                if (not assertion or parent['original_language']!='ja'
                        or normalize_search(assertion['title'])!=normalize_search(parent['title'])
                        or confirmation['publisher_book_id'] not in assertion['books']):continue
                origin=json.loads(stored[parent['entry_id']]['record_json'])['provenance']
                entity=self._payload(origin['raw_path'],origin['raw_sha256'],cache)['entities'][parent['qid']]
                urls={s.get('mainsnak',{}).get('datavalue',{}).get('value') for s in entity.get('claims',{}).get('P856',[])
                    if s.get('rank')!='deprecated' and s.get('mainsnak',{}).get('snaktype')=='value'
                    and isinstance(s.get('mainsnak',{}).get('datavalue',{}).get('value'),str)}
                if assertion.get('wikidata_source_url',assertion['source_url']) not in urls:continue
                if parent['authors'] and {person_key(a['name']) for a in parent['authors']}!={person_key(a['official_credit']) for a in match['authors']}:continue
                books=cache.get(('book_confirmation_documents',),{}).get(
                    ('kadokawa',confirmation['manifest_path'],confirmation['manifest_sha256']),{})
                book=books.get(confirmation['publisher_book_id'])
                if not book or any(_key(book['title'])!=_key(t['main']+' '+context+' '+label)
                        for t,context in zip(mains,contexts)):continue
                membership={'rule_version':'kadokawa-official-named-part-edition-membership-v1',
                    'scope':'edition_membership_only','edition_id':identity,'series_entry_id':parent['entry_id'],
                    'volume_number':None,'volume':volume,'part_label':label,'part_order':None,
                    'part_context':{'value':contexts[0],'locator':primary[0]['locator'],'source_value':primary[0]['value']},
                    'numeric_order_inferred':False,'main_titles':mains,
                    'publisher_book_id':confirmation['publisher_book_id'],
                    'publisher_confirmation_proof_sha256':binding['proof_sha256'],
                    'official_series_url':assertion['source_url'],'promotion_raw_sha256':assertion['raw_sha256'],
                    'explicit_book_link_evidence':assertion['books'][confirmation['publisher_book_id']],
                    'series_identity_basis':'exact_wikidata_P856_and_explicit_official_novel_book_list',
                    'person_identity_asserted':False,'original_work_identity_asserted':False}
                if 'redirect_evidence' in assertion:
                    membership.update(wikidata_source_url=assertion['wikidata_source_url'],redirect_evidence=assertion['redirect_evidence'],
                        series_identity_basis='exact_wikidata_P856_direct_official_relocation_and_explicit_novel_book_list')
                if paper_witness is not None:membership['legacy_paper_form_witness']=paper_witness
                result[identity]=membership
        return result

    def _dre_series_assertions(self, manifest_path, expected_sha256, cache):
        from .light_novel_ndl_series_v1 import publisher_assertions
        manifest = self._payload(manifest_path, expected_sha256, cache)
        assertions = publisher_assertions(manifest['dre_series_proof'], self._artifact, cache)
        if not assertions or any(a['index_id'] != 'dre-novels-series' for a in assertions.values()):
            raise ValueError('explicit DRE series sources required')
        return assertions

    def _dre_membership_candidates(self, rows, stored, authorities, assertions, cache):
        from .light_novel_ndl_series_v1 import person_key
        result = {}
        for row in rows:
            if row['entry_kind'] != 'edition':
                continue
            candidates = []
            record = json.loads(stored[row['entry_id']]['record_json'])
            for assertion in assertions.values():
                scoped = self._dre_label_assertion(assertion, record)
                if scoped is None:
                    continue
                scoped_assertion, label_witness = scoped
                match = self._publisher_series_match(scoped_assertion, record, cache)
                if match is None:
                    continue
                series = [s for s in rows if s['entry_kind'] == 'series' and s['entry_id'].startswith('lns:wd:')
                    and normalize_search(assertion['title']) in
                        {normalize_search(t) for t in [s['title'], *s['aliases']]}]
                if len(series) != 1:
                    continue
                parent = series[0]
                parent_record = json.loads(stored[parent['entry_id']]['record_json'])
                origin = parent_record['provenance']
                entity = self._payload(origin['raw_path'],origin['raw_sha256'],cache)['entities'][parent_record['id']]
                official_urls = {s.get('mainsnak',{}).get('datavalue',{}).get('value')
                    for s in entity.get('claims',{}).get('P856',[]) if s.get('rank')!='deprecated'
                    and s.get('mainsnak',{}).get('snaktype')=='value'
                    and isinstance(s.get('mainsnak',{}).get('datavalue',{}).get('value'),str)}
                official_identity = assertion['source_url'] in official_urls
                named_authors = {person_key(a['name']) for a in parent['authors']}
                official_authors = {person_key(n) for n in assertion['authors']}
                if (parent['original_language'] != 'ja'
                        or named_authors and named_authors != official_authors
                        or not official_identity and (not parent['authors'] or normalize_search(assertion['publisher']) not in
                            {normalize_search(p['label']) for p in parent['publishers']})):
                    continue
                ndl_authors = {identifier.rsplit('/', 1)[-1] for author in match['authors']
                               for identifier in author['authority_ids']}
                own_author_ids = {identifier['value'].rsplit('/',1)[-1]
                    for author in record['source_record']['contributors'] if author['role']=='author'
                    for identifier in author['identifiers'] if identifier['source']=='ndl-authority'}
                if not ndl_authors or own_author_ids != ndl_authors:
                    continue
                author_matches = []
                for author in ([] if official_identity else parent['authors']):
                    shared = authorities.get(author['qid'], set()) & ndl_authors
                    if not shared:
                        break
                    author_matches.append({'author_qid':author['qid'],'ndl_authorities':sorted(shared)})
                else:
                    if not official_identity and ndl_authors != {v for a in author_matches for v in a['ndl_authorities']}:
                        continue
                    candidates.append({'rule_version':'dre-publisher-wikidata-edition-membership-v1',
                        'scope':'edition_membership_only','edition_id':row['entry_id'],
                        'series_entry_id':parent['entry_id'],'volume_number':match['volume_number'],
                        'official_series_url':assertion['source_url'],'author_matches':author_matches,
                        'series_identity_basis':'exact_wikidata_P856_and_explicit_publisher_series' if official_identity else 'independent_author_authority_crosswalk',
                        'person_identity_asserted':False,
                        'edition_label_witness':label_witness,
                        'ndl_bibliography_match':match})
            if len(candidates) == 1:
                result[row['entry_id']] = candidates[0]
        return result

    def _resolve_scoped_wikidata_memberships(self, manifest_path, expected_sha256, *, edition_ids, own_roles, dre_series=False, kadokawa_promotions=False, named_parts=False):
        if isinstance(edition_ids, (str, bytes)) or edition_ids is None:
            raise ValueError('an explicit edition ID collection is required')
        requested = list(edition_ids)
        for identity in requested:
            if not _entry(identity).startswith('lne:ndl:'):
                raise ValueError('only typed NDL edition IDs may be requested')
        if len(requested) != len(set(requested)):
            raise ValueError('duplicate requested edition ID')
        result = {'scope': 'kadokawa_named_part_and_wikidata_series_only' if named_parts else 'kadokawa_promotion_and_wikidata_series_only' if kadokawa_promotions else 'dre_publisher_and_wikidata_series_only' if dre_series else 'wikidata_series_own_direct_roles_only' if own_roles else 'wikidata_series_only',
                  'requested_editions': len(requested),
                  'candidate_series': 0, 'linked': 0, 'unchanged': 0,
                  'protected_existing': 0, 'unresolved_editions': 0}
        if not requested:
            return result
        placeholders = ','.join('?' for _ in requested)
        with self._connect() as db:
            editions = {r['entry_id']: dict(r) for r in db.execute(
                'SELECT * FROM entries WHERE entry_id IN (' + placeholders + ')', requested)}
            if set(editions) != set(requested) or any(r['kind'] != 'edition' for r in editions.values()):
                raise ValueError('requested edition is missing or has a different kind')
            # record.kind is intentional: changing only SQL kind cannot hide a
            # second title candidate and turn ambiguous evidence into a link.
            series = db.execute("SELECT * FROM entries WHERE "
                "json_extract(record_json,'$.schema_version')='light-novel-metadata-v1' "
                "AND (kind='series' OR json_extract(record_json,'$.kind')='series')").fetchall()
        cache = {}
        assertions = (self._kadokawa_promotion_assertions(manifest_path,expected_sha256,cache) if kadokawa_promotions
            else self._dre_series_assertions(manifest_path, expected_sha256, cache) if dre_series else None)
        titles = {normalize_search(a['title']) for a in assertions.values()} if assertions else set()
        for entry in editions.values():
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored metadata SHA mismatch')
            record = json.loads(entry['record_json'])
            if record.get('schema_version') not in {'light-novel-edition-v1', 'light-novel-edition-bulk-v1'}:
                raise ValueError('requested record is not an NDL edition')
            titles.update(normalize_search(title['value']) for title in record['source_record']['titles'])
        selected = dict(editions)
        for entry in series:
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored metadata SHA mismatch')
            record = json.loads(entry['record_json'])
            if titles & {normalize_search(t) for t in [record['title'], *record['aliases']]}:
                identity = _entry(entry['entry_id'])
                if not identity.startswith('lns:wd:'):
                    raise ValueError('candidate is not a typed Wikidata series')
                selected[identity] = dict(entry)
        result['candidate_series'] = len(selected) - len(editions)
        rows = self._membership_base_rows(selected, cache)
        authorities = {} if kadokawa_promotions else self._author_authorities(manifest_path, expected_sha256, cache)
        candidates = (self._kadokawa_named_part_membership_candidates(rows,selected,assertions,cache) if named_parts
            else self._kadokawa_promotion_membership_candidates(rows,selected,assertions,cache) if kadokawa_promotions
            else self._dre_membership_candidates(rows, selected, authorities, assertions, cache) if dre_series
            else self._own_role_membership_candidates(rows, selected, authorities, cache)
            if own_roles else self._membership_candidates(rows, selected, authorities))
        result['unresolved_editions'] = len(requested) - len(candidates)
        with self._connect() as db:
            if own_roles:
                db.execute('BEGIN IMMEDIATE')
                current_candidates = {}
                for current in db.execute("SELECT * FROM entries WHERE "
                        "json_extract(record_json,'$.schema_version')='light-novel-metadata-v1' "
                        "AND (kind='series' OR json_extract(record_json,'$.kind')='series')"):
                    if hashlib.sha256(current['record_json'].encode()).hexdigest() != current['record_sha256']:
                        raise ValueError('stored metadata SHA mismatch')
                    record = json.loads(current['record_json'])
                    if titles & {normalize_search(t) for t in [record['title'], *record['aliases']]}:
                        current_candidates[current['entry_id']] = (current['kind'], current['record_sha256'])
                expected_candidates = {identity: (entry['kind'], entry['record_sha256'])
                    for identity, entry in selected.items() if identity not in editions}
                if current_candidates != expected_candidates:
                    raise ValueError('membership candidate set changed during resolution')
            # All selected witnesses must succeed before the first INSERT.
            for identity, entry in selected.items():
                current = db.execute('SELECT kind,record_sha256,record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()
                if (current is None or (current['kind'], current['record_sha256']) != (entry['kind'], entry['record_sha256'])
                        or own_roles and hashlib.sha256(current['record_json'].encode()).hexdigest() != current['record_sha256']):
                    raise ValueError('selected membership metadata changed during resolution')
            for edition_id in requested:
                membership = candidates.get(edition_id)
                previous = db.execute('SELECT * FROM edition_series_memberships WHERE edition_id=?', (edition_id,)).fetchone()
                if membership is None:
                    result['protected_existing'] += int(previous is not None)
                    continue
                series_id = membership['series_entry_id']
                proof = {'manifest_path': str(Path(manifest_path).resolve()), 'manifest_sha256': expected_sha256,
                         'edition_record_sha256': selected[edition_id]['record_sha256'],
                         'series_record_sha256': selected[series_id]['record_sha256'], 'membership': membership}
                if kadokawa_promotions:
                    current_binding=db.execute('SELECT proof_sha256 FROM edition_book_confirmations WHERE edition_id=? AND provider=?',(edition_id,'kadokawa')).fetchone()
                    if current_binding is None or current_binding[0]!=membership['publisher_confirmation_proof_sha256']:
                        raise ValueError('publisher confirmation changed during membership resolution')
                    proof['kind']='kadokawa_named_part_membership_v1' if named_parts else 'kadokawa_promotion_membership_v1'
                elif dre_series:
                    proof['kind'] = 'dre_wikidata_series_membership'
                elif own_roles:
                    proof['kind'] = 'wikidata_own_direct_role_membership_v2'
                encoded = _json(proof)
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                if previous:
                    if (previous['series_id'] == series_id and previous['proof_json'] == encoded
                            and previous['proof_sha256'] == digest):
                        result['unchanged'] += 1
                    else:
                        result['protected_existing'] += 1
                    continue
                db.execute('INSERT INTO edition_series_memberships VALUES(?,?,?,?)',
                           (edition_id, series_id, encoded, digest))
                result['linked'] += 1
        return result

    def _wikipedia_novel_credits(self, manifest_path, expected_sha256, stored, cache):
        """Verify bibliographic credits independently of classified article prose."""
        from .light_novel_wikipedia_credit_v1 import (
            verify_novel_credit, match_novel_credit,
            verify_novel_credit_v2, match_novel_credit_v2,
        )
        document = self._payload(manifest_path, expected_sha256, cache)
        version = document.get('version')
        if version == 'light-novel-wikipedia-author-credits-v1':
            verify_credit, match_credit = verify_novel_credit, match_novel_credit
        elif version == 'light-novel-wikipedia-author-credits-v2':
            verify_credit, match_credit = verify_novel_credit_v2, match_novel_credit_v2
        else:
            raise ValueError('unsupported Wikipedia novel-credit manifest')
        specs = {}
        for spec in document['series']:
            identity = _entry(spec['series_entry_id'])
            if not identity.startswith('lns:wd:') or identity in specs:
                raise ValueError('duplicate or invalid Wikipedia credit series')
            specs[identity] = spec
        result = {}
        for identity, spec in specs.items():
            if identity not in stored:
                continue
            entry = stored[identity]
            if entry['kind'] != 'series' or entry['record_sha256'] != spec['series_record_sha256']:
                raise ValueError('Wikipedia credit series metadata differs')
            record = json.loads(entry['record_json'])
            # The caller authenticates the series; this payload lookup shares
            # the same operation cache and does not infer a missing author QID.
            provenance = record['provenance']
            entity = self._payload(provenance['raw_path'], provenance['raw_sha256'], cache)['entities'][record['id']]
            key = ('wikipedia_novel_credit', version, identity, entry['record_sha256'],
                   spec['article_raw_path'], spec['article_raw_sha256'], spec['revision_id'])
            if key not in cache:
                article = self._payload(spec['article_raw_path'], spec['article_raw_sha256'], cache)
                cache[key] = verify_credit(record, entity, article, spec['revision_id'])
            credit = cache[key]
            anchor_id = _entry(spec['anchor_edition_id'])
            if not anchor_id.startswith('lne:ndl:'):
                raise ValueError('Wikipedia credit anchor must be an NDL edition')
            if anchor_id not in stored:
                with self._connect() as db:
                    anchor = db.execute('SELECT * FROM entries WHERE entry_id=?', (anchor_id,)).fetchone()
                if anchor is None:
                    raise ValueError('Wikipedia credit anchor is absent')
                stored[anchor_id] = dict(anchor)
            anchor = stored[anchor_id]
            if anchor['kind'] != 'edition' or anchor['record_sha256'] != spec['anchor_edition_record_sha256']:
                raise ValueError('Wikipedia credit anchor metadata differs')
            self._membership_base_rows({anchor_id: anchor}, cache)
            anchor_parts = self._edition_primary_bibliographic_parts(json.loads(anchor['record_json']), cache)
            match = match_credit(credit, anchor_parts, normalize_search) if anchor_parts else None
            if match is None:
                raise ValueError('Wikipedia credit anchor does not reproduce its bibliography')
            result[identity] = {**credit,
                'anchor_edition_id': anchor_id, 'anchor_edition_record_sha256': anchor['record_sha256'],
                'anchor_authority_ids': match['matched_authority_ids'],
                'article_raw_sha256': spec['article_raw_sha256']}
        return result

    def _wikipedia_credit_membership_candidates(self, rows, stored, credits, cache):
        from .light_novel_wikipedia_credit_v1 import match_novel_credit, match_novel_credit_v2
        index = {}
        for row in rows:
            if row['entry_kind'] == 'series' and row['entry_id'].startswith('lns:wd:'):
                for title in [row['title'], *row['aliases']]:
                    index.setdefault(normalize_search(title), set()).add(row['entry_id'])
        result = {}
        for row in rows:
            if row['entry_kind'] != 'edition':
                continue
            record = json.loads(stored[row['entry_id']]['record_json'])
            candidates = set()
            for title in record['source_record']['titles']:
                candidates.update(index.get(normalize_search(title['value']), ()))
            # A second title candidate is uncertainty, even without a credit
            # manifest for it. A selective manifest cannot hide a competitor.
            if len(candidates) != 1:
                continue
            identity = next(iter(candidates))
            credit = credits.get(identity)
            if credit is None:
                continue
            parts = self._edition_primary_bibliographic_parts(record, cache)
            match_credit = (match_novel_credit_v2
                if credit['rule_version'] == 'wikipedia-work-specific-novel-credit-v2'
                else match_novel_credit)
            match = match_credit(credit, parts, normalize_search) if parts else None
            if match is None or match['matched_authority_ids'] != credit['anchor_authority_ids']:
                continue
            result[row['entry_id']] = {**match,
                'anchor_edition_id': credit['anchor_edition_id'],
                'anchor_edition_record_sha256': credit['anchor_edition_record_sha256'],
                'article_raw_sha256': credit['article_raw_sha256'],
                'article_attribution': credit['attribution']}
        return result

    def resolve_wikipedia_credit_edition_memberships(self, manifest_path, expected_sha256, *, edition_ids):
        """Add scoped edition links from explicit, work-specific author credits.

        This does not assert person identity, fill P50, or create original works.
        Existing authority-based proofs and every existing binding are retained.
        """
        if isinstance(edition_ids, (str, bytes)) or edition_ids is None:
            raise ValueError('an explicit edition ID collection is required')
        requested = list(edition_ids)
        if len(requested) != len(set(requested)) or any(
                not _entry(identity).startswith('lne:ndl:') for identity in requested):
            raise ValueError('unique typed NDL edition IDs are required')
        result = {'scope': 'wikipedia_novel_credit_only', 'requested_editions': len(requested),
                  'candidate_series': 0, 'linked': 0, 'unchanged': 0,
                  'protected_existing': 0, 'unresolved_editions': 0}
        if not requested:
            return result
        with self._connect() as db:
            editions = {row['entry_id']: dict(row) for row in db.execute(
                'SELECT * FROM entries WHERE entry_id IN (' + ','.join('?' for _ in requested) + ')', requested)}
            if set(editions) != set(requested) or any(row['kind'] != 'edition' for row in editions.values()):
                raise ValueError('requested edition is missing or has a different kind')
            series = db.execute("SELECT * FROM entries WHERE "
                "json_extract(record_json,'$.schema_version')='light-novel-metadata-v1' "
                "AND (kind='series' OR json_extract(record_json,'$.kind')='series')").fetchall()
        titles = set()
        for entry in editions.values():
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored metadata SHA mismatch')
            record = json.loads(entry['record_json'])
            if record.get('schema_version') not in {'light-novel-edition-v1', 'light-novel-edition-bulk-v1'}:
                raise ValueError('requested record is not an NDL edition')
            titles.update(normalize_search(title['value']) for title in record['source_record']['titles'])
        selected = dict(editions)
        for entry in series:
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored metadata SHA mismatch')
            record = json.loads(entry['record_json'])
            if titles & {normalize_search(title) for title in [record['title'], *record['aliases']]}:
                if not _entry(entry['entry_id']).startswith('lns:wd:'):
                    raise ValueError('candidate is not a typed Wikidata series')
                selected[entry['entry_id']] = dict(entry)
        result['candidate_series'] = len(selected) - len(editions)
        cache = {}
        rows = self._membership_base_rows(selected, cache)
        credits = self._wikipedia_novel_credits(manifest_path, expected_sha256, selected, cache)
        candidates = self._wikipedia_credit_membership_candidates(rows, selected, credits, cache)
        result['unresolved_editions'] = len(requested) - len(candidates)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current_candidates = {}
            for current in db.execute("SELECT * FROM entries WHERE "
                    "json_extract(record_json,'$.schema_version')='light-novel-metadata-v1' "
                    "AND (kind='series' OR json_extract(record_json,'$.kind')='series')"):
                if hashlib.sha256(current['record_json'].encode()).hexdigest() != current['record_sha256']:
                    raise ValueError('stored metadata SHA mismatch')
                record = json.loads(current['record_json'])
                if titles & {normalize_search(title) for title in [record['title'], *record['aliases']]}:
                    current_candidates[current['entry_id']] = (current['kind'], current['record_sha256'])
            expected_candidates = {identity: (entry['kind'], entry['record_sha256'])
                for identity, entry in selected.items() if identity.startswith('lns:wd:')}
            if current_candidates != expected_candidates:
                raise ValueError('membership candidate set changed during resolution')
            for identity, entry in selected.items():
                current = db.execute('SELECT kind,record_sha256 FROM entries WHERE entry_id=?', (identity,)).fetchone()
                if current is None or (current['kind'], current['record_sha256']) != (entry['kind'], entry['record_sha256']):
                    raise ValueError('selected membership metadata changed during resolution')
            for identity in requested:
                membership = candidates.get(identity)
                previous = db.execute('SELECT * FROM edition_series_memberships WHERE edition_id=?', (identity,)).fetchone()
                if membership is None:
                    result['protected_existing'] += int(previous is not None)
                    continue
                series_id = membership['series_entry_id']
                proof = {'kind': 'wikipedia_novel_credit_membership',
                    'manifest_path': str(Path(manifest_path).resolve()), 'manifest_sha256': expected_sha256,
                    'edition_record_sha256': selected[identity]['record_sha256'],
                    'series_record_sha256': selected[series_id]['record_sha256'], 'membership': membership}
                encoded = _json(proof)
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                if previous:
                    if previous['series_id'] == series_id and previous['proof_json'] == encoded and previous['proof_sha256'] == digest:
                        result['unchanged'] += 1
                    else:
                        result['protected_existing'] += 1
                    continue
                db.execute('INSERT INTO edition_series_memberships VALUES(?,?,?,?)', (identity, series_id, encoded, digest))
                result['linked'] += 1
        return result

    def _attach_memberships(self, rows, stored, cache):
        with self._connect() as db:
            bindings = db.execute('SELECT * FROM edition_series_memberships').fetchall()
        if not bindings:
            return
        indexed = {r['entry_id']: r for r in rows}
        stored = {r['entry_id']: r for r in stored}
        plans = {}
        for binding in bindings:
            try:
                if hashlib.sha256(binding['proof_json'].encode()).hexdigest() != binding['proof_sha256']:
                    continue
                proof = json.loads(binding['proof_json'])
                edition_id, series_id = binding['edition_id'], binding['series_id']
                if (edition_id not in indexed or series_id not in indexed
                        or stored[edition_id]['record_sha256'] != proof['edition_record_sha256']
                        or stored[series_id]['record_sha256'] != proof['series_record_sha256']):
                    continue
                if proof.get('kind') == 'futaba_mnovels_workgroup_membership':
                    record = json.loads(stored[series_id]['record_json'])
                    source = record['publisher_series_proof']
                    group = self._futaba_workgroups(source['manifest_path'], source['manifest_sha256'], cache).get(source['reference_id'])
                    member = self._futaba_group_members(group, cache).get(edition_id) if group else None
                    if (member is None or edition_id not in record['member_edition_ids']
                            or proof != self._futaba_workgroup_membership(record, stored[series_id]['record_sha256'], edition_id, member)):
                        continue
                    indexed[edition_id].update(series_entry_id=series_id, volume_number=None,
                        relation_resolution='publisher_work_group_membership_verified',
                        admission_status='publisher_work_group_membership_verified',
                        story_series_identity_asserted=False, original_work_identity_asserted=False)
                    continue
                if proof.get('kind') == 'publisher_ndl_series_membership':
                    key = ('publisher_ndl_series_memberships',)
                    if key not in plans:
                        plans[key] = self._ndl_membership_candidates(rows, stored, cache)
                    membership = plans[key].get(edition_id)
                    if membership != proof['membership'] or membership.get('series_entry_id') != series_id:
                        continue
                    if membership.get('rule_version') == 'earthstar-explicit-ndl-workgroup-membership-v1':
                        indexed[edition_id].update(series_entry_id=series_id, volume_number=membership['volume_number'],
                            relation_resolution='publisher_work_group_membership_verified', light_novel_confirmed=True,
                            admission_status='publisher_work_group_membership_verified', series_membership_evidence=membership,
                            story_series_identity_asserted=False, original_work_identity_asserted=False)
                    else:
                        indexed[edition_id].update(series_entry_id=series_id, volume_number=membership['volume_number'],
                            relation_resolution='edition_series_membership_verified', light_novel_confirmed=True,
                            admission_status='edition_series_membership_verified', series_membership_evidence=membership)
                    continue
                if proof.get('kind') == 'wikipedia_novel_credit_membership':
                    key = ('wikipedia_novel_credit_memberships', proof['manifest_path'], proof['manifest_sha256'])
                    if key not in plans:
                        credits = self._wikipedia_novel_credits(proof['manifest_path'], proof['manifest_sha256'], stored, cache)
                        plans[key] = self._wikipedia_credit_membership_candidates(rows, stored, credits, cache)
                    membership = plans[key].get(edition_id)
                    if membership != proof['membership'] or membership.get('series_entry_id') != series_id:
                        continue
                    light_novel_confirmed = True
                    if membership.get('light_novel_classification_asserted') is False:
                        # A work-specific light-bungei author credit proves a
                        # relationship, not an LN genre. Retain only an
                        # independently confirmed edition or parent flag.
                        light_novel_confirmed = (indexed[edition_id].get('light_novel_confirmed') is True
                            or indexed[series_id].get('light_novel_confirmed') is True)
                    indexed[edition_id].update(series_entry_id=series_id, volume_number=membership['volume_number'],
                        relation_resolution='edition_series_membership_verified', light_novel_confirmed=light_novel_confirmed,
                        admission_status='edition_series_membership_verified', series_membership_evidence=membership)
                    continue
                if proof.get('kind') == 'kadokawa_named_part_membership_v1':
                    key=('kadokawa_named_part_memberships',proof['manifest_path'],proof['manifest_sha256'])
                    if key not in plans:
                        assertions=self._kadokawa_promotion_assertions(proof['manifest_path'],proof['manifest_sha256'],cache)
                        plans[key]=self._kadokawa_named_part_membership_candidates(rows,stored,assertions,cache)
                    membership=plans[key].get(edition_id)
                    if membership!=proof['membership'] or membership.get('series_entry_id')!=series_id:
                        continue
                    indexed[edition_id].update(series_entry_id=series_id,volume_number=None,
                        relation_resolution='edition_series_membership_verified',light_novel_confirmed=True,
                        admission_status='edition_series_membership_verified',series_membership_evidence=membership)
                    continue
                if proof.get('kind') == 'kadokawa_promotion_membership_v1':
                    key=('kadokawa_promotion_memberships',proof['manifest_path'],proof['manifest_sha256'])
                    if key not in plans:
                        assertions=self._kadokawa_promotion_assertions(proof['manifest_path'],proof['manifest_sha256'],cache)
                        plans[key]=self._kadokawa_promotion_membership_candidates(rows,stored,assertions,cache)
                    membership=plans[key].get(edition_id)
                    if membership!=proof['membership'] or membership.get('series_entry_id')!=series_id:
                        continue
                    indexed[edition_id].update(series_entry_id=series_id,volume_number=membership['volume_number'],
                        relation_resolution='edition_series_membership_verified',light_novel_confirmed=True,
                        admission_status='edition_series_membership_verified',series_membership_evidence=membership)
                    continue
                if proof.get('kind') == 'dre_wikidata_series_membership':
                    key = ('dre_memberships', proof['manifest_path'], proof['manifest_sha256'])
                    if key not in plans:
                        assertions = self._dre_series_assertions(proof['manifest_path'], proof['manifest_sha256'], cache)
                        authorities = self._author_authorities(proof['manifest_path'], proof['manifest_sha256'], cache)
                        plans[key] = self._dre_membership_candidates(rows, stored, authorities, assertions, cache)
                    membership = plans[key].get(edition_id)
                    if membership != proof['membership'] or membership.get('series_entry_id') != series_id:
                        continue
                    indexed[edition_id].update(series_entry_id=series_id, volume_number=membership['volume_number'],
                        relation_resolution='edition_series_membership_verified', light_novel_confirmed=True,
                        admission_status='edition_series_membership_verified', series_membership_evidence=membership)
                    continue
                if proof.get('kind') == 'wikidata_own_direct_role_membership_v2':
                    key = ('own_direct_roles', proof['manifest_path'], proof['manifest_sha256'])
                    if key not in plans:
                        authorities = self._author_authorities(proof['manifest_path'], proof['manifest_sha256'], cache)
                        plans[key] = self._own_role_membership_candidates(rows, stored, authorities, cache)
                    membership = plans[key].get(edition_id)
                    if membership != proof['membership'] or membership.get('series_entry_id') != series_id:
                        continue
                    indexed[edition_id].update(series_entry_id=series_id, volume_number=membership['volume_number'],
                        relation_resolution='edition_series_membership_verified', light_novel_confirmed=True,
                        admission_status='edition_series_membership_verified', series_membership_evidence=membership)
                    continue
                key = (proof['manifest_path'], proof['manifest_sha256'])
                if key not in plans:
                    authorities = self._author_authorities(*key, cache)
                    plans[key] = self._membership_candidates(rows, stored, authorities)
                membership = plans[key].get(edition_id)
                if membership != proof['membership'] or membership.get('series_entry_id') != series_id:
                    continue
                indexed[edition_id].update(series_entry_id=series_id, volume_number=membership['volume_number'],
                    relation_resolution='edition_series_membership_verified', light_novel_confirmed=True,
                    admission_status='edition_series_membership_verified', series_membership_evidence=membership)
            except (KeyError, TypeError, ValueError, OSError):
                # A stale relation cannot prevent access to independently valid bibliography.
                continue

    @staticmethod
    def _work_bridge_witnesses(db):
        witnesses = []
        for identity, kind, encoded, pin in db.execute(
                "SELECT entry_id,kind,record_json,record_sha256 FROM entries WHERE kind IN ('series','work','publisher_group')"):
            if hashlib.sha256(encoded.encode()).hexdigest() != pin:
                raise ValueError('current bridge witness metadata SHA differs')
            witnesses.append({'entry_id': identity, 'entry_kind': kind,
                'record_sha256': pin, 'record': json.loads(encoded)})
        return witnesses

    def _feature_rows(self, entry_ids=None):
        from .light_novel_feature_exports_v1 import verify_feature_export
        with self._connect() as db:
            if entry_ids is None:
                bindings = db.execute('SELECT * FROM features').fetchall()
            elif not entry_ids:
                return {}
            else:
                entry_ids = list(entry_ids)
                bindings = db.execute('SELECT * FROM features WHERE entry_id IN (' + ','.join('?' for _ in entry_ids) + ')', entry_ids).fetchall()
        manifests, result = {}, {}
        bridge_witnesses = None
        for binding in bindings:
            key = (binding['manifest_path'], binding['manifest_sha256'])
            try:
                if key not in manifests:
                    try:
                        manifests[key] = verify_feature_export(*key)
                    except (ValueError, OSError, KeyError, TypeError):
                        # Retry on the next operation, not for each binding.
                        manifests[key] = {'rows': []}
                for row in manifests[key]['rows']:
                    if row.get('entry_id', row.get('series_id')) == binding['entry_id']:
                        if manifests[key].get('version') == 'light-novel-work-archived-label-feature-export-v1':
                            from .light_novel_work_archive_bridge_v1 import check_current_target
                            if bridge_witnesses is None:
                                with self._connect() as db:
                                    bridge_witnesses = self._work_bridge_witnesses(db)
                            target = next((w for w in bridge_witnesses if w['entry_id'] == binding['entry_id']), None)
                            if target is None:
                                raise ValueError('current bridge target absent')
                            check_current_target(row, target['record'], target['record_sha256'], bridge_witnesses)
                        result[binding['entry_id']] = row
                        break
            except (ValueError, OSError, KeyError, TypeError):
                # Invalid features cannot erase still-valid bibliographic access.
                continue
        return result

    def import_features(self, manifest_path, expected_sha256):
        from .light_novel_feature_exports_v1 import verify_feature_export, target_allowed
        document = verify_feature_export(manifest_path, expected_sha256)
        targets = [(row, _entry(row.get('entry_id', row.get('series_id')))) for row in document['rows']]
        identities = sorted({identity for _, identity in targets})
        bridge = document.get('version') == 'light-novel-work-archived-label-feature-export-v1'

        def selected_entries(db):
            selected = {}
            for start in range(0, len(identities), 400):
                batch = identities[start:start + 400]
                selected.update((row['entry_id'], dict(row)) for row in db.execute(
                    'SELECT * FROM entries WHERE entry_id IN (' + ','.join('?' for _ in batch) + ')', batch))
            return selected

        with self._connect() as db:
            stored = selected_entries(db)
            initial_witnesses = self._work_bridge_witnesses(db) if bridge else None
        cache, known = {}, {}
        for identity, entry in stored.items():
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored feature target metadata SHA mismatch')
            row = self._project(json.loads(entry['record_json']), cache)
            if row['entry_id'] != identity or row['entry_kind'] != entry['kind']:
                raise ValueError('stored feature target typed identity differs')
            known[identity] = row
        result = {'imported': 0, 'held': 0}
        plans = []
        for row, entry_id in targets:
            if entry_id not in known or not target_allowed(document, row, {**known[entry_id], 'record_sha256': stored[entry_id]['record_sha256']}):
                result['held'] += 1
            else:
                if bridge:
                    from .light_novel_work_archive_bridge_v1 import check_current_target
                    check_current_target(row, json.loads(stored[entry_id]['record_json']),
                        stored[entry_id]['record_sha256'], initial_witnesses)
                plans.append(entry_id)
        path = str(Path(manifest_path).resolve())
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = selected_entries(db)
            if set(current) != set(stored):
                raise ValueError('feature target set changed during import')
            if any((current[identity]['kind'], current[identity]['record_sha256']) !=
                   (entry['kind'], entry['record_sha256']) for identity, entry in stored.items()):
                raise ValueError('feature target metadata changed during import')
            if bridge:
                current_witnesses = self._work_bridge_witnesses(db)
                for row, entry_id in targets:
                    if entry_id in plans:
                        check_current_target(row, json.loads(current[entry_id]['record_json']),
                            current[entry_id]['record_sha256'], current_witnesses)
            for entry_id in plans:
                db.execute('INSERT OR REPLACE INTO features VALUES(?,?,?)', (entry_id, path, expected_sha256))
                result['imported'] += 1
        return result

    @staticmethod
    def _apply_feature(row, feature):
        if not feature or row['entry_kind'] in {'publisher_group', 'publisher_reference'}:
            return
        feature_scope = 'work' if row['entry_kind'] == 'work' else 'series'
        if feature_scope == 'work' and (feature.get('scope') != 'work'
                or feature.get('entity_kind') != 'light_novel_work'
                or feature.get('work_id') != row['entry_id'] or feature.get('entry_id') != row['entry_id']):
            return
        if row['entry_kind'] == 'series' and feature.get('scope') == 'work':
            return
        public_label_keys = {'label_id', 'label', 'axis', 'decision', 'scope', 'relevance',
            'raw_confidence', 'calibrated_confidence', 'calibration_status', 'calibration_version',
            'evidence', 'missing_reason', 'spoiler'}
        labels = [{k: v for k, v in label.items() if k in public_label_keys}
                  for label in feature.get('labels', feature.get('admitted_labels', []))
                  if label.get('scope') == feature_scope and label.get('decision') == 'present']
        content_ids = {s.get('evidence_id') for s in feature.get('source_attributions', [])
                       if s.get('kind') in ('synopsis', 'excerpt', 'note')}
        for label in labels:
            label['evidence'] = [_safe(e) for e in label.get('evidence', [])]
            for evidence in label['evidence']:
                evidence['quote'] = None
            label['evidence_basis'] = 'content' if any(e.get('evidence_id') in content_ids for e in label['evidence']) else 'bibliographic'
        row['native_features'] = row['llm_features'] = labels
        row['llm_held_features'] = [{k: v for k, v in label.items() if k in {'label_id', 'reason', 'axis'}}
                                  for label in feature.get('held_labels', [])]
        row['content_sources'] = [_content_source(s,
            work_id=row['entry_id'] if feature_scope == 'work' else None,
            archive_context=feature.get('archive_context'), fresh_classification=feature.get('fresh_classification'))
            for s in feature.get('source_attributions', feature.get('source_attribution', []))]
        row['content_features_adopted'] = bool(labels) and feature.get('content_features_adopted') is True
        row['recommendable'] = bool(row['genres'] or labels)
        row['recommendation_mode'] = (('work_content_preferences' if feature_scope == 'work' else 'series_content_preferences')
            if row['content_features_adopted'] else row['recommendation_mode'])
        if (feature_scope == 'work' and feature.get('archive_context') == 'historical_mixed_metadata_lead_and_plot'
                and feature.get('fresh_classification') is False):
            row['work_feature_context'] = 'historical_mixed_metadata_lead_and_plot'
        row['classification_axes'] = [{'axis': axis, 'adopted_count': sum(label.get('axis') == axis for label in labels),
            'held_count': sum(label.get('axis', label.get('label_id', '').split('.')[0]) == axis for label in feature.get('held_labels', [])),
            'unknown': not any(label.get('axis') == axis for label in labels)} for axis in AXES]

    def _candidate_ids(self, records, *, query=None, entry_id=None, confirmation_titles=None):
        """Use digest-checked saved fields only to find a verification superset.

        All admitted candidates and their potentially competing series are
        subsequently reprojected from raw evidence. Saved fields are never a
        public projection or proof of a relation.
        """
        key = normalize_search(query or '')
        punctuationless_key = _search_without_punctuation(key)
        terms = _search_terms(query or '')
        if not key and entry_id is None:
            return None
        values, title_keys, series_titles, qids = {}, {}, {}, {}
        edges = {identity: set() for identity in records}
        for identity, record in records.items():
            base = record.get('anchor_edition_record', record)
            source = base.get('source_record', {})
            titles = [base.get('title', ''), *base.get('aliases', []),
                      *[t['value'] for t in source.get('titles', [])],
                      *source.get('extra', {}).get('title_readings', [])]
            titles.extend((confirmation_titles or {}).get(identity, ()))
            if record.get('schema_version') == OVERLAP_BUNKO_DETAIL_REFERENCE_SCHEMA:
                titles.extend(record.get('isbn', []))
                titles.extend(re.sub(r'[^0-9Xx]', '', isbn) for isbn in record.get('isbn', []))
            if (record.get('publisher_series_proof', {}).get('cobalt_series_path')
                    or record.get('publisher_series_proof', {}).get('earthstar_series_path')
                    or record.get('publisher_series_proof', {}).get('kind') in {'futaba_mnovels_workgroup', 'dre-novels-series-v2'}) and record.get('title'):
                titles.append(record['title'])
            authors = [a.get('label', '') if isinstance(a, dict) else a for a in base.get('authors', [])]
            if record.get('schema_version') in {profile['schema'] for profile in SOURCE_GROUP_PROVIDERS.values()}:
                children = record['publisher_product_references']
                authors.extend(c['name'] for child in children for c in child['credits'] if c['role'] == 'author')
                child_titles = [child['title'] for child in children]
                if record['schema_version'] == SOURCE_GROUP_PROVIDERS['ecobalt']['schema']:
                    authors.append(record['heading_author_display'])
                if record['schema_version'] in {p['schema'] for p in SOURCE_GROUP_PROVIDERS.values() if p.get('index_only')}:
                    authors.extend(credit['name'] for credit in record['index_credit_literals'])
            elif record.get('schema_version') == ECOBALT_REFERENCE_SCHEMA:
                child_titles = []
                authors.append(record['author_display'])
            elif record.get('schema_version') == FUTABA_REFERENCE_SCHEMA:
                child_titles = []
                authors.extend(credit['name'] for credit in record['publisher_product_reference']['credits'] if credit['role'] == 'author')
            elif record.get('schema_version') in {KADOKAWA_REFERENCE_SCHEMA, KADOKAWA_PRINT_REFERENCE_SCHEMA, KADOKAWA_TRADE_REFERENCE_SCHEMA, TOBOOKS_REFERENCE_SCHEMA, GAGAGA_REFERENCE_SCHEMA, KODANSHA_REFERENCE_SCHEMA, OVERLAP_DETAIL_REFERENCE_SCHEMA, OVERLAP_BUNKO_DETAIL_REFERENCE_SCHEMA, SHUEISHA_DX_REFERENCE_SCHEMA, GC_REFERENCE_SCHEMA, OVERLAP_BUNKO_REFERENCE_SCHEMA, ALPHAPOLIS_REFERENCE_SCHEMA, HIFUMI_REFERENCE_SCHEMA, ICHIJINSHA_IRIS_REFERENCE_SCHEMA, ICHIJINSHA_PROMOTION_REFERENCE_SCHEMA, EARTHSTAR_REFERENCE_SCHEMA}:
                child_titles = []
                authors.extend(credit['name'] for credit in record['publisher_product_reference']['credit_literals'])
                if record.get('schema_version') in {GAGAGA_REFERENCE_SCHEMA, KODANSHA_REFERENCE_SCHEMA, OVERLAP_DETAIL_REFERENCE_SCHEMA, OVERLAP_BUNKO_DETAIL_REFERENCE_SCHEMA, SHUEISHA_DX_REFERENCE_SCHEMA, GC_REFERENCE_SCHEMA, OVERLAP_BUNKO_REFERENCE_SCHEMA, ALPHAPOLIS_REFERENCE_SCHEMA, HIFUMI_REFERENCE_SCHEMA, ICHIJINSHA_IRIS_REFERENCE_SCHEMA, ICHIJINSHA_PROMOTION_REFERENCE_SCHEMA, EARTHSTAR_REFERENCE_SCHEMA}:
                    authors.extend(credit['name'] for credit in record['publisher_product_reference']['credits'])
            else:
                child_titles = []
            if entry_id is None:
                values[identity] = (_search_text([*titles, *child_titles]), _search_text(authors))
            title_keys[identity] = {normalize_search(t) for t in titles if t}
            # A possible RDF ISBD main title widens selection only. Its literal
            # source-field structure is checked later by _source_with_title_parts.
            for title in titles:
                if title and len(parts := re.split(r' (?:=|:) ', title, maxsplit=1)) > 1:
                    title_keys[identity].add(normalize_search(parts[0]))
            if identity.startswith('lns:'):
                for title in title_keys[identity]:
                    series_titles.setdefault(title, set()).add(identity)
            if record.get('source') == 'wikidata':
                qids[record['id']] = identity
        for identity, record in records.items():
            for prop in ('P179', 'P527'):
                for qid in record.get('relations', {}).get(prop, []):
                    if qid in qids:
                        other = qids[qid]
                        edges[identity].add(other)
                        edges[other].add(identity)
        with self._connect() as db:
            bindings = db.execute('SELECT edition_id,series_id FROM edition_series_memberships').fetchall()
        for binding in bindings:
            a, b = binding['edition_id'], binding['series_id']
            if a in edges and b in edges:
                edges[a].add(b)
                edges[b].add(a)
        selected = {entry_id} & records.keys() if entry_id else {
            identity for identity, (title_text, names) in values.items()
            if _search_contains(title_text, key, punctuationless_key, terms) or key in names}
        pending = list(selected)
        while pending:
            identity = pending.pop()
            relatives = set(edges[identity])
            for title in title_keys[identity]:
                relatives.update(series_titles.get(title, ()))
            added = relatives - selected
            selected.update(added)
            pending.extend(added)
        return selected

    def _rows(self, *, include_features=True, feature_entry_ids=None, _cache=None,
              candidate_query=None, detail_entry_id=None, recommendation_liked_ids=None):
        cache = {} if _cache is None else _cache
        with self._connect() as db:
            statement = 'SELECT e.*, r.status FROM entries e LEFT JOIN read_status r USING(entry_id)'
            parameters = ()
            if recommendation_liked_ids is not None:
                # Editions are leaves in _families: only liked/read leaves can
                # change an exclusion. Authenticate those leaves and parents,
                # keeping every ranked series/work and its Wikidata relations.
                statement += ''' WHERE e.kind IN ('series','work') OR
                    (e.kind='edition' AND r.status='read') OR
                    e.entry_id IN (SELECT value FROM json_each(?)) OR
                    e.entry_id IN (SELECT m.series_id FROM edition_series_memberships m
                        LEFT JOIN read_status status ON status.entry_id=m.edition_id
                        WHERE status.status='read' OR
                        m.edition_id IN (SELECT value FROM json_each(?)))'''
                liked_json = json.dumps(list(recommendation_liked_ids))
                parameters = (liked_json, liked_json)
            stored = db.execute(statement, parameters).fetchall()
            has_features = db.execute('SELECT count(*) FROM features').fetchone()[0]
        records = {}
        for entry in stored:
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored metadata SHA mismatch')
            records[entry['entry_id']] = json.loads(entry['record_json'])
        cache[('reference_all_stored',)] = stored
        cache[('reference_all_records',)] = records
        confirmation_titles = self._saved_confirmation_search_titles(stored, records, cache) if (
            normalize_search(candidate_query or '') and detail_entry_id is None) else None
        candidate_ids = self._candidate_ids(records, query=candidate_query, entry_id=detail_entry_id,
                                            confirmation_titles=confirmation_titles)
        if candidate_ids is not None:
            stored = [entry for entry in stored if entry['entry_id'] in candidate_ids]
        features = self._feature_rows(feature_entry_ids) if include_features and has_features else {}
        rows = []
        for entry in stored:
            record = records[entry['entry_id']]
            row = self._project(record, cache)
            if row['entry_id'] != entry['entry_id'] or row['entry_kind'] != entry['kind']:
                raise ValueError('stored typed identity differs')
            row['read_status'] = entry['status'] or 'unknown'
            self._apply_feature(row, features.get(row['entry_id']))
            rows.append(row)
        self._attach_memberships(rows, stored, cache)
        self._attach_book_confirmations(rows, stored, cache)
        self._attach_bibliographic_correspondences(rows, stored, cache)
        return rows

    @staticmethod
    def _families(rows):
        by_qid = {r['qid']: r for r in rows if r['qid']}
        parent = {r['entry_id']: r['entry_id'] for r in rows}

        def find(key):
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        def join(a, b):
            parent[find(a)] = find(b)

        for row in rows:
            series_id = row.get('series_entry_id')
            if row['entry_kind'] == 'edition' and series_id in parent:
                join(row['entry_id'], series_id)
            for qid in row['relations'].get('P179', []):
                other = by_qid.get(qid)
                if other and other['entry_kind'] == 'series' and row['entry_kind'] in {'work', 'series'}:
                    join(row['entry_id'], other['entry_id'])
            if row['entry_kind'] == 'series':
                for qid in row['relations'].get('P527', []):
                    other = by_qid.get(qid)
                    if other and other['entry_kind'] in {'work', 'series'}:
                        join(other['entry_id'], row['entry_id'])
        return {key: find(key) for key in parent}

    @staticmethod
    def _filtered(rows, *, query='', genre=None, language=None, year_from=None, year_to=None,
                  read_filter='all', entry_kind=None):
        if read_filter not in READ_STATUSES | {'all', 'exclude_read'}:
            raise ValueError('invalid read filter')
        if entry_kind not in (None, '', 'series', 'work', 'edition', 'publisher_group', 'publisher_reference'):
            raise ValueError('invalid entry kind')
        years = []
        for value in (year_from, year_to):
            if value in (None, ''):
                years.append(None)
            elif isinstance(value, bool):
                raise ValueError('invalid publication year')
            else:
                years.append(int(value))
        key = normalize_search(query)
        punctuationless_key = _search_without_punctuation(key)
        terms = _search_terms(query)
        result = []
        for row in rows:
            children = row.get('publisher_product_references', []) if row['entry_kind'] == 'publisher_group' else []
            child_titles = [child['title'] for child in children]
            names = [a['name'] or a['qid'] for a in row['authors']]
            names.extend(c['name'] for child in children for c in child['credits'] if c['role'] == 'author')
            if row.get('publisher_work_group_kind') == SOURCE_GROUP_PROVIDERS['ecobalt']['public_kind']:
                names.append(row['heading_author_display'])
            if row.get('publisher_work_group_kind') in {p['public_kind'] for p in SOURCE_GROUP_PROVIDERS.values() if p.get('index_only')}:
                names.extend(credit['name'] for credit in row['index_credit_literals'])
            if row['entry_kind'] == 'publisher_reference':
                if 'author_display' in row:
                    names.append(row['author_display'])
                elif row.get('publisher_reference_kind') in {PUBLISHER_REFERENCE_PROVIDERS[p]['public_kind'] for p in ('kadokawa', 'kadokawaprint', 'kadokawatrade', 'tobooks', 'gagaga', 'kodansha', 'overlapdetails', 'overlapbunkodetails', 'shueishadx', 'gc', 'overlap', 'alphapolis', 'hifumi', 'ichijinshairis', 'ichijinshairisspecial', 'earthstar', 'dre')}:
                    names.extend(credit['name'] for credit in row['publisher_product_reference']['credit_literals'])
                    if row.get('publisher_reference_kind') in {'gagaga_product_reference', 'kodansha_product_reference', 'overlap_detail_product_reference', 'overlap_bunko_detail_product_reference', 'shueisha_dx_product_reference', 'gc_product_reference', 'overlap_bunko_product_reference', 'alphapolis_commercial_book_reference', 'hifumi_product_reference', 'ichijinsha_iris_product_reference', 'ichijinsha_iris_promotion_reference', 'earthstar_work_reference', 'dre_product_reference'}:
                        names.extend(credit['name'] for credit in row['publisher_product_reference']['credits'])
                else:
                    names.extend(credit['name'] for credit in row['publisher_product_reference']['credits'] if credit['role'] == 'author')
            confirmed_titles = []
            if row.get('publisher_reference_kind') == 'overlap_bunko_detail_product_reference':
                confirmed_titles.extend(row.get('isbn', []))
                confirmed_titles.extend(re.sub(r'[^0-9Xx]', '', isbn) for isbn in row.get('isbn', []))
            if (row.get('edition_bibliography_confirmed') is True
                    and re.fullmatch(r'https://www\.kadokawa\.co\.jp/product/[0-9]{12}/',
                                     row.get('confirmation_source_url', ''))):
                # Only _attach_book_confirmations sets this evidence after the
                # source frame and the complete saved match are reproduced.
                title = LightNovelCatalogue._confirmed_part_search_title(row.get('edition_confirmation_evidence'))
                if title:
                    confirmed_titles.append(title)
                confirmed_titles.extend(LightNovelCatalogue._confirmed_kadokawa_composed_search_titles(
                    row.get('edition_confirmation_evidence')))
            elif (row.get('edition_bibliography_confirmed') is True
                    and re.fullmatch(r'https://www\.tobooks\.jp/contents/[0-9]+', row.get('confirmation_source_url', ''))):
                match = row.get('edition_confirmation_evidence')
                if isinstance(match, dict) and match.get('official_source_url') == row['confirmation_source_url']:
                    confirmed_titles.extend(LightNovelCatalogue._confirmed_tobooks_search_titles(match))
            elif (row.get('edition_bibliography_confirmed') is True
                    and re.fullmatch(r'https://www\.ichijinsha\.co\.jp/iris/title/[^/?#]+/', row.get('confirmation_source_url', ''))):
                confirmed_titles.extend(LightNovelCatalogue._confirmed_iris_search_titles(
                    row.get('edition_confirmation_evidence')))
            if key and not (_search_contains(_search_text([
                    row['title'], *row['aliases'], *child_titles, *confirmed_titles]), key, punctuationless_key, terms)
                    or key in _search_text(names)):
                continue
            if genre and genre not in {g['qid'] for g in row['genres']}:
                continue
            if language and language != (row.get('language') or row['original_language']):
                continue
            if entry_kind and row['entry_kind'] != entry_kind and not (entry_kind == 'series' and row['entry_kind'] == 'publisher_group'):
                continue
            if years[0] is not None and (row['year'] is None or row['year'] < years[0]):
                continue
            if years[1] is not None and (row['year'] is None or row['year'] > years[1]):
                continue
            if read_filter == 'exclude_read' and row['read_status'] == 'read':
                continue
            if read_filter in READ_STATUSES and row['read_status'] != read_filter:
                continue
            result.append(row)
        return result

    def _publisher_reference_query_rows(self, query):
        """Use saved fields only as a superset; authenticate every returned listing."""
        cache = {}
        with self._connect() as db:
            stored = db.execute('''SELECT e.*, r.status FROM entries e LEFT JOIN read_status r USING(entry_id)
                WHERE e.kind='publisher_reference' OR e.entry_id LIKE 'lnr:pub:%' ''').fetchall()
        records = []
        for entry in stored:
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored metadata SHA mismatch')
            record = json.loads(entry['record_json'])
            identity = _entry(entry['entry_id'])
            provider = identity.split(':')[2] if identity.startswith('lnr:pub:') else None
            profile = PUBLISHER_REFERENCE_PROVIDERS.get(provider)
            if (profile is None or not isinstance(record, dict) or entry['kind'] != 'publisher_reference'
                    or record.get('schema_version') != profile['schema'] or record.get('source') != 'publisher'
                    or record.get('provider') != profile['record_provider'] or record.get('kind') != 'publisher_reference'
                    or record.get('id') != identity.rsplit(':', 1)[1]):
                raise ValueError('stored typed identity differs')
            records.append((entry, provider, record))
        key = normalize_search(query)
        punctuationless, terms = _search_without_punctuation(key), _search_terms(query)
        rows = []
        for entry, provider, record in records:
            product = record.get('publisher_product_reference', {})
            names = [record.get('author_display', '')]
            for field in ('credits', 'credit_literals'):
                names.extend(c.get('name', '') for c in product.get(field, []))
            search_titles = [record['title']]
            if provider == 'overlapbunkodetails':
                search_titles.extend(record.get('isbn', []))
                search_titles.extend(re.sub(r'[^0-9Xx]', '', isbn) for isbn in record.get('isbn', []))
            if not (_search_contains(_search_text(search_titles), key, punctuationless, terms)
                    or key in _search_text(names)):
                continue
            row = self._project_publisher_reference(provider, record, cache)
            if row['entry_id'] != entry['entry_id'] or row['entry_kind'] != entry['kind']:
                raise ValueError('stored typed identity differs')
            row['read_status'] = entry['status'] or 'unknown'
            rows.append(row)
        return rows

    def search_catalogue(self, query='', genre=None, language=None, year_from=None, year_to=None,
                         read_filter='all', page=1, page_size=30, entry_kind=None, exclude_work_qids=None):
        if isinstance(page, bool) or isinstance(page_size, bool) or not 1 <= page_size <= 100 or page < 1:
            raise ValueError('page must be positive and page_size 1..100')
        filters = dict(query=query, genre=genre, language=language, year_from=year_from,
                       year_to=year_to, read_filter=read_filter, entry_kind=entry_kind)
        query_key = normalize_search(query)
        reference_query = entry_kind == 'publisher_reference' and bool(query_key)
        kind_priority = {'series': 0, 'publisher_group': 0, 'work': 1, 'edition': 2, 'publisher_reference': 3}
        candidates = (self._publisher_reference_query_rows(query) if reference_query
                      else self._rows(include_features=False, candidate_query=query))
        rows = sorted(self._filtered(candidates, **filters), key=lambda r: (
            0 if query_key and normalize_search(r['title']) == query_key else 1,
            kind_priority[r['entry_kind']], r['title'], r['entry_id']))
        if exclude_work_qids:
            rows = [r for r in rows if r['entry_kind'] != 'work' or r['qid'] not in exclude_work_qids]
        selected = rows[(page - 1) * page_size:page * page_size]
        features = {} if reference_query else self._feature_rows({row['entry_id'] for row in selected})
        for row in selected:
            self._apply_feature(row, features.get(row['entry_id']))
        return {'items': selected, 'total': len(rows),
                'page': page, 'page_size': page_size, 'pages': math.ceil(len(rows) / page_size), 'filters': filters}

    def work_detail(self, entry_id, **_):
        entry_id = _entry(entry_id)
        if entry_id.startswith('lnr:pub:'):
            # A standalone listing has no family/member/feature identity. Authenticate
            # its own stored tuple and source without loading the catalogue graph.
            provider = entry_id.split(':')[2]
            profile = PUBLISHER_REFERENCE_PROVIDERS[provider]
            with self._connect() as db:
                entry = db.execute('''SELECT e.*, r.status FROM entries e
                    LEFT JOIN read_status r USING(entry_id) WHERE e.entry_id=?''', (entry_id,)).fetchone()
            if entry is None:
                return None
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored metadata SHA mismatch')
            record = json.loads(entry['record_json'])
            if (entry['kind'] != 'publisher_reference' or record.get('schema_version') != profile['schema']
                    or record.get('provider') != profile['record_provider']
                    or record.get('id') != entry_id.rsplit(':', 1)[1]):
                raise ValueError('stored typed identity differs')
            row = self._project_publisher_reference(provider, record, {})
            if row['entry_id'] != entry_id or row['entry_kind'] != entry['kind']:
                raise ValueError('stored typed identity differs')
            row.update(read_status=entry['status'] or 'unknown', members=[])
            return row
        rows = self._rows(feature_entry_ids={entry_id}, detail_entry_id=entry_id)
        row = next((r for r in rows if r['entry_id'] == entry_id), None)
        if row:
            families = self._families(rows)
            member_keys = ('entry_id', 'entry_kind', 'qid', 'title', 'year', 'read_status', 'source_url',
                           'light_novel_confirmed', 'admission_status', 'language', 'original_language',
                           'relation_resolution', 'volume', 'volume_number', 'publication_label', 'series_entry_id',
                           'series_membership_evidence', 'series_identity_basis', 'series_title_source',
                           'story_series_identity_asserted', 'original_work_identity_asserted',
                           'publisher_work_group_kind', 'source_frame_member_count', 'verified_bibliographic_member_count',
                           'frame_membership_complete', 'exact_grouping_filter_complete', 'global_membership_complete')
            row['members'] = [{k: r[k] for k in member_keys if k in r}
                              for r in rows if r['entry_id'] != entry_id and families[r['entry_id']] == families[entry_id]]
            row['members'].sort(key=lambda r: (
                r.get('series_membership_evidence',{}).get('rule_version')=='kadokawa-official-named-part-edition-membership-v1',
                r.get('volume_number') or 0, r['title'], r['entry_id']))
            row.setdefault('relation_resolution', 'explicit_source_assertions_only')
        return row

    def set_read_status(self, entry_id, status):
        entry_id = _entry(entry_id)
        if status not in READ_STATUSES:
            raise ValueError('invalid read status')
        if self.work_detail(entry_id) is None:
            return None
        with self._connect() as db:
            db.execute('INSERT OR REPLACE INTO read_status VALUES(?,?,?)', (entry_id, status, datetime.now(timezone.utc).isoformat()))
        return {'entry_id': entry_id, 'work_id': entry_id, 'read_status': status}

    def catalogue_facets(self):
        return self._facets_from_rows(self._rows(include_features=False))

    @staticmethod
    def _facets_from_rows(rows):
        genres = {}
        kinds = {}
        languages = {}
        for row in rows:
            kinds[row['entry_kind']] = kinds.get(row['entry_kind'], 0) + 1
            language = row.get('language') or row['original_language']
            if language:
                languages[language] = languages.get(language, 0) + 1
            for g in row['genres']:
                genres.setdefault(g['qid'], {**g, 'count': 0})['count'] += 1
        return {'languages': [{'value': k, 'count': v} for k, v in sorted(languages.items())],
                'genres': sorted(genres.values(), key=lambda g: (-g['count'], g['qid'])),
                'entry_kinds': [{'value': k, 'count': v} for k, v in sorted(kinds.items())]}

    def preference_facets(self):
        from .scale_preferences_v1 import _taxonomy
        rows = self._rows()
        features = {}
        taxonomy = _taxonomy()
        for row in rows:
            for label in row['llm_features']:
                label_id = label['label_id']
                features.setdefault(label_id, {**taxonomy.get(label_id, {'label_id': label_id, 'axis': label['axis'], 'label': label.get('label', label_id)}), 'count': 0})['count'] += 1
        return {**self._facets_from_rows(rows), 'features': list(features.values()), 'axes': list(AXES),
                'classification_scope': 'mixed_explicit_units' if any(r['entry_kind'] == 'work' and r['llm_features'] for r in rows) else 'series', 'calibrated': False}

    def catalogue_stats(self):
        return self._stats_from_rows(self._rows(), *self._failure_counts())

    def catalogue_overview(self):
        """Produce both aggregates from one fully authenticated projection."""
        rows = self._rows()
        return {'stats': self._stats_from_rows(rows, *self._failure_counts()),
                'facets': self._facets_from_rows(rows)}

    def _failure_counts(self):
        with self._connect() as db:
            failures = db.execute('SELECT count(*) FROM ingestion_failures').fetchone()[0]
            unresolved_failures = db.execute('''SELECT count(DISTINCT f.source_id) FROM ingestion_failures f
                WHERE NOT EXISTS(SELECT 1 FROM entries e WHERE json_extract(e.record_json,'$.id')=f.source_id)''').fetchone()[0]
        return failures, unresolved_failures

    @staticmethod
    def _stats_from_rows(rows, failures, unresolved_failures):
        counts = {k: sum(r['entry_kind'] == k for r in rows) for k in ('series', 'work', 'edition')}
        anchored_groups = sum(r.get('series_identity_basis') == 'publisher_work_group_and_ndl_bibliography' for r in rows)
        work_groups = anchored_groups + sum(r['entry_kind'] == 'publisher_group' for r in rows)
        counts['series'] -= anchored_groups
        statuses = {s: sum(r['read_status'] == s for r in rows) for s in READ_STATUSES}
        content = sum(r['entry_kind'] == 'series' and r.get('series_identity_basis') != 'publisher_work_group_and_ndl_bibliography'
                      and r['content_features_adopted'] for r in rows)
        content_works = sum(r['entry_kind'] == 'work' and r['content_features_adopted'] for r in rows)
        source = sum(r['entry_kind'] == 'series' and r.get('series_identity_basis') != 'publisher_work_group_and_ndl_bibliography'
                     and bool(r['genres']) for r in rows)
        return {'works': len(rows), 'searchable_works': len(rows), 'series': counts['series'],
                'publisher_work_groups': work_groups,
                'publisher_reference_count': sum(r['entry_kind'] == 'publisher_reference' for r in rows),
                'individual_works': counts['work'], 'editions': counts['edition'], 'held_works': 0,
                'verified_edition_series_relations': sum(bool(r.get('series_entry_id')) and
                    r.get('relation_resolution') != 'publisher_work_group_membership_verified' for r in rows),
                'verified_edition_work_group_relations': sum(
                    r.get('relation_resolution') == 'publisher_work_group_membership_verified' for r in rows),
                'confirmed_light_novel_editions': sum(r['entry_kind'] == 'edition' and r.get('light_novel_confirmed') is True for r in rows),
                'publisher_confirmed_editions': sum(r.get('edition_bibliography_confirmed') is True for r in rows),
                # Each projected row is a unique edition, independent of the
                # number of publisher references or provider bindings it has.
                'publisher_bibliographic_correspondences': sum(r.get('publisher_bibliography_matched') is True for r in rows),
                'content_feature_series': content, 'content_feature_works': content_works, 'source_genre_series': source,
                'feature_bearing_works': sum(bool(r['genres'] or r['llm_features']) for r in rows),
                'recommendable_works': sum(r['recommendable'] for r in rows), 'source_genre_works': sum(bool(r['genres']) for r in rows),
                'llm_works': content + content_works, 'strict72_works': 0, 'raw_classified_works': sum(bool(r['llm_features']) for r in rows),
                'personalized': False, 'read_status_counts': statuses, 'ingestion_failures': failures,
                'ingestion_failure_events': failures, 'unresolved_ingestion_failures': unresolved_failures,
                'coverage_complete': False, 'human_quality_verified': False, 'classification_scope': 'mixed_explicit_units' if content_works else 'series'}

    def similar_works(self, entry_id, limit=12, exclude_read=True):
        entry_id = _entry(entry_id)
        if entry_id.startswith((*( 'lnr:pub:' + provider + ':' for provider in PUBLISHER_REFERENCE_PROVIDERS),
                                *( 'lng:pub:' + provider + ':' for provider in SOURCE_GROUP_PROVIDERS))):
            provider = entry_id.split(':')[2]
            is_reference = entry_id.startswith('lnr:')
            profile = PUBLISHER_REFERENCE_PROVIDERS[provider] if is_reference else SOURCE_GROUP_PROVIDERS[provider]
            expected_kind = 'publisher_reference' if is_reference else 'publisher_group'
            expected_schema = profile['schema']
            if isinstance(limit, bool) or not 1 <= limit <= 100:
                raise ValueError('limit must be 1..100')
            with self._connect() as db:
                entry = db.execute('SELECT * FROM entries WHERE entry_id=?', (entry_id,)).fetchone()
            if entry is None:
                raise ValueError('liked light-novel entry is absent')
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored metadata SHA mismatch')
            record = json.loads(entry['record_json'])
            if entry['kind'] != expected_kind or record.get('schema_version') != expected_schema:
                raise ValueError('stored typed identity differs')
            row = self._project_publisher_reference(provider, record, {}) if is_reference else self._project_publisher_source_group(provider, record, {})
            if row['entry_id'] != entry_id or row['entry_kind'] != entry['kind']:
                raise ValueError('stored typed identity differs')
            if not row['genres'] and not row['llm_features']:
                return {'mode': 'insufficient_evidence', 'items': [], 'liked_entry_ids': [entry_id],
                        'classification_scope': expected_kind, 'personalized': False,
                        'explanation': ('掲載書誌には推薦に使える内容特徴・資料ジャンルが未収録です。' if is_reference else
                                        '掲載グループには推薦に使える内容特徴・資料ジャンルが未収録です。'),
                        'human_quality_verified': False}
        return self.recommend_preferences(liked_entry_ids=[entry_id], limit=limit, exclude_read=exclude_read)

    def recommend_preferences(self, *, liked_entry_ids=None, preferred_features=None, avoided_features=None,
                              axis_weights=None, genre=None, language=None, year_from=None, year_to=None,
                              exclude_read=True, limit=12, reference_genres=None):
        from .scale_preferences_v1 import _taxonomy, _feature_ids, _weights, _normalise_list
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError('limit must be 1..100')
        taxonomy = _taxonomy()
        preferred = _feature_ids(preferred_features, 'preferred_features', taxonomy)
        avoided = _feature_ids(avoided_features, 'avoided_features', taxonomy)
        weights = _weights(axis_weights)
        liked = [_entry(x) for x in _normalise_list(liked_entry_ids, 'liked_entry_ids')]
        rows = self._rows(include_features=False, recommendation_liked_ids=liked)
        indexed = {r['entry_id']: r for r in rows}
        if any(key not in indexed for key in liked):
            raise ValueError('liked light-novel entry is absent')
        liked_features = self._feature_rows(set(liked))
        for key in liked:
            self._apply_feature(indexed[key], liked_features.get(key))
        families = self._families(rows)
        excluded = {families[key] for key in liked}
        if exclude_read:
            excluded |= {families[r['entry_id']] for r in rows if r['read_status'] == 'read'}
        source_genres = {g['qid']: g['label'] for key in liked for g in indexed[key]['genres']}
        # Internal bridge only: the combined catalogue authenticates world-work
        # detail before supplying these source declarations. These are ranking
        # references and never become the candidate's native labels.
        reference_sources = {}
        for reference in reference_genres or []:
            qid = _qid(reference['qid'])
            if reference.get('scope') != 'work' or not str(reference.get('source_url', '')).startswith('https://www.wikidata.org/wiki/Q'):
                raise ValueError('invalid authenticated world-work genre reference')
            source_genres[qid] = reference.get('label') or qid
            reference_sources[qid] = _safe(reference)
        requested_features = preferred | {f['label_id'] for key in liked for f in indexed[key]['llm_features']}
        candidates = self._filtered(rows, genre=genre, language=language, year_from=year_from, year_to=year_to)
        series_families = {families[r['entry_id']] for r in rows if r['entry_kind'] == 'series'}
        uses_native_scores = bool(requested_features or avoided)
        if uses_native_scores:
            feature_ids = {row['entry_id'] for row in candidates
                if families[row['entry_id']] not in excluded and row['entry_kind'] not in {'edition', 'publisher_group', 'publisher_reference'}
                and not (row['entry_kind'] == 'work' and families[row['entry_id']] in series_families)}
            features = self._feature_rows(feature_ids)
            for row in candidates:
                self._apply_feature(row, features.get(row['entry_id']))
        ranked = []
        for row in candidates:
            if families[row['entry_id']] in excluded or row['entry_kind'] in {'edition', 'publisher_group', 'publisher_reference'}:
                continue
            # A known volume is offered through the series card, not a duplicate recommendation.
            if row['entry_kind'] == 'work' and families[row['entry_id']] in series_families:
                continue
            shared = set(source_genres) & {g['qid'] for g in row['genres']}
            labels = {l['label_id']: l for l in row['llm_features']}
            matches = requested_features & labels.keys()
            if avoided & labels.keys():
                continue
            score = 0.0
            reasons = []
            if shared:
                score += .35 * len(shared) / len(set(source_genres) | {g['qid'] for g in row['genres']})
                reasons.append({'kind': 'source_genre', 'shared': [{'qid': q, 'label': source_genres[q]} for q in sorted(shared)],
                                'source_locator': 'claims.P136', 'scope': row['entry_kind'], 'source_url': row['source_url'],
                                'reference_sources': [reference_sources[q] for q in sorted(shared) if q in reference_sources]})
            for label_id in sorted(matches):
                label = labels[label_id]
                relevance = label.get('relevance')
                if relevance is not None:
                    score += weights.get(label['axis'], 0) * float(relevance)
                reason_scope = 'work' if row['entry_kind'] == 'work' else 'series'
                reason_kind = (('work_content_feature' if label.get('evidence_basis') == 'content' else 'work_bibliographic_feature')
                    if reason_scope == 'work' else ('series_content_feature' if label.get('evidence_basis') == 'content' else 'series_bibliographic_feature'))
                reasons.append({'kind': reason_kind, 'label_id': label_id,
                                'label': taxonomy.get(label_id, {}).get('label', label_id), 'scope': reason_scope,
                                'evidence': label.get('evidence', []), 'sources': row['content_sources'], 'calibration_status': 'uncalibrated'})
                if relevance is None:
                    reasons[-1]['relevance_unknown'] = True
            if reasons:
                ranked.append({**row, 'score': round(score, 6), 'score_kind': 'uncalibrated_work_similarity' if row['entry_kind'] == 'work' else 'uncalibrated_series_similarity', 'reasons': reasons})
        ranked.sort(key=lambda r: (-r['score'], r['title'], r['entry_id']))
        chosen, used = [], set()
        for row in ranked:
            family = families[row['entry_id']]
            if family not in used:
                used.add(family)
                chosen.append(row)
                if len(chosen) == limit:
                    break
        if not uses_native_scores:
            # Native labels cannot affect a genre-only score. Authenticate the
            # selected cards' labels for display, retaining the source reasons.
            features = self._feature_rows({row['entry_id'] for row in chosen})
            for row in chosen:
                self._apply_feature(row, features.get(row['entry_id']))
        for row in chosen:
            matched = avoided & {label['label_id'] for label in row['llm_features']}
            unknown = avoided - matched
            row['avoided_feature_status'] = {'matched': sorted(matched), 'unknown': sorted(unknown)}
            unknown_axes = {taxonomy[label_id]['axis'] for label_id in unknown}
            # A different adopted label, including on this same axis, cannot
            # establish the absence of an unclassified requested feature.
            row['feature_insufficiency'] = [{'axis': axis, 'status': 'unknown',
                'reason': 'requested_avoided_feature_absence_unproved'}
                for axis in AXES if axis in unknown_axes]
        work_scoped = any(r['entry_kind'] == 'work' and r['llm_features'] for r in chosen)
        result_scope = ('mixed_explicit_units' if any(r['entry_kind'] == 'series' for r in chosen) else 'work') if work_scoped else 'series'
        return {'mode': ('mixed_explicit_unit_preferences' if result_scope == 'mixed_explicit_units' else 'work_preferences') if work_scoped else ('series_preferences' if chosen else 'insufficient_evidence'), 'personalized': False,
                'items': chosen, 'liked_entry_ids': liked, 'classification_scope': result_scope,
                'explanation': ('作品・シリーズの内容特徴と資料記載ジャンルによる未校正の類似度です。対象の単位を区別しています。' if work_scoped else 'シリーズの内容特徴と資料記載ジャンルによる未校正の類似度です。書誌のみと内容根拠付きの理由を区別しています。') if chosen else '利用できる共通特徴が不足しています。',
                'read_unknown_is_unread': False, 'human_quality_verified': False}
