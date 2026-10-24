"""Local paginated catalogue UI. Only named assets and public fields are served."""
from __future__ import annotations

import json
import math
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit


_QID = r'Q[1-9][0-9]*'
_ENTRY_ID = r'(?:ln[sw]:wd:Q[1-9][0-9]*|ln[se]:ndl:[A-Za-z0-9_-][A-Za-z0-9._-]{0,119})'
_FAMILIES = ('all', 'world', 'light_novel')
_ENTRY_KINDS = ('series', 'work', 'edition')
_ADMISSION_STATES = ('bibliographic_candidate_only', 'light_novel_confirmed', 'edition_series_membership_verified',
                     'ndl_series_identity_verified')
_PREFERENCE_AXES = ('theme', 'structure', 'protagonist', 'atmosphere')
_STAT_KEYS = ('works', 'searchable_works', 'feature_bearing_works', 'recommendable_works',
              'strict72_works', 'llm_works', 'raw_classified_works', 'eligible_classified_works', 'personal_score_works', 'held_works',
              'content_history_links', 'bibliographic_history_links',
              'light_novel_series', 'light_novel_featured_series', 'light_novel_works', 'light_novel_editions')


def _text(value):
    return str(value) if value is not None else ''


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _terms(values):
    return [{'qid': _text(v.get('qid')), 'label': _text(v.get('label', v.get('name')))}
            if isinstance(v, dict) else {'qid': '', 'label': _text(v)} for v in values or []]


def _feature(value, *, hidden_quote_evidence_ids=(), hidden_summary_evidence_ids=()):
    if not isinstance(value, dict):
        return {'label': _text(value)}
    result = {k: (_number(value[k]) if k in ('relevance', 'confidence', 'score') else _text(value[k]))
            for k in ('label', 'label_id', 'feature_id', 'axis', 'relevance', 'confidence', 'score',
                      'evidence', 'source_url') if k in value}
    if 'confidence' not in result and 'raw_confidence' in value:
        result['confidence'] = _number(value['raw_confidence'])
    if not result.get('label') and result.get('label_id'):
        from recommend_book.classification.validation import load_taxonomy
        result['label'] = next((item['label'] for item in load_taxonomy()['labels']
                                if item['label_id'] == result['label_id']), result['label_id'])
    if isinstance(value.get('evidence'), list):
        result['evidence'] = ' / '.join(str(item['quote']) for item in value['evidence']
                                      if isinstance(item, dict) and item.get('quote')
                                      and item.get('evidence_id') not in hidden_quote_evidence_ids)
        result['evidence_summary'] = ' / '.join(item['summary'] for item in value['evidence']
                                      if isinstance(item, dict) and isinstance(item.get('summary'), str)
                                      and item['summary'].strip()
                                      and item.get('evidence_id') not in hidden_summary_evidence_ids)
    return result


def _link(value):
    value = _text(value)
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ''
    return value if parsed.scheme in ('http', 'https') and parsed.netloc else ''


def _attribution(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ''
    parts = []
    for key in ('credit', 'text', 'attribution', 'authors', 'author', 'contributors', 'source_name', 'title'):
        item = value.get(key)
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            parts.extend(v for v in item if isinstance(v, str))
    return ' / '.join(dict.fromkeys(parts))


def _recommendation_label_names():
    """Resolve public Japanese labels without exposing taxonomy internals."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    path = root / 'config' / 'recommendation-label-display-ja.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        value = {}
    return value.get('labels', {}) if isinstance(value, dict) else {}


def _reason_terms(values):
    labels = _recommendation_label_names()
    result = []
    for value in values or []:
        if isinstance(value, dict):
            identifier = _text(value.get('qid') or value.get('label_id'))
            label = _text(value.get('label') or value.get('name') or labels.get(identifier, identifier))
            result.append({'qid': identifier, 'label': label})
        elif isinstance(value, str):
            result.append({'qid': value if re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]*', value) else '',
                           'label': labels.get(value, value)})
    return result


_AXIS_NAMES_JA = {
    'genre': 'ジャンル', 'theme': 'テーマ', 'structure': '物語構造',
    'protagonist': '主人公', 'style': '文体', 'atmosphere': '雰囲気',
}


def _public_feature_insufficiency(value):
    """Turn internal per-axis proof diagnostics into a short user explanation."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ''
    unknown = []
    for item in value:
        if not isinstance(item, dict):
            continue
        axis = item.get('axis')
        if axis in _AXIS_NAMES_JA and axis not in unknown:
            unknown.append(axis)
    if not unknown:
        return ''
    labels = '・'.join(_AXIS_NAMES_JA[axis] for axis in unknown)
    return f'{labels}は未確認です。避けたい特徴の有無を判断できません。'


def _recommendation_sources(value):
    """Flatten both source and nested source_attributions contracts."""
    values = value if isinstance(value, list) else [value]
    flattened = []
    for item in values:
        if not isinstance(item, dict):
            continue
        nested = item.get('source_attributions')
        if isinstance(nested, list):
            flattened.extend(source for source in nested if isinstance(source, dict))
        else:
            flattened.append(item)
    return flattened


def _annotate_recommendation_reasons(item, payload):
    """Add truthful origin labels before the generic public serializer runs."""
    reasons = item.get('reasons')
    if not isinstance(reasons, list):
        return
    preferred = set(payload.get('preferred_features', []))
    has_likes = bool(payload.get('liked_qids') or payload.get('liked_entry_ids'))
    for reason in reasons:
        if not isinstance(reason, dict):
            continue
        kind = reason.get('kind')
        shared = reason.get('shared') or []
        shared_ids = {value if isinstance(value, str) else value.get('qid')
                      for value in shared if isinstance(value, (str, dict))}
        if kind == 'native_feature_match':
            if preferred & shared_ids:
                reason.update(direction='prefer', detail='指定した特徴と一致')
            elif has_likes:
                reason.update(direction='context', detail='基準作品との共通点')
            else:
                reason.update(direction='prefer', detail='指定した特徴と一致')
        elif kind == 'source_genre':
            reason.update(direction='context' if has_likes else 'prefer',
                          detail='基準作品との共通点' if has_likes else '資料記載ジャンルが共通')
        elif kind == 'same_language':
            reason.update(direction='context', detail='原言語が共通')
        elif kind == 'publication_period':
            reason.update(direction='context', detail='刊行年代が近い')
        elif kind == 'same_author':
            reason.update(direction='context', detail='著者が共通')
        elif kind == 'series_content_feature':
            reason.update(direction='context', detail='基準シリーズと内容特徴が共通')


def _preference_tokens(value, *, name, maximum=50, maximum_length=128):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f'invalid {name}')
    result = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > maximum_length or item in result:
            raise ValueError(f'invalid {name}')
        result.append(item)
    return result


