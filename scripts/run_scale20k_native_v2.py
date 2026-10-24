"""Classify genuine novel content independently of source P136 availability.

The fixed v1 classifier, guards, review, rights and control verifier are reused.
Only display eligibility SQL changes. No catalogue metadata is promoted.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from types import ModuleType, SimpleNamespace

from recommend_book import scale20k_native_v1 as fixed_state

ROOT = Path(__file__).resolve().parents[1]
DRIVER_V1_SHA = '59ceada510c757c9bba223af8a4affdb4d6f6c767410261042940774ed9e59f7'
BUILDER_NAME = 'prepare_scale20k_content_sources_v2.py'
POLICY = 'novel_content_with_display_permission_source_genre_optional'


def eligible_native_count(database):
    with sqlite3.connect(Path(database).resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        return db.execute('SELECT count(*) FROM native_classifications n JOIN works w USING(qid) '
                          'WHERE w.display_allowed=1').fetchone()[0]


def catalogue_snapshot(database, qids):
    qids = sorted(set(qids))
    if not qids or len(qids) > 100:
        raise ValueError('One bounded actual selected authority set required')
    placeholders = ','.join('?' for _ in qids)
    with sqlite3.connect(Path(database).resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        result = {qid: {'version_id': version, 'display_allowed': display, 'recommendable': recommendable,
                        'genres': [], 'native': None}
                  for qid, version, display, recommendable in db.execute(
                      'SELECT qid,version_id,display_allowed,recommendable FROM works WHERE qid IN (' + placeholders + ')', qids)}
        if set(result) != set(qids):
            raise ValueError('Selected authority absent from catalogue')
        for qid, genre, label in db.execute('SELECT qid,genre_qid,label FROM work_genres WHERE qid IN ('
                                          + placeholders + ') ORDER BY qid,genre_qid,label', qids):
            result[qid]['genres'].append([genre, label])
        for qid, manifest, digest, labels in db.execute(
                'SELECT qid,manifest_path,manifest_sha256,labels_json FROM native_classifications WHERE qid IN (' + placeholders + ')', qids):
            result[qid]['native'] = {'manifest_path': manifest, 'manifest_sha256': digest, 'labels': json.loads(labels)}
    return result


def require_new_display_works(snapshot):
    for row in snapshot.values():
        if row['display_allowed'] != 1 or row['native'] is not None:
            raise ValueError('Only new display-authorized authorities may be classified')
        if not row['genres'] and row['recommendable'] != 0:
            raise ValueError('Unknown source genre must remain source recommendable=0')


def genre_counts(snapshot):
    known = sum(bool(row['genres']) for row in snapshot.values())
    return {'source_genre_known': known, 'source_genre_unknown': len(snapshot) - known}


def validate_import_observation(before, after, expected_rows, export, expected_sha):
    if set(before) != set(after):
        raise ValueError('Actual selected authority set changed during import')
    expected = {row['qid']: row['admitted_labels'] for row in expected_rows}
    if len(expected) != len(expected_rows) or not set(expected) <= set(before):
        raise ValueError('Admitted authority set differs from selected works')
    require_new_display_works(before)
    for qid, original in before.items():
        current = after[qid]
        if {k: v for k, v in original.items() if k != 'native'} != {k: v for k, v in current.items() if k != 'native'}:
            raise ValueError('Source metadata changed during native import')
        wanted = ({'manifest_path': str(Path(export).resolve()), 'manifest_sha256': expected_sha,
                   'labels': expected[qid]} if qid in expected else None)
        if current['native'] != wanted:
            raise ValueError('Actual imported manifest or labels differ from authentic projection')


def choose_slots(requested, *, unknown_count, unknown_passed):
    if type(requested) is not int or requested not in (4, 8):
        raise ValueError('Existing bounded four/eight slots required')
    return 4 if unknown_count and not unknown_passed else requested


def _replace_sql(function, before, after):
    count = 0
    for node in ast.walk(function):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and before in node.value:
            if node.value.count(before) != 1:
                raise ValueError('Exact eligibility SQL boundary differs')
            node.value = node.value.replace(before, after)
            count += 1
    if count != 1:
        raise ValueError('Exactly one fixed eligibility SQL required')


def _selected_qids(document, state):
    path = Path(document['work_mapping']).resolve(strict=True)
    if state.sha(path) != document['artifact_sha256'][str(path)]:
        raise ValueError('Selected mapping differs from authenticated export pin')
    rows = state.read(path)
    qids = [row['wikidata_qid'] for row in rows]
    if not 1 <= len(qids) <= 100 or len(set(qids)) != len(qids):
        raise ValueError('Unique actual selected mapping required')
    return qids


def load_controller():
    path = ROOT / 'scripts/run_scale20k_native_v1.py'
    if fixed_state.sha(path) != DRIVER_V1_SHA:
        raise ValueError('Fixed v1 controller recipe changed')
    tree = ast.parse(path.read_bytes())
    original_import = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'observed_import')
    _replace_sql(original_import, ' AND display_allowed=1 AND recommendable=1', ' AND display_allowed=1')
    locks = [node for node in ast.walk(original_import) if isinstance(node, ast.With) and any(
        isinstance(item.context_expr, ast.Call) and isinstance(item.context_expr.func, ast.Attribute)
        and item.context_expr.func.attr == 'writer_lock' for item in node.items)]
    if len(locks) != 1:
        raise ValueError('Exact exclusive native import boundary differs')
    locks[0].body.insert(0, ast.parse('_before_eligibility = before_import_policy(path, expected_sha, document)').body[0])
    locks[0].body.append(ast.parse('after_import_policy(path, expected_sha, document, _before_eligibility, report, value)').body[0])
    module = ModuleType('scale20k_display_content_controller_v2')
    module.__file__ = str(Path(__file__).resolve())
    exec(compile(ast.fix_missing_locations(tree), module.__file__, 'exec'), module.__dict__)
    # Isolated facade: never mutate the fixed state's module or function globals.
    state = SimpleNamespace(**fixed_state.__dict__)
    state.eligible_count = lambda: eligible_native_count(state.DATABASE)
    module.state = state
    controller_sha = state.sha(__file__)
    builder = ROOT / 'scripts' / BUILDER_NAME
    builder_sha = state.sha(builder)
    contexts = {}
    unknown_passed = False
    original_window = module.run_window

    def stable_code():
        if state.sha(__file__) != controller_sha or state.sha(builder) != builder_sha:
            raise ValueError('New eligibility controller/source entry changed during window')

    def before_import_policy(export, digest, document):
        stable_code()
        context = contexts[export.parent.name]
        selected = _selected_qids(document, state)
        if set(selected) != set(context['before_classification']):
            raise ValueError('Actual import and originally selected authority set differ')
        before = catalogue_snapshot(state.DATABASE, selected)
        require_new_display_works(before)
        if before != context['before_classification']:
            raise ValueError('Selected source metadata changed before actual import')
        return before

    def after_import_policy(export, digest, document, before, report, imported):
        stable_code()
        if imported['stage'] != 'actual_catalogue_import_complete' or imported['result']['failed']:
            raise ValueError('Actual genuine native import did not complete')
        after = catalogue_snapshot(state.DATABASE, before)
        validate_import_observation(before, after, document['verified_projection']['rows'], export, digest)
        context = contexts[export.parent.name]
        output = state.BASE / 'native-eligibility' / export.parent.name / 'policy-observation.json'
        policy = {'version': 'scale20k-native-display-eligibility-v2', 'policy': POLICY,
            'database_path': str(Path(state.DATABASE).resolve()),
            'controller_path': str(Path(__file__).resolve()), 'controller_sha256': controller_sha,
            'source_builder_path': str(builder), 'source_builder_sha256': builder_sha,
            'source_builder_observation_used': bool(context['source_policy']),
            'classification_manifest_path': str(export), 'classification_manifest_sha256': digest,
            'before_import': before, 'after_import': after, 'source_genre_counts': genre_counts(before),
            'selected': len(before), 'admitted': len(document['verified_projection']['rows']),
            'first_unknown_probe': context['first_unknown_probe'],
            'requested_concurrency': context['concurrency'],
            'metadata_mutations': 0, 'full_public_native_verifier_required': True,
            'import_report_path': str(report), 'import_report_sha256': state.sha(report),
            'pre_classification_path': str(context['pre_path']),
            'pre_classification_sha256': state.sha(context['pre_path']),
            'observed_at': datetime.now(timezone.utc).isoformat()}
        state.save(output, policy)
        context['policy_path'] = output

    module.before_import_policy = before_import_policy
    module.after_import_policy = after_import_policy

    def publication():
        original = state.ROOT / 'data/autonomous-next-v3/scale-30000-v1/content-operational-driver-v2.py'
        if state.sha(original) != state.OPERATION_SHA:
            raise ValueError('Fixed publication/control recipe changed')
        parsed = ast.parse(original.read_bytes())
        finalize = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == 'finalize')
        _replace_sql(finalize, ' AND w.display_allowed=1 AND w.recommendable=1', ' AND w.display_allowed=1')
        result = ModuleType('scale20k_display_eligible_publication')
        result.__file__ = str(original)
        exec(compile(ast.fix_missing_locations(parsed), str(original), 'exec'), result.__dict__)
        result.ROOT, result.BASE, result.DATABASE, result.LOCK = state.ROOT, state.BASE, state.DATABASE, state.WRITER_LOCK
        result.observed_import = module.observed_import
        save = result.save
        def save_control(destination, document):
            if document.get('version') == 'scale-content-window-control-v4':
                stable_code()
                context = contexts[destination.stem]
                extra = {'native_eligibility_policy': context['policy_path'],
                         'native_eligibility_controller': Path(__file__).resolve(),
                         'native_eligibility_source_builder': builder,
                         'native_eligibility_pre_classification': context['pre_path']}
                if context.get('source_policy'):
                    extra['native_eligibility_source_observation'] = context['source_policy']
                document['control_artifacts'].update({name: {'path': str(path.resolve()), 'sha256': state.sha(path)}
                                                      for name, path in extra.items()})
            save(destination, document)
        result.save = save_control
        return result

    def run_window(**kwargs):
        nonlocal unknown_passed
        stable_code()
        source = Path(kwargs['source']).resolve(strict=True)
        mapping = state.read(source / 'work-mapping.json')
        before = catalogue_snapshot(state.DATABASE, [row['wikidata_qid'] for row in mapping])
        require_new_display_works(before)
        unknown = genre_counts(before)['source_genre_unknown']
        source_policy = None
        entry = state.source_entry(kwargs['source_index'], kwargs['source_index_sha'], kwargs['global_index'])
        if unknown or entry.get('eligibility_policy_sha256'):
            source_policy = source / 'native-eligibility-policy.json'
            if state.sha(source_policy) != entry.get('eligibility_policy_sha256'):
                raise ValueError('Unknown-genre source needs independently pinned actual source preparation observation')
            policy = state.read(source_policy)
            if (policy['version'] != 'scale20k-source-display-eligibility-v2' or policy['policy'] != POLICY
                    or policy['source_manifest_sha256'] != kwargs['source_sha']
                    or policy['source_builder_sha256'] != builder_sha
                    or policy['observer_sha256'] != controller_sha or policy['catalogue_after'] != before):
                raise ValueError('Actual novel/content source preparation binding differs')
        slots = choose_slots(kwargs['concurrency'], unknown_count=unknown, unknown_passed=unknown_passed)
        name = kwargs['fixed']['chunks'][0]['run_name']
        pre_path = state.BASE / 'native-eligibility' / name / 'before-classification.json'
        state.save(pre_path, {'policy': POLICY, 'catalogue': before, 'source_manifest_sha256': kwargs['source_sha'],
            'controller_sha256': controller_sha, 'requested_concurrency': slots, 'model_started': False})
        contexts[name] = {'before_classification': before, 'concurrency': slots,
                         'first_unknown_probe': bool(unknown and not unknown_passed),
                         'pre_path': pre_path, 'source_policy': source_policy}
        kwargs['concurrency'] = slots
        control, digest, receipt = original_window(**kwargs)
        verify_policy_control(control, digest)
        if unknown:
            unknown_passed = True
        return control, digest, receipt

    module.load_publication = publication
    module.run_window = run_window
    return module


def verify_policy_control(path, expected_sha):
    from recommend_book.scale_content_control_v4 import verify_control_receipt
    receipt = verify_control_receipt(path, expected_sha)
    pins = receipt['control_artifacts']
    policy = fixed_state.read(pins['native_eligibility_policy']['path'])
    if (policy['version'] != 'scale20k-native-display-eligibility-v2' or policy['policy'] != POLICY
            or policy['controller_path'] != pins['native_eligibility_controller']['path']
            or policy['controller_sha256'] != pins['native_eligibility_controller']['sha256']
            or policy['source_builder_path'] != pins['native_eligibility_source_builder']['path']
            or policy['source_builder_sha256'] != pins['native_eligibility_source_builder']['sha256']
            or policy['classification_manifest_sha256'] != pins['export']['sha256']
            or policy['import_report_sha256'] != pins['import_report']['sha256']):
        raise ValueError('Actual eligibility policy/control binding differs')
    public = fixed_state.read(pins['export']['path'])
    pre_pin = pins['native_eligibility_pre_classification']
    pre = fixed_state.read(pre_pin['path'])
    run = fixed_state.read(pins['run']['path'])
    if (policy['pre_classification_path'] != pre_pin['path']
            or policy['pre_classification_sha256'] != pre_pin['sha256']
            or pre['catalogue'] != policy['before_import']
            or pre['controller_sha256'] != policy['controller_sha256']
            or pre['requested_concurrency'] != policy['requested_concurrency']
            or policy['selected'] != run['selected'] or len(policy['before_import']) != run['selected']
            or policy['admitted'] != len(public['verified_projection']['rows'])
            or policy['admitted'] != receipt['gate']['accepted']
            or policy['import_report_path'] != pins['import_report']['path']
            or policy['requested_concurrency'] != run['initial_concurrency']):
        raise ValueError('Actual source selection/import policy counts or observations differ')
    if 'native_eligibility_source_observation' in pins:
        source_policy = fixed_state.read(pins['native_eligibility_source_observation']['path'])
        if (source_policy['source_builder_sha256'] != policy['source_builder_sha256']
                or source_policy['observer_sha256'] != policy['controller_sha256']
                or source_policy['source_manifest_sha256'] != pre['source_manifest_sha256']
                or source_policy['catalogue_before'] != policy['before_import']
                or source_policy['catalogue_after'] != policy['before_import']):
            raise ValueError('Canonical source eligibility and import observations differ')
    elif policy['source_genre_counts']['source_genre_unknown'] or policy['source_builder_observation_used']:
        raise ValueError('Unknown source genre requires actual canonical preparation observation')
    validate_import_observation(policy['before_import'], policy['after_import'],
        public['verified_projection']['rows'], pins['export']['path'], pins['export']['sha256'])
    if policy['source_genre_counts'] != genre_counts(policy['before_import']):
        raise ValueError('Source genre count differs from unchanged actual metadata')
    if policy['first_unknown_probe'] and (policy['requested_concurrency'] != 4
            or run['initial_concurrency'] != 4 or run['final_concurrency'] != 4):
        raise ValueError('First unknown-source-genre cohort requires actual four slots')
    return receipt


if __name__ == '__main__':
    load_controller().main()
