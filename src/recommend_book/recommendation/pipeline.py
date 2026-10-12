"""Local recommendation artifacts; personal records never leave this process."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def load_history(path: Path | None) -> list[dict]:
    if path is None:
        return []
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate history field: ' + key)
            result[key] = value
        return result
    def bad_constant(value):
        raise ValueError('Non-finite history value: ' + value)
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=pairs,
                       parse_constant=bad_constant)
    if not isinstance(value, list):
        raise ValueError('Read history must be a JSON array')
    for row in value:
        if not isinstance(row, dict):
            raise ValueError('History record must be an object')
        work = row.get('work_id')
        identifiers = row.get('external_ids', [])
        if work is not None and (not isinstance(work, str) or not work.strip()):
            raise ValueError('Invalid history work identity')
        if not isinstance(identifiers, list) or any(
            not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k].strip()
                                                  for k in ('source', 'value')) for item in identifiers
        ):
            raise ValueError('Invalid external history identity')
        if not work and not identifiers:
            raise ValueError('History needs an explicit work or external identity; titles alone are insufficient')
        if row.get('read_status') not in {'read', 'unread', 'unknown'}:
            raise ValueError('History needs an explicit read_status')
        rating = row.get('rating')
        if rating is not None and (isinstance(rating, bool) or not isinstance(rating, (int, float))
                                   or not math.isfinite(rating) or not 1 <= rating <= 5):
            raise ValueError('History rating must be finite in [1,5]')
    return value


def build_profile(rows: list[dict]) -> dict:
    rated = [row for row in rows if row.get('rating') is not None]
    grouped: dict[str, list[dict]] = {}
    native: dict[str, list[dict]] = {}
    for row in rated:
        rating = row['rating']
        if isinstance(rating, bool) or not isinstance(rating, (int, float)) or not math.isfinite(rating) or not 1 <= rating <= 5:
            raise ValueError('Invalid profile rating')
        for key, strength in row.get('features', {}).items():
            if strength > 0:
                grouped.setdefault(key, []).append(row)
        for key, strength in row.get('native_features', {}).items():
            if strength > 0:
                native.setdefault(key, []).append(row)
    def stats(items):
        return {'count': len(items), 'mean_rating': sum(row['rating'] for row in items) / len(items),
                'high_count': sum(row['rating'] >= 4 for row in items),
                'low_count': sum(row['rating'] <= 2 for row in items)}
    return {
        'version': 'personal-profile-v1', 'rated_count': len(rated),
        'featureless_rated_count': sum(not row.get('features') for row in rated),
        'mean_rating': sum(row['rating'] for row in rated) / len(rated) if rated else None,
        'rating_distribution': dict(sorted(Counter(row['rating'] for row in rated).items())),
        'features': {key: stats(items) for key, items in sorted(grouped.items())},
        'native_source_features': {key: stats(items) for key, items in sorted(native.items())},
        'limits': ['Observed associations in selected rated works, not causal preferences.',
                   'Native source features without a bridge do not score target novels.',
                   'Missing features remain unknown; ratings are never classification gold.'],
    }


def render_recommendations(result: dict, profile: dict) -> str:
    display_path = Path(__file__).resolve().parents[3] / 'config/recommendation-label-display-ja.json'
    labels_ja = json.loads(display_path.read_text(encoding='utf-8'))['labels'] if display_path.exists() else {}
    cards = []
    for position, row in enumerate(result['ranking'], 1):
        status = {'unknown': '読書状態未確認', 'unread': '未読確認済み', 'read': '既読'}.get(row.get('read_status'), '読書状態未確認')
        score = row.get('score')
        score_text = f'{score:.3f}' if score is not None else '根拠不足・算出なし'
        labels = '、'.join(labels_ja.get(key, key) for key in sorted(row.get('features', {}))) or '分類根拠が不足しています'
        explanations = row.get('explanations', [])
        positive = sorted({feature['feature_id'] for item in explanations if item.get('contribution', 0) > 0
                           for feature in item.get('features', [])})
        negative = sorted({feature['feature_id'] for item in explanations if item.get('contribution', 0) < 0
                           for feature in item.get('features', [])})
        def names(keys):
            return escape('、'.join(labels_ja.get(key, key) for key in keys))
        reasons = '<p>似ている評価済み作品の好評価と低評価を両方使って推定しています。</p>'
        if positive:
            reasons += '<p>好評価の作品と共通する要素：' + names(positive) + '</p>'
        if negative:
            reasons += '<p>低めの評価の作品とも共通する要素：' + names(negative) + '</p>'
        if not positive and not negative:
            reasons = '<p>好みと比較できる根拠が不足しています。</p>'
        metadata = ' · '.join(filter(None, ['、'.join(row.get('authors', [])),
            '原言語：' + str(row['original_language']) if row.get('original_language') else '',
            str(row['first_publication_year']) + '年' if row.get('first_publication_year') is not None else '']))
        links = []
        for url in row.get('source_urls', [])[:3]:
            if isinstance(url, str) and urlsplit(url).scheme in {'http', 'https'}:
                links.append(f'<a href="{escape(url, quote=True)}" rel="noreferrer">作品情報{len(links)+1}</a>')
        cards.append(f'<article data-state="{escape(str(row.get("read_status", "unknown")), quote=True)}">'
            f'<h2>{position}. {escape(str(row["title"]))}</h2><p>{status} · 推薦スコア {score_text}</p>'
            f'<p>{escape(metadata)}</p><p>{escape(labels)}</p>'
            f'<details><summary>推薦の根拠</summary>{reasons}<p>{" · ".join(links)}</p></details></article>')
    return '''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>世界の小説 推薦PoC</title><style>body{font:16px/1.7 system-ui;margin:2rem auto;max-width:1000px;padding:0 1rem;background:#f5f6f8;color:#20252b}article{background:white;border:1px solid #d8dce2;border-radius:8px;padding:1rem;margin:1rem 0}h2{font-size:1.25rem}pre{white-space:pre-wrap;overflow-wrap:anywhere}select{font:inherit;padding:.3rem}</style>
<h1>世界の小説 推薦PoC</h1><p>スコアは既存評価からの推定値です。候補作品の本人の実評価、分類の正解率、校正済み確率を意味しません。読書状態未確認の作品を未読とは断定していません。</p>
<label>表示 <select id="state"><option value="all">すべて</option><option value="unread">未読確認済み</option><option value="unknown">読書状態未確認</option></select></label><p id="empty" hidden>該当する候補はありません。既読・未読履歴を追加すると、確認済みの作品を絞り込めます。</p>''' + ''.join(cards) + '''<script>document.getElementById('state').addEventListener('change',e=>{let visible=0;document.querySelectorAll('article').forEach(a=>{a.hidden=e.target.value!=='all'&&a.dataset.state!==e.target.value;if(!a.hidden)visible++;});document.getElementById('empty').hidden=visible>0;});</script></html>'''


def run_recommendation(*, world_database: Path, narou_database: Path, annotations: Path,
                       output: Path, history: Path | None = None, bridge: Path | None = None,
                       limit: int = 20, require_confirmed_unread: bool = False,
                       evaluate: bool = True) -> dict:
    from .engine import rank_candidates
    from .narou import load_narou_reference
    from .world import load_world_database
    from .evaluation import evaluate_recommendations
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError('Recommendation limit must be positive')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Output must be a new directory; existing artifacts are immutable')
    paths = {'world_database': Path(world_database), 'narou_database': Path(narou_database),
             'annotations': Path(annotations)}
    if history is not None:
        paths['history'] = Path(history)
    if bridge is None:
        bridge = Path(__file__).resolve().parents[3] / 'config/recommendation-feature-bridge.json'
    paths['bridge'] = Path(bridge)
    project_root = Path(__file__).resolve().parents[3]
    code_paths = [Path(__file__), *(Path(__file__).parent / (name + '.py') for name in ('engine', 'narou', 'world', 'evaluation')),
                  project_root / 'config/taxonomy.json', project_root / 'config/recommendation-label-display-ja.json',
                  project_root / 'pyproject.toml', project_root / 'uv.lock']
    code_before = {str(path.relative_to(project_root)): _sha(path) for path in code_paths}
    before = {key: _sha(path) for key, path in paths.items()}
    world = load_world_database(Path(world_database))
    reference = load_narou_reference(Path(narou_database), Path(annotations), bridge=Path(bridge))
    read_history = load_history(history)
    result = rank_candidates(world['rows'], reference['rows'], limit=limit,
                             read_history=read_history, require_confirmed_unread=require_confirmed_unread)
    profile = build_profile(reference['rows'])
    evaluation_report = evaluate_recommendations(reference['rows']) if evaluate else None
    native_evaluation = None
    if evaluate:
        evaluation_report['feature_space'] = 'partial_world_taxonomy_bridge'
        native_rows = [{**row, 'features': dict(row.get('native_features', {})),
                        'feature_provenance': {}} for row in reference['rows']]
        native_evaluation = evaluate_recommendations(native_rows)
        native_evaluation['feature_space'] = 'narou_native_theme_features'
        native_evaluation['quality_limits']['feature_space_interpretation'] = 'Theme-only sensitivity check; does not reproduce the original text-and-theme recommender.'
    after = {key: _sha(path) for key, path in paths.items()}
    if before != after:
        raise ValueError('Recommendation source changed while being read')
    if code_before != {str(path.relative_to(project_root)): _sha(path) for path in code_paths}:
        raise ValueError('Recommendation implementation changed while running')
    overlap = {row['work_id'] for row in world['rows']} & {row['work_id'] for row in reference['rows']}
    source_external = {(item['source'], item['value']) for row in reference['rows'] for item in row.get('external_ids', [])}
    overlap.update(row['work_id'] for row in world['rows'] if source_external &
                   {(item['source'], item['value']) for item in row.get('external_ids', [])})
    manifest = {
        'schema_version': 'world-novel-recommendation-poc-v1',
        'created_at': datetime.now(timezone.utc).isoformat(), 'source_sha256': before,
        'execution_artifact_sha256': code_before,
        'source_paths': {key: str(path.resolve()) for key, path in paths.items()},
        'world_audit': world['audit'], 'reference_audit': reference['audit'],
        'ranking_audit': result['audit'], 'reference_rated_count': profile['rated_count'],
        'target_rated_overlap_count': len(overlap),
        'target_domain_recommendation_quality': 'unverified',
        'classification_human_quality': 'pending_human_gold',
        'score_meaning': 'Mean-centered nearest-neighbor rating estimate, not an observed rating or probability.',
        'limits': ['Source-domain grouped retrospective evaluation does not verify cross-domain recommendation quality.',
                   'Unmapped preferences are retained in the profile but do not rank target works.',
                   'No feedback is fabricated or collected automatically.'],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=output.name + '.staging-', dir=output.parent))
    _json(staging / 'recommendations.json', result)
    _json(staging / 'profile.json', profile)
    if evaluation_report is not None:
        _json(staging / 'source-domain-evaluation.json', evaluation_report)
        _json(staging / 'native-theme-evaluation.json', native_evaluation)
    (staging / 'recommendations.html').write_text(render_recommendations(result, profile), encoding='utf-8')
    manifest['artifact_sha256'] = {path.name: _sha(path) for path in sorted(staging.iterdir())}
    _json(staging / 'manifest.json', manifest)
    os.rename(staging, output)
    return {'output': str(output), 'world_count': len(world['rows']),
            'reference_count': len(reference['rows']), 'recommendation_count': len(result['ranking']),
            'target_rated_overlap_count': len(overlap), 'target_quality': 'unverified'}
