"""Fail-closed, opt-in label-level projection for reviewed classifications.

The normal adoption policy is whole-work: an uncertain review withholds every
feature for that work.  This module provides a deliberately separate
projection for the narrow case where an independent review names the affected
labels and leaves the remaining labels supported.  It never changes the
native result, confidence values, evidence, or quality ledger.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping, Sequence


POLICY_VERSION = "label-level-admission-v1"
LOCAL_REVIEW_REASON = "semantic_entailment_uncertain"
FORBIDDEN_REVIEW_REASONS = {
    "evidence_subject_scope_mismatch",
    "unresolved_version_scope_conflict",
}


class LabelAdmissionError(ValueError):
    """Raised when an explicit label-level projection cannot be trusted."""


def _fail(message: str) -> None:
    raise LabelAdmissionError(message)


def _unique_strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ) or len(set(value)) != len(value):
        _fail(f"{field} must be a nonempty unique string list")
    return list(value)


def _finite_probability(value: Any, field: str, *, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        _fail(f"{field} must be a finite probability")


def _validate_label(label: Mapping[str, Any], work_id: str) -> str:
    if not isinstance(label, Mapping):
        _fail("native labels must be objects")
    label_id = label.get("label_id")
    if not isinstance(label_id, str) or not label_id:
        _fail("native label identity is required")
    if label.get("decision") != "present" or label.get("scope") != "work":
        _fail(f"label {label_id} is not a present work-scoped assertion")
    evidence = label.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        _fail(f"label {label_id} has no source evidence")
    for item in evidence:
        if not isinstance(item, Mapping):
            _fail(f"label {label_id} evidence is not an object")
        for field in ("evidence_id", "locator"):
            if not isinstance(item.get(field), str) or not item[field]:
                _fail(f"label {label_id} evidence lacks {field}")
        if not any(isinstance(item.get(field), str) and item[field] for field in ("quote", "summary")):
            _fail(f"label {label_id} evidence lacks source detail")
    _finite_probability(label.get("raw_confidence"), f"label {label_id}.raw_confidence")
    _finite_probability(label.get("relevance"), f"label {label_id}.relevance", allow_none=True)
    return label_id


def _review_findings(review: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    findings = review.get("findings")
    if not isinstance(findings, list):
        _fail("review findings must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for finding in findings:
        if not isinstance(finding, Mapping):
            _fail("review finding is not an object")
        labels = _unique_strings(finding.get("label_ids"), "finding.label_ids")
        reason = finding.get("reason_code")
        if reason != LOCAL_REVIEW_REASON:
            _fail("only local semantic uncertainty can be projected")
        if not isinstance(finding.get("reason"), str) or not finding["reason"].strip():
            _fail("review finding requires an explicit reason")
        for label_id in labels:
            if label_id in result:
                _fail(f"duplicate review finding for {label_id}")
            result[label_id] = finding
    return result


def admit_label_level(
    native_result: Mapping[str, Any],
    quality_case: Mapping[str, Any],
    review: Mapping[str, Any],
    *,
    admit_label_ids: Sequence[str],
) -> dict[str, Any]:
    """Validate and return a projection for explicitly admitted labels.

    ``quality_case`` is the immutable adoption binding and ``review`` is the
    separate review output containing label-scoped findings.  The caller must
    provide the labels explicitly; omission never means "admit all".
    """
    if not isinstance(native_result, Mapping) or not isinstance(quality_case, Mapping) or not isinstance(review, Mapping):
        _fail("native result, quality case, and review must be objects")
    work_id = native_result.get("work_id")
    if not isinstance(work_id, str) or not work_id:
        _fail("native result work_id is required")
    if quality_case.get("work_id") != work_id or review.get("work_id") != work_id:
        _fail("label-level inputs must bind to one work")
    if quality_case.get("status") != "uncertain":
        _fail("label-level admission requires an uncertain quality case")
    reasons = _unique_strings(quality_case.get("reason_codes"), "quality_case.reason_codes")
    if set(reasons) != {LOCAL_REVIEW_REASON}:
        _fail("quality case contains a global or non-local hold")
    for flag in ("human_gold", "authority_merge_approved", "confidence_calibrated"):
        if quality_case.get(flag, False) is not False or review.get(flag, False) is not False:
            _fail(f"review authority flag {flag} must remain false")

    labels = native_result.get("labels")
    if not isinstance(labels, list) or not labels:
        _fail("native labels are required")
    by_id: dict[str, Mapping[str, Any]] = {}
    for label in labels:
        label_id = _validate_label(label, work_id)
        if label_id in by_id:
            _fail(f"duplicate native label {label_id}")
        by_id[label_id] = label
    native_ids = set(by_id)
    reviewed_ids = set(_unique_strings(review.get("reviewed_label_ids"), "review.reviewed_label_ids"))
    if reviewed_ids != native_ids:
        _fail("review must examine every native label exactly once")
    uncertain_ids = set(_unique_strings(quality_case.get("label_ids"), "quality_case.label_ids"))
    if not uncertain_ids or not uncertain_ids <= native_ids:
        _fail("quality case labels do not match the native result")
    findings = _review_findings(review)
    if set(findings) != uncertain_ids:
        _fail("review findings must name exactly the quality-case affected labels")
    if set(quality_case.get("reasons", [])) and len(quality_case.get("reasons", [])) != len(uncertain_ids):
        _fail("quality case reason count does not match affected labels")
    requested = _unique_strings(list(admit_label_ids), "admit_label_ids")
    requested_ids = set(requested)
    if not requested_ids <= native_ids:
        _fail("requested label is absent from the native result")
    if requested_ids & uncertain_ids:
        _fail("affected labels must remain withheld")
    if native_result.get("conflicts") not in (None, []):
        _fail("native result has a global conflict")
    # Any explicit global issue marker is fail-closed.  Current review v2 does
    # not emit these fields; absence is safe only because the quality case is
    # restricted above to the local semantic reason.
    for field in ("scope_issue", "identity_issue", "rights_issue", "version_issue", "global_issue"):
        if review.get(field) not in (None, False, [], ""):
            _fail(f"review contains global {field}")
    admitted = [deepcopy(by_id[label_id]) for label_id in requested]
    return {
        "policy_version": POLICY_VERSION,
        "work_id": work_id,
        "native_label_ids": sorted(native_ids),
        "affected_label_ids": sorted(uncertain_ids),
        "admitted_label_ids": sorted(requested_ids),
        "withheld_label_ids": sorted(native_ids - requested_ids),
        "labels": admitted,
        "review_reason_codes": reasons,
        "evidence_policy": "preserve-native-evidence-and-confidence-v1",
    }


def filter_feature_projection(
    features: Mapping[str, Any],
    feature_provenance: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Filter an already validated derivation without inventing metadata."""
    if decision.get("policy_version") != POLICY_VERSION:
        _fail("unknown label admission policy")
    admitted = set(_unique_strings(decision.get("admitted_label_ids"), "admitted_label_ids"))
    if not isinstance(features, Mapping) or not isinstance(feature_provenance, Mapping):
        _fail("feature projection must be mappings")
    if not admitted <= set(features):
        _fail("admitted label lacks an existing feature derivation")
    if any(label not in feature_provenance for label in admitted):
        _fail("admitted label lacks existing provenance")
    return (
        {label: deepcopy(features[label]) for label in sorted(admitted)},
        {label: deepcopy(feature_provenance[label]) for label in sorted(admitted)},
    )


