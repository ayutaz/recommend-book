import os
from pathlib import Path
import subprocess
import sys


def test_child_preparer_loads_with_frozen_package_without_later_state(tmp_path):
    root = Path(__file__).resolve().parents[1]
    package = tmp_path / 'src/recommend_book'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    program = (
        'import sys; sys.path.insert(0,sys.argv[1]); '
        'from prepare_world_novel_chunk_v1 import frozen_prepare,prepare; '
        'assert callable(frozen_prepare) and callable(prepare); print("loaded")'
    )
    result = subprocess.run(
        [sys.executable, '-c', program, str(root / 'scripts')], cwd=root,
        env={**os.environ, 'PYTHONPATH': str(tmp_path / 'src')},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'loaded'
