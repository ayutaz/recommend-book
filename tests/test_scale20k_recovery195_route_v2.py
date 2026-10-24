from pathlib import Path

import pytest

from recommend_book import scale20k_recovery195_v2 as proof
from recommend_book import scale_content_control_v4 as control


def test_only_explicit_gap_or_normal_following_route_is_allowed(monkeypatch, tmp_path):
    p = tmp_path / "prior.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(proof, "verify_entry", lambda *args, **kwargs: {"prior_control": {"path": str(p), "sha256": "a" * 64}})
    monkeypatch.setattr(control, "verify_control_receipt", lambda *args: {"window_index": 194})
    proof.verify_resume_route(197, p, "a" * 64, p, "b" * 64)
    for route in (195, 196, 198):
        with pytest.raises(ValueError):
            proof.verify_resume_route(route, p, "a" * 64, p, "b" * 64)
    with pytest.raises(ValueError):
        proof.verify_resume_route(197, p, "c" * 64, p, "b" * 64)
    monkeypatch.setattr(control, "verify_control_receipt", lambda *args: {"window_index": 197})
    proof.verify_resume_route(198, p, "a" * 64, p, "b" * 64)


def test_gap_never_skips_entry_authentication(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise ValueError("missing actual quarantine witness")
    monkeypatch.setattr(proof, "verify_entry", fail)
    with pytest.raises(ValueError, match="missing actual quarantine witness"):
        proof.verify_resume_route(197, tmp_path, "a" * 64, tmp_path, "b" * 64)
