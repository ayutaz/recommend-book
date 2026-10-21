"""Local paginated catalogue UI. Only named assets and public fields are served."""
from __future__ import annotations

import json
import math
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


_QID = r'Q[1-9][0-9]*'
_PREFERENCE_AXES = ('theme', 'structure', 'protagonist', 'atmosphere')
_STAT_KEYS = ('works', 'searchable_works', 'feature_bearing_works', 'recommendable_works',
              'strict72_works', 'llm_works', 'raw_classified_works', 'eligible_classified_works', 'personal_score_works', 'held_works',
              'content_history_links', 'bibliographic_history_links')


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
               'year_from', 'year_to', 'exclude_read', 'limit'}
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
    return {'liked_qids': liked, 'preferred_features': preferred, 'avoided_features': avoided,
            'axis_weights': axis_weights, 'genre': genre or None, 'language': language or None,
            **years, 'exclude_read': exclude_read, 'limit': limit}


def _public_preference_facets(raw):
    if not isinstance(raw, dict):
        raise TypeError('invalid preference facets')
    axes = raw.get('axes') if isinstance(raw.get('axes'), dict) else raw
    if isinstance(raw.get('native_features'), list):
        axes = {axis: [] for axis in _PREFERENCE_AXES}
        for value in raw['native_features']:
            if isinstance(value, dict) and value.get('axis') in axes:
                axes[value['axis']].append(value)
    result = {'axes': {}}
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
    qid = result['qid']
    result.update(aliases=[_text(v) for v in row.get('aliases', [])],
                  authors=[{'qid': _text(v.get('qid')), 'name': _text(v.get('name'))}
                           for v in row.get('authors', []) if isinstance(v, dict)],
                  genres=_terms(row.get('genres')), region=_terms(row.get('region')),
                  year=_number(row.get('year')), recommendable=bool(row.get('recommendable')),
                  read_status=row.get('read_status') if row.get('read_status') in ('read', 'unread', 'reading') else 'unknown',
                  source_url=f'https://www.wikidata.org/wiki/{qid}' if re.fullmatch(_QID, qid) else '')
    if 'rights' in row:
        result['rights'] = {key: row['rights'].get(key) if row['rights'].get(key) in ('allowed', 'hold') else 'hold'
                            for key in ('display', 'local_storage', 'external_llm')}
    if 'provenance' in row:
        result['provenance'] = {'retrieved_at': _text(row['provenance'].get('retrieved_at'))}
    hidden_quotes, hidden_summaries = set(), set()
    if 'content_sources' in row:
        result['content_sources'] = []
        for source in row['content_sources']:
            if not isinstance(source, dict):
                continue
            displayed = source.get('display_allowed') is True
            if not displayed or source.get('quote_allowed') is not True:
                hidden_quotes.update(value for value in source.get('evidence_ids', []) if isinstance(value, str))
            if not displayed:
                hidden_summaries.update(value for value in source.get('evidence_ids', []) if isinstance(value, str))
            if displayed:
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
            reason = {'kind': _text(value.get('kind')), 'shared': _terms(value.get('shared')),
                      'weight': _number(value.get('weight'))}
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
        result['feature_insufficiency'] = _text(row.get('feature_insufficiency'))
    if 'recommendation_provenance' in row:
        provenance = row.get('recommendation_provenance')
        values = provenance if isinstance(provenance, list) else [provenance]
        result['recommendation_provenance'] = []
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
            if item:
                result['recommendation_provenance'].append(item)
    return result


