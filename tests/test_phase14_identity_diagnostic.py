import importlib.util
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location(
    "phase14_identity_diagnostic",
    Path(__file__).resolve().parents[1] / "scripts/phase14_identity_diagnostic.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
build_report = _MODULE.build_report


def test_phase14_diagnostic_covers_two_duplicates_and_three_replays(tmp_path):
    report = build_report(tmp_path / "diagnostic")

    assert [item["qid"] for item in report["duplicate_cases"]] == ["Q10936605", "Q105464593"]
    held = {item["qid"]: item for item in report["held_complete_input_cases"]}
    assert set(held) == {"Q848612", "Q2384134", "Q213019"}
    assert held["Q848612"]["post_patch_replay"]["repair_effect"] == "eligible_after_title_key_fix"
    assert held["Q2384134"]["post_patch_replay"]["repair_effect"] == "eligible_after_title_key_fix"
    assert held["Q213019"]["post_patch_replay"]["repair_effect"] == "held"


def test_phase14_duplicate_diagnosis_never_authorizes_public_merge(tmp_path):
    report = build_report(tmp_path / "diagnostic")

    for case in report["duplicate_cases"]:
        assert case["exact_qid_equal"] is True
        assert case["public_work_ids_are_distinct"] is True
        assert case["merge_authorized"] is False
        assert case["status"] == "held_scope_and_crosswalk_unverified"