def validate_request(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the small, explicit opt-in request consumed by the builder."""
    if not isinstance(document, Mapping) or document.get("schema_version") != "label-admission-request-v1":
        _fail("label admission request schema is unknown")
    review_path = document.get("review_path")
    review_sha256 = document.get("review_sha256")
    admission_path = document.get("review_admission_path")
    admission_sha256 = document.get("review_admission_sha256")
    quality_path = document.get("quality_review_path")
    quality_sha256 = document.get("quality_review_sha256")
    source_audit_path = document.get("source_audit_path")
    source_audit_sha256 = document.get("source_audit_sha256")
    review_final_path = document.get("review_final_path")
    review_final_sha256 = document.get("review_final_sha256")
    if (not isinstance(review_path, str) or not review_path
            or not isinstance(review_sha256, str) or len(review_sha256) != 64
            or not isinstance(admission_path, str) or not admission_path
            or not isinstance(admission_sha256, str) or len(admission_sha256) != 64):
        _fail("label admission request review binding is required")
    if (not isinstance(quality_path, str) or not quality_path
            or not isinstance(quality_sha256, str) or len(quality_sha256) != 64
            or not isinstance(source_audit_path, str) or not source_audit_path
            or not isinstance(source_audit_sha256, str) or len(source_audit_sha256) != 64
            or not isinstance(review_final_path, str) or not review_final_path
            or not isinstance(review_final_sha256, str) or len(review_final_sha256) != 64):
        _fail("label admission request quality/source-audit binding is required")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        _fail("label admission request cases are required")
    seen: set[str] = set()
    normalized = []
    for case in cases:
        if not isinstance(case, Mapping):
            _fail("label admission request case is not an object")
        work_id = case.get("work_id")
        if not isinstance(work_id, str) or not work_id or work_id in seen:
            _fail("label admission request work identities must be unique")
        seen.add(work_id)
        normalized.append({"work_id": work_id,
                           "admit_label_ids": _unique_strings(case.get("admit_label_ids"), "admit_label_ids")})
    return {"schema_version": document["schema_version"], "review_path": review_path,
            "review_sha256": review_sha256, "review_admission_path": admission_path,
            "review_admission_sha256": admission_sha256, "quality_review_path": quality_path,
            "quality_review_sha256": quality_sha256, "source_audit_path": source_audit_path,
            "source_audit_sha256": source_audit_sha256, "review_final_path": review_final_path,
            "review_final_sha256": review_final_sha256, "cases": normalized}
