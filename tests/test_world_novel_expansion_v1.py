"""Offline expansion boundaries; these tests never start Codex or touch live DBs."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from recommend_book.models import WorkInput
from recommend_book.usage_guard import model_hash

ROOT = Path(__file__).resolve().parents[1]


def runner():
    spec = importlib.util.spec_from_file_location("world_expansion_test", ROOT / "scripts/run_world_novel_expansion_v1.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def source_fixture(tmp_path, module):
    base = tmp_path / "expansion"
    source = base / "sources/0000"
    source.mkdir(parents=True)
    inputs, ledger, database = source / "inputs.jsonl", source / "ledger.json", source / "source.sqlite"
    work = WorkInput(work_id="w1", title="Novel", source_record_ids=["s1"])
    inputs.write_text(work.model_dump_json() + "\n", encoding="utf-8")
    write(ledger, {"fixture_only": True})
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE works(work_id TEXT,source_version_id TEXT)")
        db.execute("CREATE TABLE source_records(version_id TEXT,source TEXT,source_id TEXT)")
        db.execute("INSERT INTO works VALUES('w1','s1')")
        db.execute("INSERT INTO source_records VALUES('s1','wikidata','Q123')")
    mapping = [{"work_id": "w1", "input_work_id": "w1", "wikidata_qid": "Q123",
                "catalogue_work_id": "wd:Q123", "input_sha256": model_hash(work)}]
    write(source / "work-mapping.json", mapping)
    write(source / "manifest.json", {"version": "scale-content-classification-source-v1",
        "window_index": 0, "selected": 1, "required_purpose_admissions_allowed": True,
        "source_database": str(database), "source_database_sha256": module.sha(database),
        "chunks": [{"run_name": module.run_name(0), "inputs_path": str(inputs), "inputs_sha256": module.sha(inputs),
                    "usage_ledger_path": str(ledger), "usage_ledger_sha256": module.sha(ledger)}]})
    excluded = base / "excluded.json"
    write(excluded, {"qids": ["Q1"], "input_hashes": ["a" * 64]})
    index = base / "source-index.json"
    write(index, {"version": module.INDEX_VERSION, "exclusions": {"path": str(excluded), "sha256": module.sha(excluded)},
        "windows": [{"window_index": 0, "source_path": str(source), "source_sha256": module.sha(source / "manifest.json"),
                     "mapping_sha256": module.sha(source / "work-mapping.json")}]})
    return base, index, source


def test_source_seal_and_actual_identity_are_checked(tmp_path):
    module = runner()
    base, index, source = source_fixture(tmp_path, module)
    windows = module.load_sources(index, module.sha(index), base=base)
    assert windows[0]["qids"] == ["Q123"]
    write(source / "work-mapping.json", [])
    with pytest.raises(ValueError, match="mapping"):
        module.load_sources(index, module.sha(index), base=base)


@pytest.mark.parametrize("kind", ["qid", "hash", "purpose", "input"])
def test_old_or_unadmitted_or_changed_input_is_rejected(tmp_path, kind):
    module = runner()
    base, index, source = source_fixture(tmp_path, module)
    document = module.read(index)
    if kind in ("qid", "hash"):
        excluded = Path(document["exclusions"]["path"])
        write(excluded, {"qids": ["Q123"] if kind == "qid" else [], "input_hashes":
                        [model_hash(WorkInput(work_id="w1", title="Novel", source_record_ids=["s1"]))] if kind == "hash" else []})
        document["exclusions"]["sha256"] = module.sha(excluded)
    elif kind == "purpose":
        manifest = module.read(source / "manifest.json")
        manifest["required_purpose_admissions_allowed"] = False
        write(source / "manifest.json", manifest)
        document["windows"][0]["source_sha256"] = module.sha(source / "manifest.json")
    else:
        (source / "inputs.jsonl").write_text("changed", encoding="utf-8")
    write(index, document)
    with pytest.raises(ValueError):
        module.load_sources(index, module.sha(index), base=base)


def test_recipe_retains_one_work_10m_and_review_v6(tmp_path):
    module = runner()
    recipe = module.load_coordinator(tmp_path, lambda *_: [])
    assert recipe.EXPANSION_RUNTIME == tmp_path
    assert "10000000" in recipe._expansion_source
    assert "'batch_size': 1" in recipe._expansion_source
    assert "review_scale_content_v6" in recipe._expansion_source
    assert "full=True" in recipe._expansion_source.replace(" ", "")


def test_recipe_invokes_fresh_reservation_before_touching_old_state(tmp_path):
    module = runner()
    called = []
    def reserve(source, number):
        called.append((source, number))
        raise ValueError("fresh reservation denied")
    recipe = module.load_coordinator(tmp_path, reserve)
    with pytest.raises(ValueError, match="fresh reservation denied"):
        recipe.execute_window(tmp_path, tmp_path, 0)
    assert called == [(tmp_path.resolve(), 0)]


def test_gate_counts_admission_failures_and_current_display(monkeypatch):
    module = runner()
    counts = {"actual_display_feature_works": 70, "actual_content_non_genre_feature_works": 1}
    monkeypatch.setattr(module, "display_counts", lambda _: counts)
    public = {"gate": {"passed": True}, "verified_projection": {"rows": [{"qid": f"Q{i}"} for i in range(95)]}}
    run = {"selected": 100, "failed": 0, "pending": 0}
    assert module.export_gate(public, run)["passed"]
    public["verified_projection"]["rows"].pop()
    assert not module.export_gate(public, run)["passed"]


def test_failed_preflight_never_calls_model_and_same_attempt_never_retries(tmp_path, monkeypatch):
    module = runner()
    base, index, source = source_fixture(tmp_path, module)
    called = []
    def fail(*args, **kwargs):
        called.append("preflight")
        raise ValueError("fixture preflight denial")
    monkeypatch.setattr(module, "prepare_window", fail)
    monkeypatch.setattr(module, "execute_window", lambda *args, **kwargs: called.append("model"))
    with pytest.raises(ValueError, match="preflight denial"):
        module.run_one(index, module.sha(index), tmp_path, 0, base=base)
    with pytest.raises(FileExistsError):
        module.run_one(index, module.sha(index), tmp_path, 0, base=base)
    assert called == ["preflight"]


def test_initial_eight_slots_cannot_borrow_an_old_control(tmp_path):
    module = runner()
    base, index, _ = source_fixture(tmp_path, module)
    with pytest.raises(ValueError, match="Initial expansion"):
        module.run_one(index, module.sha(index), tmp_path, 0, concurrency=8, base=base)


def test_publication_tail_loading_never_calls_import(tmp_path, monkeypatch):
    module = runner()
    # A clean checkout has no private operational artifacts. Test the loader
    # boundary with an independently pinned synthetic operation instead.
    operation = tmp_path / 'data/autonomous-next-v3/scale-30000-v1/content-operational-driver-v2.py'
    operation.parent.mkdir(parents=True)
    operation.write_text('def finalize(*args):\n    return {}\n', encoding='utf-8')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module.fixed, 'OPERATION_SHA', module.sha(operation))
    called = []
    tail = module.load_publication_tail(tmp_path / "not-opened.sqlite", lambda *args: called.append(args), base=tmp_path)
    assert tail.BASE == tmp_path
    assert tail.DATABASE == tmp_path / "not-opened.sqlite"
    assert callable(tail.finalize)
    assert called == []
    assert not (tmp_path / "not-opened.sqlite").exists()


def test_duplicate_windows_fail_before_any_prepare(tmp_path):
    module = runner()
    base, index, _ = source_fixture(tmp_path, module)
    document = module.read(index)
    document["windows"].append(document["windows"][0])
    write(index, document)
    with pytest.raises(ValueError, match="indices"):
        module.load_sources(index, module.sha(index), base=base)