_HTML = '''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>小説の本棚</title><link rel="stylesheet" href="/style.css"><script src="/app.js" defer></script></head>
<body><header><div class="wrap"><p class="eyebrow">FICTION CATALOGUE</p><h1>次に読む一冊を、探す。</h1><p>小説・ライトノベルの書誌と、根拠のある類似作品をたどる本棚。</p></div></header>
<main class="wrap"><section aria-label="収録状況"><div id="stats" class="stats"></div><p class="muted">資料のジャンルは粗い特徴です。LLM分類と区別して表示します。推薦の品質・個人向け改善は未評価です。</p></section>
<section class="panel"><form id="search-form"><label class="search-label" for="search-query">書名・別題・著者</label><div class="search-row"><input id="search-query" name="q" type="search" placeholder="気になる作品や著者を検索"><button type="submit">検索</button></div><div class="filters"><label>ジャンル<select id="genre"><option value="">すべて</option></select></label><label>原言語<select id="language"><option value="">すべて</option></select></label><label>刊行年から<input id="year-from" type="number" min="-5000" max="3000" placeholder="例 1900"></label><label>まで<input id="year-to" type="number" min="-5000" max="3000" placeholder="例 2026"></label><label class="check"><input id="exclude-read" type="checkbox">既読を除く</label><button type="button" id="reset">条件をリセット</button></div></form></section>
<section class="panel preference-panel" aria-labelledby="preference-title"><h2 id="preference-title">好みから探す</h2><p class="muted">検索結果や詳細画面で「好きな作品に追加」を押すと、複数作品を基準にできます。特徴は採用済みの分類だけを使います。</p><div class="preference-favorites"><strong>好きな作品</strong><div id="favorite-works" class="selected-list"><span class="muted">まだ選択されていません。</span></div></div><div id="preference-facets" class="preference-facets"></div><div class="filters"><label>ジャンル<select id="preference-genre"><option value="">すべて</option></select></label><label>原言語<select id="preference-language"><option value="">すべて</option></select></label><label>刊行年から<input id="preference-year-from" type="number" min="-5000" max="3000"></label><label>まで<input id="preference-year-to" type="number" min="-5000" max="3000"></label><label class="check"><input id="preference-exclude-read" type="checkbox" checked>既読を除く</label><button type="button" class="primary" id="recommend">この条件で推薦</button></div><p class="muted">推薦スコアは未校正の順位付けです。個人の読書履歴による精度改善や的中確率を示しません。</p><p id="preference-message" role="status" aria-live="polite"></p><div id="recommendations" class="recommendations"></div></section>
<div class="result-heading"><h2 id="shown-count">読み込み中…</h2><span class="muted">読書状態が不明の作品も表示します</span></div><p id="message" role="status" aria-live="polite"></p><div id="results" class="grid"></div><nav class="pagination" aria-label="ページ"><button id="page-prev">前のページ</button><span id="page-label"></span><button id="page-next">次のページ</button></nav>
<dialog id="detail"><div class="dialog-head"><span>作品の詳細</span><button id="close-detail" aria-label="詳細を閉じる">閉じる</button></div><div id="detail-body"></div></dialog></main><footer class="wrap">読書状態の変更はこの端末に保存され、いつでも戻せます。</footer></body></html>'''