def _preference_payload(data):
    if not isinstance(data, dict):
        raise ValueError('recommendation body must be an object')
    allowed = {'liked_qids', 'preferred_features', 'avoided_features', 'axis_weights', 'genre', 'language',
               'year_from', 'year_to', 'exclude_read', 'limit', 'liked_entry_ids', 'family'}
    if set(data) - allowed:
        raise ValueError('unknown recommendation field')
    liked = _preference_tokens(data.get('liked_qids'), name='liked_qids', maximum=50, maximum_length=24)
    if any(not re.fullmatch(_QID, item) for item in liked):
        raise ValueError('invalid liked_qids')
    preferred = _preference_tokens(data.get('preferred_features'), name='preferred_features')
    avoided = _preference_tokens(data.get('avoided_features'), name='avoided_features')
    if set(preferred) & set(avoided):
        raise ValueError('a feature cannot be both preferred and avoided')
    weights = data.get('axis_weights', {})
    if not isinstance(weights, dict) or set(weights) - set(_PREFERENCE_AXES):
        raise ValueError('invalid axis_weights')
    axis_weights = {}
    for axis, value in weights.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 10:
            raise ValueError('invalid axis_weights')
        axis_weights[axis] = float(value)
    def optional_text(key, maximum):
        value = data.get(key, '')
        if value is None:
            return ''
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f'invalid {key}')
        return value
    genre = optional_text('genre', 64)
    if genre and not re.fullmatch(_QID, genre):
        raise ValueError('invalid genre')
    language = optional_text('language', 32)
    years = {}
    for key in ('year_from', 'year_to'):
        value = data.get(key)
        if value in (None, ''):
            years[key] = None
        elif isinstance(value, int) and not isinstance(value, bool) and -5000 <= value <= 3000:
            years[key] = value
        else:
            raise ValueError(f'invalid {key}')
    if years['year_from'] is not None and years['year_to'] is not None and years['year_from'] > years['year_to']:
        raise ValueError('invalid year range')
    exclude_read = data.get('exclude_read', True)
    if not isinstance(exclude_read, bool):
        raise ValueError('invalid exclude_read')
    limit = data.get('limit', 30)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError('invalid limit')
    result = {'liked_qids': liked, 'preferred_features': preferred, 'avoided_features': avoided,
            'axis_weights': axis_weights, 'genre': genre or None, 'language': language or None,
            **years, 'exclude_read': exclude_read, 'limit': limit}
    if 'liked_entry_ids' in data:
        identifiers = _preference_tokens(data['liked_entry_ids'], name='liked_entry_ids', maximum_length=160)
        if any(not re.fullmatch(_ENTRY_ID, identifier) for identifier in identifiers):
            raise ValueError('invalid liked_entry_ids')
        result['liked_entry_ids'] = identifiers
    if 'family' in data:
        if data['family'] not in _FAMILIES:
            raise ValueError('invalid family')
        result['family'] = data['family']
    return result


def _public_preference_facets(raw):
    if not isinstance(raw, dict):
        raise TypeError('invalid preference facets')
    axes = raw.get('axes') if isinstance(raw.get('axes'), dict) else raw
    if isinstance(raw.get('native_features'), list):
        axes = {axis: [] for axis in _PREFERENCE_AXES}
        for value in raw['native_features']:
            if isinstance(value, dict) and value.get('axis') in axes:
                axes[value['axis']].append(value)
    result = {'axes': {},
              'coverage_note': '分類体系の選択肢です。作品ごとの採用件数は未集計で、推薦時に個別検証します。'}
    for axis in _PREFERENCE_AXES:
        values = axes.get(axis, []) if isinstance(axes, dict) else []
        if not isinstance(values, list):
            raise TypeError('invalid preference facets')
        public = []
        for value in values:
            if not isinstance(value, dict):
                continue
            label_id = value.get('label_id', value.get('qid'))
            label = value.get('label', value.get('name'))
            if not isinstance(label_id, str) or not label_id or len(label_id) > 128 or not isinstance(label, str) or not label:
                continue
            item = {'label_id': label_id, 'label': label[:200]}
            count = value.get('count')
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                item['count'] = count
            public.append(item)
        result['axes'][axis] = public
    return result


