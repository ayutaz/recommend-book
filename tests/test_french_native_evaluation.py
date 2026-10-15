import copy

import pytest

from recommend_book.classification.validation import input_hash
from recommend_book.models import Evidence, WorkInput
from recommend_book.recommendation.french_native_evaluation import evaluate_french_native


LABELS = ("adventure", "crime-fiction", "education", "fantastic")
ASSIGNMENTS = (
    ("rd1155", "crime-fiction", "Gaboriau"),
    ("rd1150", "crime-fiction", "Gaboriau"),
    ("rd1282", "education", "Sand"),
    ("rd0737", "fantastic", "Aurevilly"),
    ("rd0721", "fantastic", "Villiers"),
)


def cohort():
    works, references, outcomes = [], [], []
    for rid, genre, author in ASSIGNMENTS:
        wid = f"reference:cligs-french:{rid}"
        expression = f"cligs-french:{rid}:test-edition"
        work = WorkInput(
            work_id=wid, expression_id=expression, title="", original_language="fr",
            evidence=[Evidence(
                evidence_id=f"excerpt:{rid}", kind="excerpt", text="Une phrase française.",
                source_url="https://example.invalid/test", rights_policy_id="test-only",
                llm_input_allowed=True, language="fr", locator="body:0:23",
                expression_ref=expression,
            )],
        )
        works.append(work)
        references.append({
            "reference_id": rid, "work_id": wid, "native_genre": genre,
            "author_group": author, "source_locator": f"metadata.csv:idno={rid}:subgenre",
            "source_sha256": "a" * 64,
        })
        result = {
            "work_id": wid, "expression_id": expression, "input_hash": input_hash(work),
            "model": "test-model", "cli_version": "test-cli",
            "prompt_version": "1.0-native-french-genre", "task_version": "native-french-genre-v1",
            "genres": [{"label": label, "state": "unknown", "evidence": [],
                        "unknown_reason": "Insufficient supplied evidence."} for label in LABELS],
        }
        outcomes.append({
            "work_id": wid, "expression_id": expression, "input_hash": input_hash(work),
            "status": "completed", "result": result, "reason": None,
        })
    return works, references, outcomes


def evaluate(works, refs, outcomes):
    return evaluate_french_native(
        works, outcomes, reference_sidecar=refs, model="test-model", cli_version="test-cli",
    )


def present(work, outcome, label):
    decision = next(d for d in outcome["result"]["genres"] if d["label"] == label)
    decision.update(state="present", unknown_reason=None, evidence=[{
        "evidence_id": work.evidence[0].evidence_id, "locator": work.evidence[0].locator,
        "quote": "Une phrase française.",
    }])


def test_planned_denominators_and_unannotated_unknowns_survive_partial_execution():
    works, refs, outcomes = cohort()
    present(works[0], outcomes[0], "crime-fiction")
    # A predicted genre without a source annotation is not a false positive.
    present(works[0], outcomes[0], "adventure")
    for outcome, state in zip(outcomes[2:], ("held", "failed", "notexecuted")):
        outcome.update(status=state, result=None, reason=f"Test {state}")
    before = copy.deepcopy((works, refs, outcomes))
    report = evaluate(works, refs, outcomes)
    assert report["genres"]["crime-fiction"] == {
        "source_positive_n": 2, "detected_n": 1, "not_detected_n": 1,
        "positive_detection_fraction": 0.5,
    }
    assert report["genres"]["education"]["source_positive_n"] == 1
    assert report["genres"]["fantastic"]["source_positive_n"] == 2
    assert report["genres"]["adventure"]["positive_detection_fraction"] is None
    assert report["micro_positive_detection"] == {
        "source_positive_n": 5, "detected_n": 1, "not_detected_n": 4,
        "positive_detection_fraction": 0.2,
    }
    assert report["coverage"]["decisions"] == {
        "planned_n": 20, "present_n": 2, "unknown_n": 6,
        "held_n": 4, "failed_n": 4, "notexecuted_n": 4,
    }
    unannotated = next(row for row in report["comparisons"]
                       if row["work_id"] == works[0].work_id and row["label"] == "adventure")
    assert unannotated["reference_state"] == "unannotated_unknown"
    assert unannotated["detected"] is None
    assert report["source_author_groups"] == {"Gaboriau": 2, "Sand": 1, "Aurevilly": 1, "Villiers": 1}
    assert report["fresh_holdout"] is report["quality_validated"] is False
    assert (works, refs, outcomes) == before