_CSS = '''*{box-sizing:border-box}body{margin:0;background:#f6f4ee;color:#24362f;font-family:system-ui,"Yu Gothic",sans-serif;line-height:1.7}header{background:#203e34;color:#faf7ee;padding:48px 0 38px}.wrap{max-width:1180px;margin:auto;padding:0 28px}.eyebrow{font-size:12px;letter-spacing:.2em;color:#c9d7a6}h1{font-size:clamp(27px,4vw,43px);margin:10px 0}h2{font-size:19px}h3{margin:0 0 8px;font-size:20px;line-height:1.5}p{margin:8px 0}main{padding-top:26px!important}.stats{display:flex;flex-wrap:wrap;gap:12px}.stat{background:#fff;padding:12px 16px;border-radius:10px;flex:1;min-width:128px}.stat strong{display:block;font-size:24px}.stat span,.muted{font-size:13px;color:#66716b}.panel{background:white;border:1px solid #dedfd4;border-radius:14px;padding:24px;margin:24px 0}.search-label{font-weight:650}.search-row{display:flex;gap:12px;margin:8px 0 18px}.search-row input{flex:1;min-width:0}input,select,button{font:inherit;border:1px solid #bec8bb;border-radius:7px;padding:9px 12px;background:#fff;color:inherit}button{cursor:pointer}button:hover{background:#e8efdf}button:disabled{opacity:.4;cursor:default}.search-row button,.primary{background:#315845;color:#fff}.filters{display:flex;gap:14px;flex-wrap:wrap;align-items:end}.filters label:not(.check){display:flex;flex-direction:column;font-size:13px;gap:5px}.filters input[type=number]{width:115px}.check{padding:9px 0}.check input{accent-color:#315845}.result-heading{display:flex;justify-content:space-between;align-items:center;gap:14px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.card{background:white;border:1px solid #dedfd4;border-radius:12px;padding:22px;display:flex;flex-direction:column;gap:8px;min-width:0}.title-button{padding:0;border:0;text-align:left;font-size:20px;font-weight:700;background:none;overflow-wrap:anywhere}.title-button:hover{background:none;text-decoration:underline}.tag{font-size:12px;border-radius:5px;padding:3px 7px;background:#edf1e3;display:inline-block;margin:3px}.card-bottom{margin-top:auto;padding-top:10px;font-size:13px}.card select{font-size:13px;width:100%;margin-top:6px}.preference-panel h2{margin-top:0}.preference-favorites{border-top:1px solid #dedfd4;padding-top:12px;margin-top:16px}.selected-list{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 16px}.selected-item{display:inline-flex;gap:7px;align-items:center;background:#edf1e3;border-radius:7px;padding:5px 8px;font-size:13px}.selected-item button{padding:0 4px;border:0;background:transparent}.preference-facets{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:18px}.facet-group{border:1px solid #dedfd4;border-radius:9px;padding:12px}.facet-group h3{font-size:15px}.facet-options{display:flex;flex-wrap:wrap;gap:7px}.facet-option{font-size:13px;padding:5px 8px;background:#f6f4ee;border-radius:6px}.facet-option input{accent-color:#315845;margin-right:5px}.facet-option.avoid{background:#f8e9e3}.recommendations{display:grid;gap:12px;margin-top:14px}.recommendation-card{border-top:1px solid #dedfd4;padding:14px 0}.recommendation-card h3 button{padding:0;border:0;background:transparent;text-align:left;font-weight:700}.recommendation-reason{font-size:13px;margin-left:1em}.recommendation-provenance{font-size:12px}.recommendation-warning{color:#8a4531;font-size:13px}.pagination{display:flex;justify-content:center;align-items:center;gap:20px;padding:30px 0}footer{padding-top:24px!important;padding-bottom:36px!important;color:#66716b;font-size:13px}dialog{border:1px solid #bec8bb;border-radius:14px;width:min(820px,94vw);max-height:90vh;padding:26px}dialog::backdrop{background:#10221999}.dialog-head{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #dedfd4;padding-bottom:14px;margin-bottom:22px}.detail-section{border-top:1px solid #dedfd4;margin-top:22px;padding-top:14px}.similar-row{padding:14px 0;border-bottom:1px solid #eee}a{color:#315845}#message,#preference-message{color:#8a4531}@media(max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.preference-facets{grid-template-columns:1fr}}@media(max-width:580px){.grid{grid-template-columns:1fr}.wrap{padding:0 17px}.panel{padding:16px}.result-heading{display:block}.filters{gap:10px}.stat{min-width:110px}header{padding:28px 0}.pagination{gap:10px;font-size:13px}}'''