def public_work(row):
    """Explicit boundary: never serialize a database row or provenance packet wholesale."""
    result = {key: _text(row.get(key)) for key in ('qid', 'work_id', 'title', 'original_language')}
    if 'original_language' in row and row['original_language'] is None:
        result['original_language'] = None
    qid = result['qid'] if re.fullmatch(_QID, result['qid']) else ''
    result['qid'] = qid
    result.update(aliases=[_text(v) for v in row.get('aliases', [])],
                  authors=[{'qid': _text(v.get('qid')), 'name': _text(v.get('name'))}
                           for v in row.get('authors', []) if isinstance(v, dict)],
                  genres=_terms(row.get('genres')), region=_terms(row.get('region')),
                  year=_number(row.get('year')), recommendable=bool(row.get('recommendable')),
                  read_status=row.get('read_status') if row.get('read_status') in ('read', 'unread', 'reading') else 'unknown',
                  source_url=_link(row.get('source_url')) or (f'https://www.wikidata.org/wiki/{qid}' if qid else ''))
    identifier = _text(row.get('entry_id'))
    if re.fullmatch(_ENTRY_ID, identifier) or re.fullmatch(_QID, identifier):
        result['entry_id'] = identifier
    if row.get('entry_kind') in _ENTRY_KINDS:
        result['entry_kind'] = row['entry_kind']
    if row.get('family') in _FAMILIES[1:]:
        result['family'] = row['family']
    for key in ('light_novel_confirmed', 'label_candidate'):
        if isinstance(row.get(key), bool):
            result[key] = row[key]
    if row.get('admission_status') in _ADMISSION_STATES:
        result['admission_status'] = row['admission_status']
    if row.get('relation_resolution') in ('original_work_and_story_series_unresolved', 'edition_series_membership_verified'):
        result['relation_resolution'] = row['relation_resolution']
    if row.get('series_identity_basis') == 'publisher_series_and_ndl_bibliography':
        result['series_identity_basis'] = row['series_identity_basis']
        result['series_assertion_source_url'] = _link(row.get('series_assertion_source_url'))
    for key in ('language', 'publication_label', 'volume'):
        if isinstance(row.get(key), str):
            result[key] = row[key][:500]
    if isinstance(row.get('isbn'), list):
        result['isbn'] = [item for item in row['isbn'] if isinstance(item, str) and re.fullmatch(r'[0-9Xx-]{10,20}', item)]
    if 'members' in row:
        result['members'] = []
        for member in row.get('members') or []:
            if not isinstance(member, dict) or not re.fullmatch(_ENTRY_ID, _text(member.get('entry_id'))):
                continue
            # Members are compact bibliography links, never nested database records.
            item = {key: _text(member.get(key)) for key in ('entry_id', 'title')}
            item['entry_kind'] = member.get('entry_kind') if member.get('entry_kind') in _ENTRY_KINDS else 'work'
            item['family'] = 'light_novel'
            item['source_url'] = _link(member.get('source_url'))
            item['read_status'] = member.get('read_status') if member.get('read_status') in ('read', 'unread', 'reading') else 'unknown'
            item['year'] = _number(member.get('year'))
            for key in ('light_novel_confirmed', 'label_candidate'):
                if isinstance(member.get(key), bool):
                    item[key] = member[key]
            if member.get('admission_status') in _ADMISSION_STATES:
                item['admission_status'] = member['admission_status']
            result['members'].append(item)
    if 'rights' in row:
        result['rights'] = {key: row['rights'].get(key) if row['rights'].get(key) in ('allowed', 'hold') else 'hold'
                            for key in ('display', 'local_storage', 'external_llm')}
    if 'provenance' in row:
        result['provenance'] = {'retrieved_at': _text(row['provenance'].get('retrieved_at'))}
    hidden_quotes, hidden_summaries = set(), set()
    recommendation_source_fallback = False
    content_sources = row.get('content_sources', row.get('source_attributions'))
    if content_sources is None and row.get('recommendation_provenance'):
        content_sources = _recommendation_sources(row['recommendation_provenance'])
        recommendation_source_fallback = True
    if content_sources is not None:
        result['content_sources'] = []
        seen_sources = set()
        for source in content_sources:
            if not isinstance(source, dict):
                continue
            displayed = source.get('display_allowed') is True or (
                recommendation_source_fallback and source.get('display_allowed') is not False)
            if not displayed or source.get('quote_allowed') is not True:
                hidden_quotes.update(value for value in source.get('evidence_ids', []) if isinstance(value, str))
            if not displayed:
                hidden_summaries.update(value for value in source.get('evidence_ids', []) if isinstance(value, str))
            if displayed:
                source_key = (_link(source.get('source_url')), _text(source.get('revision')),
                              _link(source.get('permalink')), _text(source.get('locator')))
                if source_key in seen_sources:
                    continue
                seen_sources.add(source_key)
                attribution = source.get('attribution')
                attribution = attribution if isinstance(attribution, dict) else {}
                result['content_sources'].append({
                    'source_url': _link(source.get('source_url')), 'license': _text(source.get('license')),
                    'license_url': _link(source.get('license_url')), 'attribution': _attribution(source.get('attribution')),
                    'permalink': _link(source.get('permalink') or attribution.get('permalink')),
                    'history_url': _link(source.get('history_url') or attribution.get('history_url')),
                    'output_license': _text(source.get('output_license')),
                    'output_license_url': _link(source.get('output_license_url')),
                    'locator': _text(source.get('locator')), 'revision': _text(source.get('revision')),
                    'changed_from_source': _text(source.get('changed_from_source')),
                })
    for key in ('strict72_features', 'llm_features'):
        if key in row:
            result[key] = [_feature(v, hidden_quote_evidence_ids=hidden_quotes,
                                   hidden_summary_evidence_ids=hidden_summaries) for v in row[key]]
    if 'classification_axes' in row:
        from recommend_book.scale_catalogue_v1 import CLASSIFICATION_AXES
        result['classification_axes'] = [
            {'axis': value['axis'],
             **{key: value[key] if type(value.get(key)) is int and value[key] >= 0 else 0
                for key in ('adopted_count', 'held_count')},
             'unknown': value.get('unknown') is True}
            for value in row['classification_axes']
            if isinstance(value, dict) and value.get('axis') in CLASSIFICATION_AXES]
    if 'recommendation_mode' in row:
        result['recommendation_mode'] = row['recommendation_mode'] if row['recommendation_mode'] in (
            'source_similarity', 'insufficient_evidence', 'llm_similarity', 'preference_recommendation') else 'insufficient_evidence'
    if 'score' in row:
        result['score'] = _number(row['score'])
    if 'reasons' in row:
        result['reasons'] = []
        for value in row['reasons']:
            if not isinstance(value, dict):
                continue
            relation = _text(value.get('relation'))
            kind = _text(value.get('kind'))
            direction = 'avoid' if kind == 'avoided_feature' or relation == 'penalty' else (
                'prefer' if kind in ('native_feature_match', 'source_genre') or relation == 'match' else 'context')
            detail = ('避けたい特徴に一致' if direction == 'avoid' else
                      '重視した特徴に一致' if direction == 'prefer' else
                      '基準シリーズと内容特徴が共通' if kind == 'series_content_feature' else
                      '資料記載ジャンルによる補完' if kind == 'fallback_source' else
                      '詳細特徴が不足しています' if kind == 'feature_unavailable' else '')
            reason = {'kind': kind, 'shared': _reason_terms(value.get('shared')),
                      'weight': _number(value.get('weight'))}
            if direction:
                reason['direction'] = direction
            if relation:
                reason['relation'] = relation
            if detail:
                reason['detail'] = detail
            for key in ('axis', 'label', 'detail', 'message', 'direction', 'relation', 'evidence_status'):
                candidate = value.get(key)
                if isinstance(candidate, (str, int, bool)) or (isinstance(candidate, float) and math.isfinite(candidate)):
                    reason[key] = candidate
            for key in ('model_confidence', 'relevance'):
                candidate = value.get(key)
                if isinstance(candidate, (int, float)) and not isinstance(candidate, bool) and math.isfinite(candidate):
                    reason[key] = candidate
            if value.get('source_url'):
                reason['source_url'] = _link(value.get('source_url'))
            if value.get('source_locator'):
                reason['source_locator'] = _text(value.get('source_locator'))
            result['reasons'].append(reason)
    if 'feature_insufficiency' in row:
        result['feature_insufficiency'] = _public_feature_insufficiency(row.get('feature_insufficiency'))
    if 'recommendation_provenance' in row:
        values = _recommendation_sources(row.get('recommendation_provenance'))
        result['recommendation_provenance'] = []
        seen_provenance = set()
        for value in values:
            if not isinstance(value, dict):
                continue
            item = {}
            for key in ('source_url', 'license_url', 'history_url'):
                if value.get(key):
                    item[key] = _link(value.get(key))
            for key in ('license', 'revision', 'locator', 'attribution'):
                if isinstance(value.get(key), (str, int, float)):
                    item[key] = _text(value.get(key))
            if isinstance(value.get('attribution'), dict):
                item['attribution'] = _attribution(value.get('attribution'))
            item['output_license'] = _text(value.get('output_license')) if value.get('output_license') else ''
            item['changed_from_source'] = _text(value.get('changed_from_source')) if value.get('changed_from_source') else ''
            key = (item.get('source_url', ''), item.get('revision', ''), item.get('license', ''))
            if key in seen_provenance:
                continue
            seen_provenance.add(key)
            if any(item.get(key) for key in ('source_url', 'license', 'revision', 'locator', 'attribution',
                                             'output_license', 'changed_from_source')):
                result['recommendation_provenance'].append(item)
    return result


_HTML = '''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>小説の本棚</title><link rel="stylesheet" href="/style.css"><script src="/app.js" defer></script></head>
<body><header><div class="wrap"><p class="eyebrow">FICTION CATALOGUE</p><h1>次に読む一冊を、探す。</h1><p>小説・ライトノベルの書誌と、根拠のある類似作品をたどる本棚。</p></div></header>
<main class="wrap"><section aria-label="収録状況"><div id="stats" class="stats"></div><p class="muted">資料のジャンルは粗い特徴です。LLM分類と区別して表示します。推薦の品質・個人向け改善は未評価です。</p></section>
<section class="panel"><form id="search-form"><label class="search-label" for="search-query">書名・別題・著者</label><div class="search-row"><input id="search-query" name="q" type="search" placeholder="気になる作品や著者を検索"><button type="submit">検索</button></div><div class="filters"><label>対象<select id="family"><option value="all">全作品</option><option value="light_novel">日本のライトノベル</option><option value="world">世界の小説</option></select></label><label>種別<select id="entry-kind"><option value="">すべて</option><option value="series">シリーズ</option><option value="work">各巻・単独作品</option><option value="edition">版・書誌</option></select></label><label>ジャンル<select id="genre"><option value="">すべて</option></select></label><label>原言語<select id="language"><option value="">すべて</option></select></label><label>刊行年から<input id="year-from" type="number" min="-5000" max="3000" placeholder="例 1900"></label><label>まで<input id="year-to" type="number" min="-5000" max="3000" placeholder="例 2026"></label><label class="check"><input id="exclude-read" type="checkbox">既読を除く</label><button type="button" id="reset">条件をリセット</button></div></form></section>
<section class="panel preference-panel" aria-labelledby="preference-title"><h2 id="preference-title">好みから探す</h2><p class="muted">検索結果や詳細画面で「好きな作品に追加」を押すと、複数作品を基準にできます。選択肢は分類体系に定義されたラベルです。作品ごとの採用件数は未集計で、推薦時に個別検証します。</p><div class="preference-favorites"><strong>好きな作品</strong><div id="favorite-works" class="selected-list"><span class="muted">まだ選択されていません。</span></div></div><div id="preference-facets" class="preference-facets"></div><div id="axis-weights" class="axis-weights" aria-label="分類軸の重み"></div><div class="filters"><label>推薦対象<select id="preference-family"><option value="all">全作品</option><option value="light_novel">日本のライトノベル</option><option value="world">世界の小説</option></select></label><label>ジャンル<select id="preference-genre"><option value="">すべて</option></select></label><label>原言語<select id="preference-language"><option value="">すべて</option></select></label><label>刊行年から<input id="preference-year-from" type="number" min="-5000" max="3000"></label><label>まで<input id="preference-year-to" type="number" min="-5000" max="3000"></label><label class="check"><input id="preference-exclude-read" type="checkbox" checked>既読を除く</label><button type="button" class="primary" id="recommend">この条件で推薦</button></div><p class="muted">推薦スコアは未校正の順位付けです。個人の読書履歴による精度改善や的中確率を示しません。</p><p id="preference-message" role="status" aria-live="polite"></p><div id="recommendations" class="recommendations"></div></section>
<section class="panel history-panel" aria-labelledby="history-title"><h2 id="history-title">この端末の読書状態</h2><p class="muted">既読・未読・読書中の状態は台帳のローカル保存先に保存されます。削除する前にJSONへ持ち出せます。削除対象は読書状態だけで、作品・出典・分類証跡は残ります。</p><div class="filters"><button type="button" id="export-history">読書状態をJSONで保存</button><button type="button" id="delete-history">保存後に読書状態を削除</button></div><p id="history-message" role="status" aria-live="polite"></p></section>
<div class="result-heading"><h2 id="shown-count">読み込み中…</h2><span class="muted">シリーズ・各巻・版の読書状態はそれぞれ保存します</span></div><p id="message" role="status" aria-live="polite"></p><div id="results" class="grid"></div><nav class="pagination" aria-label="ページ"><button id="page-prev">前のページ</button><span id="page-label"></span><button id="page-next">次のページ</button></nav>
<dialog id="detail"><div class="dialog-head"><span>作品の詳細</span><button id="close-detail" aria-label="詳細を閉じる">閉じる</button></div><div id="detail-body"></div></dialog></main><footer class="wrap">読書状態はこの端末に保存されます。書き出しと削除は「この端末の読書状態」から行えます。</footer></body></html>'''

