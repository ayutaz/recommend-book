"""V20 authority, exclusive attempts and one adapter over fixed successful recipes.

No historical model result is imported or rebound by this module. Source flags
are a seal prerequisite; the unchanged mandatory preflight is the AI admission.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import ModuleType
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'data/autonomous-next-v3/scale-20000-classifications-v1'
RUNTIME = ROOT / 'data/autonomous-next-v3/preparation/scale30k-content-runtime-v1'
DATABASE = ROOT / 'data/autonomous-next-v3/scale-v1/catalogue.sqlite'
WRITER_LOCK = ROOT / 'data/autonomous-next-v3/catalogue-writer.lock'
REQUIREMENTS_SHA = '04b48c43e6a1ca31b018de00ec45aefc9fd2123913a9331e66c48419bed6f9c9'
BASELINE_SHA = '5734eda48467012f4c257a57027e655cb829ba309730a5dc2cce2a01c47ddf36'
WORKS_BASELINE_SHA = '252b219b7306e871711cd78bb825edbce8d1d9d41bad914409360b7734d49165'
PLAN_SHA = '11a86adaa9ae2f91e03f278a190a82a68f7d45651bee7678fe3f2189699dc594'
PRIOR_CONTROL = ROOT / 'data/autonomous-next-v3/scale-100000-v1/window-controls/scale100k-content-5001-5100-v8.json'
PRIOR_CONTROL_SHA = '1ddaf5320834349f53be5c064a71b94575c7fca13e5a16a32f00e530dfa13344'
PREPARER_SHA = 'd7805efa0598830be77a228dbfb76ce63ae12d1fcadf037d2bb42ac50643b9b0'
COORDINATOR_SHA = 'a9e045dc57d67ea4a700ee6af213c9cce196ea20ce41e97302c297a77e6f9928'
REVIEW_SHA = 'f9b0f7538a7282b6781f54adbb03d8f7cec9324547978f54ac17619ee374ef20'
TERMINAL_SHA = '7f2bad82dd3110a939413331d967e4c253c7714583a619cd28427621f0b7bfe6'
OPERATION_SHA = '783226f2d3d8e0934ad611261801911f7a1b17b25cf51006d4276e3820f2f1c8'


class SourceSupplyExhausted(ValueError):
    """No further sealed window in this independently fixed handoff."""


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, allow_nan=False)
        stream.write('\n')


def verify_requirements():
    path = BASE / 'requirements.json'
    if (sha(path) != REQUIREMENTS_SHA
            or sha(BASE / 'baseline-native-10000.jsonl') != BASELINE_SHA
            or sha(BASE / 'baseline-works-100372.jsonl') != WORKS_BASELINE_SHA):
        raise ValueError('Frozen v20 requirements or baseline bytes changed')
    value = read(path)
    constraints = value['constraints']
    if (value['version'] != 'scale20k-v20-requirements-v1'
            or value['plan_sha256'] != PLAN_SHA or sha(ROOT / value['plan'].replace('\\', '/')) != PLAN_SHA
            or value['baseline']['native'] != 10000
            or value['requirements']['2']['eligible_unique_native_sol_works_minimum'] != 20000
            or value['requirements']['2']['new_accepted_works_minimum'] != 10000
            or constraints['max_parallel_cli'] != 8 or constraints['max_works_per_model_call'] != 5
            or constraints['failure_rate_max'] != .05 or constraints['display_adoption_min'] != .7
            or constraints['same_failed_input_auto_retry'] != 0):
        raise ValueError('Frozen v20 plan or constraints differ')
    return value


def load_inventory(path, expected_sha):
    if not expected_sha or sha(path) != expected_sha:
        raise ValueError('Independent v20 inventory bytes differ')
    value = read(path)
    if (value['version'] != 'scale20k-native-exclusions-v1'
            or value['requirements_sha256'] != REQUIREMENTS_SHA
            or value['baseline_native_sha256'] != BASELINE_SHA
            or len(set(value['qids'])) != len(value['qids'])
            or len(set(value['input_hashes'])) != len(value['input_hashes'])):
        raise ValueError('V20 inventory authority differs')
    return value


def validate_baseline_membership(inventory):
    """One startup census, not a replay of historical native proofs."""
    rows = [json.loads(line) for line in (BASE / 'baseline-native-10000.jsonl').read_bytes().splitlines() if line.strip()]
    qids = {row['qid'] for row in rows}
    if len(rows) != 10000 or len(qids) != 10000 or not qids <= set(inventory['qids']):
        raise ValueError('Inventory must exclude all 10000 distinct baseline native authorities')


def source_entry(path, expected_sha, global_index):
    if sha(path) != expected_sha:
        raise ValueError('Independent source index bytes differ')
    value = read(path)
    if (value['version'] != 'scale20k-source-index-v1'
            or value['requirements_sha256'] != REQUIREMENTS_SHA):
        raise ValueError('V20 source index authority differs')
    entries = value['windows']
    indices = [row['window_index'] for row in entries]
    if (any(type(index) is not int or index < 93 for index in indices)
            or len(set(indices)) != len(indices)):
        raise ValueError('Unique integer source window indices required')
    for row in entries:
        if row['window_index'] == global_index:
            return row
    raise SourceSupplyExhausted('Next window not in fixed source index; require a new explicit handoff')


def eligible_count():
    with sqlite3.connect(DATABASE.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        return db.execute('SELECT count(*) FROM native_classifications n JOIN works w USING(qid) '
                          'WHERE w.display_allowed=1 AND w.recommendable=1').fetchone()[0]


def process_created_at(pid):
    """Actual Windows process creation time; unknown is recorded as unknown."""
    if type(pid) is not int or pid <= 0 or os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in values)):
            return None
        ticks = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
        return datetime.fromtimestamp(ticks / 10000000 - 11644473600, timezone.utc).isoformat()
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def writer_lock(*, purpose, timeout=900, poll_seconds=2):
    deadline, token = time.monotonic() + timeout, uuid4().hex
    WRITER_LOCK.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            handle = os.open(WRITER_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise FileExistsError('Bounded writer wait expired; existing owner retained')
            # Observe PID birth for diagnosis only; never infer permission to unlink.
            try:
                owner = read(WRITER_LOCK)
                process_created_at(owner.get('pid') if isinstance(owner, dict) else None)
            except (OSError, ValueError):
                pass
            time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))
    owner = {'pid': os.getpid(), 'process_created_at': process_created_at(os.getpid()),
             'started_at': datetime.now(timezone.utc).isoformat(), 'purpose': purpose, 'token': token}
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(owner, stream)
        yield
    finally:
        try:
            if read(WRITER_LOCK) == owner:
                WRITER_LOCK.unlink()
        except FileNotFoundError:
            pass


def current_source(source, global_index, expected_sha, inventory):
    from recommend_book.models import WorkInput
    from recommend_book.usage_guard import model_hash
    from recommend_book.scale_content_recovery_v1 import _verify_mapping_identity
    source = Path(source).resolve(strict=True)
    if not source.is_relative_to(BASE.resolve()) or sha(source / 'manifest.json') != expected_sha:
        raise ValueError('Exact v20 source namespace and manifest pin required')
    fixed = read(source / 'manifest.json')
    if (type(global_index) is not int or global_index < 93
            or fixed['version'] != 'scale-content-classification-source-v1'
            or fixed['window_index'] != global_index
            or fixed.get('required_purpose_admissions_allowed') is not True
            or len(fixed['chunks']) != 1):
        raise ValueError('One exact usage-admitted v20 source seal required')
    chunk = fixed['chunks'][0]
    for key in ('inputs_path', 'usage_ledger_path'):
        if not Path(chunk[key]).resolve(strict=True).is_relative_to(BASE.resolve()):
            raise ValueError('New inputs and ledger must stay in v20 namespace')
    database = Path(fixed['source_database']).resolve(strict=True)
    if not database.is_relative_to(BASE.resolve()):
        refs = {(str(Path(row['path']).resolve(strict=True)), row['sha256'])
                for row in inventory.get('source_database_refs', [])}
        if (str(database), fixed['source_database_sha256']) not in refs:
            raise ValueError('Unpinned external source database reference')
    if (sha(chunk['inputs_path']) != chunk['inputs_sha256']
            or sha(chunk['usage_ledger_path']) != chunk['usage_ledger_sha256']
            or sha(database) != fixed['source_database_sha256']):
        raise ValueError('Actual v20 source bytes differ from sealed hashes')
    works = [WorkInput.model_validate_json(line) for line in Path(chunk['inputs_path']).read_bytes().splitlines() if line.strip()]
    mapping = read(source / 'work-mapping.json')
    _verify_mapping_identity(database, mapping, works)
    hashes, qids = {model_hash(work) for work in works}, {row['wikidata_qid'] for row in mapping}
    expected_name = canonical_run_name(global_index)
    if (type(fixed['selected']) is not int or not 1 <= fixed['selected'] <= 100
            or chunk['run_name'] != expected_name or len(works) != fixed['selected']
            or len(hashes) != len(works) or len(qids) != len(works)):
        raise ValueError('Unique actual selected works and canonical v20 run name required')
    return fixed, hashes, qids


def canonical_run_name(global_index):
    if type(global_index) is not int or global_index < 93:
        raise ValueError('V20 global window must be an integer at least 93')
    cohort = global_index - 93
    return f'scale20k-content-{cohort*100+1:04d}-{cohort*100+100:04d}-v1'


def code_paths():
    """Actual new/fixed recipes only; no collector, UI or mutable documents."""
    return [Path(__file__).resolve(), *[ROOT / 'scripts' / name for name in (
        'run_scale20k_native_v1.py', 'prepare_scale20k_content_chunk_v1.py',
        'run_scale20k_content_classification_v1.py',
        'prepare_scale100k_content_chunk_v1.py', 'prepare_scale100k_content_chunk_v2.py',
        'prepare_scale_content_chunk_v1.py', 'prepare_scale_content_chunk_v4.py',
        'prepare_scale_classification_v1.py', 'native_snapshot_terminal_v1.py',
        'run_scale_content_classification_v6.py', 'run_scale100k_content_classification_v1.py',
        'run_scale100k_content_classification_v2.py', 'run_scale100k_content_classification_v3.py',
        'run_scale100k_content_classification_v4.py', 'run_scale100k_content_classification_v5.py',
        'review_scale_content_v6.py')],
        ROOT / 'data/autonomous-next-v3/scale-30000-v1/content-operational-driver-v2.py']


def code_snapshot():
    return {str(path): sha(path) for path in code_paths()}


def verify_code_snapshot(snapshot):
    for path, expected in snapshot.items():
        if sha(path) != expected:
            raise ValueError('Actual classification/guard recipe changed during window: ' + path)


def reserve_window(source, global_index, source_sha, inventory_path, inventory_sha,
                   source_index_path, source_index_sha):
    inventory = load_inventory(inventory_path, inventory_sha)
    entry = source_entry(source_index_path, source_index_sha, global_index)
    if Path(entry['source_path']).resolve(strict=True) != Path(source).resolve(strict=True) or entry['source_sha256'] != source_sha:
        raise ValueError('Selected window differs from independently fixed source index')
    fixed, hashes, qids = current_source(source, global_index, source_sha, inventory)
    name = fixed['chunks'][0]['run_name']
    for path in (RUNTIME / ('terminal-' + name), RUNTIME / ('preflight.' + name + '.json'),
                 RUNTIME / 'runtime/data/autonomous-next-v3/classification' / name):
        if path.exists():
            raise ValueError('Already prepared or attempted window; no automatic resume')
    path = BASE / 'attempt-reservations' / (name + '.json')
    value = {'version': 'scale20k-native-attempt-reservation-v1', 'global_index': global_index,
             'run_name': name, 'source_path': str(Path(source).resolve()), 'source_sha256': source_sha,
             'inventory_path': str(Path(inventory_path).resolve()), 'inventory_sha256': inventory_sha,
             'source_index_path': str(Path(source_index_path).resolve()), 'source_index_sha256': source_index_sha,
             'input_hashes': sorted(hashes), 'qids': sorted(qids), 'actual_overlap_count': 0,
             'helper_sha256': sha(__file__), 'requirements_sha256': REQUIREMENTS_SHA, 'batch_size': 1}
    with writer_lock(purpose='v20_native_attempt_reservation'):
        old_hashes, old_qids = set(inventory['input_hashes']), set(inventory['qids'])
        for old in (BASE / 'attempt-reservations').glob('*.json'):
            receipt = read(old)
            old_hashes.update(receipt['input_hashes']); old_qids.update(receipt['qids'])
        if hashes & old_hashes or qids & old_qids:
            raise ValueError('Already attempted input or authority; preserve old failure/hold')
        with sqlite3.connect(DATABASE.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
            if db.execute('SELECT count(*) FROM native_classifications WHERE qid IN ('
                          + ','.join('?' for _ in qids) + ')', sorted(qids)).fetchone()[0]:
                raise ValueError('Existing native authority cannot be replaced')
        save(path, value)
    return path


def verify_reservation(source, global_index):
    if sha(BASE / 'requirements.json') != REQUIREMENTS_SHA:
        raise ValueError('Frozen v20 requirements changed before native launch')
    fixed = read(Path(source) / 'manifest.json')
    path = BASE / 'attempt-reservations' / (fixed['chunks'][0]['run_name'] + '.json')
    value = read(path)
    inventory = load_inventory(value['inventory_path'], value['inventory_sha256'])
    entry = source_entry(value['source_index_path'], value['source_index_sha256'], global_index)
    fixed, hashes, qids = current_source(source, global_index, value['source_sha256'], inventory)
    if (value['version'] != 'scale20k-native-attempt-reservation-v1'
            or value['global_index'] != global_index or type(value['global_index']) is not int
            or value['source_path'] != str(Path(source).resolve())
            or value['run_name'] != fixed['chunks'][0]['run_name']
            or value['input_hashes'] != sorted(hashes) or value['qids'] != sorted(qids)
            or type(value['actual_overlap_count']) is not int or value['actual_overlap_count'] != 0
            or value['batch_size'] != 1 or value['helper_sha256'] != sha(__file__)
            or value['requirements_sha256'] != REQUIREMENTS_SHA
            or Path(entry['source_path']).resolve(strict=True) != Path(source).resolve(strict=True)
            or entry['source_sha256'] != value['source_sha256']
            or (RUNTIME / ('terminal-' + value['run_name'])).exists()):
        raise ValueError('Exact unstarted v20 reservation binding differs')
    return [path, Path(value['inventory_path']), Path(value['source_index_path']), BASE / 'requirements.json',
            BASE / 'baseline-native-10000.jsonl', *code_paths()]


def instrument_terminal_source(text):
    """Add an actual birth observation to the unchanged terminal proof recipe."""
    if sha(ROOT / 'scripts/native_snapshot_terminal_v1.py') != TERMINAL_SHA:
        raise ValueError('Fixed native terminal observer changed')
    tree, found = ast.parse(text), 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == 'started_at' for k in node.keys):
            pid = next((v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and k.value == 'pid'), None)
            if pid is not None:
                node.keys.append(ast.Constant('process_created_at'))
                node.values.append(ast.Call(ast.Name('process_created_at', ast.Load()), [pid], []))
                found += 1
    if found != 1:
        raise ValueError('Exact native birth observation boundary differs')
    tree.body.insert(0, ast.ImportFrom('recommend_book.scale20k_native_v1',
                                      [ast.alias('process_created_at')], 0))
    return ast.unparse(ast.fix_missing_locations(tree))


def load_recipe(kind, owner_path):
    """Expand one SHA-fixed successful recipe, then parameterize its AST once.

    The old adapters remain immutable dependencies. This adapter has no driver
    generation substitution ladder and changes no labels, validators or prompts.
    """
    if kind not in ('preparer', 'coordinator'):
        raise ValueError('Unknown fixed recipe')
    filename, parent, expected = (
        ('prepare_scale100k_content_chunk_v2.py', 'prepare_scale100k_content_chunk_v1.py', PREPARER_SHA)
        if kind == 'preparer' else
        ('run_scale100k_content_classification_v5.py', 'run_scale100k_content_classification_v4.py', COORDINATOR_SHA))
    path = ROOT / 'scripts' / filename
    if sha(path) != expected:
        raise ValueError('Successful immutable recipe bytes changed')
    raw = path.read_text(encoding='utf-8').replace('\r\n', '\n')
    boundary = '\nexec(compile(adapted_source('
    if raw.count(boundary) != 1:
        raise ValueError('Exact successful recipe expansion boundary differs')
    result = ModuleType('scale20k_' + kind)
    result.__file__ = str(Path(owner_path).resolve())
    exec(compile(raw.split(boundary)[0], str(path), 'exec'), result.__dict__)
    source = result.adapted_source((ROOT / 'scripts' / parent).read_bytes())
    tree = ast.parse(source)
    counts = {'namespace': 0, 'state_import': 0, 'preparer_import': 0, 'old_pins': 0, 'terminal': 0, 'stage_pins': 0}

    class Configure(ast.NodeTransformer):
        def visit_Constant(self, node):
            if isinstance(node.value, str) and node.value.startswith('data/autonomous-next-v3/scale-100000-v1/'):
                node.value = node.value.replace('data/autonomous-next-v3/scale-100000-v1/',
                                               'data/autonomous-next-v3/scale-20000-classifications-v1/', 1)
                counts['namespace'] += 1
            return node

        def visit_ImportFrom(self, node):
            if node.module == 'recommend_book.scale100k_native_v1':
                node.module = 'recommend_book.scale20k_native_v1'; counts['state_import'] += 1
            elif node.module == 'prepare_scale100k_content_chunk_v2':
                node.module = 'prepare_scale20k_content_chunk_v1'; counts['preparer_import'] += 1
            return node

        def visit_Expr(self, node):
            call = node.value
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name) and call.func.value.id == 'v19_pins'
                    and call.func.attr == 'extend'):
                counts['old_pins'] += 1
                return None  # New state.verify_reservation supplies the actual dependencies.
            return self.generic_visit(node)

        def visit_Call(self, node):
            self.generic_visit(node)
            if (isinstance(node.func, ast.Name) and node.func.id == 'compile'
                    and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == 'terminal_source'):
                node.args[0] = ast.Call(ast.Name('instrument_terminal_source', ast.Load()), [node.args[0]], [])
                counts['terminal'] += 1
            return node

        def visit_Assign(self, node):
            self.generic_visit(node)
            if any(isinstance(target, ast.Name) and target.id == 'pins' for target in node.targets):
                if not isinstance(node.value, ast.List):
                    raise ValueError('Exact export dependency list differs')
                node.value.elts.append(ast.Starred(ast.Call(ast.Name('stage_process_pins', ast.Load()), [], []), ast.Load()))
                counts['stage_pins'] += 1
            return node

    tree = Configure().visit(tree)
    expected_counts = ({'namespace': 0, 'state_import': 0, 'preparer_import': 0, 'old_pins': 0, 'terminal': 0, 'stage_pins': 0}
                       if kind == 'preparer' else
                       {'namespace': 3, 'state_import': 1, 'preparer_import': 1, 'old_pins': 1, 'terminal': 1, 'stage_pins': 1})
    if counts != expected_counts:
        raise ValueError('V20 fixed recipe parameter boundaries differ: ' + str(counts))
    # The preparer's generated child code is a literal program, not an import AST.
    if kind == 'preparer':
        matches = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and 'from prepare_scale100k_content_chunk_v2 import frozen_prepare;' in node.value:
                node.value = node.value.replace('from prepare_scale100k_content_chunk_v2 import frozen_prepare;',
                                                'from prepare_scale20k_content_chunk_v1 import frozen_prepare;')
                matches += 1
        if matches != 1:
            raise ValueError('Actual frozen preparer child import differs')
    result.instrument_terminal_source = instrument_terminal_source
    result.process_created_at = process_created_at
    result.REVIEW_SHA = REVIEW_SHA
    exec(compile(ast.fix_missing_locations(tree), result.__file__, 'exec'), result.__dict__)
    return result
