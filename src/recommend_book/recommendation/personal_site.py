"""Pure offline renderer for a public, provisional genre recommendation delivery."""
from __future__ import annotations

from html import escape
import json
import math
import re


def _strings(value):
    if isinstance(value, str):
        return [value] if value else []
    return [str(item.get('name', '')) if isinstance(item, dict) else str(item)
            for item in value or [] if item]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _fmt(value):
    return f'{value:.4f}'.rstrip('0').rstrip('.') or '0'


def render_personal_site(delivery: dict, label_names: dict) -> str:
    """Render supplied public ranking only; no files, network, history, or DB access.

    Extra ranking/audit fields are not exported. Input order is preserved. A row
    without adopted content labels, finite score, or supported status is unscored.
    Stable control IDs allow a browser to verify all local filtering paths.
    """
    names = {str(key): str(value) for key, value in label_names.items()}
    ranking = []
    for source in delivery.get('ranking', []):
        labels = list(dict.fromkeys(_strings(source.get('content_labels'))))
        score = _number(source.get('score'))
        supported = bool(labels) and score is not None and source.get('score_status') == 'supported'
        explanations = [{'feature_id': str(item.get('feature_id', '')), 'contribution': _number(item.get('contribution'))}
                        for item in source.get('explanations', []) if item.get('feature_id')]
        ranking.append({'work_id': str(source.get('work_id', '')), 'title': str(source.get('title') or '書名不明'),
                        'authors': _strings(source.get('authors')), 'display_title_aliases': _strings(source.get('display_title_aliases')),
                        'read_status': source.get('read_status') if source.get('read_status') in ('read', 'unread') else 'unknown',
                        'content_labels': labels, 'score': score if supported else None,
                        'score_status': 'supported' if supported else 'unsupported', 'explanations': explanations})
        if source.get('classification_basis') == 'source_declared_genre':
            ranking[-1]['classification_basis'] = 'source_declared_genre'
            qid = source.get('source_qid')
            if isinstance(qid, str) and re.fullmatch(r'Q[1-9][0-9]*', qid):
                ranking[-1]['source_qid'] = qid
    cards = []
    for index, row in enumerate(ranking):
        labels = row['content_labels']
        supported = row['score_status'] == 'supported'
        score_text = 'スコア ' + _fmt(row['score']) if supported else '未採点'
        detail = ('嗜好との対応根拠に基づく暫定スコア' if supported else
                  '原分類の採用特徴なし' if not labels else '嗜好との対応根拠なし')
        read_text = {'read': '既読', 'unread': '未読', 'unknown': '不明'}[row['read_status']]
        author = '・'.join(row['authors']) or '著者情報なし'
        aliases = '<p class="aliases">別題：' + escape(' ／ '.join(row['display_title_aliases'])) + '</p>' if row['display_title_aliases'] else ''
        chips = ''.join('<span class="chip">' + escape(names.get(label, label)) + '</span>' for label in labels) or '<span class="muted">分類特徴なし</span>'
        reasons = []
        if supported:
            for item in row['explanations']:
                contribution = item['contribution']
                if contribution is not None:
                    sign = '+' if contribution > 0 else ''
                    reasons.append(escape(names.get(item['feature_id'], item['feature_id'])) + '：寄与 ' + sign + _fmt(contribution))
        why = '<p class="reasons">' + ' ／ '.join(reasons) + '</p>' if reasons else ''
        basis = ''
        if row.get('classification_basis') == 'source_declared_genre':
            qid = row.get('source_qid')
            link = f' · <a href="https://www.wikidata.org/wiki/{qid}" rel="noreferrer">Wikidata</a>' if qid else ''
            basis = '<p class="aliases classification-basis">資料記載のジャンル（暫定）' + link + '</p>'
        cards.append(f'<li id="work-{index}" class="card" data-work-id="{escape(row["work_id"], quote=True)}">'
                     f'<div class="card-top"><span class="order">表示順 {index + 1}</span>'
                     f'<strong class="score {"scored" if supported else "unscored"}">{escape(score_text)}</strong></div>'
                     f'<h2>{escape(row["title"])}</h2><p class="author">{escape(author)}</p>{aliases}'
                     f'<p class="read-status">読書状態：{read_text}</p>{basis}<div class="chips">{chips}</div>'
                     f'<p class="score-detail">{detail}</p>{why}'
                     f'<p class="work-id">作品ID：{escape(row["work_id"])}</p></li>')
    present_labels = sorted({label for row in ranking for label in row['content_labels']}, key=lambda label: (names.get(label, label), label))
    options = ''.join(f'<option value="{escape(label, quote=True)}">{escape(names.get(label, label))}</option>' for label in present_labels)
    payload = json.dumps({'ranking': ranking}, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for char, replacement in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        payload = payload.replace(char, replacement)
    template = '''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>暫定genre嗜好推薦</title>
<style>
:root{color-scheme:light;font-family:system-ui,"Yu Gothic",sans-serif;color:#203047;background:#f4f6fa}
*{box-sizing:border-box}body{margin:0}main{max-width:1100px;margin:auto;padding:36px 22px 60px}
h1{font-size:clamp(1.6rem,4vw,2.2rem);margin:8px 0 12px;line-height:1.4}h2{font-size:1.13rem;line-height:1.6;margin:12px 0 6px}
.eyebrow{font-size:.8rem;letter-spacing:.08em;color:#516784}.intro{line-height:1.9;max-width:850px}
.notice{background:#fff5d9;border:1px solid #e5cc87;border-radius:12px;padding:15px 18px;line-height:1.8;margin:22px 0}
.controls{display:grid;grid-template-columns:minmax(220px,2fr) minmax(160px,1fr) minmax(150px,1fr) auto;gap:12px;align-items:end;background:white;padding:18px;border-radius:12px;border:1px solid #d9e1ec}
label{display:block;font-size:.85rem;font-weight:600;margin-bottom:7px}input,select,button{font:inherit;padding:11px;border:1px solid #b4c0d1;border-radius:7px;background:white;color:#203047;width:100%}
button{cursor:pointer;background:#e9f0fb;white-space:nowrap}input:focus,select:focus,button:focus{outline:3px solid #a6c6ff;outline-offset:2px}
.result-info{margin:20px 0;color:#50627a}#results{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;list-style:none;padding:0;margin:0}
.card{background:white;border:1px solid #d9e1ec;border-radius:12px;padding:20px;overflow-wrap:anywhere}.card-top{display:flex;justify-content:space-between;gap:15px;align-items:center}.order{color:#63748c;font-size:.82rem}
.score{font-size:.9rem;padding:5px 9px;border-radius:6px}.scored{background:#e8f0ff;color:#244d8a}.unscored{background:#f0f1f3;color:#596375}
.author,.aliases,.read-status,.score-detail,.reasons{font-size:.87rem;line-height:1.8;margin:7px 0}.aliases,.score-detail,.muted{color:#68748a}.read-status{color:#516078}.chips{display:flex;flex-wrap:wrap;gap:7px;margin:14px 0}.chip{background:#edf2f8;border-radius:6px;padding:4px 8px;font-size:.8rem}.work-id{font-size:.72rem;color:#778399;margin:15px 0 0}
#empty-state{padding:45px;text-align:center;background:white;border:1px dashed #aab8cb;border-radius:12px;line-height:1.8}footer{font-size:.82rem;line-height:1.8;color:#64738a;margin-top:28px}[hidden]{display:none!important}
@media(max-width:760px){main{padding:24px 15px 40px}.controls{grid-template-columns:1fr 1fr}.search-control{grid-column:1/-1}#results{grid-template-columns:1fr}}
@media(max-width:420px){.controls{grid-template-columns:1fr}.search-control{grid-column:auto}}
</style></head><body><main>
<header><div class="eyebrow">LOCAL RECOMMENDATION · EXPERIMENTAL</div><h1>暫定genre嗜好推薦</h1>
<p class="intro">ジャンルの対応を手がかりに、作品候補を並べています。書名・別題・著者・分類・表示された理由から検索できます。</p></header>
<aside class="notice" aria-label="推薦の確認状況"><strong>品質は未検証です。</strong> 分類・嗜好の対応には不確実性があります。表示順とスコアは候補を比較するための値で、好みに合う確率ではありません。読書状態が不明の作品を未読とは扱いません。</aside>
<section class="controls" aria-label="作品の絞り込み">
<div class="search-control"><label for="search-query">全文検索</label><input id="search-query" type="search" placeholder="書名・別題・著者・分類・理由" autocomplete="off"></div>
<div><label for="label-filter">分類を1つ選択</label><select id="label-filter"><option value="">すべての分類</option>__OPTIONS__</select></div>
<div><label for="score-status">採点の状態</label><select id="score-status"><option value="all">すべて</option><option value="supported">採点あり</option><option value="unsupported">未採点</option></select></div>
<button id="reset-filters" type="button">絞り込みを解除</button></section>
<p id="shown-count" class="result-info" role="status" aria-live="polite">__COUNT__件 / __COUNT__件</p>
<div id="empty-state" __EMPTY_HIDDEN__><strong>該当する作品は0件です。</strong><br>検索語や分類・採点の条件を変更してください。</div>
<ol id="results">__CARDS__</ol>
<noscript><p>検索・分類フィルターにはJavaScriptを有効にしてください。作品一覧はそのまま読めます。</p></noscript>
<footer>未採点は「好みに合わない」という判定ではありません。採用された分類特徴や嗜好との対応根拠が不足しています。原分類の特徴がない作品も候補として保持しています。</footer>
<script id="recommendation-data" type="application/json">__DATA__</script>
<script>
"use strict";
const data=JSON.parse(document.getElementById("recommendation-data").textContent);
const query=document.getElementById("search-query"),label=document.getElementById("label-filter"),status=document.getElementById("score-status");
const cards=Array.from(document.querySelectorAll("#results > .card"));
const normalize=value=>String(value).normalize("NFKC").toLocaleLowerCase("ja");
const searchable=cards.map(card=>normalize(card.textContent));
function applyFilters(){
 const term=normalize(query.value).trim();let count=0;
 data.ranking.forEach((row,index)=>{
  const matches=(!term||searchable[index].includes(term))&&(!label.value||row.content_labels.includes(label.value))&&(status.value==="all"||row.score_status===status.value);
  cards[index].hidden=!matches;if(matches)count++;
 });
 document.getElementById("shown-count").textContent=count+"件 / "+data.ranking.length+"件";
 document.getElementById("empty-state").hidden=count!==0;
}
query.addEventListener("input",applyFilters);label.addEventListener("change",applyFilters);status.addEventListener("change",applyFilters);
document.getElementById("reset-filters").addEventListener("click",()=>{query.value="";label.value="";status.value="all";applyFilters();query.focus();});
applyFilters();
</script></main></body></html>'''
    replacements = {'__OPTIONS__': options, '__COUNT__': str(len(ranking)),
                    '__EMPTY_HIDDEN__': 'hidden' if ranking else '', '__CARDS__': ''.join(cards), '__DATA__': payload}
    return re.sub(r'__(?:OPTIONS|COUNT|EMPTY_HIDDEN|CARDS|DATA)__', lambda match: replacements[match[0]], template)
