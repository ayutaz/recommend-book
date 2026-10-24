"""Read-only final v20 audit with the explicitly sealed gate161 recovery.

The SHA-fixed v1 auditor remains the sole baseline/count/public-native auditor.
Only new-generation control dispatch and the excluded failed-window receipt are
added. No failed native window is replayed, imported, or treated as passed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/autonomous-next-v3/scale-20000-classifications-v1'
DATABASE = ROOT / 'data/autonomous-next-v3/scale-v1/catalogue.sqlite'
AUDIT1 = ROOT / 'scripts/audit_scale20k_catalogue_v1.py'
AUDIT1_SHA = '3c0c668904259d4784d3ce0d8ec29b13557082b01b0c72b5e0d4efc2d393bbd0'
UPSTREAM_INDEX_SHA = 'ecafd3a14d1718d95f4596c1edbc910dc9cef8b08a71e5ec71e98b207d5d26da'
DIAGNOSTIC = BASE / 'recovery-gate161-v1/diagnostic-v1.json'
DIAGNOSTIC_SHA = '3ad917634a9c0271341e91d57fd4570b2ff363ad81d0cd7a96905807c60bea3b'
FAILED_RESERVATION_SHA = 'e78cfe78e0b2dd6236fa4e44b0d176f910f176c618ce4bce0dc17b8a28996864'
ELIGIBLE_RECOVERY_ANCHOR = 15952
NEW_CODE = (
    'src/recommend_book/scale20k_native_v2.py', 'src/recommend_book/scale20k_recovery_v1.py',
    'scripts/run_scale20k_native_v3.py', 'scripts/run_scale20k_content_classification_v2.py',
    'scripts/prepare_scale20k_content_chunk_v2.py', 'scripts/prepare_scale20k_content_sources_v3.py',
)
OLD_CODE = {
    'scripts/audit_scale20k_catalogue_v1.py': AUDIT1_SHA,
    'src/recommend_book/scale20k_native_v1.py': 'dde58428553eb9ed1f16e8ed0fb4c7932c8aafb19debafc9aa5cbbca887030e1',
    'scripts/run_scale20k_native_v1.py': '59ceada510c757c9bba223af8a4affdb4d6f6c767410261042940774ed9e59f7',
    'scripts/prepare_scale20k_content_chunk_v1.py': 'e4d3110fd4c35741ea51476835b4df5f54869d9bfb851ac4f5e16026057f9435',
    'scripts/run_scale20k_content_classification_v1.py': 'cc087ffc68e8d78439b59f07244cf396b7cf21b1391e9b9c9ab5566ec016404f',
    'scripts/run_scale20k_native_v2.py': 'ba52758a2026b8e46b3ffd5f9332672a5c893974167bfafda626ab84d8cf4f1b',
    'scripts/prepare_scale20k_content_sources_v2.py': 'cf3440a869291f8a2a0f83f05be8190a9568c62fa3fc030a35740d567488dadd',
}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pinned(pin):
    path = Path(pin['path']).resolve(strict=True)
    if sha(path) != pin['sha256']:
        raise ValueError('Sealed artifact bytes differ: ' + str(path))
    return json.loads(path.read_bytes())


def load_module(path, expected):
    path = Path(path).resolve(strict=True)
    if sha(path) != expected:
        raise ValueError('Frozen audit/recovery code differs: ' + str(path))
    spec = importlib.util.spec_from_file_location('_final_audit_' + path.stem, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def code_pins(entry):
    raw = entry['code_pins']
    if isinstance(raw, dict):
        items = ({'path': path, 'sha256': digest} if isinstance(digest, str) else digest
                 for path, digest in raw.items())
    else:
        items = iter(raw)
    output = {}
    for pin in items:
        path = Path(pin['path'])
        if not path.is_absolute():
            path = ROOT / path
        path = path.resolve(strict=True)
        if str(path) in output or sha(path) != pin['sha256']:
            raise ValueError('Recovery code pin is duplicated or changed')
        output[str(path)] = pin['sha256']
    for relative in NEW_CODE:
        path = str((ROOT / relative).resolve(strict=True))
        if path not in output:
            raise ValueError('Required recovery code pin missing: ' + relative)
    # The sealed entry has exactly six new named pins. Historical code is
    # authenticated against these independent constants, never added to it.
    for relative, expected in OLD_CODE.items():
        if sha(ROOT / relative) != expected:
            raise ValueError('Independent old code pin differs: ' + relative)
    return output


def canonical_name(index, generation='v2'):
    start = 1 + (index - 93) * 100
    return f'scale20k-content-{start:04d}-{start + 99:04d}-{generation}'


class RecoveryAudit:
    def __init__(self, entry_path, entry_sha, *, database=DATABASE):
        self.database = Path(database).resolve(strict=True)
        self.entry_pin = {'path': str(Path(entry_path).resolve(strict=True)), 'sha256': entry_sha}
        self.entry = pinned(self.entry_pin)
        self.code = code_pins(self.entry)
        helper_path = ROOT / 'src/recommend_book/scale20k_recovery_v1.py'
        self.helper = load_module(helper_path, self.code[str(helper_path.resolve())])
        self.recovery = self.helper.verify_recovery_entry(entry_path, entry_sha, database=self.database)
        if not self.recovery['pins'].get('process_witnesses'):
            raise ValueError('Actual failed-controller/quarantine process witnesses are required')
        self.driver = load_module(ROOT / 'scripts/run_scale20k_native_v3.py',
            self.code[str((ROOT / 'scripts/run_scale20k_native_v3.py').resolve())])
        self.builder = load_module(ROOT / NEW_CODE[-1], self.code[str((ROOT / NEW_CODE[-1]).resolve())])
        self.audit1 = load_module(AUDIT1, AUDIT1_SHA)
        self.verified = set()
        self.control_counts = {}
        self.shortfalls = {}
        self.excluded = self.excluded_failed_window()

    def excluded_failed_window(self):
        diagnostic = pinned({'path': str(DIAGNOSTIC), 'sha256': DIAGNOSTIC_SHA})
        pins = self.recovery['pins']
        for key in ('control', 'quarantine'):
            expected = pins['failed_control' if key == 'control' else key]
            if diagnostic[key] != expected:
                raise ValueError('Excluded failed diagnostic/root pin differs')
        summary, control, public = (pinned(diagnostic[key]) for key in ('summary', 'control', 'export'))
        quarantine = pinned(pins['quarantine'])
        rows = pinned(pins['quarantine_rows'])['rows']
        gate = diagnostic['actual_gate']
        if (control['gate'] != gate or summary['gate'] != gate or quarantine['failed_gate'] != gate
                or gate.get('passed') is not False or gate.get('selected') != 15
                or gate.get('accepted') != 14 or gate.get('failure_rate') != 1 / 15
                or diagnostic['native_completed'] != 14 or diagnostic['native_failed'] != 1
                or diagnostic['failure_category'] != 'provider_capacity'
                or diagnostic['attempts'] != 1 or diagnostic['same_failed_inputs_retried'] != 0
                or len(rows) != 14 or {row['qid'] for row in rows} != set(pins['failed_qids'])
                or quarantine.get('remaining') != ELIGIBLE_RECOVERY_ANCHOR
                or public['gate']['passed'] is not False or len(public['verified_projection']['rows']) != 14
                or {row['qid'] for row in public['verified_projection']['rows']} != set(pins['failed_qids'])):
            raise ValueError('Excluded failed-window gate/count/identity differs')
        if self._failed_remaining():
            raise ValueError('Excluded failed native rows are present in the catalogue')
        return {**{name: diagnostic[name] for name in ('summary', 'control', 'export')},
            'quarantine': pins['quarantine'], 'quarantine_rows': pins['quarantine_rows'],
            'removed': 14, 'catalogue_rows_remaining': 0, 'failed_gate': gate}

    def _failed_remaining(self):
        qids = sorted(self.recovery['pins']['failed_qids'])
        with sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True) as db:
            return db.execute('SELECT count(*) FROM native_classifications WHERE qid IN ('
                + ','.join('?' for _ in qids) + ')', qids).fetchone()[0]

    def _source(self, receipt, public, index, name):
        artifacts = receipt['control_artifacts']
        failed_attempt = pinned({'path': str(BASE / 'attempt-reservations/scale20k-content-6801-6900-v1.json'),
                                 'sha256': FAILED_RESERVATION_SHA})
        failed_qids = failed_attempt['qids']
        quarantine_qids = set(self.recovery['pins']['failed_qids'])
        if (failed_attempt['version'] != 'scale20k-native-attempt-reservation-v1'
                or failed_attempt['global_index'] != 161
                or failed_attempt['run_name'] != 'scale20k-content-6801-6900-v1'
                or len(failed_qids) != 15 or len(set(failed_qids)) != 15
                or any(not isinstance(qid, str) or not qid for qid in failed_qids)
                or len(quarantine_qids) != 14 or not quarantine_qids <= set(failed_qids)):
            raise ValueError('Authenticated failed15/quarantined14 authority sets differ')
        reservation_path = BASE / 'attempt-reservations' / (name + '.json')
        reservation_key = str(reservation_path.resolve(strict=True))
        reservation = pinned({'path': reservation_key, 'sha256': public['artifact_sha256'][reservation_key]})
        if (reservation['version'] != 'scale20k-native-attempt-reservation-v2'
                or reservation['global_index'] != index or reservation['run_name'] != name
                or reservation['recovery_entry'] != self.entry_pin or reservation['actual_overlap_count'] != 0
                or reservation['helper_sha256'] != self.code[str((ROOT / NEW_CODE[0]).resolve())]
                or set(reservation['excluded_failed_qids']) != set(failed_qids)):
            raise ValueError('Actual source reservation/recovery entry differs')
        source = Path(reservation['source_path']).resolve(strict=True)
        if not source.is_relative_to(BASE.resolve()):
            raise ValueError('Recovery source outside v20 namespace')
        source_manifest = pinned({'path': str(source / 'manifest.json'), 'sha256': reservation['source_sha256']})
        source_index = pinned({'path': reservation['source_index_path'], 'sha256': reservation['source_index_sha256']})
        entries = [row for row in source_index['windows'] if row['window_index'] == index]
        if (len(entries) != 1 or Path(entries[0]['source_path']).resolve() != source
                or entries[0]['source_sha256'] != reservation['source_sha256']
                or source_manifest['window_index'] != index or source_manifest['chunks'][0]['run_name'] != name
                or source_manifest['upstream_window_index'] != index + 1
                or source_manifest['upstream_source_index_sha256'] != UPSTREAM_INDEX_SHA
                or Path(public['source_preparation']).resolve() != source
                or public['source_preparation_sha256'] != reservation['source_sha256']
                or entries[0]['eligibility_policy_sha256'] != artifacts['native_eligibility_source_observation']['sha256']):
            raise ValueError('Actual source/index/upstream window binding differs')
        source_policy = pinned(artifacts['native_eligibility_source_observation'])
        policy = pinned(artifacts['native_eligibility_policy'])
        pre = pinned(artifacts['native_eligibility_pre_classification'])
        origin = source_policy['origin']
        if (source_policy['version'] != 'scale20k-source-recovery-eligibility-v3'
                or source_policy['new_window_index'] != index or source_policy['new_run_name'] != name
                or Path(source_policy['source_manifest_path']).resolve() != source / 'manifest.json'
                or source_policy['source_manifest_sha256'] != reservation['source_sha256']
                or pre['source_manifest_sha256'] != reservation['source_sha256']
                or source_policy['catalogue_before'] != policy['before_import']
                or source_policy['catalogue_after'] != policy['before_import']
                or source_policy['upstream_window_index'] != index + 1
                or origin['window_index'] != index + 1 or origin['index_sha256'] != UPSTREAM_INDEX_SHA
                or origin['source_sha256'] != source_manifest['upstream_source_manifest_sha256']
                or origin['input_identity'] is None
                or source_policy['source_builder_sha256'] != self.code[str((ROOT / NEW_CODE[-1]).resolve())]
                or source_policy['observer_sha256'] != self.code[str((ROOT / 'scripts/run_scale20k_native_v3.py').resolve())]
                or source_policy['required_purpose_admissions_allowed'] is not True
                or source_policy['metadata_mutations'] != 0
                or source_policy['selected'] != source_manifest['selected']
                or set(source_policy['qids']) != set(reservation['qids'])):
            raise ValueError('Canonical source recovery observation differs')
        for pin in source_policy['bindings'].values():
            if sha(pin['path']) != pin['sha256']:
                raise ValueError('Selected source binding bytes differ')
        if sha(origin['index_path']) != UPSTREAM_INDEX_SHA or sha(Path(origin['source_path']) / 'manifest.json') != origin['source_sha256']:
            raise ValueError('Independent upstream source/index changed')
        reproduced_origin = self.builder.validate_origin(origin_source=Path(origin['source_path']),
            origin_source_sha256=origin['source_sha256'], origin_index=Path(origin['index_path']),
            origin_index_sha256=UPSTREAM_INDEX_SHA, origin_window_index=index + 1,
            origin_run_name=origin['run_name'], new_window_index=index)
        if reproduced_origin != origin:
            raise ValueError('Saved upstream identity differs from the canonical source verifier')
        mapping = json.loads((source / 'work-mapping.json').read_bytes())
        identity = {row['wikidata_qid']: row['input_sha256'] for row in mapping}
        if (len(identity) != source_manifest['selected'] or set(identity) != set(reservation['qids'])
                or set(identity.values()) != set(reservation['input_hashes'])
                or any(origin['input_identity'].get(qid) != digest for qid, digest in identity.items())
                or set(identity) & set(failed_qids)
                or set(identity.values()) & set(self.entry.get('failed_input_hashes', []))):
            raise ValueError('Fresh selected QID/input identity differs from its upstream source')
        self._shortfall(source_policy, origin, identity, index, name, public)
        if self.driver.catalogue_snapshot(self.database, policy['after_import']) != policy['after_import']:
            raise ValueError('Current catalogue source flags/labels differ from actual import observation')

    def _shortfall(self, source_policy, origin, identity, index, name, public):
        pin = source_policy.get('exact_shortfall_receipt')
        if pin is None:
            return
        receipt = pinned(pin)
        selected = self.builder._shortfall_selection(receipt)
        before, target, remaining = (receipt[key] for key in
            ('eligible_native_count', 'target_eligible_native', 'remaining'))
        prior = receipt['prior_control']
        previous = pinned(prior)
        if (receipt.get('version') != 'scale20k-exact-shortfall-v1'
                or any(type(value) is not int for value in (before, target, remaining))
                or target != 20000 or remaining != target - before
                or not 0 < remaining < origin['selected'] or len(identity) != remaining
                or selected != identity or receipt.get('run_index', receipt.get('window_index')) != index
                or receipt['run_name'] != name or pin['run_name'] != name
                or receipt['origin_index_sha256'] != origin['index_sha256']
                or receipt['origin_source_sha256'] != origin['source_sha256']
                or previous['window_index'] != index - 1 or previous['gate']['passed'] is not True
                or Path(prior['path']).resolve() != Path(public['prior_control_receipt']).resolve()
                or public['artifact_sha256'].get(str(Path(prior['path']).resolve())) != prior['sha256']
                or Path(pin['prior_control']['path']).resolve() != Path(prior['path']).resolve()
                or pin['prior_control']['sha256'] != prior['sha256']
                or any(type(receipt[key]) is not int or receipt[key] != 0 for key in
                       ('existing_native_overlap_count', 'existing_attempt_overlap_count'))):
            raise ValueError('Exact shortfall receipt/count/identity/prior binding differs')
        for key in ('version', 'eligible_native_count', 'target_eligible_native', 'remaining',
                    'existing_native_overlap_count', 'existing_attempt_overlap_count'):
            if pin[key] != receipt[key]:
                raise ValueError('Exact shortfall policy summary differs from its pinned receipt')
        if self.builder._shortfall_selection(pin) != identity:
            raise ValueError('Exact shortfall policy selection differs')
        omitted = source_policy['unused_final_shortfall']
        expected_omitted = {qid: digest for qid, digest in origin['input_identity'].items() if qid not in identity}
        if (source_policy['upstream_subset_status'] != 'unused_final_shortfall'
                or source_policy['upstream_subset_holds'] is not None
                or len(omitted) != len(expected_omitted)
                or {row['qid']: row['input_sha256'] for row in omitted} != expected_omitted
                or any(row['status'] != 'unused_final_shortfall' for row in omitted)):
            raise ValueError('Unsent final-shortfall omissions differ or were presented as guard holds')
        self.shortfalls[index] = {'window_index': index, 'receipt': {'path': pin['path'], 'sha256': pin['sha256']},
            'eligible_before': before, 'target': target, 'selected': remaining, 'prior_control': prior}

    def _shortfall_history(self):
        output = []
        for index, shortfall in sorted(getattr(self, 'shortfalls', {}).items()):
            prior_indices = set(range(161, index))
            if not prior_indices <= self.verified or not prior_indices <= self.control_counts.keys():
                raise ValueError('Exact shortfall lacks the complete genuine preceding import chain')
            historical = ELIGIBLE_RECOVERY_ANCHOR + sum(self.control_counts[i] for i in prior_indices)
            if historical != shortfall['eligible_before'] or shortfall['selected'] != 20000 - historical:
                raise ValueError('Exact shortfall eligible-before differs from authenticated historical imports')
            pinned(shortfall['receipt'])
            output.append({**shortfall, 'authenticated_eligible_before': historical})
        return output

    def dispatch(self, path, expected_sha):
        path = Path(path).resolve(strict=True)
        if path.parent.name.endswith('-v1'):
            return self.audit1.verify_window_control(path, expected_sha, database=self.database)
        match = re.fullmatch(r'scale20k-content-(\d{4,})-(\d{4,})-v2', path.parent.name)
        if not match or not path.is_relative_to((BASE / 'classification-exports-v3').resolve()):
            raise ValueError('Unsupported recovery public namespace')
        start, end = map(int, match.groups())
        index = 93 + (start - 1) // 100
        if (start - 1) % 100 or end != start + 99 or not 161 <= index <= 214:
            raise ValueError('Recovery window range differs')
        public = pinned({'path': str(path), 'sha256': expected_sha})
        control_path = BASE / 'window-controls' / (path.parent.name + '.json')
        control_sha = sha(control_path)
        receipt = self.driver.verify_policy_control(control_path, control_sha)
        export = receipt['control_artifacts']['export']
        if (receipt['window_index'] != index or receipt['gate']['passed'] is not True
                or Path(export['path']).resolve() != path or export['sha256'] != expected_sha):
            raise ValueError('Passed recovery control/public binding differs')
        for name, relative in (('native_eligibility_controller', 'scripts/run_scale20k_native_v3.py'),
                               ('native_eligibility_source_builder', NEW_CODE[-1])):
            pin = receipt['control_artifacts'][name]
            if Path(pin['path']).resolve() != (ROOT / relative).resolve() or pin['sha256'] != self.code[str((ROOT / relative).resolve())]:
                raise ValueError('Passed recovery policy code pin differs')
        prior = (self.recovery['pins']['prior_control'] if index == 161 else {
            'path': str(BASE / 'window-controls' / (canonical_name(index - 1) + '.json')),
            'sha256': sha(BASE / 'window-controls' / (canonical_name(index - 1) + '.json'))})
        prior_document = pinned(prior)
        if (prior_document['window_index'] != index - 1 or prior_document['gate']['passed'] is not True
                or Path(public['prior_control_receipt']).resolve() != Path(prior['path']).resolve()
                or public['artifact_sha256'].get(str(Path(prior['path']).resolve())) != prior['sha256']):
            raise ValueError('Recovery prior must be the preceding passed v2 control or passed160 v1')
        self._source(receipt, public, index, path.parent.name)
        self.verified.add(index)
        self.control_counts[index] = receipt['gate']['accepted']
        return {**receipt, 'audit_control_pin': {'path': str(control_path), 'sha256': control_sha}}

    def audit(self):
        result = self.audit1.audit(database=self.database, control_verifier=self.dispatch)
        result['version'] = 'scale20k-catalogue-audit-v2'
        result['audit_code_sha256'] = sha(__file__)
        result['parent_auditor'] = {'path': str(AUDIT1), 'sha256': AUDIT1_SHA}
        result['recovery_entry'] = self.entry_pin
        result['excluded_failed_windows'] = [self.excluded]
        if result['status'] == 'complete':
            try:
                if not self.verified or self.verified != set(range(161, max(self.verified) + 1)):
                    raise ValueError('Passed recovery controls are not a contiguous genuine chain')
                result['exact_shortfall_windows'] = self._shortfall_history()
                # Fresh absence/code/entry checks only; no failed-native replay.
                remaining = self._failed_remaining()
                self.excluded['catalogue_rows_remaining'] = remaining
                if remaining:
                    raise ValueError('Excluded failed native rows reappeared during final audit')
                pinned(self.entry_pin)
                code_pins(self.entry)
            except (OSError, ValueError, KeyError) as exc:
                result['status'] = 'violations'
                result['issues'].append(str(exc))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recovery-entry', type=Path, required=True)
    parser.add_argument('--recovery-entry-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = RecoveryAudit(args.recovery_entry, args.recovery_entry_sha256).audit()
    except (OSError, ValueError, TypeError, KeyError, sqlite3.DatabaseError) as exc:
        result = {'version': 'scale20k-catalogue-audit-v2', 'status': 'violations', 'issues': [str(exc)], 'model_calls': 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'output': str(args.output)}))
    raise SystemExit(0 if result['status'] == 'complete' else 1)


if __name__ == '__main__':
    main()
