"""Run the unchanged final auditor with four independent proof readers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time


@contextmanager
def parallel_manifest_verifier(pairs, verify, *, workers=4):
    pairs = list(pairs)
    if workers not in (1, 2, 4) or not pairs or len(set(pairs)) != len(pairs):
        raise ValueError('Unique proof bindings and at most four readers required')
    lock = threading.Lock()
    completed = [0]
    def observed(path, expected):
        started = time.perf_counter()
        rows = verify(path, expected)
        with lock:
            completed[0] += 1
            print(json.dumps({'event': 'actual_manifest_verified', 'n': completed[0],
                'run': Path(path).parent.name, 'rows': len(rows),
                'elapsed_seconds': time.perf_counter() - started}), flush=True)
        return rows
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {pair: executor.submit(observed, *pair) for pair in pairs}
        def consume(path, expected):
            key = (str(Path(path).resolve(strict=True)), expected)
            if key not in futures:
                raise ValueError('Auditor requested an unplanned or repeated proof')
            return futures.pop(key).result()
        try:
            yield consume
        finally:
            for future in futures.values():
                future.cancel()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    source = root / 'scripts/audit_scale20k_catalogue_v4.py'
    spec = importlib.util.spec_from_file_location('parallel_final_audit', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = module.DATABASE
    if '--database' in argv:
        database = Path(argv[argv.index('--database') + 1]).resolve(strict=True)
    with sqlite3.connect(database.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        pairs = [(str(Path(path).resolve(strict=True)), digest) for path, digest in db.execute(
            'SELECT DISTINCT manifest_path,manifest_sha256 FROM native_classifications '
            'ORDER BY manifest_path,manifest_sha256')]
    original = module._current_manifest_verifier
    scopes = []
    def factory(*args, **kwargs):
        scope = parallel_manifest_verifier(pairs, original(*args, **kwargs))
        scopes.append(scope)
        return scope.__enter__()
    module._current_manifest_verifier = factory
    prior = sys.argv
    sys.argv = [str(source), *argv]
    try:
        module.main()
    finally:
        sys.argv = prior
        for scope in reversed(scopes):
            scope.__exit__(None, None, None)


if __name__ == '__main__':
    main()
