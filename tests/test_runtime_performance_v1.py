"""Bounded runtime-cache contracts for the authenticated LN catalogue."""

import hashlib
import json

from recommend_book import light_novel_feature_exports_v1 as exports
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue


def _manifest(tmp_path):
    manifest = tmp_path / "feature-manifest.json"
    witness = tmp_path / "feature-witness.json"
    witness.write_text('{"source":"original"}', encoding="utf-8")
    witness_sha = hashlib.sha256(witness.read_bytes()).hexdigest()
    payload = {
        "version": "fixture-feature-export-v1",
        "rows": [{"entry_id": "lns:wd:Q1", "scope": "series"}],
        "artifact_sha256": {str(witness): witness_sha},
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest, witness, hashlib.sha256(manifest.read_bytes()).hexdigest()


def _catalogue(tmp_path):
    cat = LightNovelCatalogue(tmp_path / "catalogue.sqlite")
    with cat._connect() as db:
        db.execute(
            "INSERT INTO entries VALUES(?,?,?,?)",
            ("lns:wd:Q1", "series", "{}", hashlib.sha256(b"{}").hexdigest()),
        )
    return cat


def test_feature_export_cache_reuses_verified_result_and_preserves_projection(
    tmp_path, monkeypatch
):
    manifest, witness, manifest_sha = _manifest(tmp_path)
    cat = _catalogue(tmp_path)
    calls = []

    def verify(path, expected):
        calls.append((path, expected))
        return json.loads(manifest.read_text(encoding="utf-8"))

    monkeypatch.setattr(exports, "verify_feature_export", verify)
    with cat._connect() as db:
        db.execute(
            "INSERT INTO features VALUES(?,?,?)",
            ("lns:wd:Q1", str(manifest), manifest_sha),
        )

    request_cache = {}
    first = cat._feature_rows_request({"lns:wd:Q1"}, request_cache)
    second = cat._feature_rows_request({"lns:wd:Q1"}, request_cache)

    assert first == second
    assert len(calls) == 1


def test_feature_export_cache_drops_mutated_source_without_reusing_stale_rows(
    tmp_path, monkeypatch
):
    manifest, witness, manifest_sha = _manifest(tmp_path)
    cat = _catalogue(tmp_path)
    monkeypatch.setattr(
        exports,
        "verify_feature_export",
        lambda path, expected: json.loads(manifest.read_text(encoding="utf-8")),
    )
    with cat._connect() as db:
        db.execute(
            "INSERT INTO features VALUES(?,?,?)",
            ("lns:wd:Q1", str(manifest), manifest_sha),
        )

    request_cache = {}
    assert cat._feature_rows_request({"lns:wd:Q1"}, request_cache)["lns:wd:Q1"]["entry_id"] == "lns:wd:Q1"
    witness.write_text('{"source":"tampered"}', encoding="utf-8")

    # The cache re-check observes the changed pinned bytes and fails closed by
    # withholding the stale feature projection.
    assert cat._feature_rows_request({"lns:wd:Q1"}, request_cache) == {}


def test_feature_export_without_dependency_closure_is_never_cached(tmp_path, monkeypatch):
    manifest, _, manifest_sha = _manifest(tmp_path)
    cat = _catalogue(tmp_path)
    calls = []

    def verify(path, expected):
        calls.append(1)
        return {"version": "fixture-feature-export-v1", "rows": []}

    monkeypatch.setattr(exports, "verify_feature_export", verify)
    with cat._connect() as db:
        db.execute(
            "INSERT INTO features VALUES(?,?,?)",
            ("lns:wd:Q1", str(manifest), manifest_sha),
        )

    cat._feature_rows_request({"lns:wd:Q1"}, {})
    cat._feature_rows_request({"lns:wd:Q1"}, {})

    # An export that omits its artifact SHA map cannot be reused.
    assert len(calls) == 2
