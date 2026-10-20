"""Compact genuine window-control receipts; never authorize label reuse.

Each new window still runs the full native/rights verifier. These receipts bind
actual terminal/import observations for continuation without recursively copying
old native source corpora into future public feature proofs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def actual_gate(selected, accepted, adopted, content_non_genre, *, stopped=False):
    if (type(selected) is not int or not 1 <= selected <= 100
            or any(type(v) is not int or not 0 <= v <= selected
                   for v in (accepted, adopted, content_non_genre))
            or adopted > accepted or content_non_genre > adopted):
        raise ValueError('Invalid actual accepted/display/content window counts')
    reasons = []
    if (selected - accepted) / selected > .05:
        reasons.append('actual_validation_or_admission_failure_above_5_percent')
    if adopted / selected < .70:
        reasons.append('actual_display_feature_adoption_below_70_percent')
    if content_non_genre == 0:
        reasons.append('no_actual_content_cited_non_genre_adoption_return_to_content')
    if stopped:
        reasons.append('native_stop_or_quota_signal')
    return {'passed': not reasons, 'selected': selected, 'accepted': accepted,
            'actual_display_feature_works': adopted,
            'actual_content_non_genre_feature_works': content_non_genre,
            'failure_rate': (selected - accepted) / selected,
            'actual_display_adoption_rate': adopted / selected, 'reasons': reasons}


def verify_control_receipt(path, expected_sha256):
    """Check only genuine process-control files, never past source admission."""
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Content control receipt SHA differs')
    receipt = json.loads(raw)
    if receipt.get('version') != 'scale-content-window-control-v1':
        raise ValueError('Unknown content control receipt')
    for name, pin in receipt['control_artifacts'].items():
        if not Path(pin['path']).is_absolute() or sha(pin['path']) != pin['sha256']:
            raise ValueError('Content process-control artifact changed: ' + name)
    export = json.loads(Path(receipt['control_artifacts']['export']['path']).read_bytes())
    run = json.loads(Path(receipt['control_artifacts']['run']['path']).read_bytes())
    imported = json.loads(Path(receipt['control_artifacts']['import_report']['path']).read_bytes())
    if export['version'] != 'scale-content-native-classification-export-v1':
        raise ValueError('Content route cannot reuse a metadata-only gate')
    process = imported['actual_process']
    if (imported['stage'] != 'actual_catalogue_import_complete'
            or process['actual_exit_code'] != 0 or process['process_exit_observed'] is not True
            or type(process['pid']) is not int or process['pid'] <= 0
            or imported['classification_manifest_sha256'] != receipt['control_artifacts']['export']['sha256']
            or imported['result']['failed'] != 0
            or imported['result']['raw_classified'] != receipt['gate']['accepted']
            or imported['result']['adopted'] != receipt['gate']['actual_display_feature_works']):
        raise ValueError('Actual content import/control observation differs')
    gate = actual_gate(run['selected'], receipt['gate']['accepted'],
        receipt['gate']['actual_display_feature_works'],
        receipt['gate']['actual_content_non_genre_feature_works'],
        stopped=bool(run.get('stopped') or run.get('stop_reasons')))
    if gate != receipt['gate'] or not gate['passed'] or not export['gate']['passed']:
        raise ValueError('Actual content continuation gate failed or differs')
    if receipt['run_id'] != run['run_id'] or receipt['coordinator_sha256'] != export['coordinator_sha256']:
        raise ValueError('Content control run/coordinator differs')
    if path.read_bytes() != raw:
        raise ValueError('Content control receipt changed during verification')
    # No read of export.artifact_sha256 dependencies here: this is process
    # control, not permission or readmission of any old native feature.
    return receipt
