"""Pure, positive-only scoring for the fixed five-novel French pilot.

The caller supplies the frozen WorkInputs, separately preserved source annotation
sidecar and terminal outcomes. This function does not acquire data, verify run
lineage, renew usage permission, or start a model. Production validation checks
completed predictions; the caller remains responsible for original-run lineage.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import re

from recommend_book.classification.validation import input_hash
from recommend_book.models import WorkInput


LABELS = ("adventure", "crime-fiction", "education", "fantastic")
ASSIGNMENTS = (
    ("rd1155", "crime-fiction", "Gaboriau"),
    ("rd1150", "crime-fiction", "Gaboriau"),
    ("rd1282", "education", "Sand"),
    ("rd0737", "fantastic", "Aurevilly"),
    ("rd0721", "fantastic", "Villiers"),
)
SOURCE_COMMIT = "1b91e940475c8a7e866457a3953fed5edb0a29f1"
STATES = ("completed", "held", "failed", "notexecuted")
REFERENCE_KEYS = frozenset({
    "reference_id", "work_id", "native_genre", "author_group", "source_locator", "source_sha256",
})
OUTCOME_KEYS = frozenset({
    "work_id", "expression_id", "input_hash", "status", "result", "reason",
})


def _work_id(reference_id: str) -> str:
    return f"reference:cligs-french:{reference_id}"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Nonempty {name} is required")
    return value


def _exact_keys(row: object, keys: frozenset[str], name: str) -> Mapping:
    if not isinstance(row, Mapping) or set(row) != keys:
        raise ValueError(f"Incorrect {name} fields")
    return row


def _roster(rows: Sequence, *, name: str, work_objects: bool = False) -> dict:
    expected = {_work_id(rid) for rid, _, _ in ASSIGNMENTS}
    if not isinstance(rows, (list, tuple)) or len(rows) != len(expected):
        raise ValueError(f"The fixed five-work {name} roster is required")
    by_id = {}
    for row in rows:
        if work_objects:
            if not isinstance(row, WorkInput):
                raise ValueError("Works must be validated WorkInput objects")
            wid = row.work_id
        else:
            if not isinstance(row, Mapping):
                raise ValueError(f"Incorrect {name} row")
            wid = row.get("work_id")
        if not isinstance(wid, str) or wid not in expected or wid in by_id:
            raise ValueError(f"Foreign or duplicate identity in {name}")
        by_id[wid] = row
    if set(by_id) != expected:
        raise ValueError(f"Missing identity in {name}")
    return by_id


def _positive_metric(denominator: int, detected: int) -> dict:
    return {
        "source_positive_n": denominator,
        "detected_n": detected,
        "not_detected_n": denominator - detected,
        "positive_detection_fraction": detected / denominator if denominator else None,
    }


def evaluate_french_native(
    works: Sequence[WorkInput],
    outcomes: Sequence[dict],
    *,
    reference_sidecar: Sequence[dict],
    model: str,
    cli_version: str,
) -> dict:
    """Compare actual outcomes with five fixed, separately supplied positives.

    Every planned work needs an explicit terminal outcome, including notexecuted.
    Reference rows require reference_id/work_id/native_genre/author_group and
    the original annotation source_locator/source_sha256. They are audit
    metadata, never model inputs. Source provenance is carried, not fabricated
    or authenticated by this pure calculation. Unlisted genres stay unknown.
    """
    # Keep the production validator as the sole result schema/grounding contract.
    from recommend_book.classification.native_genre_v1 import validate_native

    _text(model, "model")
    _text(cli_version, "cli_version")
    work_by_id = _roster(works, name="works", work_objects=True)
    reference_by_id = _roster(reference_sidecar, name="references")
    outcome_by_id = _roster(outcomes, name="outcomes")
    references = []
    predictions = {}
    work_states = Counter({state: 0 for state in STATES})

    for rid, genre, author_group in ASSIGNMENTS:
        wid = _work_id(rid)
        ref = _exact_keys(reference_by_id[wid], REFERENCE_KEYS, "reference")
        if (ref["reference_id"], ref["native_genre"], ref["author_group"]) != (rid, genre, author_group):
            raise ValueError("Reference differs from fixed source-positive assignments")
        locator = _text(ref["source_locator"], "source locator")
        if rid not in locator:
            raise ValueError("Source annotation locator must identify its reference row")
        if not isinstance(ref["source_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", ref["source_sha256"]):
            raise ValueError("Source annotation SHA256 is required")
        references.append(dict(ref))

        work = work_by_id[wid]
        if not work.expression_id:
            raise ValueError("Exact French input expression identity is required")
        outcome = _exact_keys(outcome_by_id[wid], OUTCOME_KEYS, "outcome")
        if (outcome["expression_id"], outcome["input_hash"]) != (work.expression_id, input_hash(work)):
            raise ValueError("Incorrect terminal outcome input identity")
        state = outcome["status"]
        if not isinstance(state, str) or state not in STATES:
            raise ValueError("Invalid terminal outcome status")
        work_states[state] += 1
        if state == "completed":
            if outcome["reason"] is not None or not isinstance(outcome["result"], Mapping):
                raise ValueError("Completed outcome requires a result and null reason")
            result = outcome["result"]
            validate_native(result, work)
            if (result["model"], result["cli_version"]) != (model, cli_version):
                raise ValueError("Incorrect native prediction model or CLI identity")
            predictions[wid] = {decision["label"]: decision["state"] for decision in result["genres"]}
        else:
            if outcome["result"] is not None:
                raise ValueError("Noncompleted outcome cannot carry an accepted prediction")
            _text(outcome["reason"], "noncompleted outcome reason")
            predictions[wid] = {label: state for label in LABELS}

    comparisons = []
    decision_states = Counter({state: 0 for state in ("present", "unknown", "held", "failed", "notexecuted")})
    label_coverage = {}
    genre_metrics = {}
    for label in LABELS:
        label_states = Counter({state: 0 for state in decision_states})
        positives = detected = 0
        for rid, source_genre, _ in ASSIGNMENTS:
            wid = _work_id(rid)
            state = predictions[wid][label]
            label_states[state] += 1
            is_annotated = source_genre == label
            if is_annotated:
                positives += 1
                detected += state == "present"
            comparisons.append({
                "reference_id": rid, "work_id": wid, "label": label,
                "reference_state": "source_positive" if is_annotated else "unannotated_unknown",
                "prediction_state": state,
                "detected": state == "present" if is_annotated else None,
            })
        genre_metrics[label] = _positive_metric(positives, detected)
        label_coverage[label] = {"planned_n": 5, **{f"{s}_n": n for s, n in label_states.items()}}
        decision_states.update(label_states)

    detected_n = sum(metric["detected_n"] for metric in genre_metrics.values())
    return {
        "version": "french-native-positive-evaluation-v1",
        "task_version": "native-french-genre-v1",
        "reference_kind": "editor_curated_native_positive_metadata",
        "source_commit": SOURCE_COMMIT,
        "model": model, "cli_version": cli_version,
        "planned_work_n": 5,
        "genres": genre_metrics,
        "micro_positive_detection": _positive_metric(5, detected_n),
        "coverage": {
            "works": {"planned_n": 5, **{f"{s}_n": work_states[s] for s in STATES}},
            "decisions": {"planned_n": 20, **{f"{s}_n": n for s, n in decision_states.items()}},
            "by_genre": label_coverage,
        },
        "comparisons": comparisons,
        "source_annotation_sidecar": references,
        "source_author_groups": dict(Counter(author for _, _, author in ASSIGNMENTS)),
        "baselines": {
            "always_present": {**_positive_metric(5, 5), "valid_evidence_backed_output": False},
            "always_unknown": {**_positive_metric(5, 0), "valid_evidence_backed_output": False},
        },
        "fresh_holdout": False,
        "quality_validated": False,
        "human_gold_created": False,
        "world72_gold": False,
        "source_provenance_verified_by_this_function": False,
        "native_run_lineage_verified_by_this_function": False,
        "unsupported_metrics": [
            "precision", "recall_over_exhaustive_labels", "F1", "closed_world_accuracy",
            "confidence_calibration", "world72_accuracy",
        ],
        "limitations": [
            "Five source positives measure corpus-label alignment and pipeline feasibility only.",
            "Detailed operational story-level genre criteria are not supplied by the source.",
            "All unannotated labels remain unknown; source genre bins are not exhaustive negatives.",
            "Gaboriau supplies two works; this is not an author-independent holdout.",
            "Source annotations concern whole novels; predictions concern the supplied original-language excerpts.",
            "Schema and exact-quote validation do not establish semantic correctness or expert adjudication.",
        ],
    }
