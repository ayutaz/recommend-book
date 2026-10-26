"""Exercise Python's real cold module locks, outside pytest's warm imports."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize('series_version', [
    'light-novel-series-feature-export-v1', 'light-novel-series-feature-export-v2',
])
def test_cold_mixed_export_dispatch_initializes_package_without_deadlock(tmp_path, series_version):
    code = textwrap.dedent(r'''
        from concurrent.futures import ThreadPoolExecutor
        import hashlib
        import json
        from pathlib import Path
        import socket
        import subprocess
        import sys
        import threading

        root = Path(sys.argv[1])
        series_version = sys.argv[2]
        def forbidden(*args, **kwargs):
            raise AssertionError('No network, model, or archived subprocess allowed')
        socket.create_connection = forbidden
        socket.socket.connect = forbidden
        subprocess.Popen = forbidden
        assert 'recommend_book.classification' not in sys.modules
        from recommend_book.light_novel_feature_exports_v1 import verify_feature_export
        assert 'recommend_book.classification' not in sys.modules

        def save(name, value):
            path = root / name
            raw = json.dumps(value).encode()
            path.write_bytes(raw)
            return path, hashlib.sha256(raw).hexdigest()
        unknown = save('unknown.json', {'version': 'unknown'})
        for args, message in [(unknown, 'Unknown LN feature export recipe'),
                              ((unknown[0], '0' * 64), 'LN feature export manifest SHA differs')]:
            try:
                verify_feature_export(*args)
            except ValueError as exc:
                assert str(exc) == message, repr(exc)
            else:
                raise AssertionError('Invalid dispatcher input accepted')
        assert 'recommend_book.classification' not in sys.modules

        # Both real verifiers reject at their own authenticated boundary before
        # any archive replay or classification. Nothing about imports is mocked.
        runtime = root / 'runtime'
        runtime.mkdir()
        (runtime / 'light-novel-runtime-snapshot.json').write_text('{}')
        series = save('series.json', {'version': series_version,
            'runtime_root': str(runtime), 'runtime_manifest_sha256': '0' * 64})
        archive = save('archive.json', {
            'version': 'light-novel-work-archived-label-feature-export-v1',
            'context': 'forged_context'})
        # The separately tested external closure is not this import regression's
        # subject. Import its lightweight module without warming classification.
        from recommend_book import light_novel_work_archive_preflight_v1 as preflight
        audit_calls = []
        audit_lock = threading.Lock()
        audit = {'version': preflight.VERSION, 'bridge_manifest_sha256': archive[1],
                 'artifact_sha256': {}, 'cache_allowed': False, 'full_replay_required': True}
        def fixture_preflight(path, expected_sha256):
            assert (path, expected_sha256) == archive
            with audit_lock:
                audit_calls.append(threading.get_ident())
            return dict(audit)
        preflight.check_preflight = fixture_preflight
        assert 'recommend_book.classification' not in sys.modules
        assert 'recommend_book.light_novel_work_archive_bridge_v1' not in sys.modules
        assert audit_calls == []
        barrier = threading.Barrier(8)
        def invoke(index):
            barrier.wait(timeout=15)
            arguments = series if index % 2 == 0 else archive
            expected = ('Series immutable runtime manifest differs' if index % 2 == 0
                        else 'Unknown archived bridge version or historical context')
            try:
                verify_feature_export(*arguments)
            except ValueError as exc:
                assert str(exc) == expected, repr(exc)
                return expected
            raise AssertionError('Real verifier did not run')
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(invoke, range(8)))
        assert len(results) == 8
        # Four archive calls each pass preflight once, then the real validator
        # rejects context. Its failure must prevent the post-success audit.
        assert len(audit_calls) == 4
        for name in ('recommend_book.classification',
                     'recommend_book.classification.runner',
                     'recommend_book.light_novel_classification_v1',
                     'recommend_book.light_novel_work_archive_bridge_v1'):
            module = sys.modules[name]
            assert not module.__spec__._initializing, name
        print('cold mixed dispatch: eight real validators completed')
    ''')
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / 'src'), PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path), series_version],
        cwd=root, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'eight real validators completed' in result.stdout