_JS = r'''"use strict";
const $=id=>document.getElementById(id), state={page:1,pages:1,total:0,request:0,favorites:new Map(),preferenceFacets:{}};
const statusNames={unknown:'不明',unread:'未読',reading:'読書中',read:'既読'};
const axisNames={theme:'テーマ',structure:'物語構造',protagonist:'主人公',atmosphere:'雰囲気'};
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
async function api(path,options){const response=await fetch(path,options);if(!response.ok){let message='読み込みに失敗しました。';try{message=(await response.json()).error||message;}catch{}throw Error(message);}return response.json();}
function describe(row){return [(row.authors||[]).map(x=>x.name).join('・')||'著者情報なし',row.year?row.year+'年':'刊行年不明',languageName(row.original_language)||'言語不明'].join(' / ');}
function languageName(code){if(/^Q[0-9]+$/.test(code))return '言語名未取得';try{return code?new Intl.DisplayNames(['ja'],{type:'language'}).of(code):'';}catch{return code;}}
function tags(parent,values){for(const v of values||[])parent.append(node('span',typeof v==='string'?v:v.label,'tag'));}
function statusControl(row){const label=node('label','読書状態：'),select=node('select');select.setAttribute('aria-label',row.title+'の読書状態');for(const [value,text]of Object.entries(statusNames)){const option=node('option',text);option.value=value;select.append(option);}select.value=row.read_status||'unknown';select.addEventListener('change',async()=>{select.disabled=true;try{await api('/api/works/'+row.qid+'/read-status',{method:'POST',headers:{'Content-Type':'application/json','X-Catalogue-Request':'1'},body:JSON.stringify({status:select.value})});row.read_status=select.value;await search();}catch(e){select.value=row.read_status||'unknown';$('message').textContent=e.message;}finally{select.disabled=false;}});label.append(select);return label;}
function renderFavorites(){const parent=$('favorite-works');parent.replaceChildren();if(!state.favorites.size){parent.append(node('span','まだ選択されていません。','muted'));return;}for(const row of state.favorites.values()){const item=node('span',undefined,'selected-item');item.append(node('span',row.title||row.qid));const remove=node('button','解除');remove.type='button';remove.setAttribute('aria-label',row.title+'を好きな作品から解除');remove.addEventListener('click',()=>{state.favorites.delete(row.qid);renderFavorites();});item.append(remove);parent.append(item);}}
function favoriteControl(row){const button=node('button',state.favorites.has(row.qid)?'好きな作品から解除':'好きな作品に追加','favorite-toggle');button.type='button';button.addEventListener('click',()=>{if(state.favorites.has(row.qid))state.favorites.delete(row.qid);else state.favorites.set(row.qid,{qid:row.qid,title:row.title||row.qid});renderFavorites();button.textContent=state.favorites.has(row.qid)?'好きな作品から解除':'好きな作品に追加';});return button;}
function card(row){const c=node('article',undefined,'card'),title=node('button',row.title||'書名不明','title-button');title.type='button';title.addEventListener('click',()=>showDetail(row.qid));c.append(title,node('p',describe(row),'muted'));const aliases=(row.aliases||[]).slice(0,3);if(aliases.length)c.append(node('p','別題：'+aliases.join(' / '),'muted'));const genres=node('div');tags(genres,row.genres);c.append(genres);const bottom=node('div',undefined,'card-bottom');bottom.append(favoriteControl(row),node('p',row.recommendable?'資料記載の特徴から類似候補を探せます':'推薦特徴は未収録','muted'),statusControl(row));c.append(bottom);return c;}
function query(){const q=new URLSearchParams({page:state.page,page_size:30});for(const [key,id]of [['q','search-query'],['genre','genre'],['language','language'],['year_from','year-from'],['year_to','year-to']])if($(id).value)q.set(key,$(id).value);if($('exclude-read').checked)q.set('exclude_read','1');return q;}
async function search(){const request=++state.request;$('message').textContent='';$('shown-count').textContent='検索中…';try{const data=await api('/api/works?'+query());if(request!==state.request)return;state.pages=data.pages;state.page=data.page;state.total=data.total;$('results').replaceChildren(...data.items.map(card));$('shown-count').textContent=data.total.toLocaleString()+'作品'+(data.total?' ・ '+((data.page-1)*data.page_size+1)+'–'+Math.min(data.page*data.page_size,data.total)+'件目':'');if(!data.total)$('results').append(node('p','条件に合う作品がありません。検索語や絞り込み条件を変えてください。'));$('page-label').textContent=data.page+' / '+Math.max(1,data.pages);$('page-prev').disabled=data.page<=1;$('page-next').disabled=data.page>=data.pages;}catch(e){if(request===state.request){$('shown-count').textContent='検索を完了できませんでした';$('message').textContent=e.message;}}}
function section(parent,title){const section=node('section',undefined,'detail-section');section.append(node('h3',title));parent.append(section);return section;}
function sourceCredits(parent,sources){if(!sources.length)return;const credits=section(parent,'内容分類の出典と利用条件');credits.append(node('p','分類はAIが生成し、出典・引用・利用条件を自動照合しています。人による確認は行っていません。再利用時は出典・変更内容の表示と同一ライセンスの条件を確認してください。原作の利用許諾は含みません。','muted'));for(const source of sources){const p=node('p');if(source.source_url){const a=node('a','出典の記事・版');a.href=source.source_url;a.target='_blank';a.rel='noopener noreferrer';p.append(a);}if(source.revision)p.append(node('span',' ・ 版 '+source.revision));if(source.history_url){const h=node('a','寄稿者の履歴');h.href=source.history_url;h.target='_blank';h.rel='noopener noreferrer';p.append(node('span',' ・ '),h);}credits.append(p);if(source.attribution)credits.append(node('p',source.attribution));if(source.locator){const sectionName=source.locator.match(/(?:^|;)section:([^;]+)/);credits.append(node('p','参照箇所：'+(sectionName?sectionName[1]:source.locator),'muted'));}if(source.license){const p=node('p');if(source.license_url){const a=node('a',source.license);a.href=source.license_url;a.target='_blank';a.rel='noopener noreferrer';p.append(a);}else p.textContent=source.license;credits.append(p);}if(source.output_license){const p=node('p','この分類・引用レコードの利用条件：');if(source.output_license_url){const a=node('a',source.output_license);a.href=source.output_license_url;a.target='_blank';a.rel='noopener noreferrer';p.append(a);}else p.append(node('span',source.output_license));credits.append(p);}if(source.changed_from_source)credits.append(node('p','変更内容：'+source.changed_from_source,'muted'));}}
async function showDetail(qid){const dialog=$('detail'),body=$('detail-body');body.replaceChildren(node('p','読み込み中…'));if(!dialog.open)dialog.showModal();try{const row=await api('/api/works/'+qid);body.replaceChildren(node('h2',row.title),node('p',describe(row)),favoriteControl(row));if(row.aliases?.length)body.append(node('p','別題：'+row.aliases.join(' / ')));body.append(statusControl(row));if(row.source_url){const p=node('p'),a=node('a','出典の作品ページ');a.href=row.source_url;a.target='_blank';a.rel='noopener noreferrer';p.append(a);body.append(p);}if(row.provenance?.retrieved_at)body.append(node('p','取得日：'+row.provenance.retrieved_at,'muted'));const coarse=section(body,'資料記載のジャンル');tags(coarse,row.genres);coarse.append(node('p',row.genres?.length?'資料に記載された粗い特徴です。詳細分類の72ラベルと同一ではありません。':'資料記載のジャンルは未収録です。','muted'));const rich=section(body,'LLMの詳細分類');const axes={genre:'ジャンル',theme:'テーマ',structure:'物語構造',protagonist:'主人公',style:'文体',atmosphere:'雰囲気'};for(const item of row.classification_axes||[])rich.append(node('p',axes[item.axis]+'：採用 '+item.adopted_count+'件 / 保留 '+item.held_count+'件'+(item.unknown?' / 不明':''),'muted'));const features=[...(row.strict72_features||[]),...(row.llm_features||[])];if(!features.length)rich.append(node('p','詳細分類は未収録です。'));for(const f of features){const n=node('p',f.label||f.label_id||f.feature_id||'分類特徴');if(f.relevance!==undefined)n.append(node('span',' ・ 関連度 '+(f.relevance===null?'不明':f.relevance)));if(f.confidence!==undefined)n.append(node('span',' ・ モデル確信度 '+(f.confidence===null?'不明':f.confidence)));rich.append(n);if(f.evidence)rich.append(node('p','出典の引用：'+f.evidence,'muted'));if(f.evidence_summary)rich.append(node('p','AIによる根拠の要約：'+f.evidence_summary,'muted'));}rich.append(node('p','関連度とモデル確信度は校正されていない生の値です。正解の確率や個人向け推薦スコアではありません。','muted'));sourceCredits(body,row.content_sources||[]);const similar=section(body,'この作品に近い候補');similar.append(node('p','資料記載のジャンル・書誌特徴による類似候補です。個人の読書履歴に基づく推薦ではありません。','muted'));const data=await api('/api/works/'+qid+'/similar?exclude_read='+($('exclude-read').checked?'1':'0'));if(!data.items.length)similar.append(node('p','根拠のある類似候補はまだありません。'));for(const item of data.items){const div=node('div',undefined,'similar-row'),button=node('button',item.title);button.addEventListener('click',()=>showDetail(item.qid));div.append(button,node('p',describe(item),'muted'));const names={genre:'資料記載のジャンル',source_genre:'資料記載のジャンル',language:'原言語',same_language:'原言語',year:'刊行年代',publication_period:'刊行年代',author:'著者',same_author:'著者',region:'地域',llm_feature:'LLM分類'};for(const reason of item.reasons||[]){const shared=(reason.shared||[]).map(x=>{let label=typeof x==='string'?x:x.label;if(reason.kind==='same_language')return languageName(label);if(reason.kind==='publication_period')return label+'年';if(reason.kind==='same_author')return (item.authors||[]).find(a=>a.qid===label)?.name||'共通する著者';return label;}).join('・');div.append(node('p',(names[reason.kind]||'資料の共通点')+'：'+shared));}if(item.score!==undefined&&item.score!==null)div.append(node('p','類似度 '+Number(item.score).toFixed(3)+'（暫定・確率ではありません）','muted'));similar.append(div);}}catch(e){body.replaceChildren(node('p',e.message));}}
async function stats(){try{const data=await api('/api/stats');const labels=[['works','台帳作品'],['searchable_works','検索可能'],['feature_bearing_works','推薦特徴あり'],['source_genre_works','資料ジャンルあり'],['eligible_classified_works','AI処理済み'],['llm_works','LLM特徴あり'],['personal_score_works','個人スコアあり'],['held_works','表示保留'],['content_history_links','履歴の内容接続'],['bibliographic_history_links','履歴の書誌接続']];$('stats').replaceChildren(...labels.map(([key,label])=>{const c=node('div',undefined,'stat');c.append(node('strong',data[key]===undefined?'未集計':Number(data[key]).toLocaleString()),node('span',label));return c;}));}catch(e){$('message').textContent=e.message;}}
async function facets(){try{const data=await api('/api/facets');for(const item of data.genres||[]){for(const id of ['genre','preference-genre']){const option=node('option',item.label+' ('+item.count.toLocaleString()+')');option.value=item.qid;$(id).append(option);}}for(const item of data.languages||[]){for(const id of ['language','preference-language']){const option=node('option',(languageName(item.value)||'不明')+' ('+item.count.toLocaleString()+')');option.value=item.value;$(id).append(option);}}}catch(e){$('message').textContent=e.message;}}
function renderPreferenceFacets(data){state.preferenceFacets=data.axes||{};const parent=$('preference-facets');parent.replaceChildren();for(const axis of ['theme','structure','protagonist','atmosphere']){const group=node('fieldset',undefined,'facet-group');group.append(node('h3',axisNames[axis]));const options=node('div',undefined,'facet-options');for(const item of state.preferenceFacets[axis]||[]){if(!item.label_id&&!item.qid)continue;const id=item.label_id||item.qid,label=node('label',undefined,'facet-option');const prefer=document.createElement('input');prefer.type='checkbox';prefer.value=id;prefer.dataset.axis=axis;prefer.dataset.direction='prefer';prefer.setAttribute('aria-label',axisNames[axis]+' '+(item.label||id)+'を重視');const avoid=document.createElement('input');avoid.type='checkbox';avoid.value=id;avoid.dataset.axis=axis;avoid.dataset.direction='avoid';avoid.setAttribute('aria-label',axisNames[axis]+' '+(item.label||id)+'を避ける');label.append(prefer,node('span',(item.label||id)+'を重視'),avoid,node('span','避ける'));options.append(label);}if(!options.childElementCount)options.append(node('span','採用済みの選択肢はありません。','muted'));group.append(options);parent.append(group);}}
async function preferenceFacets(){try{renderPreferenceFacets(await api('/api/preference-facets'));}catch(e){$('preference-message').textContent=e.message;}}
function preferencePayload(){const preferred=[],avoided=[],weights={};for(const input of document.querySelectorAll('#preference-facets input:checked')){(input.dataset.direction==='avoid'?avoided:preferred).push(input.value);weights[input.dataset.axis]=1;}for(const axis of Object.keys(axisNames))if(weights[axis]===undefined)weights[axis]=1;const value=(id)=>$(id).value;const payload={liked_qids:[...state.favorites.keys()],preferred_features:preferred,avoided_features:avoided,axis_weights:weights,genre:value('preference-genre'),language:value('preference-language'),year_from:value('preference-year-from')?Number(value('preference-year-from')):null,year_to:value('preference-year-to')?Number(value('preference-year-to')):null,exclude_read:$('preference-exclude-read').checked,limit:30};return payload;}
function renderRecommendations(data){const parent=$('recommendations');parent.replaceChildren();if(data.explanation)parent.append(node('p',data.explanation,'muted'));if(!data.items?.length){parent.append(node('p','条件に合う候補がありません。特徴を減らすか、好きな作品を追加してください。','muted'));return;}for(const row of data.items){const article=node('article',undefined,'recommendation-card');const title=node('button',row.title||'書名不明');title.type='button';title.addEventListener('click',()=>showDetail(row.qid));article.append(node('h3',undefined).appendChild(title).parentNode,node('p',describe(row),'muted'),favoriteControl(row));if(row.score!==undefined&&row.score!==null)article.append(node('p','推薦スコア '+Number(row.score).toFixed(3)+'（未校正・順位付け用）','muted'));for(const reason of row.reasons||[]){const shared=(reason.shared||[]).map(item=>typeof item==='string'?item:item.label||item.qid).join('・');const text=reason.detail||reason.message||shared||reason.label||reason.kind||'一致した根拠';article.append(node('p',(reason.direction==='avoid'?'避けたい特徴：':'好みに合う理由：')+text,'recommendation-reason'));}if(row.feature_insufficiency)article.append(node('p','特徴不足：'+row.feature_insufficiency,'recommendation-warning'));for(const source of row.recommendation_provenance||[]){const p=node('p',undefined,'recommendation-provenance');if(source.source_url){const a=node('a','根拠の出典');a.href=source.source_url;a.target='_blank';a.rel='noopener noreferrer';p.append(a);}if(source.revision)p.append(node('span',' ・ 版 '+source.revision));article.append(p);}parent.append(article);}}
async function recommend(){const button=$('recommend');button.disabled=true;$('preference-message').textContent='';try{renderRecommendations(await api('/api/recommendations',{method:'POST',headers:{'Content-Type':'application/json','X-Catalogue-Request':'1'},body:JSON.stringify(preferencePayload())}));}catch(e){$('preference-message').textContent=e.message;}finally{button.disabled=false;}}
$('search-form').addEventListener('submit',e=>{e.preventDefault();state.page=1;search();});for(const id of ['genre','language','exclude-read'])$(id).addEventListener('change',()=>{state.page=1;search();});$('page-prev').addEventListener('click',()=>{if(state.page>1){state.page--;search();window.scrollTo({top:0,behavior:'smooth'});}});$('page-next').addEventListener('click',()=>{if(state.page<state.pages){state.page++;search();window.scrollTo({top:0,behavior:'smooth'});}});$('reset').addEventListener('click',()=>{HTMLFormElement.prototype.reset.call($('search-form'));state.page=1;search();});$('recommend').addEventListener('click',recommend);$('close-detail').addEventListener('click',()=>$('detail').close());renderFavorites();stats();facets();preferenceFacets();search();
'''


