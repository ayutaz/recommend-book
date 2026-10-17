"""Focused tests for the opt-in label-level admission policy."""
import importlib

import pytest


policy = importlib.import_module("recommend_book.label_admission_v1")


def native(*, conflict=None):
    return {
        "work_id": "work-example",
        "conflicts": [] if conflict is None else [conflict],
        "labels": [
            {
                "label_id": "genre.fantasy",
                "axis": "genre",
                "decision": "present",
                "scope": "work",
                "raw_confidence": 0.86,
                "relevance": 0.75,
                "evidence": [{
                    "evidence_id": "se:example:description:abc",
                    "locator": "/opf:package/opf:metadata/dc:description",
                    "quote": "An otherworldly adventure.",
                }],
            },
            {
                "label_id": "structure.journey",
                "axis": "structure",
                "decision": "present",
                "scope": "work",
                "raw_confidence": 0.99,
                "relevance": 1.0,
                "evidence": [{
                    "evidence_id": "se:example:description:abc",
                    "locator": "/opf:package/opf:metadata/dc:description",
                    "summary": "The protagonist travels through successive lands.",
                }],
            },
        ],
    }


def bindings(*, reason_code="semantic_entailment_uncertain", finding_labels=None):
    affected = finding_labels or ["genre.fantasy"]
    case = {
        "work_id": "work-example",
        "status": "uncertain",
        "reason_codes": [reason_code],
        "reasons": ["One label is semantically uncertain."],
        "label_ids": affected,
        "human_gold": False,
        "authority_merge_approved": False,
        "confidence_calibrated": False,
    }
    review = {
        "work_id": "work-example",
        "reviewed_label_ids": ["genre.fantasy", "structure.journey"],
        "findings": [{
            "label_ids": affected,
            "reason_code": reason_code,
            "reason": "The review cannot establish the affected taxonomy label.",
        }],
        "human_gold": False,
        "authority_merge_approved": False,
        "confidence_calibrated": False,
    }
    return case, review


def test_explicit_opt_in_admits_supported_label_and_withholds_affected_label():
    case, review = bindings()
    decision = policy.admit_label_level(native(), case, review,
                                        admit_label_ids=["structure.journey"])
    assert decision["admitted_label_ids"] == ["structure.journey"]
    assert decision["affected_label_ids"] == ["genre.fantasy"]
    assert decision["withheld_label_ids"] == ["genre.fantasy"]
    assert decision["labels"][0]["raw_confidence"] == 0.99


def test_omitting_explicit_labels_fails_closed():
    case, review = bindings()
    with pytest.raises(policy.LabelAdmissionError, match="admit_label_ids"):
        policy.admit_label_level(native(), case, review, admit_label_ids=[])


@pytest.mark.parametrize("change, message", [
    ("scope", "not a present work-scoped"),
    ("finding", "affected labels must remain withheld"),
    ("reason", "global or non-local hold"),
    ("conflict", "global conflict"),
])
def test_global_or_affected_uncertainty_cannot_be_bypassed(change, message):
    case, review = bindings()
    result = native(conflict="authority") if change == "conflict" else native()
    if change == "scope":
        result["labels"][1]["scope"] = "edition"
    elif change == "finding":
        case["label_ids"] = ["structure.journey"]
        review["findings"][0]["label_ids"] = ["structure.journey"]
    elif change == "reason":
        case["reason_codes"] = ["unresolved_version_scope_conflict"]
        review["findings"][0]["reason_code"] = "unresolved_version_scope_conflict"
    with pytest.raises(policy.LabelAdmissionError, match=message):
        policy.admit_label_level(result, case, review,
                                 admit_label_ids=["structure.journey"])


def test_filter_preserves_existing_strength_and_provenance_only():
    decision = {"policy_version": policy.POLICY_VERSION,
                "admitted_label_ids": ["structure.journey"]}
    features, provenance = policy.filter_feature_projection(
        {"genre.fantasy": 0.75, "structure.journey": 1.0},
        {"structure.journey": [{"raw_confidence": 0.99}]}, decision)
    assert features == {"structure.journey": 1.0}
    assert provenance == {"structure.journey": [{"raw_confidence": 0.99}]}


def test_request_requires_a_review_binding_and_explicit_cases():
    request = {"schema_version": "label-admission-request-v1",
               "review_path": "C:/review.final.json", "review_sha256": "a" * 64,
               "review_admission_path": "C:/review-admission.json", "review_admission_sha256": "b" * 64,
               "quality_review_path": "C:/quality-adoption-review.json", "quality_review_sha256": "c" * 64,
               "source_audit_path": "C:/source-audit.json", "source_audit_sha256": "d" * 64,
               "review_final_path": "C:/review.final.json", "review_final_sha256": "e" * 64,
               "cases": [{"work_id": "work-example", "admit_label_ids": ["structure.journey"]}]}
    assert policy.validate_request(request)["cases"][0]["work_id"] == "work-example"
    with pytest.raises(policy.LabelAdmissionError, match="review binding"):
        policy.validate_request({**request, "review_sha256": "bad"})