def test_always_present_and_unknown_baselines_do_not_grant_quality_pass():
    works, refs, outcomes = cohort()
    unknown = evaluate(works, refs, outcomes)
    assert unknown["micro_positive_detection"]["detected_n"] == 0
    for work, outcome in zip(works, outcomes):
        for label in LABELS:
            present(work, outcome, label)
    report = evaluate(works, refs, outcomes)
    assert report["micro_positive_detection"]["positive_detection_fraction"] == 1
    assert report["baselines"]["always_present"]["detected_n"] == 5
    assert report["baselines"]["always_unknown"]["detected_n"] == 0
    assert report["baselines"]["always_present"]["valid_evidence_backed_output"] is False
    assert report["quality_validated"] is False
    assert report["unsupported_metrics"] == [
        "precision", "recall_over_exhaustive_labels", "F1", "closed_world_accuracy",
        "confidence_calibration", "world72_accuracy",
    ]


def test_no_execution_remains_five_misses_with_twenty_unexecuted_decisions():
    works, refs, outcomes = cohort()
    for outcome in outcomes:
        outcome.update(status="notexecuted", result=None, reason="Planned run did not start.")
    report = evaluate(works, refs, outcomes)
    assert report["micro_positive_detection"] == {
        "source_positive_n": 5, "detected_n": 0, "not_detected_n": 5,
        "positive_detection_fraction": 0.0,
    }
    assert report["coverage"]["decisions"]["notexecuted_n"] == 20
    assert report["coverage"]["works"]["completed_n"] == 0
    assert report["quality_validated"] is False


@pytest.mark.parametrize("target", ["works", "references", "outcomes"])
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "foreign"])
def test_exact_five_identity_rosters_are_required(target, mutation):
    works, refs, outcomes = cohort()
    sequence = {"works": works, "references": refs, "outcomes": outcomes}[target]
    if mutation == "missing":
        sequence.pop()
    elif mutation == "duplicate":
        sequence[-1] = copy.deepcopy(sequence[0])
    elif target == "works":
        sequence[-1] = sequence[-1].model_copy(update={"work_id": "foreign-work"})
    else:
        sequence[-1]["work_id"] = "foreign-work"
    with pytest.raises(ValueError):
        evaluate(works, refs, outcomes)


@pytest.mark.parametrize("mutation", [
    "outcome_hash", "outcome_expression", "result_work", "result_hash", "result_expression", "model", "cli",
    "duplicate_label", "missing_label", "world72_label", "confidence", "decision_extra",
    "result_extra", "outcome_extra", "quote", "evidence", "present_without_evidence",
    "unknown_with_evidence", "unknown_without_reason", "held_with_result", "failed_without_reason",
    "false_status", "reference_genre", "reference_author", "reference_extra", "reference_hash",
])
def test_bad_identity_predictions_and_reference_mutation_are_rejected(mutation):
    works, refs, outcomes = cohort()
    outcome = outcomes[0]
    result = outcome["result"]
    if mutation == "outcome_hash": outcome["input_hash"] = "f" * 64
    elif mutation == "outcome_expression": outcome["expression_id"] = "foreign"
    elif mutation == "result_work": result["work_id"] = "foreign-work"
    elif mutation == "result_hash": result["input_hash"] = "f" * 64
    elif mutation == "result_expression": result["expression_id"] = "foreign"
    elif mutation == "model": result["model"] = "foreign"
    elif mutation == "cli": result["cli_version"] = "foreign"
    elif mutation == "duplicate_label": result["genres"][-1]["label"] = "adventure"
    elif mutation == "missing_label": result["genres"].pop()
    elif mutation == "world72_label": result["genres"][0]["label"] = "genre.adventure"
    elif mutation == "confidence": result["genres"][0]["confidence"] = 0.9
    elif mutation == "decision_extra": result["genres"][0]["unsafe"] = "extra"
    elif mutation == "result_extra": result["unsafe"] = "extra"
    elif mutation == "outcome_extra": outcome["unsafe"] = "extra"
    elif mutation == "quote":
        present(works[0], outcome, "crime-fiction")
        result["genres"][1]["evidence"][0]["quote"] = "Invented quote"
    elif mutation == "evidence":
        present(works[0], outcome, "crime-fiction")
        result["genres"][1]["evidence"][0]["evidence_id"] = works[1].evidence[0].evidence_id
    elif mutation == "present_without_evidence":
        result["genres"][0].update(state="present", unknown_reason=None)
    elif mutation == "unknown_with_evidence":
        present(works[0], outcome, "crime-fiction")
        result["genres"][1]["state"] = "unknown"
    elif mutation == "unknown_without_reason": result["genres"][0]["unknown_reason"] = ""
    elif mutation == "held_with_result": outcome.update(status="held", reason="Held")
    elif mutation == "failed_without_reason": outcome.update(status="failed", result=None)
    elif mutation == "false_status": outcome["status"] = False
    elif mutation == "reference_genre": refs[0]["native_genre"] = "education"
    elif mutation == "reference_author": refs[1]["author_group"] = "different-author"
    elif mutation == "reference_extra": refs[0]["confidence"] = 0.9
    elif mutation == "reference_hash": refs[0]["source_sha256"] = "not-a-sha"
    with pytest.raises(ValueError):
        evaluate(works, refs, outcomes)
