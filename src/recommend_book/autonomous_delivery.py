"""Local delivery from verified data, without new user ratings or read claims."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

from recommend_book.recommendation.pipeline import build_profile, render_recommendations


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def verify_baseline(manifest_path, root):
    root = Path(root).resolve(strict=True)
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    verified = []
    for item in manifest['artifacts']:
        path = (root / item['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Baseline artifact would escape workspace')
        if not path.is_file() or file_sha(path) != item['sha256']:
            raise ValueError('Protected baseline artifact changed: ' + item['path'])
        verified.append({'path': item['path'], 'sha256': item['sha256']})
    return {'unchanged': True, 'verified_count': len(verified), 'artifacts': verified}


def build_delivery(world, references, selection, *, ranker, limit=100,
                   target_popularity_comparable=False):
    selected = deepcopy(selection['recommended_setting'])
    if selected.get('family') not in {'personal', 'popularity', 'hybrid_rank', 'personal_tie_robust'}:
        raise ValueError('Unsupported recommendation family')
    target = deepcopy(selected)
    popularity_required = selected['family'] in {'popularity', 'hybrid_rank'}
    fallback = popularity_required and not target_popularity_comparable
    if fallback:
        target = deepcopy(selection.get('personal_setting', {'id': 'personal', 'family': 'personal'}))
        if target.get('family') not in {'personal', 'personal_tie_robust'}:
            raise ValueError('Fallback must be an evidence-based personal family')
    result = ranker(world['rows'], references['rows'], target, limit=limit)
    unread = ranker(world['rows'], references['rows'], target, limit=limit, require_confirmed_unread=True)
    audit = {'version': 'autonomous-world-selection-v1', 'source_setting': selected,
             'target_setting': target, 'fallback_applied': fallback,
             'fallback_reason': 'No verified comparable target popularity policy' if fallback else None,
             'target_popularity_comparable': target_popularity_comparable,
             'world_transfer_verified': False, 'user_tasks_required': 0,
             'new_user_ratings_generated': 0, 'read_status_inferred': False,
             'narou_popularity_transferred': False,
             'source_selection_basis': selection.get('selection_basis'),
             'world_quality_status': 'unverified_no_target_feedback'}
    result['audit']['autonomous_selection'] = audit
    profile = build_profile(references['rows'])
    html = render_recommendations(result, profile)
    html = html.replace('既読・未読履歴を追加すると、確認済みの作品を絞り込めます。',
                        '読書状態未確認の候補は「すべて」または「読書状態未確認」で表示できます。')
    notice = '<p>保存済みの評価データだけを使っています。追加の評価や履歴入力は必要ありません。世界の小説での好みの一致は未検証です。</p>'
    if fallback:
        notice += '<p>評価元の比較では人気順を含めて方式を選定しました。この作品集合には比較可能な人気指標がないため、共通する特徴を使う参考順位を表示しています。</p>'
    html = html.replace('<label>表示 ', notice + '<label>表示 ', 1)
    return {'recommendations': result, 'confirmed_unread': unread, 'selection': audit,
            'profile': profile, 'html': html, 'world_audit': deepcopy(world['audit']),
            'reference_audit': deepcopy(references['audit'])}


def run_delivery(*, world_database, narou_database, annotations, selection_path,
                 baseline_manifest, output, limit=100):
    from recommend_book.recommendation.world import load_world_database
    from recommend_book.recommendation.narou import load_narou_reference
    from recommend_book.recommendation.autonomous_evaluation import rank_with_setting
    root = Path(__file__).resolve().parents[2]
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Delivery output must be a new namespace')
    output.parent.mkdir(parents=True, exist_ok=True)
    baseline_before = verify_baseline(baseline_manifest, root)
    inputs = {name: Path(path).resolve(strict=True) for name, path in
              {'world_database': world_database, 'narou_database': narou_database,
               'annotations': annotations, 'selection': selection_path,
               'baseline_manifest': baseline_manifest}.items()}
    hashes = {name: file_sha(path) for name, path in inputs.items()}
    selection = json.loads(inputs['selection'].read_text(encoding='utf-8'))
    world = load_world_database(inputs['world_database'])
    refs = load_narou_reference(inputs['narou_database'], inputs['annotations'])
    built = build_delivery(world, refs, selection, ranker=rank_with_setting, limit=limit)
    with tempfile.TemporaryDirectory(prefix='.delivery-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'published'
        stage.mkdir()
        artifacts = {'world-works.json': world['rows'], 'world-audit.json': built['world_audit'],
                     'reference-audit.json': built['reference_audit'], 'profile.json': built['profile'],
                     'recommendations.json': built['recommendations'],
                     'confirmed-unread.json': built['confirmed_unread'], 'selection-audit.json': built['selection']}
        for name, value in artifacts.items():
            (stage / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        (stage / 'recommendations.html').write_text(built['html'], encoding='utf-8')
        baseline_after = verify_baseline(baseline_manifest, root)
        if hashes != {name: file_sha(path) for name, path in inputs.items()}:
            raise ValueError('Delivery source changed during generation')
        manifest = {'version': 'autonomous-recommendation-delivery-v1',
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'sources': {name: {'path': str(path), 'sha256': hashes[name]} for name, path in inputs.items()},
                    'outputs': {path.name: file_sha(path) for path in sorted(stage.iterdir())},
                    'baseline_before': baseline_before, 'baseline_after': baseline_after,
                    'work_count': len(world['rows']), 'existing_ratings': sum(r.get('rating') is not None for r in refs['rows']),
                    'ranking_count': len(built['recommendations']['ranking']),
                    'supported_count': built['recommendations']['audit']['supported_count'],
                    'confirmed_unread_count': built['confirmed_unread']['audit']['eligible_count'],
                    'user_tasks_required': 0, 'new_user_ratings_generated': 0,
                    'human_quality_verified': False, 'target_domain_quality_verified': False}
        (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        stage.rename(output)
    return {key: manifest[key] for key in ('work_count', 'existing_ratings', 'ranking_count', 'supported_count', 'confirmed_unread_count', 'user_tasks_required')}
