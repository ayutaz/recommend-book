"""Append only selected new public works to the pinned 1,989-row local delivery."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
from html import escape
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/autonomous-next-v3'
BASELINE_MANIFEST_SHA = '3fc3094737ed5fde9fdfd663382798525a4d6b8ddf088fdba7f32891c6588a02'
BASELINE_DELIVERY_SHA = '3316ebddc171958d4df648f1832c6b1549977199bed198abbf79284c4280b20a'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def safe_url(value):
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    host = parsed.hostname or ''
    if parsed.scheme != 'https' or parsed.username or parsed.password or not any(
            host == domain or host.endswith('.'+domain)
            for domain in ('wikipedia.org', 'wikidata.org', 'creativecommons.org', 'openlibrary.org')):
        raise ValueError('Public credit URL is outside the fixed source allowlist')
    return value


def source_credits(work):
    """Never export raw paths, arbitrary attribution keys, evidence prose or quotes."""
    rows = []
    for ev in work.evidence:
        attribution = ev.attribution
        row = {'evidence_id': ev.evidence_id, 'source_url': safe_url(ev.source_url),
            'permalink': safe_url(attribution.get('permalink') or ev.source_url),
            'history_url': safe_url(attribution.get('history_url')),
            'license': ev.license, 'license_url': safe_url(ev.license_url),
            'credit': attribution.get('credit') or 'Public bibliographic source contributors',
            'changes': 'Evidence was screened and converted to plain text for provisional AI classification; source text is not reproduced in this delivery.',
            'share_alike_notice': 'Wikipedia-derived classification material is attributed under CC BY-SA 4.0 where applicable.'}
        rows.append(row)
    return rows


def merge_rankings(old_rows, new_rows):
    ids = [r['work_id'] for r in old_rows + new_rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate work ID would replace or multiply an old public row')
    merged = deepcopy(old_rows + new_rows)
    merged.sort(key=lambda row: (row['score'] is None, -(row['score'] or 0), row['work_id']))
    return merged


def join_identical_projection(target, identity, value):
    if identity in target and target[identity] != value:
        raise ValueError('Duplicate supplement metadata/source projection differs')
    target[identity] = value


def claim_completed_work(target, identity, owner):
    if identity in target:
        raise ValueError('Completed work has multiple adopted supplement owners')
    target[identity] = owner


def require_owner_reviews(owners, selected_ids, supplied_review_hashes):
    for identity in selected_ids:
        if identity not in owners or owners[identity]['review_sha256'] not in supplied_review_hashes:
            raise ValueError('Selected completed work lacks its owning database bound review document')


def credit_html(credits):
    cards = []
    for work_id, records in credits.items():
        links = []
        for record in records:
            links.append('<li>' + escape(record['credit']) + ' · ' +
                ' · '.join('<a href="'+escape(record[key], quote=True)+'" rel="noreferrer">'+text+'</a>'
                    for key, text in (('permalink','固定版'),('history_url','履歴'),('license_url',record['license'] or 'ライセンス')) if record[key]) +
                '<br>' + escape(record['changes']) + '</li>')
        cards.append('<details><summary>'+escape(work_id)+'</summary><ul>'+''.join(links)+'</ul></details>')
    return '<section aria-label="追加作品の出典"><h2>追加作品の出典と変更表示</h2><p>Wikipedia由来の分類資料は、該当する場合 CC BY-SA 4.0 に基づき表示しています。原資料の本文や引用は掲載していません。</p>'+''.join(cards)+'</section>'


def build(args):
    runtime = args.runtime_root.resolve(strict=True)
    sys.path.insert(0, str(runtime/'src'))
    from recommend_book.usage_guard_v4 import UsageGuard, model_hash
    from recommend_book.next_database_v4 import load_next_world_database, get_next_inputs, _quality_document
    from recommend_book.models import WorkInput
    from recommend_book.recommendation.personal_poc import PROFILE_PINS, PURPOSE, load_pinned_json
    from recommend_book.recommendation.personal_ranking import rank_personal_features
    from recommend_book.recommendation.personal_site import render_personal_site
    protected = {}
    def pin(path, expected=None):
        path = Path(path).resolve(strict=True)
        digest = sha(path)
        if expected is not None and digest != expected:
            raise ValueError('Pinned delivery source changed: '+path.name)
        protected[str(path)] = digest
        return path
    baseline = BASE/'database/phase3-personal-poc-with-declared-genres-v2'
    manifest = load_pinned_json(pin(baseline/'manifest.json', BASELINE_MANIFEST_SHA), BASELINE_MANIFEST_SHA)
    old = load_pinned_json(pin(baseline/'delivery.json', BASELINE_DELIVERY_SHA), BASELINE_DELIVERY_SHA)
    if manifest['output_sha256']['delivery.json'] != BASELINE_DELIVERY_SHA or len(old['ranking']) != 1989:
        raise ValueError('Pinned baseline contract differs')
    for relative in ('src/recommend_book/recommendation/personal_ranking.py',
                     'src/recommend_book/recommendation/personal_site.py',
                     'config/recommendation-label-display-ja.json'):
        pin(ROOT/relative, manifest['source_sha256'][str(ROOT/relative)])
    selected = []
    for path in args.selected_inputs:
        pin(path)
        selected.extend(WorkInput.model_validate_json(line) for line in path.read_bytes().splitlines() if line.strip())
    if not selected or len({w.work_id for w in selected}) != len(selected):
        raise ValueError('Selected new works empty or duplicate')
    old_ids = {r['work_id'] for r in old['ranking']}
    if any(w.work_id in old_ids for w in selected):
        raise ValueError('Selected new work already appears in prior public delivery')
    guard = UsageGuard(pin(args.ledger, args.ledger_sha256), expected_ledger_sha256=args.ledger_sha256)
    originals, owners, metadata, admitted = {}, {}, {}, {}
    database_audits, adopted_reviews = [], set()
    for database in args.database:
        pin(database)
        for work in get_next_inputs(database)['classification']:
            join_identical_projection(originals, work.work_id, work)
        bibliography = load_next_world_database(database, guard=guard, usage='display', content_classified=False)
        classified = load_next_world_database(database, guard=guard, usage='display', content_classified=True)
        for row in bibliography['rows']:
            join_identical_projection(metadata, row['work_id'], row)
        for row in classified['rows']:
            if row['work_id'] in admitted:
                raise ValueError('Classified display row belongs to multiple supplements')
            admitted[row['work_id']] = row
        connection = sqlite3.connect(database.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)
        try:
            runs = {r[0]: json.loads(r[1]).get('quality_adoption_review')
                for r in connection.execute('SELECT run_id,provenance_json FROM next_classification_runs')}
            for work_id, run_id in connection.execute("SELECT work_id,run_id FROM next_classification_inputs WHERE status='completed'"):
                binding = runs[run_id]
                if not binding:
                    raise ValueError('Every completed supplement work needs an adopted independent review')
                claim_completed_work(owners, work_id, {'database': str(database.resolve()),
                    'run_id': run_id, 'review_sha256': binding['sha256']})
                adopted_reviews.add(binding['sha256'])
        finally:
            connection.close()
        database_audits.append({'database': str(database.resolve()), 'sha256': sha(database),
            'classification': classified['audit'], 'metadata': bibliography['audit']})
    if any(originals.get(w.work_id) != w for w in selected):
        raise ValueError('Selected complete input differs from adopted database source projection')
    if not {w.work_id for w in selected} <= set(owners):
        raise ValueError('All selected recommendation additions need authentic completed classifications')
    cases = {}
    review_bindings = []
    for path in args.quality_review:
        pin(path)
        if sha(path) not in adopted_reviews:
            raise ValueError('Delivery quality review was not bound into this adopted database')
        document, bound_cases, files = _quality_document(path.resolve())
        for source, digest in files.items():
            pin(source, digest)
        for case in bound_cases.values():
            if case['work_id'] in cases:
                raise ValueError('Repeated review work identity')
            cases[case['work_id']] = case
        review_bindings.append({'path': str(path.resolve()), 'sha256': sha(path), 'run_id': document['run_id']})
    supplied_reviews = {binding['sha256'] for binding in review_bindings}
    require_owner_reviews(owners, [w.work_id for w in selected], supplied_reviews)
    profile_dir = BASE/'evaluation/phase3-personal-profile-v1/runtime-v2'
    snapshots = {name: load_pinned_json(pin(profile_dir/name, digest), digest) for name, digest in PROFILE_PINS.items()}
    private, contract = snapshots['private-profile.json'], snapshots['purpose-contract.json']
    if (contract['purpose'] != PURPOSE or private['purpose'] != snapshots['purpose-receipts.json']
        or private['purpose']['contract_sha256'] != PROFILE_PINS['purpose-contract.json']
        or private['summary'] != snapshots['profile-summary.json']
        or model_hash(snapshots['public-rows.json']) != contract['public_rows_sha256']
        or len(private['private_rows']) != 2):
        raise ValueError('Pinned private two-reference purpose contract differs')
    for descriptor in contract['artifacts'].values():
        pin(descriptor['path'], descriptor['sha256'])
    labels_path = pin(ROOT/'config/recommendation-label-display-ja.json')
    labels = json.loads(labels_path.read_bytes())['labels']
    candidates, proofs, credits = [], [], {}
    for work in selected:
        if not guard.check_input(work, 'display').allowed:
            raise ValueError('Selected full source/credit display purpose denied')
        if work.work_id not in metadata:
            raise ValueError('Selected metadata lacks independent display permission')
        row = deepcopy(admitted.get(work.work_id, metadata[work.work_id]))
        case = cases.get(work.work_id)
        blocked = bool(case and (case['status'] == 'held' or args.uncertain_policy == 'metadata-only'))
        # Keep the full admitted row/provenance in a local proof. Apply a separate
        # whole-work delivery selection policy; never rewrite a native feature packet.
        proofs.append({'work_id': work.work_id, 'admitted_row': deepcopy(row),
            'quality_case': case, 'recommendation_features_withheld': blocked,
            'selection_policy': args.uncertain_policy, 'owning_adopted_supplement': owners[work.work_id]})
        if blocked:
            row = deepcopy(metadata[work.work_id])
        if any(label not in labels or label.startswith('style.') for label in row['features']):
            raise ValueError('Candidate content labels outside ranking policy')
        row['features'] = {label: 1.0 for label in row['features']}
        row['display_title_aliases'] = [alias['title'] if isinstance(alias, dict) else alias for alias in row['display_title_aliases']]
        candidates.append(row)
        credits[work.work_id] = source_credits(work)
    new = rank_personal_features(candidates, private['private_rows'], limit=len(candidates))
    merged = merge_rankings(old['ranking'], new['ranking'])
    delivery = {'version': 'personal-feature-ranking-v1', 'ranking': merged,
        'audit': deepcopy(old['audit']) | {'input_candidates': len(merged), 'eligible_n': len(merged),
            'supported_n': sum(r['score'] is not None for r in merged),
            'unknown_read_status_n': sum(r['read_status']=='unknown' for r in merged),
            'new_world_pilot_n': len(new['ranking']), 'prior_rows_unchanged_n': len(old['ranking']),
            'quality_verified': False, 'confidence_calibrated': False,
            'catalogue_candidate_n': len(merged), 'catalogue_feature_bearing_n': sum(bool(r['content_labels']) for r in merged)}}
    html = render_personal_site(delivery, labels)
    html = html.replace('<script id="recommendation-data"', credit_html(credits)+'<script id="recommendation-data"', 1)
    report = {'version': 'world-pilot-delivery-v1', 'selected_n': len(selected),
        'new_display_n': len(new['ranking']), 'new_feature_bearing_n': sum(bool(r['content_labels']) for r in new['ranking']),
        'new_supported_n': new['audit']['supported_n'], 'merged_display_n': len(merged),
        'adopted_database_audits': database_audits,
        'recommendation_selection_policy': args.uncertain_policy,
        'uncertain_whole_work_metadata_only_n': sum(bool(cases.get(w.work_id,{}).get('status')=='uncertain' and args.uncertain_policy=='metadata-only') for w in selected),
        'held_whole_work_metadata_only_n': sum(cases.get(w.work_id,{}).get('status')=='held' for w in selected),
        'feature_weights': 'Binary admitted-label presence; raw confidence and relevance are not ranking weights.',
        'quality_reviews': review_bindings, 'old_scores_unchanged': True,
        'private_profile_connection_n': 2, 'private_rows_exported': False,
        'comparison_reruns': 0, 'new_model_calls': 0, 'human_gold': False,
        'quality_verified': False, 'gui_verified': False, 'unread_guaranteed': False,
        'uncertain_database_features_may_remain': True}
    if any(sha(path) != digest for path, digest in protected.items()):
        raise ValueError('Delivery source changed before publication')
    args.output.mkdir(parents=True, exist_ok=False)
    save(args.output/'delivery.json', delivery)
    save(args.output/'public-source-credits.json', credits)
    save(args.output/'admitted-new-rows.local.json', proofs)
    save(args.output/'report.json', report)
    (args.output/'index.html').write_text(html, encoding='utf-8')
    save(args.output/'component-purpose-proof.json', {'version': 'world-pilot-component-purpose-v1',
        'public_admission': 'Frozen v4 guard and exact adopted database verified on every display read.',
        'private_purpose': PURPOSE, 'private_source_pins': PROFILE_PINS,
        'whole_work_recommendation_selection_policy': args.uncertain_policy,
        'native_feature_packets_changed': False, 'source_sha256': protected})
    save(args.output/'manifest.json', {'version': 'world-pilot-merged-delivery-v1', 'source_sha256': protected,
        'adapter_sha256': sha(Path(__file__)), 'output_sha256': {p.name: sha(p) for p in args.output.iterdir()},
        'private_rows_exported': False, 'quality_verified': False})
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('runtime-root','ledger','output'):
        parser.add_argument('--'+flag, type=Path, required=True)
    parser.add_argument('--database', type=Path, action='append', required=True)
    parser.add_argument('--ledger-sha256', required=True)
    parser.add_argument('--selected-inputs', type=Path, action='append', required=True)
    parser.add_argument('--quality-review', type=Path, action='append', required=True)
    parser.add_argument('--uncertain-policy', choices=['metadata-only','retain-database-features'], default='metadata-only')
    print(json.dumps(build(parser.parse_args()), ensure_ascii=False))
