import importlib.util
from pathlib import Path
from types import SimpleNamespace


def test_missing_recipe_constants_and_worker_entry_are_bound_without_file_edits():
    path = Path(__file__).resolve().parents[1] / "scripts/run_scale20k_final_continuation_v3.py"
    spec = importlib.util.spec_from_file_location("_final_entry3_test", path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    adapter = entry.load_adapter()
    from recommend_book import scale20k_final_native_v1 as state
    from recommend_book import scale20k_native_v3 as legacy
    assert state.OPERATION_SHA == legacy.OPERATION_SHA
    calls = []
    controller = SimpleNamespace(run=lambda *a: None,
        observed_command=lambda argv, directory, **kw: calls.append((argv, kw)))
    adapter.configure_controller(controller)
    controller.observed_command(["python", str(entry.PARENT), "--worker"], "unused")
    assert Path(calls[0][0][1]) == path
    assert calls[0][1]["request_fields"]["final_driver_entry_sha256"] == entry.sha(path)