def create_server(database=None, *, catalogue=None, port=8766):
    """Create a loopback-only server, opening the explicitly supplied SQLite store."""
    if catalogue is None:
        if database is None:
            raise ValueError('database is required')
        from recommend_book.scale_catalogue_v1 import ScaleCatalogue
        catalogue = ScaleCatalogue(database)

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
                if path in ('/api/stats', '/api/facets'):
                    raw = catalogue.catalogue_stats() if path == '/api/stats' else catalogue.catalogue_facets()
                    result = {key: raw[key] for key in (*_STAT_KEYS, 'source_genre_works') if key in raw and isinstance(raw[key], int)} if path == '/api/stats' else {}
                    result['languages'] = [{'value': _text(v.get('value')), 'count': int(v['count'])} for v in raw.get('languages', [])]
                    result['genres'] = [dict(_terms([v])[0], count=int(v['count'])) for v in raw.get('genres', [])]
                    self._send(200, result)
                    return
                if path == '/api/works':
                    year_from = integer('year_from', 0, -5000, 3000) if value('year_from') else None
                    year_to = integer('year_to', 0, -5000, 3000) if value('year_to') else None
                    if year_from is not None and year_to is not None and year_from > year_to:
                        raise ValueError
                    data = catalogue.search_catalogue(query=value('q')[:500], genre=value('genre') or None,
                        language=value('language') or None, year_from=year_from, year_to=year_to,
                        read_filter='exclude_read' if value('exclude_read') == '1' else 'all',
                        page=integer('page', 1, 1, 1000000), page_size=integer('page_size', 30, 1, 100))
                    self._send(200, {key: data[key] for key in ('total', 'page', 'page_size', 'pages')} | {'items': [public_work(v) for v in data['items']]})
                    return
                match = re.fullmatch(r'/api/works/(' + _QID + r')(/similar)?', path)
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
            if self.path == '/api/recommendations':
                if not hasattr(catalogue, 'recommend_preferences'):
                    self._send(501, {'error': '好みの推薦はまだ利用できません。'})
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 65536 or self.headers.get('Content-Type') != 'application/json':
                        raise ValueError
                    payload = _preference_payload(json.loads(self.rfile.read(length)))
                    raw = catalogue.recommend_preferences(**payload)
                    if not isinstance(raw, dict) or not isinstance(raw.get('items', []), list):
                        raise TypeError
                    items = []
                    for value in raw.get('items', []):
                        if not isinstance(value, dict):
                            continue
                        item = dict(value)
                        item.setdefault('recommendation_mode', 'preference_recommendation')
                        items.append(public_work(item))
                    response = {'mode': 'preference_recommendation', 'personalized': False,
                                'preference_based': True, 'history_evaluation': 'unverified',
                                'items': items, 'explanation': _text(raw.get('explanation'))}
                    if raw.get('feature_insufficiency'):
                        response['feature_insufficiency'] = _text(raw['feature_insufficiency'])
                    self._send(200, response)
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    self._send(400, {'error': '推薦条件を確認してください。'})
                except Exception:
                    self._send(500, {'error': '推薦結果を作成できませんでした。'})
                return
            match = re.fullmatch(r'/api/works/(' + _QID + r')/read-status', self.path)
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
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args(argv)
    if not args.database.is_file():
        parser.error('指定した台帳が見つかりません。')
    server = create_server(args.database, port=args.port)
    print(f'小説の本棚: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
