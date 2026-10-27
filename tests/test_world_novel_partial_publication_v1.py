"""Contract tests for the read-only reviewed partial projection."""

import hashlib
import json

import pytest

from recommend_book.world_novel_partial_publication_v1 import (
    PARTIAL_EXPORT_VERSION,
    PartialPublicationError,
    _partial_contract,
    canonical_json,
    value_sha,
)


def _partial(tmp_path):
    artifact = tmp_path / "immutable.json"
    artifact.write_text('{"frozen":true}\n', encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    return {
        "version": PARTIAL_EXPORT_VERSION,
        "state": "partial_recovery_projection",
        "public_db_adoption_allowed": False,
        "recovery": {
            "source_terminal": False,
            "adopted_completed": 1,
            "protected_artifact_sha256": {str(artifact): digest},
        },
        "verified_projection": {
            "publishable": False,
            "coordinator_chain_pins": [{"path": str(artifact), "sha256": digest}],
            "rows": [{"qid": "Q1", "work_id": "wd:Q1", "admitted_labels": []}],
        },
        "gate": {"passed": False},
    }


def test_partial_contract_requires_nonpublic_source(tmp_path):
    partial = _partial(tmp_path)
    _partial_contract(partial)
    partial["public_db_adoption_allowed"] = True
    with pytest.raises(PartialPublicationError, match="nonpublic"):
        _partial_contract(partial)


def test_partial_contract_requires_coordinator_chain(tmp_path):
    partial = _partial(tmp_path)
    partial["verified_projection"]["coordinator_chain_pins"] = []
    with pytest.raises(PartialPublicationError, match="Coordinator"):
        _partial_contract(partial)


def test_value_sha_is_canonical_and_deterministic():
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert value_sha({"a": 1, "b": 2}) == value_sha({"b": 2, "a": 1})