_CSS = '''*{box-sizing:border-box}body{margin:0;background:#f6f4ee;color:#24362f;font-family:system-ui,"Yu Gothic",sans-serif;line-height:1.7}header{background:#203e34;color:#faf7ee;padding:48px 0 38px}.wrap{max-width:1180px;margin:auto;padding:0 28px}.eyebrow{font-size:12px;letter-spacing:.2em;color:#c9d7a6}h1{font-size:clamp(27px,4vw,43px);margin:10px 0}h2{font-size:19px}h3{margin:0 0 8px;font-size:20px;line-height:1.5}p{margin:8px 0}main{padding-top:26px!important}.stats{display:flex;flex-wrap:wrap;gap:12px}.stat{background:#fff;padding:12px 16px;border-radius:10px;flex:1;min-width:128px}.stat strong{display:block;font-size:24px}.stat span,.muted{font-size:13px;color:#66716b}.panel{background:white;border:1px solid #dedfd4;border-radius:14px;padding:24px;margin:24px 0}.search-label{font-weight:650}.search-row{display:flex;gap:12px;margin:8px 0 18px}.search-row input{flex:1;min-width:0}input,select,button{font:inherit;border:1px solid #bec8bb;border-radius:7px;padding:9px 12px;background:#fff;color:inherit}button{cursor:pointer}button:hover{background:#e8efdf}button:disabled{opacity:.4;cursor:default}.search-row button,.primary{background:#315845;color:#fff}.filters{display:flex;gap:14px;flex-wrap:wrap;align-items:end}.filters label:not(.check){display:flex;flex-direction:column;font-size:13px;gap:5px}.filters input[type=number]{width:115px}.check{padding:9px 0}.check input{accent-color:#315845}.result-heading{display:flex;justify-content:space-between;align-items:center;gap:14px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.card{background:white;border:1px solid #dedfd4;border-radius:12px;padding:22px;display:flex;flex-direction:column;gap:8px;min-width:0}.title-button{padding:0;border:0;text-align:left;font-size:20px;font-weight:700;background:none;overflow-wrap:anywhere}.title-button:hover{background:none;text-decoration:underline}.tag{font-size:12px;border-radius:5px;padding:3px 7px;background:#edf1e3;display:inline-block;margin:3px}.card-bottom{margin-top:auto;padding-top:10px;font-size:13px}.card select{font-size:13px;width:100%;margin-top:6px}.preference-panel h2{margin-top:0}.preference-favorites{border-top:1px solid #dedfd4;padding-top:12px;margin-top:16px}.selected-list{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 16px}.selected-item{display:inline-flex;gap:7px;align-items:center;background:#edf1e3;border-radius:7px;padding:5px 8px;font-size:13px}.selected-item button{padding:0 4px;border:0;background:transparent}.preference-facets{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:18px}.axis-weights{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:18px}.axis-weight{display:flex;flex-direction:column;gap:4px;font-size:13px}.axis-weight input{padding:0}.facet-group{border:1px solid #dedfd4;border-radius:9px;padding:12px}.facet-group h3{font-size:15px}.facet-options{display:flex;flex-wrap:wrap;gap:7px}.facet-option{font-size:13px;padding:5px 8px;background:#f6f4ee;border-radius:6px}.facet-option input{accent-color:#315845;margin-right:5px}.facet-option.avoid{background:#f8e9e3}.recommendations{display:grid;gap:12px;margin-top:14px}.recommendation-card{border-top:1px solid #dedfd4;padding:14px 0}.recommendation-card h3 button{padding:0;border:0;background:transparent;text-align:left;font-weight:700}.recommendation-reason{font-size:13px;margin-left:1em}.recommendation-provenance{font-size:12px}.recommendation-warning{color:#8a4531;font-size:13px}.history-panel{border-color:#c9d7a6}.pagination{display:flex;justify-content:center;align-items:center;gap:20px;padding:30px 0}footer{padding-top:24px!important;padding-bottom:36px!important;color:#66716b;font-size:13px}dialog{border:1px solid #bec8bb;border-radius:14px;width:min(820px,94vw);max-height:90vh;padding:26px}dialog::backdrop{background:#10221999}.dialog-head{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #dedfd4;padding-bottom:14px;margin-bottom:22px}.detail-section{border-top:1px solid #dedfd4;margin-top:22px;padding-top:14px}.similar-row{padding:14px 0;border-bottom:1px solid #eee}a{color:#315845}#message,#preference-message,#history-message{color:#8a4531}@media(max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.preference-facets{grid-template-columns:1fr}.axis-weights{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:580px){.grid{grid-template-columns:1fr}.wrap{padding:0 17px}.panel{padding:16px}.result-heading{display:block}.filters{gap:10px}.stat{min-width:110px}header{padding:28px 0}.pagination{gap:10px;font-size:13px}}'''

