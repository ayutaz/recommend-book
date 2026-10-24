import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]

def load():
    spec = importlib.util.spec_from_file_location("final_chunk_v5_test", ROOT / "scripts/prepare_scale_content_chunk_v5.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_new_cli_context_is_passed_to_the_complete_preparer(monkeypatch):
    module = load()
    from recommend_book import scale_guard_context_v1
    # This test calls a standalone-child boundary inside pytest. Its process
    # lifetime cache must not replace UsageGuard's JSON decoder for later tests.
    monkeypatch.setattr(scale_guard_context_v1, "enable_process_cache", lambda: None)
    calls = []
    def prepare_chunk(*args, **kwargs):
        calls.append((args, kwargs)); return "prepared"
    monkeypatch.setattr(module.recipe, "load_bounded_preparer", lambda: SimpleNamespace(prepare_chunk=prepare_chunk))
    assert module.frozen_prepare("runtime", "inputs", "ledger", "fresh", 4) == "prepared"
    assert calls == [(("runtime", "inputs", "ledger"), {
        "cli_version": "codex-cli 0.164.0", "run_name": "fresh", "concurrency": 4})]

def test_eight_slots_still_require_a_verified_clean_prior(monkeypatch):
    module = load()
    def reject(*args): raise ValueError("unclean prior")
    monkeypatch.setattr(module.recipe, "trial_control", reject)
    with pytest.raises(ValueError, match="unclean prior"):
        module.prepare("not-used", "not-used", "not-used", "fresh", 8,
                       prior_control=Path("prior.json"), prior_control_sha256="bad")
