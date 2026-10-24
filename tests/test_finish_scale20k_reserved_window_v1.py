import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def test_continuation_reads_real_native_export_before_any_import_or_execution(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/finish_scale20k_reserved_window_v1.py"
    spec = importlib.util.spec_from_file_location("_final_tail_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    controller = ModuleType("isolated_controller")
    class FirstRead(Exception):
        pass
    reads = []
    def read(path):
        reads.append(path)
        raise FirstRead
    controller.state = SimpleNamespace(BASE=tmp_path, read=read)
    controller.Path = Path
    controller.observed_command = lambda *a, **k: pytest.fail("No execution before authentic native export")
    tail = module.build_tail(controller)
    fixed = {"chunks": [{"run_name": "fresh"}]}
    with pytest.raises(FirstRead):
        tail(source=None, source_sha=None, global_index=212, fixed=fixed,
             inventory_path=None, inventory_sha=None, previous_control=None,
             previous_sha=None, concurrency=4, publication=None)
    assert reads == [tmp_path / "classification-exports/fresh/manifest.json"]
