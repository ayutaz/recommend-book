"""Reconcile public labels with the catalogue's existing conservative display holds.

This is process continuation only. Native, semantic and rights evidence stays
unchanged; old controls retain their original validation contract.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import sqlite3

from .scale_content_control_v3 import actual_gate, sha
from .scale_content_control_v3 import verify_control_receipt as verify_legacy_control


def display_policy_sha256():
    from .scale_catalogue_v1 import _display_native_labels
    return hashlib.sha256(inspect.getsource(_display_native_labels).encode('utf-8')).hexdigest()


def display_counts(rows):
    from .scale_catalogue_v1 import _display_native_labels
    published, displayed, published_content, displayed_content = 0, 0, 0, 0
    seen, held = set(), []
    for row in rows:
        qid = row['qid']
        if qid in seen:
            raise ValueError('Duplicate publication work identity')
        seen.add(qid)
        labels = row['admitted_labels']
        content_ids = set(row['content_evidence_ids'])
        def has_content(values):
            return any(label['axis'] != 'genre' and any(
                ref['evidence_id'] in content_ids for ref in label['evidence']) for label in values)
        kept, holds = _display_native_labels(labels)
        published += bool(labels)
        displayed += bool(kept)
        published_content += has_content(labels)
        displayed_content += has_content(kept)
        held.extend({'qid': qid, **item} for item in holds)
    return {'policy_sha256': display_policy_sha256(),
            'published_feature_works': published,
            'published_content_non_genre_feature_works': published_content,
            'actual_display_feature_works': displayed,
            'actual_content_non_genre_feature_works': displayed_content,
            'held_labels': held}


def observe_catalogue_display(database, export_path, expected_sha256):
    """Read only this imported window; never reimport or replay native permissions."""
    raw = Path(export_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Display observation export SHA differs')
    public = json.loads(raw)
    rows = public['verified_projection']['rows']
    before = display_policy_sha256()
    database = Path(database).resolve(strict=True)
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        actual = {qid: json.loads(labels) for qid, labels in db.execute(
            'SELECT n.qid,n.labels_json FROM native_classifications n JOIN works w USING(qid) '
            'WHERE n.manifest_sha256=? AND w.display_allowed=1 AND w.recommendable=1',
            (expected_sha256,))}
        if actual != {row['qid']: row['admitted_labels'] for row in rows}:
            raise ValueError('Current catalogue labels or eligible identity set differs')
        counts = display_counts(rows)
    after = display_policy_sha256()
    if before != counts['policy_sha256'] or before != after:
        raise ValueError('Display policy changed during observation')
    return {'classification_manifest_sha256': expected_sha256,
            'display_projection': counts, 'matched_eligible_works': len(actual),
            'database_path': str(database),
            'persisted_rows': [{'qid': qid, 'admitted_labels': labels}
                               for qid, labels in sorted(actual.items())],
            'policy_sha256_before': before, 'policy_sha256_after': after,
            'observer_code_path': str(Path(__file__).resolve()),
            'observer_code_sha256': sha(__file__), 'read_only': True, 'model_calls': 0}


def _successful_process(process):
    return (process.get('actual_exit_code') == 0
            and process.get('process_exit_observed') is True
            and type(process.get('pid')) is int and process['pid'] > 0)


def verify_control_receipt(path, expected_sha256):
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Content control receipt SHA differs')
    receipt = json.loads(raw)
    if receipt.get('version') != 'scale-content-window-control-v4':
        return verify_legacy_control(path, expected_sha256)
    for name, pin in receipt['control_artifacts'].items():
        if not Path(pin['path']).is_absolute() or sha(pin['path']) != pin['sha256']:
            raise ValueError('Content process-control artifact changed: ' + name)
    def artifact(name):
        return json.loads(Path(receipt['control_artifacts'][name]['path']).read_bytes())
    public, run, imported = artifact('export'), artifact('run'), artifact('import_report')
    if public.get('version') not in ('scale-content-native-classification-export-v2',
                                     'scale-content-native-classification-export-v3'):
        raise ValueError('Content route cannot reuse a metadata-only gate')
    projection = public['verified_projection']
    counts = display_counts(projection['rows'])
    if receipt.get('display_projection') != counts:
        raise ValueError('Actual display projection differs from current policy and pinned rows')
    published = counts['published_feature_works']
    content = counts['published_content_non_genre_feature_works']
    if (projection['actual_feature_adopted_works'] != published
            or projection['actual_content_non_genre_feature_works'] != content
            or public['gate']['actual_feature_adopted_works'] != published
            or public['gate'].get('actual_content_non_genre_feature_works',
                content if public['version'].endswith('-v2') else None) != content
            or len(projection['rows']) != receipt['gate']['accepted']):
        raise ValueError('Actual publication counts differ from pinned rows')
    if (receipt['gate']['actual_display_feature_works'] != counts['actual_display_feature_works']
            or receipt['gate']['actual_content_non_genre_feature_works'] != counts['actual_content_non_genre_feature_works']):
        raise ValueError('Actual display projection differs from control counts')
    if (imported.get('stage') != 'actual_catalogue_import_complete'
            or not _successful_process(imported['actual_process'])
            or artifact('import:process-start.json')['pid'] != imported['actual_process']['pid']
            or artifact('import:process-exit.json') != imported['actual_process']
            or artifact('import:stdout.json') != imported['result']
            or imported['classification_manifest_sha256'] != receipt['control_artifacts']['export']['sha256']
            or imported['result']['failed'] != 0
            or imported['result']['raw_classified'] != receipt['gate']['accepted']
            or imported['result']['adopted'] != receipt['gate']['actual_display_feature_works']):
        raise ValueError('Actual content import/control observation differs')
    policy = imported['result'].get('display_policy_sha256')
    if policy is not None:
        if policy != counts['policy_sha256']:
            raise ValueError('Actual import display policy differs')
    else:
        # Historic imports cannot acquire a retroactive import-time fingerprint.
        # A genuine bounded read-only observation proves their current view.
        if 'display_observation' not in receipt['control_artifacts']:
            raise ValueError('Historic import needs an actual current display observation')
        observed = artifact('display_observation')
        details = observed['observation']
        process = observed['actual_process']
        request = artifact('display:request.json')
        import_request = artifact('import:request.json')
        expected_rows = sorted(({'qid': row['qid'], 'admitted_labels': row['admitted_labels']}
                                for row in projection['rows']), key=lambda row: row['qid'])
        code_pin = receipt['control_artifacts']['display:observer_code']
        if (observed.get('stage') != 'actual_catalogue_display_observed'
                or observed.get('binding_phase') != 'post_import_current_catalogue'
                or not _successful_process(process)
                or artifact('display:process-start.json')['pid'] != process['pid']
                or artifact('display:process-exit.json') != process
                or artifact('display:stdout.json') != details
                or observed['classification_manifest_sha256'] != imported['classification_manifest_sha256']
                or observed['display_projection'] != counts
                or details['classification_manifest_sha256'] != imported['classification_manifest_sha256']
                or details['display_projection'] != counts
                or details['matched_eligible_works'] != receipt['gate']['accepted']
                or details['persisted_rows'] != expected_rows
                or details['database_path'] != import_request['argv'][-3]
                or request['argv'][-3:] != [details['database_path'],
                    receipt['control_artifacts']['export']['path'], imported['classification_manifest_sha256']]
                or request.get('shell') is not False or request.get('read_only') is not True
                or details['policy_sha256_before'] != counts['policy_sha256']
                or details['policy_sha256_after'] != counts['policy_sha256']
                or details['observer_code_path'] != str(Path(__file__).resolve())
                or details['observer_code_path'] != code_pin['path']
                or details['observer_code_sha256'] != code_pin['sha256']
                or details.get('read_only') is not True or details.get('model_calls') != 0):
            raise ValueError('Actual current display observation differs')
    gate = actual_gate(run['selected'], receipt['gate']['accepted'],
        counts['actual_display_feature_works'], counts['actual_content_non_genre_feature_works'],
        stopped=bool(run.get('stopped') or run.get('stop_reasons')))
    if gate != receipt['gate'] or not gate['passed'] or not public['gate']['passed']:
        raise ValueError('Actual content continuation gate failed or differs')
    if receipt['run_id'] != run['run_id'] or receipt['coordinator_sha256'] != public['coordinator_sha256']:
        raise ValueError('Content control run/coordinator differs')
    if path.read_bytes() != raw:
        raise ValueError('Content control receipt changed during verification')
    return receipt
