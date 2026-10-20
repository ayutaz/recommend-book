"""Apply frozen SE admissions to existing candidates; demonstrate withdrawal.

No acquisition, model call, native packet rewrite or historical score recompute.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from html import escape
import importlib.util
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


def pin_profile_artifacts(contract, pin, *, feature_recipe=None):
    """Permit only exact historical feature-recipe bytes from a frozen runtime.

    This validates evidence for the existing profile, never imports archived
    code or changes its contract. All other artifacts retain their current pins.
    """
    proof = None
    if feature_recipe is not None:
        relative = 'src/recommend_book/next_database.py'
        descriptor = contract['artifacts']['feature_recipe']
        if Path(descriptor['path']).resolve(strict=True) != (ROOT / relative).resolve(strict=True):
            raise ValueError('Profile feature recipe original path differs')
        archived = Path(feature_recipe).resolve(strict=True)
        preparation = (BASE / 'preparation').resolve(strict=True)
        if not archived.is_relative_to(preparation):
            raise ValueError('Profile feature recipe archive must be inside preparation')
        parts = archived.relative_to(preparation).parts
        if len(parts) != 5 or parts[1:] != ('runtime', 'src', 'recommend_book', 'next_database.py'):
            raise ValueError('Profile feature recipe archived relative path differs')
        namespace = preparation / parts[0]
        manifest = pin(namespace / 'source-snapshot-manifest.json')
        snapshot = json.loads(manifest.read_bytes())
        if (snapshot.get('version') != 'native-snapshot-runtime-v1'
                or Path(snapshot['runtime_root']).resolve(strict=True) != namespace / 'runtime'
                or snapshot.get('copied_original_sha256', {}).get(relative) != descriptor['sha256']):
            raise ValueError('Profile feature recipe archived source manifest differs')
        archived = pin(archived, descriptor['sha256'])
        proof = {'version': 'profile-feature-recipe-archive-binding-v1', 'artifact_key': 'feature_recipe',
            'original_path': descriptor['path'], 'archived_path': str(archived),
            'sha256': descriptor['sha256'], 'source_snapshot_manifest_path': str(manifest),
            'source_snapshot_manifest_sha256': sha(manifest)}
    for key, descriptor in contract['artifacts'].items():
        if key != 'feature_recipe' or feature_recipe is None:
            pin(descriptor['path'], descriptor['sha256'])
    return proof


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
                      accepted_labels=None, review_status='accepted',
                      label_admission=None):
    """Preserve the old identity/metadata; only add admitted work-level features."""
    if baseline['work_id'] != old_id or admitted['work_id'] != supplement_id:
        raise ValueError('Exact frozen identity bridge required')
    if accepted_labels is not None and set(admitted['features']) != set(accepted_labels):
        raise ValueError('Frozen feature admission differs')
    result = {key: deepcopy(baseline[key]) for key in
              ('work_id', 'title', 'authors', 'display_title_aliases', 'read_status')}
    if review_status not in {'accepted', 'uncertain', 'held', 'partial'}:
        raise ValueError('Independent review status required')
    if review_status == 'partial':
        if not isinstance(label_admission, dict) or label_admission.get('policy_version') != 'label-level-admission-v1':
            raise ValueError('Validated label-level admission required for partial projection')
        added = set(label_admission.get('admitted_label_ids', []))
        if not added <= set(admitted['features']):
            raise ValueError('Partial projection differs from existing feature derivation')
    else:
        added = set(admitted['features']) if review_status == 'accepted' else set()
    result['features'] = {label: 1.0 for label in sorted(set(baseline['content_labels']) | added)}
    result['feature_provenance'] = deepcopy(admitted['feature_provenance']) if added else {}
    for label in baseline['content_labels']:
        result['feature_provenance'].setdefault(label, []).append({
            'scope': 'work', 'admission': 'frozen-prior-public-delivery',
            'work_id': old_id})
    return result


def native_result_for_work(database, work_id):
    """Read the exact adopted native result from a supplement in read-only mode."""
    database = Path(database).resolve(strict=True)
    with sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True) as connection:
        row = connection.execute(
            'SELECT run_id,result_json FROM next_classification_inputs WHERE work_id=?',
            (work_id,)).fetchone()
    if row is None or not row[1]:
        raise ValueError('Label-level admission requires one completed native result')
    return row[0], json.loads(row[1])


def load_label_admission_policy(path):
    """Load the opt-in policy from the root checkout, never frozen runtime."""
    spec = importlib.util.spec_from_file_location('recommend_book_phase14_label_policy', path)
    if spec is None or spec.loader is None:
        raise ValueError('Label admission policy module cannot be loaded')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    excluded_types = {'Q3331189', 'Q53731850', 'Q49084', 'Q277759', 'Q1667921', 'Q1279564', 'Q105420'}
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
    from recommend_book.next_database_v4 import (load_next_world_database,
        get_next_inputs, _quality_document, extract_classification_features)
    from recommend_book.recommendation.personal_poc import PROFILE_PINS, PURPOSE, load_pinned_json
    from recommend_book.recommendation.personal_ranking import rank_personal_features
    from recommend_book.recommendation.personal_site import render_personal_site
    label_admission_cases, label_reviews = {}, {}
    label_policy = None
    if getattr(args, 'label_admission_request', None):
        label_policy_module = load_label_admission_policy(pin(ROOT/'src/recommend_book/label_admission_v1.py'))
        label_policy = label_policy_module.validate_request(json.loads(pin(args.label_admission_request).read_bytes()))
        review_path = pin(label_policy['review_path'], label_policy['review_sha256'])
        review_admission_path = pin(label_policy['review_admission_path'], label_policy['review_admission_sha256'])
        quality_review_path = pin(label_policy['quality_review_path'], label_policy['quality_review_sha256'])
        source_audit_path = pin(label_policy['source_audit_path'], label_policy['source_audit_sha256'])
        review_final_path = pin(label_policy['review_final_path'], label_policy['review_final_sha256'])
        if source_audit_path != review_path:
            raise ValueError('Label admission review must use the bound source-audit artifact')
        source_audit = json.loads(source_audit_path.read_bytes())
        if source_audit.get('artifact_sha256', {}).get('review-admission.json') != label_policy['review_admission_sha256']:
            raise ValueError('Label admission receipt is not bound by source-audit artifact hashes')
        if source_audit.get('artifact_sha256', {}).get('review.final.json') != label_policy['review_final_sha256']:
            raise ValueError('Label admission review.final is not bound by source-audit artifact hashes')
        review_admission = json.loads(review_admission_path.read_bytes())
        if (review_admission.get('protocol') != 'complete-input-native-output-internal-review-v2'
                or review_admission.get('prompt_sha256') != review_admission.get('approved_exact_prompt_sha256')
                or review_admission.get('usage_ledger_sha256') != args.ledger_sha256):
            raise ValueError('Label admission review receipt is not bound to the frozen review protocol')
        review_document = json.loads(review_path.read_bytes())
        if review_document.get('version') != 'world-pilot-independent-source-review-v2':
            raise ValueError('Label admission requires the exact independent review v2')
        if review_document.get('artifact_sha256', {}).get('review-admission.json') != label_policy['review_admission_sha256']:
            raise ValueError('Label admission source-audit binding is incomplete')
        label_reviews = {entry['work_id']: entry for entry in review_document.get('reviews', [])}
        label_admission_cases = {entry['work_id']: entry['admit_label_ids'] for entry in label_policy['cases']}
    ledger = pin(args.ledger, args.ledger_sha256)
    explicit_authority = getattr(args, 'authority_database', None)
    authority_database = (pin(explicit_authority) if explicit_authority is not None
                          and Path(explicit_authority).resolve(strict=True) != HISTORICAL_AUTHORITY.resolve(strict=True)
                          else HISTORICAL_AUTHORITY)
    guard = UsageGuard(ledger, expected_ledger_sha256=args.ledger_sha256)
    admitted, works, work_databases, review_cases, audits = {}, {}, {}, {}, []
    for source in args.database:
        database = pin(source)
        local_works = get_next_inputs(database)['classification']
        for work in local_works:
            if work.work_id in works:
                raise ValueError('Supplement work has multiple database owners')
            works[work.work_id] = work
            work_databases[work.work_id] = database
        with sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1', uri=True) as connection:
            for (serialized,) in connection.execute('SELECT provenance_json FROM next_classification_runs'):
                binding = json.loads(serialized).get('quality_adoption_review')
                if not binding:
                    raise ValueError('Supplement lacks independent adopted review')
                document, bound_cases, files = _quality_document(pin(binding['path'], binding['sha256']))
                for path, digest in files.items():
                    pin(path, digest)
                if label_policy is not None:
                    audit_bindings = {(Path(item['path']).resolve(), item['sha256'])
                                      for item in document['source_audits']}
                    if ((source_audit_path, label_policy['source_audit_sha256']) not in audit_bindings
                            or files.get(str(source_audit_path)) != label_policy['source_audit_sha256']):
                        raise ValueError('Label admission source-audit is not bound by quality review')
                for case in bound_cases.values():
                    review_cases[case['work_id']] = case
                if label_policy is not None:
                    quality_binding = str(quality_review_path)
                    if str(Path(binding['path']).resolve()) != quality_binding or binding['sha256'] != label_policy['quality_review_sha256']:
                        raise ValueError('Label admission quality review differs from database binding')
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
    if label_policy is not None:
        requested_ids = set(label_admission_cases)
        mapped_ids = {mapping['input_work_id'] for mapping in mappings}
        uncertain_mapped_ids = {identity for identity in mapped_ids if review_cases.get(identity, {}).get('status') == 'uncertain'}
        if requested_ids != uncertain_mapped_ids:
            raise ValueError('Label admission request must cover exactly all mapped uncertain cases')
        receipt_hashes = set(review_admission.get('native_output_value_sha256', []))
        for case in review_cases.values():
            if case.get('native_result_value_sha256') not in receipt_hashes:
                raise ValueError('Label admission quality case is absent from the exact review receipt')
    profile_dir = BASE/'evaluation/phase3-personal-profile-v1/runtime-v2'
    snapshots = {name: load_pinned_json(pin(profile_dir/name, digest), digest) for name, digest in PROFILE_PINS.items()}
    private, contract = snapshots['private-profile.json'], snapshots['purpose-contract.json']
    if (contract['purpose'] != PURPOSE or private['purpose'] != snapshots['purpose-receipts.json']
            or private['purpose']['contract_sha256'] != PROFILE_PINS['purpose-contract.json']
            or private['summary'] != snapshots['profile-summary.json']
            or model_hash(snapshots['public-rows.json']) != contract['public_rows_sha256']
            or len(private['private_rows']) != 2):
        raise ValueError('Pinned private purpose contract differs')
    profile_context = pin_profile_artifacts(contract, pin,
        feature_recipe=getattr(args, 'profile_feature_recipe', None))
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
        label_decision = None
        if input_id in label_admission_cases:
            if review_status != 'uncertain' or input_id not in label_reviews:
                raise ValueError('Label admission requires a matching uncertain review case')
            admit_label_level = label_policy_module.admit_label_level
            filter_feature_projection = label_policy_module.filter_feature_projection
            run_id, native_result = native_result_for_work(work_databases[input_id], input_id)
            label_decision = admit_label_level(
                native_result, review_cases[input_id], label_reviews[input_id],
                admit_label_ids=label_admission_cases[input_id])
            derived = extract_classification_features(
                work, native_result, review_cases[input_id]['job_key'], run_id)
            features, provenance = filter_feature_projection(
                derived['features'], derived['feature_provenance'], label_decision)
            admitted[input_id] = {**admitted[input_id],
                                  'features': features,
                                  'feature_provenance': provenance}
            review_status = 'partial'
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
                features={label:1.0 for label in source['features']} if review_status in {'accepted', 'partial'} else {},
                feature_provenance=deepcopy(source['feature_provenance']) if review_status in {'accepted', 'partial'} else {})
        else:
            candidate = combine_candidate(old_by_id[delivery_id], admitted[input_id],
                old_id=delivery_id, supplement_id=input_id, review_status=review_status,
                label_admission=label_decision)
        ranked = rank_personal_features([candidate], private['private_rows'], limit=1)['ranking'][0]
        (new_rows if is_new else updates).append(ranked)
        proofs.append({'mapping': mapping, 'review_status': review_status,
            'label_decision': ({key: deepcopy(label_decision[key]) for key in (
                'policy_version', 'work_id', 'native_label_ids', 'affected_label_ids',
                'admitted_label_ids', 'withheld_label_ids', 'review_reason_codes')}
                if label_decision is not None else None),
            'destination_authority_proof': destination_proof,
            'new_candidate_proof': new_proof,
            'combined_candidate': candidate, 'old_row': old_by_id.get(delivery_id), 'updated_row': ranked})
        if review_status in {'accepted', 'partial'}:
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
    if profile_context is not None:
        save(output/'profile-context.local.json', profile_context)
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
        'withheld_uncertain_or_held_n': sum(proof['review_status'] in {'uncertain', 'held'} for proof in proofs),
        'label_level_projected_n': sum(proof['review_status'] == 'partial' for proof in proofs),
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
    parser.add_argument('--profile-feature-recipe', type=Path,
                        help='Exact contract-pinned historical feature recipe inside a frozen preparation runtime')
    parser.add_argument('--mapping', type=Path, required=True)
    parser.add_argument('--allow-new-candidates', action='store_true')
    parser.add_argument('--label-admission-request', type=Path,
                        help='Explicit, independently reviewed label-level projection; whole-work hold remains default')
    args = parser.parse_args()
    print(json.dumps(build_batch(args), ensure_ascii=False))