_JS = r'''"use strict";
const $=id=>document.getElementById(id), state={page:1,pages:1,total:0,request:0,favorites:new Map(),preferenceFacets:{}};
const statusNames={unknown:'不明',unread:'未読',reading:'読書中',read:'既読'};
const axisNames={theme:'テーマ',structure:'物語構造',protagonist:'主人公',atmosphere:'雰囲気'};
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
async function api(path,options){const response=await fetch(path,options);if(!response.ok){let message='読み込みに失敗しました。';try{message=(await response.json()).error||message;}catch{}throw Error(message);}return response.json();}
function downloadJson(data){const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='recommend-book-read-status.json';link.click();URL.revokeObjectURL(url);}
async function exportHistory(download=true){const data=await api('/api/history/export');if(download)downloadJson(data);return data;}
async function deleteHistory(){const message=$('history-message');message.textContent='';try{const exported=await exportHistory(true);if(!window.confirm('JSONの保存操作を開始しました。ファイルが保存されたことを確認できた場合のみ「OK」を押してください。確認できない場合は「キャンセル」を選んでください。読書状態だけを削除します。')){message.textContent='削除を取り消しました。';return;}await api('/api/history/read-status/delete',{method:'POST',headers:{'Content-Type':'application/json','X-Catalogue-Request':'1'},body:JSON.stringify({confirmation:'DELETE_READ_STATUS',export_sha256:exported.sha256})});message.textContent='読書状態を削除しました。作品・出典・分類証跡は残っています。';await search();}catch(e){message.textContent=e.message;}}
function entryKey(row){return row.entry_id||row.qid;}
function entryPath(id){return /^Q[1-9][0-9]*$/.test(id)?'/api/works/'+id:'/api/entries/'+encodeURIComponent(id);}
function entryLabel(row){return row.light_novel_confirmed===false?'書誌候補・ラノベ未確定':row.entry_kind==='series'?'ライトノベル・シリーズ':row.entry_kind==='edition'?'版・書誌':row.family==='light_novel'?'ライトノベル・各巻／単独作品':'小説';}
function describe(row){const language=row.entry_kind==='edition'?'版の言語：'+(languageName(row.language)||'不明')+' / 原言語：'+(languageName(row.original_language)||'不明'):languageName(row.original_language)||'原言語不明';return [(row.authors||[]).map(x=>x.name).join('・')||'著者情報なし',row.year?row.year+'年':'刊行年不明',language].join(' / ');}
function languageName(code){if(/^Q[0-9]+$/.test(code))return '言語名未取得';try{return code?new Intl.DisplayNames(['ja'],{type:'language'}).of(code):'';}catch{return code;}}
function tags(parent,values){for(const v of values||[])parent.append(node('span',typeof v==='string'?v:v.label,'tag'));}
function statusControl(row){const label=node('label',row.entry_kind==='series'?'シリーズの読書状態：':'読書状態：'),select=node('select');select.setAttribute('aria-label',row.title+'の読書状態');for(const [value,text]of Object.entries(statusNames)){const option=node('option',text);option.value=value;select.append(option);}select.value=row.read_status||'unknown';select.addEventListener('change',async()=>{select.disabled=true;try{await api(entryPath(entryKey(row))+'/read-status',{method:'POST',headers:{'Content-Type':'application/json','X-Catalogue-Request':'1'},body:JSON.stringify({status:select.value})});row.read_status=select.value;await search();}catch(e){select.value=row.read_status||'unknown';$('message').textContent=e.message;}finally{select.disabled=false;}});label.append(select);return label;}
function renderFavorites(){const parent=$('favorite-works');parent.replaceChildren();if(!state.favorites.size){parent.append(node('span','まだ選択されていません。','muted'));return;}for(const row of state.favorites.values()){const item=node('span',undefined,'selected-item');item.append(node('span',row.title||row.qid));const remove=node('button','解除');remove.type='button';remove.setAttribute('aria-label',row.title+'を好きな作品から解除');remove.addEventListener('click',()=>{state.favorites.delete(entryKey(row));renderFavorites();});item.append(remove);parent.append(item);}}
function favoriteControl(row){const button=node('button',state.favorites.has(entryKey(row))?'好きな作品から解除':'好きな作品に追加','favorite-toggle');button.type='button';button.addEventListener('click',()=>{if(state.favorites.has(entryKey(row)))state.favorites.delete(entryKey(row));else state.favorites.set(entryKey(row),{entry_id:row.entry_id,qid:row.qid,title:row.title||row.qid});renderFavorites();button.textContent=state.favorites.has(entryKey(row))?'好きな作品から解除':'好きな作品に追加';});return button;}
function card(row){const c=node('article',undefined,'card'),title=node('button',row.title||'書名不明','title-button');title.type='button';title.addEventListener('click',()=>showDetail(entryKey(row)));c.append(title,node('p',entryLabel(row),'muted'),node('p',describe(row),'muted'));const aliases=(row.aliases||[]).slice(0,3);if(aliases.length)c.append(node('p','別題：'+aliases.join(' / '),'muted'));const genres=node('div');tags(genres,row.genres);c.append(genres);const bottom=node('div',undefined,'card-bottom');bottom.append(favoriteControl(row),node('p',(row.llm_features||[]).length?'内容分類あり':row.genres?.length?'資料記載のジャンルあり':'書誌のみ（内容特徴は未収録）','muted'),statusControl(row));c.append(bottom);return c;}
function query(){const q=new URLSearchParams({page:state.page,page_size:30});for(const [key,id]of [['family','family'],['entry_kind','entry-kind'],['q','search-query'],['genre','genre'],['language','language'],['year_from','year-from'],['year_to','year-to']])if($(id).value)q.set(key,$(id).value);if($('exclude-read').checked)q.set('exclude_read','1');return q;}
async function search(){const request=++state.request;$('message').textContent='';$('shown-count').textContent='検索中…';try{const data=await api('/api/entries?'+query());if(request!==state.request)return;state.pages=data.pages;state.page=data.page;state.total=data.total;$('results').replaceChildren(...data.items.map(card));$('shown-count').textContent=data.total.toLocaleString()+'件'+(data.total?' ・ '+((data.page-1)*data.page_size+1)+'–'+Math.min(data.page*data.page_size,data.total)+'件目':'');if(!data.total)$('results').append(node('p','条件に合う作品がありません。検索語や絞り込み条件を変えてください。'));$('page-label').textContent=data.page+' / '+Math.max(1,data.pages);$('page-prev').disabled=data.page<=1;$('page-next').disabled=data.page>=data.pages;}catch(e){if(request===state.request){$('shown-count').textContent='検索を完了できませんでした';$('message').textContent=e.message;}}}
function section(parent,title){const section=node('section',undefined,'detail-section');section.append(node('h3',title));parent.append(section);return section;}
function sourceCredits(parent,sources){if(!sources.length)return;const credits=section(parent,'内容分類の出典と利用条件');credits.append(node('p','分類はAIが生成し、出典・引用・利用条件を自動照合しています。人による確認は行っていません。再利用時は出典・変更内容の表示と同一ライセンスの条件を確認してください。原作の利用許諾は含みません。','muted'));for(const source of sources){const p=node('p');if(source.source_url){const a=node('a','出典の記事・版');a.href=source.source_url;a.target='_blank';a.rel='noopener noreferrer';p.append(a);}if(source.revision)p.append(node('span',' ・ 版 '+source.revision));if(source.history_url){const h=node('a','寄稿者の履歴');h.href=source.history_url;h.target='_blank';h.rel='noopener noreferrer';p.append(node('span',' ・ '),h);}credits.append(p);if(source.attribution)credits.append(node('p',source.attribution));if(source.locator){const sectionName=source.locator.match(/(?:^|;)section:([^;]+)/);credits.append(node('p','参照箇所：'+(sectionName?sectionName[1]:source.locator),'muted'));}if(source.license){const p=node('p');if(source.license_url){const a=node('a',source.license);a.href=source.license_url;a.target='_blank';a.rel='noopener noreferrer';p.append(a);}else p.textContent=source.license;credits.append(p);}if(source.output_license){const p=node('p','この分類・引用レコードの利用条件：');if(source.output_license_url){const a=node('a',source.output_license);a.href=source.output_license_url;a.target='_blank';a.rel='noopener noreferrer';p.append(a);}else p.append(node('span',source.output_license));credits.append(p);}if(source.changed_from_source)credits.append(node('p','変更内容：'+source.changed_from_source,'muted'));}}
async function showDetail(identifier){
 const dialog=$('detail'),body=$('detail-body');body.replaceChildren(node('p','読み込み中…'));if(!dialog.open)dialog.showModal();
 try{
  const row=await api(entryPath(identifier));
  body.replaceChildren(node('h2',row.title),node('p',entryLabel(row),'muted'),node('p',describe(row)),favoriteControl(row));
  if(row.aliases?.length)body.append(node('p','別題：'+row.aliases.join(' / ')));
  body.append(statusControl(row));
  if(row.admission_status==='bibliographic_candidate_only')body.append(node('p','レーベルなどの書誌情報から集めた候補です。ライトノベルであることは未確定です。','muted'));
  if(row.publication_label)body.append(node('p','書誌に記載されたシリーズ・レーベル：'+row.publication_label,'muted'));
  if(row.series_identity_basis==='publisher_series_and_ndl_bibliography'&&row.series_assertion_source_url){const p=node('p','シリーズの同定根拠：'),a=node('a','出版社のシリーズページ');a.href=row.series_assertion_source_url;a.target='_blank';a.rel='noopener noreferrer';p.append(a);body.append(p,node('p','書名・著者などの表示は国立国会図書館の書誌情報に基づきます。','muted'));}
  if(row.relation_resolution==='original_work_and_story_series_unresolved')body.append(node('p','原作・物語シリーズとの対応は未確認です。','muted'));
  if(row.relation_resolution==='edition_series_membership_verified')body.append(node('p','物語シリーズへの所属を出典で確認済みです。版と原作品は別の項目として扱います。','muted'));
  if(row.entry_kind==='series')body.append(node('p','シリーズ全体の情報です。内容特徴や読書状態は各巻へ自動反映しません。','muted'));
  if(row.source_url){const p=node('p'),a=node('a','出典の書誌・作品ページ');a.href=row.source_url;a.target='_blank';a.rel='noopener noreferrer';p.append(a);body.append(p);}
  if(row.provenance?.retrieved_at)body.append(node('p','取得日：'+row.provenance.retrieved_at,'muted'));
  const coarse=section(body,'資料記載のジャンル');tags(coarse,row.genres);
  coarse.append(node('p',row.genres?.length?'資料に記載された粗い特徴です。詳細分類の72ラベルと同一ではありません。':'資料記載のジャンルは未収録です。','muted'));
  const rich=section(body,'LLMの詳細分類'),axes={genre:'ジャンル',theme:'テーマ',structure:'物語構造',protagonist:'主人公',style:'文体',atmosphere:'雰囲気'};
  for(const item of row.classification_axes||[])rich.append(node('p',axes[item.axis]+'：採用 '+item.adopted_count+'件 / 保留 '+item.held_count+'件'+(item.unknown?' / 不明':''),'muted'));
  const features=[...(row.strict72_features||[]),...(row.llm_features||[])];
  if(!features.length)rich.append(node('p','内容特徴は未収録です。書誌情報だけで内容の有無を判断しません。'));
  for(const f of features){const n=node('p',f.label||f.label_id||f.feature_id||'分類特徴');if(f.relevance!==undefined)n.append(node('span',' ・ 関連度 '+(f.relevance===null?'不明':f.relevance)));if(f.confidence!==undefined)n.append(node('span',' ・ モデル確信度 '+(f.confidence===null?'不明':f.confidence)));rich.append(n);if(f.evidence)rich.append(node('p','出典の引用：'+f.evidence,'muted'));if(f.evidence_summary)rich.append(node('p','AIによる根拠の要約：'+f.evidence_summary,'muted'));}
  rich.append(node('p','関連度とモデル確信度は校正されていない生の値です。正解の確率や個人向け推薦スコアではありません。','muted'));
  sourceCredits(body,row.content_sources||[]);
  if(row.members?.length){const members=section(body,'収録巻・関連する版');members.append(node('p','出典で対応関係を確認できた巻・版です。各項目の読書状態はシリーズとは別に保存します。','muted'));for(const member of row.members){const div=node('div',undefined,'similar-row'),button=node('button',member.title||'書名不明');button.addEventListener('click',()=>showDetail(entryKey(member)));div.append(button,node('p',entryLabel(member),'muted'),statusControl(member));members.append(div);}}
  const similar=section(body,'この作品に近い候補');similar.append(node('p','内容特徴と資料記載のジャンル・書誌特徴を区別して表示します。個人の読書履歴に基づく推薦ではありません。','muted'));
  const data=await api(entryPath(identifier)+'/similar?exclude_read='+($('exclude-read').checked?'1':'0'));
  if(!data.items.length)similar.append(node('p','根拠のある類似候補はまだありません。'));
  for(const item of data.items){const div=node('div',undefined,'similar-row'),button=node('button',item.title);button.addEventListener('click',()=>showDetail(entryKey(item)));div.append(button,node('p',entryLabel(item)+' / '+describe(item),'muted'));
   const names={genre:'資料記載のジャンル',source_genre:'資料記載のジャンル',language:'原言語',same_language:'原言語',year:'刊行年代',publication_period:'刊行年代',author:'著者',same_author:'著者',region:'地域',llm_feature:'LLM分類',native_feature_match:'内容分類',series_content_feature:'シリーズの内容分類'};
   for(const reason of item.reasons||[]){const shared=(reason.shared||[]).map(x=>{let label=typeof x==='string'?x:x.label;if(reason.kind==='same_language')return languageName(label);if(reason.kind==='publication_period')return label+'年';if(reason.kind==='same_author')return (item.authors||[]).find(a=>a.qid===label)?.name||'共通する著者';return label;}).join('・');div.append(node('p',(names[reason.kind]||'資料の共通点')+'：'+shared));}
   if(item.score!==undefined&&item.score!==null)div.append(node('p','類似度 '+Number(item.score).toFixed(3)+'（暫定・確率ではありません）','muted'));similar.append(div);
  }
 }catch(e){body.replaceChildren(node('p',e.message));}
}
async function stats(){try{const data=await api('/api/stats');const labels=[['works','世界小説'],['light_novel_series','ラノベのシリーズ'],['light_novel_featured_series','ラノベの内容特徴あり'],['light_novel_works','ラノベの各巻・単独作品'],['light_novel_editions','関連する版・書誌候補'],['searchable_works','検索可能'],['feature_bearing_works','推薦特徴あり'],['source_genre_works','資料ジャンルあり'],['eligible_classified_works','AI処理済み'],['llm_works','LLM特徴あり'],['personal_score_works','個人スコアあり'],['held_works','表示保留'],['content_history_links','履歴の内容接続'],['bibliographic_history_links','履歴の書誌接続']];$('stats').replaceChildren(...labels.map(([key,label])=>{const c=node('div',undefined,'stat');c.append(node('strong',data[key]===undefined?'未集計':Number(data[key]).toLocaleString()),node('span',label));return c;}));}catch(e){$('message').textContent=e.message;}}
async function facets(){try{const data=await api('/api/facets');for(const item of data.genres||[]){for(const id of ['genre','preference-genre']){const option=node('option',item.label+' ('+item.count.toLocaleString()+')');option.value=item.qid;$(id).append(option);}}for(const item of data.languages||[]){for(const id of ['language','preference-language']){const option=node('option',(languageName(item.value)||'不明')+' ('+item.count.toLocaleString()+')');option.value=item.value;$(id).append(option);}}}catch(e){$('message').textContent=e.message;}}
function renderPreferenceFacets(data){state.preferenceFacets=data.axes||{};const parent=$('preference-facets');parent.replaceChildren();if(data.coverage_note)parent.append(node('p',data.coverage_note,'muted'));for(const axis of ['theme','structure','protagonist','atmosphere']){const group=node('fieldset',undefined,'facet-group');group.append(node('h3',axisNames[axis]));const options=node('div',undefined,'facet-options');for(const item of state.preferenceFacets[axis]||[]){if(!item.label_id&&!item.qid)continue;const id=item.label_id||item.qid,label=node('label',undefined,'facet-option');const prefer=document.createElement('input');prefer.type='checkbox';prefer.value=id;prefer.dataset.axis=axis;prefer.dataset.direction='prefer';prefer.setAttribute('aria-label',axisNames[axis]+' '+(item.label||id)+'を重視');const avoid=document.createElement('input');avoid.type='checkbox';avoid.value=id;avoid.dataset.axis=axis;avoid.dataset.direction='avoid';avoid.setAttribute('aria-label',axisNames[axis]+' '+(item.label||id)+'を避ける');prefer.addEventListener('change',()=>{if(prefer.checked)avoid.checked=false;});avoid.addEventListener('change',()=>{if(avoid.checked)prefer.checked=false;});label.append(prefer,node('span',(item.label||id)+'を重視'),avoid,node('span','避ける'));options.append(label);}if(!options.childElementCount)options.append(node('span','この軸の選択肢はありません。','muted'));group.append(options);parent.append(group);}const weights=$('axis-weights');weights.replaceChildren(node('strong','分類軸の重み'));for(const axis of Object.keys(axisNames)){const label=node('label',undefined,'axis-weight');const input=document.createElement('input');input.type='range';input.min='0';input.max='2';input.step='.25';input.value='1';input.dataset.axis=axis;const output=node('output','1.00');input.addEventListener('input',()=>{output.textContent=Number(input.value).toFixed(2);});label.append(node('span',axisNames[axis]),input,output);weights.append(label);}}
async function preferenceFacets(){try{renderPreferenceFacets(await api('/api/preference-facets'));}catch(e){$('preference-message').textContent=e.message;}}
function preferencePayload(){const preferred=[],avoided=[],weights={};for(const input of document.querySelectorAll('#preference-facets input:checked')){(input.dataset.direction==='avoid'?avoided:preferred).push(input.value);}for(const input of document.querySelectorAll('#axis-weights input[data-axis]'))weights[input.dataset.axis]=Number(input.value);for(const axis of Object.keys(axisNames))if(weights[axis]===undefined)weights[axis]=1;const value=(id)=>$(id).value;const payload={liked_qids:[...state.favorites.keys()].filter(id=>/^Q[1-9][0-9]*$/.test(id)),liked_entry_ids:[...state.favorites.keys()].filter(id=>/^ln[sw]:wd:Q[1-9][0-9]*$|^ln[se]:ndl:[A-Za-z0-9_-][A-Za-z0-9._-]*$/.test(id)),family:value('preference-family'),preferred_features:preferred,avoided_features:avoided,axis_weights:weights,genre:value('preference-genre'),language:value('preference-language'),year_from:value('preference-year-from')?Number(value('preference-year-from')):null,year_to:value('preference-year-to')?Number(value('preference-year-to')):null,exclude_read:$('preference-exclude-read').checked,limit:30};return payload;}
function renderRecommendations(data){const parent=$('recommendations');parent.replaceChildren();if(data.explanation)parent.append(node('p',data.explanation,'muted'));if(!data.items?.length){parent.append(node('p','条件に合う候補がありません。特徴を減らすか、好きな作品を追加してください。','muted'));return;}for(const row of data.items){const article=node('article',undefined,'recommendation-card');const title=node('button',row.title||'書名不明');title.type='button';title.addEventListener('click',()=>showDetail(entryKey(row)));article.append(node('h3',undefined).appendChild(title).parentNode,node('p',describe(row),'muted'),favoriteControl(row));if(row.score!==undefined&&row.score!==null)article.append(node('p','推薦スコア '+Number(row.score).toFixed(3)+'（未校正・順位付け用）','muted'));for(const reason of row.reasons||[]){const shared=(reason.shared||[]).map(item=>typeof item==='string'?item:item.label||item.qid).join('・');const prefix=reason.direction==='avoid'?'避けたい特徴：':reason.direction==='context'?'補助的な一致：':reason.direction==='prefer'?'好みに合う理由：':'推薦根拠：';const detail=reason.detail||reason.message||reason.label||reason.kind||'一致した根拠';const text=shared?detail+'（'+shared+'）':detail;article.append(node('p',prefix+text,'recommendation-reason'));}if(row.feature_insufficiency)article.append(node('p','特徴不足：'+row.feature_insufficiency,'recommendation-warning'));if(row.content_sources?.length)sourceCredits(article,row.content_sources);for(const source of row.recommendation_provenance||[]){const p=node('p',undefined,'recommendation-provenance');if(source.source_url){const a=node('a','根拠の出典');a.href=source.source_url;a.target='_blank';a.rel='noopener noreferrer';p.append(a);}if(source.revision)p.append(node('span',' ・ 版 '+source.revision));article.append(p);}parent.append(article);}}
async function recommend(){const button=$('recommend');button.disabled=true;$('preference-message').textContent='';try{renderRecommendations(await api('/api/recommendations',{method:'POST',headers:{'Content-Type':'application/json','X-Catalogue-Request':'1'},body:JSON.stringify(preferencePayload())}));}catch(e){$('preference-message').textContent=e.message;}finally{button.disabled=false;}}
 $('search-form').addEventListener('submit',e=>{e.preventDefault();state.page=1;search();});for(const id of ['family','entry-kind','genre','language','exclude-read'])$(id).addEventListener('change',()=>{state.page=1;search();});$('page-prev').addEventListener('click',()=>{if(state.page>1){state.page--;search();window.scrollTo({top:0,behavior:'smooth'});}});$('page-next').addEventListener('click',()=>{if(state.page<state.pages){state.page++;search();window.scrollTo({top:0,behavior:'smooth'});}});$('reset').addEventListener('click',()=>{HTMLFormElement.prototype.reset.call($('search-form'));state.page=1;search();});$('recommend').addEventListener('click',recommend);$('export-history').addEventListener('click',async()=>{try{await exportHistory();$('history-message').textContent='JSONの保存操作を開始しました。ダウンロード先を確認してください。';}catch(e){$('history-message').textContent=e.message;}});$('delete-history').addEventListener('click',deleteHistory);$('close-detail').addEventListener('click',()=>$('detail').close());renderFavorites();stats();facets();preferenceFacets();search();
'''


