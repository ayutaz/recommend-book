"""One read-only final v20 audit, using existing authentic proof verifiers.

No model, importer, catalogue constructor, source fetch, or source-raw sweep is
called. A target/baseline failure stops before expensive native proof replay.
Test callers may supply tiny independently pinned inputs and verifier doubles;
the CLI always uses the independently frozen production pins below.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3

from recommend_book.scale_catalogue_v1 import CLASSIFICATION_AXES, _display_native_labels

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/autonomous-next-v3/scale-20000-classifications-v1'
DATABASE = ROOT / 'data/autonomous-next-v3/scale-v1/catalogue.sqlite'
FROZEN = {
    'plan': {'path': ROOT / 'docs/autonomous-next-phase-plan-v20.md',
             'sha256': '11a86adaa9ae2f91e03f278a190a82a68f7d45651bee7678fe3f2189699dc594'},
    'requirements': {'path': BASE / 'requirements.json',
                     'sha256': '04b48c43e6a1ca31b018de00ec45aefc9fd2123913a9331e66c48419bed6f9c9'},
    'baseline_native': {'path': BASE / 'baseline-native-10000.jsonl',
                        'sha256': '5734eda48467012f4c257a57027e655cb829ba309730a5dc2cce2a01c47ddf36'},
    'baseline_works': {'path': BASE / 'baseline-works-100372.jsonl',
                       'sha256': '252b219b7306e871711cd78bb825edbce8d1d9d41bad914409360b7734d49165'},
}
NATIVE_FIELDS = ('qid', 'manifest_path', 'manifest_sha256', 'labels_json', 'provenance_json')
AXES = CLASSIFICATION_AXES
# Independent root handoff: controller2's first actual window is global97.
# A mutable control cannot opt out of policy verification by deleting its pins.
FIRST_POLICY_WINDOW = 97
POLICY_CODE_PINS = {
    'native_eligibility_controller': {
        'path': ROOT / 'scripts/run_scale20k_native_v2.py',
        'sha256': 'ba52758a2026b8e46b3ffd5f9332672a5c893974167bfafda626ab84d8cf4f1b'},
    'native_eligibility_source_builder': {
        'path': ROOT / 'scripts/prepare_scale20k_content_sources_v2.py',
        'sha256': 'cf3440a869291f8a2a0f83f05be8190a9568c62fa3fc030a35740d567488dadd'},
}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pinned_json(path, expected):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Pinned artifact SHA differs: ' + str(path))
    return json.loads(raw)


def _load_script(name):
    path = ROOT / 'scripts' / name
    spec = importlib.util.spec_from_file_location('_audit_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_legacy = _load_script('audit_scale100k_catalogue_v1.py')
canonical_json, read_snapshot = _legacy.canonical_json, _legacy.read_snapshot


def verify_public_manifest(path, expected):
    """Full existing read-only native/usage/review/rights replay once per export."""
    document = pinned_json(path, expected)
    version = document.get('version')
    modules = {
        'scale-native-classification-export-v1': 'recommend_book.scale_classification_v1',
        'scale-content-native-classification-export-v2': 'recommend_book.scale_content_classification_v2',
        'scale-content-native-classification-export-v3': 'recommend_book.scale_content_classification_v3',
    }
    if version not in modules:
        raise ValueError('Unsupported public classification export: ' + str(version))
    from importlib import import_module
    from recommend_book.scale_coordinator_proof_v1 import coordinator_chain_pins
    # Also follow legacy terminal -> request -> actual coordinator code.
    for pin in coordinator_chain_pins(document):
        if sha(pin['path']) != pin['sha256']:
            raise ValueError('Actual coordinator chain pin differs')
    result = import_module(modules[version]).verify_catalogue_classification(path, expected)
    if sha(path) != expected:
        raise ValueError('Public manifest changed during verification')
    return result


def verify_window_control(path, expected, *, database=DATABASE):
    """Existing gate verifier plus the fixed controller2 observation verifier."""
    path = Path(path).resolve(strict=True)
    if not path.is_relative_to((BASE / 'classification-exports-v3').resolve()):
        raise ValueError('New v20 public export outside its sealed namespace')
    control_path = BASE / 'window-controls' / (path.parent.name + '.json')
    control_sha = sha(control_path)
    raw = pinned_json(control_path, control_sha)
    match = re.fullmatch(r'scale20k-content-(\d{4,})-(\d{4,})-v1', path.parent.name)
    if not match:
        raise ValueError('Noncanonical v20 window name')
    start, end = map(int, match.groups())
    if start < 1 or (start - 1) % 100 or end != start + 99:
        raise ValueError('Noncanonical v20 window range')
    index = 93 + (start - 1) // 100
    if type(raw.get('window_index')) is not int or raw['window_index'] != index:
        raise ValueError('Control index differs from canonical v20 window')
    if index >= FIRST_POLICY_WINDOW and 'native_eligibility_policy' not in raw['control_artifacts']:
        raise ValueError('Controller2 window lacks mandatory eligibility policy evidence')
    if 'native_eligibility_policy' in raw['control_artifacts']:
        for name, fixed in POLICY_CODE_PINS.items():
            observed = raw['control_artifacts'][name]
            if (Path(observed['path']).resolve() != Path(fixed['path']).resolve()
                    or observed['sha256'] != fixed['sha256'] or sha(fixed['path']) != fixed['sha256']):
                raise ValueError('Frozen eligibility controller/source-builder code pin differs')
        controller = _load_script('run_scale20k_native_v2.py')
        receipt = controller.verify_policy_control(control_path, control_sha)
        policy_pin = receipt['control_artifacts']['native_eligibility_policy']
        policy = pinned_json(policy_pin['path'], policy_pin['sha256'])
        # Historical before/after evidence is not a substitute for current flags.
        actual = controller.catalogue_snapshot(database, policy['after_import'])
        if actual != policy['after_import']:
            raise ValueError('Current catalogue differs from authenticated import/source-genre observation')
    else:
        from recommend_book.scale_content_control_v4 import verify_control_receipt
        receipt = verify_control_receipt(control_path, control_sha)
    export = receipt['control_artifacts']['export']
    if (Path(export['path']).resolve() != path or export['sha256'] != expected
            or receipt['gate'].get('passed') is not True):
        raise ValueError('Actual v20 control/export/gate differs')
    return {**receipt, 'audit_control_pin': {'path': str(control_path), 'sha256': control_sha}}


def _unique(rows, key):
    values = {row[key]: row for row in rows}
    if len(values) != len(rows) or any(not isinstance(k, str) or not k for k in values):
        raise ValueError('Duplicate or invalid baseline identity')
    return values


def _frozen(pins):
    raw = {}
    for name in FROZEN:
        pin = pins[name]
        raw[name] = Path(pin['path']).read_bytes()
        if hashlib.sha256(raw[name]).hexdigest() != pin['sha256']:
            raise ValueError('Frozen baseline/requirements SHA differs: ' + name)
    requirements = json.loads(raw['requirements'])
    if requirements['version'] != 'scale20k-v20-requirements-v1':
        raise ValueError('Unknown requirements version')
    native = _unique([json.loads(line) for line in raw['baseline_native'].splitlines() if line.strip()], 'qid')
    works = _unique([json.loads(line) for line in raw['baseline_works'].splitlines() if line.strip()], 'qid')
    goals = requirements['requirements']['2']
    if (len(native) != requirements['baseline']['native']
            or len(native) != goals['preserve_existing_native_rows']
            or len(works) != requirements['baseline']['stored']):
        raise ValueError('Frozen baseline count differs')
    return requirements, native, works


def _snapshot(database):
    with read_snapshot(Path(database)) as db:
        works = {row['qid']: dict(row) for row in db.execute(
            'SELECT qid,version_id,display_allowed,recommendable,language,year FROM works')}
        references = {(row[0], row[1]) for row in db.execute('SELECT qid,version_id FROM source_versions')}
        natives = {row['qid']: dict(row) for row in db.execute('SELECT ' + ','.join(NATIVE_FIELDS) + ' FROM native_classifications')}
        genres = {row[0] for row in db.execute('SELECT DISTINCT qid FROM work_genres')}
        regions = {}
        for row in db.execute('SELECT w.qid,w.row_json FROM works w JOIN native_classifications n USING(qid)'):
            # Only factual stored region fields, no language/title inference.
            regions[row[0]] = json.loads(row[1]).get('region')
    return {'works': works, 'references': references, 'natives': natives, 'genre_qids': genres, 'regions': regions}


def _period(year):
    if type(year) is not int:
        return 'unknown'
    if year < 1800:
        return 'before_1800'
    if year < 1900:
        return '1800_1899'
    if year < 2000:
        return '1900_1999'
    return '2000_onward'


def _axis_counts():
    return {axis: {key: 0 for key in ('adopted_labels', 'adopted_works', 'held_labels', 'held_works',
        'explicit_unknown_works', 'unknown_unavailable_works', 'no_display_feature_works')} for axis in AXES}


def _count_axes(stats, row):
    provenance = row['provenance']
    raw = provenance.get('native_result', {})
    displayed, display_held = _display_native_labels(row['labels'])
    held = provenance.get('label_admission', {}).get('held_labels', []) + display_held
    axes = {item['label_id']: item['axis'] for item in [*raw.get('labels', []), *row['labels']]}
    missing = {item['axis'] for item in raw.get('missing_axes', [])}
    for axis, out in stats.items():
        adopted = sum(label['axis'] == axis for label in displayed)
        withheld = {item['label_id'] for item in held
                    if item.get('axis', axes.get(item['label_id'], item['label_id'].split('.')[0])) == axis}
        out['adopted_labels'] += adopted
        out['adopted_works'] += bool(adopted)
        out['held_labels'] += len(withheld)
        out['held_works'] += bool(withheld)
        out['explicit_unknown_works'] += axis in missing
        out['unknown_unavailable_works'] += axis not in missing
        out['no_display_feature_works'] += not adopted
    return displayed


def audit(*, database=DATABASE, frozen=None, manifest_verifier=None, control_verifier=None):
    """No successful status until baselines, targets, every row and proof agree."""
    pins = FROZEN if frozen is None else frozen
    report = {'version': 'scale20k-catalogue-audit-v1', 'status': 'violations', 'issues': [],
        'database': str(Path(database).resolve()), 'observed_at': datetime.now(timezone.utc).isoformat(),
        'frozen_inputs': {name: {'path': str(pin['path']), 'sha256': pin['sha256']} for name, pin in pins.items()},
        'proofs': {'status': 'not_run_target_or_baseline', 'manifests': [], 'controls': []},
        'independent_accuracy': 'unavailable', 'calibrated_confidence': 'unavailable',
        'model_calls': 0, 'database_writes': 0, 'source_raw_rescanned': False}
    report['audit_code_sha256'] = sha(__file__)
    report['pure_helper_sha256'] = sha(_legacy.__file__)
    try:
        requirements, baseline, work_baseline = _frozen(pins)
        snapshot = _snapshot(database)
        works, natives = snapshot['works'], snapshot['natives']
        baseline_preserved = 0
        for qid, row in baseline.items():
            if qid not in natives or hashlib.sha256(canonical_json(natives[qid])).hexdigest() != row['row_sha256']:
                report['issues'].append('baseline_native_row_missing_or_changed:' + qid)
            else:
                baseline_preserved += 1
        for qid, row in work_baseline.items():
            if qid not in works or (qid, row['version_id']) not in snapshot['references']:
                report['issues'].append('baseline_original_work_version_reference_missing:' + qid)
        eligible = {qid for qid in natives if qid in works and works[qid]['display_allowed'] == 1}
        new = eligible - baseline.keys()
        counts = {'native_stored': len(natives), 'eligible_native': len(eligible), 'new_eligible_native': len(new),
            'baseline_native_preserved': baseline_preserved, 'stored_works': len(works),
            'source_recommendable_display_works': sum(w['display_allowed'] == 1 and w['recommendable'] == 1 for w in works.values()),
            'eligible_native_source_genre_known': len(eligible & snapshot['genre_qids']),
            'eligible_native_source_genre_unknown': len(eligible - snapshot['genre_qids']),
            'eligible_native_recommendable_1': sum(works[q]['recommendable'] == 1 for q in eligible),
            'baseline_current_version_changes': sum(works.get(q, {}).get('version_id') != r['version_id'] for q, r in work_baseline.items()),
            'display_feature_works': 0, 'new_display_feature_works': 0,
            'display_content_non_genre_feature_works': 0, 'new_display_content_non_genre_feature_works': 0}
        report['counts'] = counts
        if report['issues']:
            return report
        goals = requirements['requirements']['2']
        if len(eligible) < goals['eligible_unique_native_sol_works_minimum'] or len(new) < goals['new_accepted_works_minimum']:
            report['issues'].append('eligible_native_target_not_met')
            report['status'] = 'incomplete'
            return report
        verify = manifest_verifier or verify_public_manifest
        controls = control_verifier or (lambda p, h: verify_window_control(p, h, database=database))
        report['proofs']['status'] = 'verification_started'
        grouped = {}
        for qid, native in natives.items():
            if qid not in works or (qid, works[qid]['version_id']) not in snapshot['references']:
                raise ValueError('Actual native current source version missing: ' + qid)
            path = str(Path(native['manifest_path']).resolve(strict=True))
            item = grouped.setdefault(path, {'sha256': native['manifest_sha256'], 'qids': set()})
            if item['sha256'] != native['manifest_sha256']:
                raise ValueError('SQL pins conflict for a shared manifest')
            item['qids'].add(qid)
        all_axes, new_axes = _axis_counts(), _axis_counts()
        languages, periods, regions = Counter(), Counter(), Counter()
        for path, group in sorted(grouped.items()):
            document = pinned_json(path, group['sha256'])
            actual = _unique(verify(path, group['sha256']), 'qid')
            public = _unique(document['verified_projection']['rows'], 'qid')
            if actual.keys() != public.keys() or actual.keys() != group['qids']:
                raise ValueError('SQL/public/authenticated manifest work coverage differs: ' + path)
            for qid, row in actual.items():
                native = natives[qid]
                if (row['work_id'] != 'wd:' + qid or public[qid]['work_id'] != row['work_id']
                        or row['labels'] != public[qid]['admitted_labels']
                        or json.loads(native['labels_json']) != row['labels']
                        or json.loads(native['provenance_json']) != row['provenance']):
                    raise ValueError('SQL/public/authenticated identity, labels or provenance differs: ' + qid)
                if qid in eligible:
                    displayed = _count_axes(all_axes, row)
                    counts['display_feature_works'] += bool(displayed)
                    content_ids = set(row['provenance'].get('content_evidence_ids', []))
                    content_non_genre = any(label['axis'] != 'genre' and any(
                        evidence.get('evidence_id') in content_ids for evidence in label.get('evidence', []))
                        for label in displayed)
                    counts['display_content_non_genre_feature_works'] += content_non_genre
                    if qid in new:
                        _count_axes(new_axes, row)
                        counts['new_display_feature_works'] += bool(displayed)
                        counts['new_display_content_non_genre_feature_works'] += content_non_genre
                    languages[works[qid]['language'] or 'unknown'] += 1
                    periods[_period(works[qid]['year'])] += 1
                    stored = snapshot['regions'][qid]
                    region_ids = {item.get('qid') for item in (stored or []) if isinstance(item, dict) and item.get('qid')}
                    regions.update(region_ids or {'unknown'})
            if group['qids'] - baseline.keys():
                control = controls(path, group['sha256'])
                export = control['control_artifacts']['export']
                if (Path(export['path']).resolve() != Path(path) or export['sha256'] != group['sha256']
                        or control['gate'].get('passed') is not True):
                    raise ValueError('Actual import/control gate binding differs')
                report['proofs']['controls'].append(control.get('audit_control_pin', {'export_sha256': group['sha256']}))
            report['proofs']['manifests'].append({'path': path, 'sha256': group['sha256'], 'authenticated_rows': len(actual)})
        report.update(axes=all_axes, new_axes=new_axes, demographics={
            'denominator': len(eligible), 'languages': dict(languages), 'periods': dict(periods),
            'regions': dict(regions), 'region_counts_may_overlap': True},
            axis_denominator=len(eligible), new_axis_denominator=len(new),
            unknown_definition='Explicit saved native_result.missing_axes only; other unknown detail unavailable. '
                'No display feature includes held/unknown/unassessed, never negative or absent.')
        if (_snapshot(database) != snapshot or any(sha(pin['path']) != pin['sha256'] for pin in pins.values())
                or any(sha(pin['path']) != pin['sha256'] for pin in report['proofs']['manifests'])
                or any('path' in pin and sha(pin['path']) != pin['sha256'] for pin in report['proofs']['controls'])):
            raise ValueError('Catalogue or frozen inputs changed during final audit')
        report['proofs']['status'] = 'verified_once_per_distinct_manifest_and_new_window'
        report['status'] = 'complete'
    except (OSError, ValueError, TypeError, KeyError, sqlite3.DatabaseError) as exc:
        report['issues'].append(type(exc).__name__ + ': ' + str(exc))
        if report['proofs']['status'] == 'verification_started':
            report['proofs']['status'] = 'failed'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=DATABASE)
    parser.add_argument('--output', type=Path, required=True, help='New report file; existing files are never overwritten')
    args = parser.parse_args()
    result = audit(database=args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'counts': result.get('counts'), 'output': str(args.output)}, ensure_ascii=True))
    raise SystemExit(0 if result['status'] == 'complete' else 1)


if __name__ == '__main__':
    main()
