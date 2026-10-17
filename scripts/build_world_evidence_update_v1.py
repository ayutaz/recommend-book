"""Apply frozen SE admissions to existing candidates; demonstrate withdrawal.

No acquisition, model call, native packet rewrite or historical score recompute.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from html import escape
import json
from pathlib import Path
import sqlite3
import sys
from time import perf_counter
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/autonomous-next-v3'
HISTORICAL_AUTHORITY = BASE/'database/world-subject-sealed-v1/dataset.sqlite'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def save(path, value):
    with Path(path).open('xb') as stream:
        stream.write(encoded(value))


def validate_baseline(delivery):
    """Validate a public delivery snapshot before applying an update.

    The baseline is deliberately validated from its own public audit rather
    than from a historical row-count constant.  The audit field names here
    are the fields emitted by the phase10 public delivery snapshot.
    """
    rows = delivery.get('ranking') if isinstance(delivery, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError('Baseline delivery must contain non-empty ranking')
    identities = [row.get('work_id') if isinstance(row, dict) else None for row in rows]
    if any(not identity for identity in identities):
        raise ValueError('Baseline candidate requires a non-empty work_id')
    if len(set(identities)) != len(identities):
        raise ValueError('Duplicate baseline candidate identity')
    audit = delivery.get('audit')
    if not isinstance(audit, dict):
        raise ValueError('Baseline public audit required')
    expected = {
        'input_candidates': len(rows),
        'eligible_n': len(rows),
        'catalogue_candidate_n': len(rows),
        'catalogue_feature_bearing_n': sum(bool(row.get('content_labels')) for row in rows),
        'supported_n': sum(row.get('score') is not None for row in rows),
    }
    for key, value in expected.items():
        if audit.get(key) != value:
            raise ValueError(f'Baseline audit {key} differs from ranking')
    return expected


def public_url(value):
    parsed = urlparse(value)
    if (parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.hostname not in {'standardebooks.org', 'creativecommons.org'}):
        raise ValueError('Unapproved SE public credit URL')
    return value


def combine_candidate(baseline, admitted, *, old_id, supplement_id,
                      accepted_labels=None, review_status='accepted'):
    """Preserve the old identity/metadata; only add admitted work-level features."""
    if baseline['work_id'] != old_id or admitted['work_id'] != supplement_id:
        raise ValueError('Exact frozen identity bridge required')
    if accepted_labels is not None and set(admitted['features']) != set(accepted_labels):
        raise ValueError('Frozen feature admission differs')
    result = {key: deepcopy(baseline[key]) for key in
              ('work_id', 'title', 'authors', 'display_title_aliases', 'read_status')}
    if review_status not in {'accepted', 'uncertain', 'held'}:
        raise ValueError('Independent review status required')
    added = set(admitted['features']) if review_status == 'accepted' else set()
    result['features'] = {label: 1.0 for label in sorted(set(baseline['content_labels']) | added)}
    result['feature_provenance'] = deepcopy(admitted['feature_provenance']) if added else {}
    for label in baseline['content_labels']:
        result['feature_provenance'].setdefault(label, []).append({
            'scope': 'work', 'admission': 'frozen-prior-public-delivery',
            'work_id': old_id})
    return result


def replace_candidate(delivery, updated):
    rows = delivery['ranking']
    if len({row['work_id'] for row in rows}) != len(rows):
        raise ValueError('Duplicate baseline candidate identity')
    if sum(row['work_id'] == updated['work_id'] for row in rows) != 1:
        raise ValueError('Exactly one existing candidate must be replaced')
    result = deepcopy(delivery)
    result['ranking'] = [deepcopy(updated) if row['work_id'] == updated['work_id'] else deepcopy(row) for row in rows]
    result['ranking'].sort(key=lambda row: (row['score'] is None, -(row['score'] or 0), row['work_id']))
    result['audit'].update({
        'supported_n': sum(row['score'] is not None for row in result['ranking']),
        'catalogue_feature_bearing_n': sum(bool(row['content_labels']) for row in result['ranking']),
        'evidence_updated_existing_n': 1, 'prior_rows_unchanged_n': len(rows)-1})
    return result


def replace_candidates(delivery, updates):
    """Apply an explicitly authority-bound batch without appending identities."""
    if len({row['work_id'] for row in updates}) != len(updates):
        raise ValueError('Duplicate replacement identity')
    result = deepcopy(delivery)
    for row in updates:
        result = replace_candidate(result, row)
    result['audit'].update(evidence_updated_existing_n=len(updates),
                           prior_rows_unchanged_n=len(delivery['ranking'])-len(updates))
    return result


def destination_authority_proof(database, mapping, bridge):
    """Bind the existing destination to the same current authority raw snapshot.

    Read only the identity rows and current source head; the large historical
    database is never copied or repeatedly hashed for this metadata check.
    """
    database = Path(database).resolve(strict=True)
    before = database.stat()
    query = ('SELECT source,value,scope,entity_id,source_version_id FROM external_ids '
             'WHERE scope=? AND source=? AND value=? AND entity_id=?')
    parameters = ('work', 'wikidata', mapping['wikidata_qid'], mapping['delivery_work_id'])
    head_query = ('SELECT h.source,h.source_id,h.version_id,r.raw_path,r.raw_sha256 '
                  'FROM source_heads h JOIN source_records r ON r.version_id=h.version_id '
                  'WHERE h.source=? AND h.source_id=?')
    with sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        identities = [dict(row) for row in connection.execute(query, parameters)]
        heads = [dict(row) for row in connection.execute(head_query, ('wikidata', mapping['wikidata_qid']))]
    if not identities:
        raise ValueError('Destination lacks the exact work-scoped Wikidata identity')
    if len(heads) != 1:
        raise ValueError('Destination authority lacks exactly one current source head')
    head = heads[0]
    if (head['raw_sha256'] != bridge['target_raw_sha256']
            or Path(head['raw_path']).resolve(strict=True) != Path(bridge['target_raw_path']).resolve(strict=True)
            or not any(row['source_version_id'] == head['version_id'] for row in identities)):
        raise ValueError('Destination authority current source head differs from frozen SE bridge')
    after = database.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Destination authority database changed during metadata check')
    return {'database': str(database), 'database_size': before.st_size,
        'database_mtime_ns': before.st_mtime_ns, 'access': 'SQLite mode=ro&immutable=1; exact identity/current-head queries only',
        'identity_query': query, 'identity_parameters': list(parameters), 'identity_rows': identities,
        'head_query': head_query, 'head_parameters': ['wikidata', mapping['wikidata_qid']],
        'current_head': head}


def historical_identity_proof(database, qid):
    """A separate authority may add only a work absent from the historical master."""
    database = Path(database).resolve(strict=True)
    before = database.stat()
    query = 'SELECT entity_id,scope,source,value FROM external_ids WHERE source=? AND value=? AND scope=?'
    parameters = ('wikidata', qid, 'work')
    with sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(query, parameters)]
    after = database.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Historical authority database changed during metadata check')
    if rows:
        raise ValueError('New candidate Wikidata identity already exists in historical authority')
    return {'database': str(database), 'database_size': before.st_size,
        'database_mtime_ns': before.st_mtime_ns, 'access': 'SQLite mode=ro&immutable=1; work-scoped identity query only',
        'query': query, 'parameters': list(parameters), 'identity_rows': rows}


def new_candidate_proof(database, mapping, entity, current_rows):
    """Reject a second public identity or ambiguous title for a newly shown work."""
    identity, qid = mapping['delivery_work_id'], mapping['wikidata_qid']
    current_ids = {row['work_id'] for row in current_rows}
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro&immutable=1', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        has_eligibility = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='autonomous_work_eligibility'").fetchone() is not None
        eligibility = (connection.execute('SELECT work_id,eligible,reason FROM autonomous_work_eligibility WHERE work_id=?', (identity,)).fetchone()
                       if has_eligibility else None)
        inventory = connection.execute('SELECT work_id,eligible FROM next_book_inventory WHERE work_id=?', (identity,)).fetchone()
        qid_rows = [dict(row) for row in connection.execute(
            'SELECT entity_id,scope,source,value FROM external_ids WHERE source=? AND value=?', ('wikidata',qid))]
    if (has_eligibility and (not eligibility or eligibility['eligible'] != 1)) or not inventory or inventory['eligible'] != 1:
        raise ValueError('New candidate lacks existing work/novel eligibility')
    claims = entity.get('claims', {})
    active = lambda prop: [statement for statement in claims.get(prop, []) if statement.get('rank') != 'deprecated']
    excluded_types = {'Q3331189', 'Q53731850', 'Q49084', 'Q277759', 'Q1667921'}
    conflicts = active('P629') + [statement for prop in ('P31', 'P7937') for statement in active(prop)
        if statement.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('id') in excluded_types]
    if conflicts:
        raise ValueError('New candidate raw authority has explicit work/novel scope conflict')
    novel_claims = [statement for prop in ('P31', 'P7937') for statement in entity.get('claims', {}).get(prop, [])
        if statement.get('rank') != 'deprecated' and statement.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('id') == 'Q8261']
    if not novel_claims:
        raise ValueError('New candidate raw authority lacks direct novel type')
    known_ids = {row['entity_id'] for row in qid_rows if row['scope'] == 'work'} | {'declared-subclass:wd:'+qid}
    if identity in current_ids or known_ids & current_ids:
        raise ValueError('New candidate authority already has a public delivery identity')
    titles = {row['value'] for row in entity.get('labels', {}).values()}
    titles.update(row['value'] for rows in entity.get('aliases', {}).values() for row in rows)
    titles.update(statement.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('text', '')
        for statement in entity.get('claims', {}).get('P1476', []) if statement.get('rank') != 'deprecated')
    titles.discard('')
    for row in current_rows:
        displayed = {row['title']} | {alias['title'] if isinstance(alias, dict) else alias for alias in row.get('display_title_aliases', [])}
        if titles & displayed:
            raise ValueError('Hold new candidate: exact raw authority title/alias already displayed')
    return {'work_eligibility': dict(eligibility) if eligibility is not None else None,
        'autonomous_work_eligibility_table_present': has_eligibility, 'novel_inventory': dict(inventory),
        'qid_identity_rows': qid_rows, 'novel_type_claims': novel_claims,
        'raw_title_aliases_checked': sorted(titles), 'existing_display_n': len(current_rows),
        'authority_duplicate_found': False, 'exact_title_alias_duplicate_found': False}


def append_candidates(delivery, new_rows):
    identities = [row['work_id'] for row in delivery['ranking'] + new_rows]
    if len(identities) != len(set(identities)):
        raise ValueError('New candidate duplicates a public identity')
    result = deepcopy(delivery)
    result['ranking'].extend(deepcopy(new_rows))
    result['ranking'].sort(key=lambda row: (row['score'] is None, -(row['score'] or 0), row['work_id']))
    result['audit'].update(input_candidates=len(identities), eligible_n=len(identities),
        catalogue_candidate_n=len(identities), new_public_candidates_n=len(new_rows),
        unknown_read_status_n=sum(row['read_status']=='unknown' for row in result['ranking']),
        supported_n=sum(row['score'] is not None for row in result['ranking']),
        catalogue_feature_bearing_n=sum(bool(row['content_labels']) for row in result['ranking']))
    return result


def credit_section(credit):
    return ('<section aria-label="Standard Ebooks追加根拠"><h2>追加分類の出典</h2>'
        '<p>' + escape(credit['title']) + ' · Standard Ebooks contributors · '
        '<a rel="noreferrer" href="' + escape(public_url(credit['source_url']), quote=True) + '">作品と出典</a> · '
        '<a rel="noreferrer" href="' + escape(public_url(credit['license_url']), quote=True) + '">CC0 1.0</a>'
        '。編集部あらすじを分類根拠として追加。原文・引用は掲載していません。</p></section>')


def build_batch(args):
    """Bounded existing-work updates from an explicit reviewed authority mapping.

    Mapping JSON: {"cases": [{"input_work_id": "...", "delivery_work_id":
    "...", "wikidata_qid": "Q..."}]}. It is a local identity-admission receipt,
    not a title matcher. Native adoption reviews remain authoritative for labels.
    """
    started = perf_counter()
    pins = {}
    def pin(path, expected=None):
        path = Path(path).resolve(strict=True)
        digest = sha(path)
        if expected and digest != expected:
            raise ValueError('Batch source hash differs: '+path.name)
        pins[str(path)] = digest
        return path
    prior = args.baseline_dir.resolve(strict=True)
    manifest = json.loads(pin(prior/'manifest.json').read_bytes())
    for name in ('delivery.json', 'index.html', 'public-source-credits.json'):
        pin(prior/name, manifest['output_sha256'][name])
    baseline = json.loads((prior/'delivery.json').read_bytes())
    baseline_audit = validate_baseline(baseline)
    old_by_id = {row['work_id']: row for row in baseline['ranking']}
    cases_doc = json.loads(pin(args.mapping).read_bytes())
    mappings = cases_doc['cases']
    if not 1 <= len(mappings) <= 20:
        raise ValueError('Batch requires 1 through 20 explicit authority mappings')
    if (len({r['input_work_id'] for r in mappings}) != len(mappings)
            or len({r['delivery_work_id'] for r in mappings}) != len(mappings)):
        raise ValueError('Repeated batch identity mapping')
    runtime = args.runtime_root.resolve(strict=True)
    for path in (runtime/'src/recommend_book').rglob('*.py'):
        pin(path)
    for relative in ('src/recommend_book/recommendation/personal_ranking.py',
                     'src/recommend_book/recommendation/personal_site.py',
                     'config/recommendation-label-display-ja.json'):
        pin(runtime/relative, sha(pin(ROOT/relative)))
    sys.path.insert(0, str(runtime/'src'))
    from recommend_book.usage_guard_v4 import UsageGuard, model_hash
    from recommend_book.next_database_v4 import load_next_world_database, get_next_inputs, _quality_document
    from recommend_book.recommendation.personal_poc import PROFILE_PINS, PURPOSE, load_pinned_json
    from recommend_book.recommendation.personal_ranking import rank_personal_features
    from recommend_book.recommendation.personal_site import render_personal_site
    ledger = pin(args.ledger, args.ledger_sha256)
    explicit_authority = getattr(args, 'authority_database', None)
    authority_database = (pin(explicit_authority) if explicit_authority is not None
                          and Path(explicit_authority).resolve(strict=True) != HISTORICAL_AUTHORITY.resolve(strict=True)
                          else HISTORICAL_AUTHORITY)
    guard = UsageGuard(ledger, expected_ledger_sha256=args.ledger_sha256)
    admitted, works, review_cases, audits = {}, {}, {}, []
    for source in args.database:
        database = pin(source)
        local_works = get_next_inputs(database)['classification']
        for work in local_works:
            if work.work_id in works:
                raise ValueError('Supplement work has multiple database owners')
            works[work.work_id] = work
        with sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1', uri=True) as connection:
            for (serialized,) in connection.execute('SELECT provenance_json FROM next_classification_runs'):
                binding = json.loads(serialized).get('quality_adoption_review')
                if not binding:
                    raise ValueError('Supplement lacks independent adopted review')
                document, bound_cases, files = _quality_document(pin(binding['path'], binding['sha256']))
                for path, digest in files.items():
                    pin(path, digest)
                for case in bound_cases.values():
                    review_cases[case['work_id']] = case
        for purpose in ('display', 'commercial', 'redistribution'):
            readback = load_next_world_database(database, guard=guard, usage=purpose, content_classified=True)
            audits.append({'database_sha256': sha(database), 'purpose': purpose, 'audit': readback['audit']})
            by_id = {row['work_id']: row for row in readback['rows']}
            required = {row['input_work_id'] for row in mappings} & {work.work_id for work in local_works}
            held_ids = {identity for identity in required if review_cases.get(identity, {}).get('status') == 'held'}
            if held_ids:
                metadata = load_next_world_database(database, guard=guard, usage=purpose, content_classified=False)
                by_id.update({row['work_id']: row for row in metadata['rows'] if row['work_id'] in held_ids})
            if not required <= set(by_id):
                raise ValueError('Mapped supplement purpose admission denied')
            if purpose == 'display':
                admitted.update(by_id)
    profile_dir = BASE/'evaluation/phase3-personal-profile-v1/runtime-v2'
    snapshots = {name: load_pinned_json(pin(profile_dir/name, digest), digest) for name, digest in PROFILE_PINS.items()}
    private, contract = snapshots['private-profile.json'], snapshots['purpose-contract.json']
    if (contract['purpose'] != PURPOSE or private['purpose'] != snapshots['purpose-receipts.json']
            or private['purpose']['contract_sha256'] != PROFILE_PINS['purpose-contract.json']
            or private['summary'] != snapshots['profile-summary.json']
            or model_hash(snapshots['public-rows.json']) != contract['public_rows_sha256']
            or len(private['private_rows']) != 2):
        raise ValueError('Pinned private purpose contract differs')
    for descriptor in contract['artifacts'].values():
        pin(descriptor['path'], descriptor['sha256'])
    credits = json.loads((prior/'public-source-credits.json').read_bytes())
    updates, new_rows, proofs, new_credit_html = [], [], [], []
    for mapping in mappings:
        input_id, delivery_id, qid = (mapping[key] for key in ('input_work_id', 'delivery_work_id', 'wikidata_qid'))
        is_new = delivery_id not in old_by_id
        if input_id not in admitted or (is_new and not args.allow_new_candidates) or not qid.startswith('Q'):
            raise ValueError('Authority mapping lacks an admitted source or existing destination')
        work = works[input_id]
        if not guard.check_input(work, 'display').allowed:
            raise ValueError('Mapped SE input/credit display denied')
        se = [ev for ev in work.evidence if ev.rights_policy_id == 'standardebooks-editorial-cc0']
        if len(se) != 1:
            raise ValueError('Exactly one SE editorial evidence required')
        ev = se[0]
        bridge = ev.attribution['authority_bridge']
        if bridge['wikidata_work_id'] != qid:
            raise ValueError('Mapping differs from frozen source authority bridge')
        destination_proof = destination_authority_proof(
            authority_database, mapping, bridge)
        raw = json.loads(pin(bridge['target_raw_path'], bridge['target_raw_sha256']).read_bytes())
        if qid not in raw['entities']:
            raise ValueError('Missing raw authority entity')
        review_status = review_cases.get(input_id, {}).get('status', 'accepted')
        new_proof = None
        if is_new:
            historical_proof = (historical_identity_proof(HISTORICAL_AUTHORITY, qid)
                if authority_database.resolve(strict=True) != HISTORICAL_AUTHORITY.resolve(strict=True) else None)
            new_proof = new_candidate_proof(authority_database,
                mapping, raw['entities'][qid], baseline['ranking'] + new_rows)
            if historical_proof is not None:
                new_proof['historical_authority_absence_proof'] = historical_proof
            source = admitted[input_id]
            candidate = {key: deepcopy(source[key]) for key in ('title', 'authors')}
            candidate.update(work_id=delivery_id, read_status='unknown',
                display_title_aliases=[alias['title'] if isinstance(alias, dict) else alias for alias in source.get('display_title_aliases', [])],
                features={label:1.0 for label in source['features']} if review_status=='accepted' else {},
                feature_provenance=deepcopy(source['feature_provenance']) if review_status=='accepted' else {})
        else:
            candidate = combine_candidate(old_by_id[delivery_id], admitted[input_id],
                old_id=delivery_id, supplement_id=input_id, review_status=review_status)
        ranked = rank_personal_features([candidate], private['private_rows'], limit=1)['ranking'][0]
        (new_rows if is_new else updates).append(ranked)
        proofs.append({'mapping': mapping, 'review_status': review_status,
            'destination_authority_proof': destination_proof,
            'new_candidate_proof': new_proof,
            'combined_candidate': candidate, 'old_row': old_by_id.get(delivery_id), 'updated_row': ranked})
        if review_status == 'accepted':
            credit = {'evidence_id': ev.evidence_id, 'credit': 'Standard Ebooks contributors',
                'source_url': public_url(ev.attribution['ebook_url']), 'license': 'CC0-1.0',
                'license_url': public_url(ev.license_url), 'title': candidate['title'],
                'changes': 'Editorial synopsis classified into binary content labels; source prose omitted.'}
            credits.setdefault(delivery_id, []).append(credit)
            new_credit_html.append(credit_section(credit))
    delivery = append_candidates(replace_candidates(baseline, updates), new_rows)
    labels = json.loads((ROOT/'config/recommendation-label-display-ja.json').read_bytes())['labels']
    prior_html = (prior/'index.html').read_text(encoding='utf-8')
    # All prior attribution sections are public and remain part of the new view.
    start = prior_html.index('<section aria-label="追加作品の出典">')
    end = prior_html.index('<script id="recommendation-data"', start)
    html = render_personal_site(delivery, labels).replace('<script id="recommendation-data"',
        prior_html[start:end] + ''.join(new_credit_html) + '<script id="recommendation-data"', 1)
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(BASE/'database'):
        raise ValueError('Fresh separate database output directory required')
    output.mkdir()
    save(output/'delivery.json', delivery)
    save(output/'public-source-credits.json', credits)
    (output/'index.html').write_text(html, encoding='utf-8', newline='\n')
    save(output/'feature-admissions.local.json', proofs)
    save(output/'purpose-readback.local.json', audits)
    withdrawal = output/'withdrawal'
    withdrawal.mkdir()
    revoked = json.loads(ledger.read_bytes())
    revoked['revoked_input_hashes'] = sorted(key for key, value in revoked['inputs'].items() if value['evidence'])
    save(withdrawal/'usage-ledger.revoked.local.json', revoked)
    revoked_path = withdrawal/'usage-ledger.revoked.local.json'
    revoked_guard = UsageGuard(revoked_path, expected_ledger_sha256=sha(revoked_path))
    denials = []
    for database in args.database:
        denied = load_next_world_database(database, guard=revoked_guard, usage='display', content_classified=True)
        if denied['rows'] or denied['audit']['feature_adopted_work_count']:
            raise ValueError('Withdrawal failed to deny batch supplement')
        denials.append(denied['audit'])
    save(withdrawal/'purpose-denial.local.json', denials)
    for proof in proofs:
        old = proof['old_row']
        if old is None:
            continue
        candidate = {key: deepcopy(old[key]) for key in
            ('work_id', 'title', 'authors', 'display_title_aliases', 'read_status')}
        candidate.update(features={label: 1.0 for label in old['content_labels']}, feature_provenance={})
        if rank_personal_features([candidate], private['private_rows'], limit=1)['ranking'][0] != old:
            raise ValueError('Withdrawn batch candidate does not restore its prior score')
    for name in ('delivery.json', 'index.html', 'public-source-credits.json'):
        (withdrawal/name).write_bytes((prior/name).read_bytes())
    repeated = [(proof, rank_personal_features([proof['combined_candidate']], private['private_rows'], limit=1)['ranking'][0]) for proof in proofs]
    reapplied = append_candidates(replace_candidates(baseline, [row for proof,row in repeated if proof['old_row'] is not None]),
        [row for proof,row in repeated if proof['old_row'] is None])
    if encoded(reapplied) != (output/'delivery.json').read_bytes():
        raise ValueError('Batch reapplication differs')
    reapply = output/'reapplied'
    reapply.mkdir()
    save(reapply/'delivery.json', reapplied)
    for name in ('index.html', 'public-source-credits.json'):
        (reapply/name).write_bytes((output/name).read_bytes())
    if any(sha(path) != digest for path, digest in pins.items()):
        raise ValueError('Frozen batch source changed during build')
    report = {'version': 'world-evidence-update-batch-v1', 'display_n': len(delivery['ranking']),
        'feature_bearing_n': sum(bool(row['content_labels']) for row in delivery['ranking']),
        'supported_n': sum(row['score'] is not None for row in delivery['ranking']),
        'updated_existing_n': len(updates), 'unchanged_other_rows_n': baseline_audit['input_candidates']-len(updates),
        'new_public_candidates_n': len(new_rows),
        'withheld_uncertain_or_held_n': sum(proof['review_status'] != 'accepted' for proof in proofs),
        'new_labels_added_n': sum(len(set(proof['updated_row']['content_labels'])-set((proof['old_row'] or {}).get('content_labels', []))) for proof in proofs),
        'withdrawal_baseline_bytes_equal': True, 'reapplication_bytes_equal': True,
        'source_artifacts_unchanged': True, 'private_rows_exported': False,
        'model_calls': 0, 'gui_verified': False, 'quality_verified': False,
        'elapsed_seconds': round(perf_counter()-started, 4)}
    save(output/'report.json', report)
    save(output/'manifest.json', {'version': 'world-evidence-update-batch-v1', 'source_sha256': pins,
        'adapter_sha256': sha(__file__), 'output_sha256': {
            str(path.relative_to(output)): sha(path) for path in sorted(output.rglob('*')) if path.is_file()}})
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-dir', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--ledger-sha256', required=True)
    parser.add_argument('--database', type=Path, action='append', required=True)
    parser.add_argument('--authority-database', type=Path,
                        help='Separate hash-pinned authority database (default: historical master, metadata queries only)')
    parser.add_argument('--mapping', type=Path, required=True)
    parser.add_argument('--allow-new-candidates', action='store_true')
    args = parser.parse_args()
    print(json.dumps(build_batch(args), ensure_ascii=False))