def create_server(database=None, *, catalogue=None, light_novels_database=None, port=8766):
    """Create a loopback-only server, opening the explicitly supplied SQLite store."""
    if catalogue is None:
        if database is None:
            raise ValueError('database is required')
        from recommend_book.scale_catalogue_v1 import ScaleCatalogue
        catalogue = ScaleCatalogue(database)
    if light_novels_database is not None:
        from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
        from recommend_book.light_novel_site_backend_v1 import UnifiedCatalogue
        catalogue = UnifiedCatalogue(catalogue, LightNovelCatalogue(light_novels_database))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _host_allowed(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def _send(self, status, data, content_type='application/json; charset=utf-8'):
            content = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if content_type.startswith('application/json') else data.encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if not self._host_allowed():
                self._send(403, {'error': 'この接続は利用できません。'})
                return
            try:
                parsed = urlsplit(self.path)
                path = parsed.path
                params = parse_qs(parsed.query)
                def value(key, default=''):
                    return params.get(key, [default])[0]
                def integer(key, default, lower, upper):
                    number = int(value(key, str(default)))
                    if not lower <= number <= upper:
                        raise ValueError
                    return number
                if path in ('/', '/app.js', '/style.css'):
                    data, kind = {'/': (_HTML, 'text/html'), '/app.js': (_JS, 'text/javascript'), '/style.css': (_CSS, 'text/css')}[path]
                    self._send(200, data, kind + '; charset=utf-8')
                    return
                if path == '/api/preference-facets':
                    if not hasattr(catalogue, 'preference_facets'):
                        self._send(501, {'error': '好みの選択肢はまだ利用できません。'})
                        return
                    self._send(200, _public_preference_facets(catalogue.preference_facets()))
                    return
                if path == '/api/history/export':
                    from recommend_book.scale_history_management_v1 import export_reading_history
                    try:
                        self._send(200, catalogue.export_reading_history() if hasattr(catalogue, 'export_reading_history')
                                   else export_reading_history(catalogue))
                    except (OSError, TypeError, ValueError):
                        self._send(500, {'error': '読書状態をエクスポートできませんでした。'})
                    return
                if path in ('/api/stats', '/api/facets'):
                    raw = catalogue.catalogue_stats() if path == '/api/stats' else catalogue.catalogue_facets()
                    result = {key: raw[key] for key in (*_STAT_KEYS, 'source_genre_works') if key in raw and isinstance(raw[key], int)} if path == '/api/stats' else {}
                    result['languages'] = [{'value': _text(v.get('value')), 'count': int(v['count'])} for v in raw.get('languages', [])]
                    result['genres'] = [dict(_terms([v])[0], count=int(v['count'])) for v in raw.get('genres', [])]
                    self._send(200, result)
                    return
                if path in ('/api/works', '/api/entries'):
                    year_from = integer('year_from', 0, -5000, 3000) if value('year_from') else None
                    year_to = integer('year_to', 0, -5000, 3000) if value('year_to') else None
                    if year_from is not None and year_to is not None and year_from > year_to:
                        raise ValueError
                    options = dict(query=value('q')[:500], genre=value('genre') or None,
                        language=value('language') or None, year_from=year_from, year_to=year_to,
                        read_filter='exclude_read' if value('exclude_read') == '1' else 'all',
                        page=integer('page', 1, 1, 1000000), page_size=integer('page_size', 30, 1, 100))
                    family, entry_kind = value('family', 'all'), value('entry_kind') or None
                    if family not in _FAMILIES or (entry_kind is not None and entry_kind not in _ENTRY_KINDS):
                        raise ValueError
                    search = catalogue.search_catalogue
                    if hasattr(catalogue, 'search_world_catalogue'):
                        if path == '/api/works':
                            search = catalogue.search_world_catalogue
                        else:
                            options.update(family=family, entry_kind=entry_kind)
                    elif path == '/api/entries' and (family == 'light_novel' or entry_kind in ('series', 'edition')):
                        self._send(200, dict(total=0, page=options['page'], page_size=options['page_size'], pages=0, items=[]))
                        return
                    data = search(**options)
                    self._send(200, {key: data[key] for key in ('total', 'page', 'page_size', 'pages')} | {'items': [public_work(v) for v in data['items']]})
                    return
                match = re.fullmatch(r'/api/works/(' + _QID + r')(/similar)?', path)
                if match is None:
                    match = re.fullmatch(r'/api/entries/(' + _ENTRY_ID + r'|' + _QID + r')(/similar)?', unquote(path))
                if match:
                    qid, similar = match.groups()
                    row = catalogue.work_detail(qid)
                    if row is None:
                        self._send(404, {'error': '作品が見つかりません。'})
                    elif similar:
                        data = catalogue.similar_works(qid, limit=8, exclude_read=value('exclude_read', '1') == '1')
                        self._send(200, {'mode': data.get('mode'), 'personalized': False, 'items': [public_work(v) for v in data['items']]})
                    else:
                        self._send(200, public_work(row))
                    return
                self._send(404, {'error': 'ページが見つかりません。'})
            except (ValueError, TypeError, KeyError):
                self._send(400, {'error': '検索条件を確認してください。'})
            except Exception:
                self._send(500, {'error': '台帳を読み込めませんでした。'})

        def do_POST(self):
            origin = self.headers.get('Origin')
            allowed_origins = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
            if not self._host_allowed() or (origin is not None and origin not in allowed_origins) or self.headers.get('X-Catalogue-Request') != '1':
                self._send(403, {'error': 'この変更要求は利用できません。'})
                return
            if self.path == '/api/history/read-status/delete':
                try:
                    from recommend_book.scale_history_management_v1 import delete_reading_history
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 1024 or self.headers.get('Content-Type') != 'application/json':
                        raise ValueError
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError
                    options = dict(export_sha256=data.get('export_sha256'), confirmation=data.get('confirmation'))
                    result = (catalogue.delete_reading_history(**options) if hasattr(catalogue, 'delete_reading_history')
                              else delete_reading_history(catalogue, **options))
                    self._send(200, result)
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    self._send(400, {'error': '現在のエクスポートと確認文字列が必要です。'})
                except Exception:
                    self._send(500, {'error': '読書状態を削除できませんでした。'})
                return
            if self.path == '/api/recommendations':
                if not hasattr(catalogue, 'recommend_preferences'):
                    self._send(501, {'error': '好みの推薦はまだ利用できません。'})
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 65536 or self.headers.get('Content-Type') != 'application/json':
                        raise ValueError
                    payload = _preference_payload(json.loads(self.rfile.read(length)))
                    backend_payload = dict(payload)
                    if not hasattr(catalogue, 'search_world_catalogue'):
                        if payload.get('liked_entry_ids') or payload.get('family') == 'light_novel':
                            raise ValueError('light novel catalogue is unavailable')
                        backend_payload.pop('liked_entry_ids', None)
                        backend_payload.pop('family', None)
                    raw = catalogue.recommend_preferences(**backend_payload)
                    if not isinstance(raw, dict) or not isinstance(raw.get('items', []), list):
                        raise TypeError
                    items = []
                    for value in raw.get('items', []):
                        if not isinstance(value, dict):
                            continue
                        item = dict(value)
                        _annotate_recommendation_reasons(item, payload)
                        item.setdefault('recommendation_mode', 'preference_recommendation')
                        items.append(public_work(item))
                    response = {'mode': 'preference_recommendation', 'personalized': False,
                                'preference_based': True, 'history_evaluation': 'unverified',
                                'items': items, 'explanation': _text(raw.get('explanation'))}
                    if raw.get('feature_insufficiency'):
                        response['feature_insufficiency'] = _public_feature_insufficiency(raw['feature_insufficiency'])
                    self._send(200, response)
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    self._send(400, {'error': '推薦条件を確認してください。'})
                except Exception:
                    self._send(500, {'error': '推薦結果を作成できませんでした。'})
                return
            match = re.fullmatch(r'/api/works/(' + _QID + r')/read-status', self.path)
            if match is None:
                match = re.fullmatch(r'/api/entries/(' + _ENTRY_ID + r'|' + _QID + r')/read-status', unquote(self.path))
            if not match:
                self._send(404, {'error': 'ページが見つかりません。'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024 or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError
                data = json.loads(self.rfile.read(length))
                status = data.get('status')
                if status not in ('unknown', 'unread', 'reading', 'read'):
                    raise ValueError
                result = catalogue.set_read_status(match.group(1), status)
                if result is None:
                    self._send(404, {'error': '作品が見つかりません。'})
                else:
                    # Re-read the saved row; backend mutation return types need not expose rows.
                    self._send(200, public_work(catalogue.work_detail(match.group(1))))
            except (ValueError, TypeError, AttributeError):
                self._send(400, {'error': '読書状態を確認してください。'})
            except Exception:
                self._send(500, {'error': '読書状態を保存できませんでした。'})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main(argv=None):
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description='小説台帳をこの端末で開きます。')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--light-novels-database', type=Path)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args(argv)
    if not args.database.is_file():
        parser.error('指定した台帳が見つかりません。')
    if args.light_novels_database is not None and not args.light_novels_database.is_file():
        parser.error('指定したライトノベル台帳が見つかりません。')
    server = create_server(args.database, light_novels_database=args.light_novels_database, port=args.port)
    print(f'小説の本棚: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
