"""Read existing selected inventory/receipts and benchmark a future hash primitive.

No model, acquisition, database creation, native verifier or adoption is invoked.
"""
import hashlib
import argparse
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

from recommend_book.classification.native_artifact_snapshot_v1 import verify_command_receipts


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/autonomous-next-v3"
OUT = BASE / "evaluation/phase6-scaling-readiness-v1"
DB = BASE / "database/final-delivery-main-v1/classified-database/dataset.sqlite"
RUN = BASE / "classification/phase3-cached-synopsis-expansion-with-features-v1/run"


def year_group(year):
    if year is None:
        return "unknown"
    for maximum, name in [(1899, "before_1900"), (1945, "1900_1945"),
                          (1989, "1946_1989"), (2009, "1990_2009")]:
        if year <= maximum:
            return name
    return "2010_or_later"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    output = parser.parse_args().output.resolve()
    # Exclusive namespace is reserved before any measurement; never overwrite.
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    source_reads = []
    def load(path):
        binary = path.read_bytes()
        source_reads.append({"path": str(path.relative_to(ROOT)), "bytes": len(binary),
                             "sha256": hashlib.sha256(binary).hexdigest()})
        return json.loads(binary)

    manifest = load(RUN / "manifest.json")
    review = load(BASE / "evaluation/phase3-cached-synopsis-expansion-review-v1/report.json")
    current = load(BASE / "database/phase3-personal-poc-with-declared-genres-v2/manifest.json")
    adoption = load(BASE / "database/phase3-personal-poc-with-expansion-v1/verification.json")
    overlay = load(BASE / "database/phase3-personal-poc-with-expansion-v1/manifest.json")
    if source_reads[-1]["sha256"] != adoption["observed_artifact_sha256"]["manifest.json"]:
        raise ValueError("Settled downstream overlay manifest differs from observed adoption proof")
    settled = next(item for item in overlay["audit"]["supplements"]
                   if item["supplement"] == "phase3-cached-synopsis-expansion-supplement-v1")
    adopted = settled["reviewed_feature_bearing_n"]
    assert adopted == adoption["new_reviewed_feature_work_gain_n"]
    before = DB.stat()
    database_started = time.perf_counter()
    connection = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    rows = list(connection.execute("""SELECT w.work_id,w.work_kind,p.input_json
        FROM next_book_inventory b JOIN works w USING(work_id)
        JOIN next_projection_inputs p USING(work_id)
        WHERE b.eligible=1 AND p.kind='bibliographic' ORDER BY w.work_id"""))
    excluded = connection.execute("SELECT count(*) FROM next_book_inventory WHERE eligible=0").fetchone()[0]
    genre_rows = list(connection.execute("""SELECT DISTINCT i.work_id,l.label_id
        FROM next_classification_inputs i JOIN next_classification_labels l USING(job_key)
        JOIN next_book_inventory b USING(work_id)
        WHERE b.eligible=1 AND l.axis='genre' AND l.decision='present'"""))
    classifier_n = connection.execute("SELECT count(*) FROM next_projection_inputs WHERE kind='classification'").fetchone()[0]
    subject_assertions = list(connection.execute("SELECT source_version_id,value_json FROM field_assertions WHERE field_name='subjects'"))
    subject_links = list(connection.execute("""SELECT DISTINCT p.work_id,s.source_version_id
        FROM next_projection_inputs p JOIN next_source_refs s
        ON s.kind=p.kind AND s.input_hash=p.input_hash WHERE p.kind='bibliographic'"""))
    connection.close()
    database_seconds = time.perf_counter() - database_started
    after = DB.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    genre_by_work = {}
    for work, genre in genre_rows:
        genre_by_work.setdefault(work, set()).add(genre)
    subjects_by_work = {}
    by_source = {}
    for source, value in subject_assertions:
        by_source.setdefault(source, set()).update(json.loads(value))
    for work, source in subject_links:
        subjects_by_work.setdefault(work, set()).update(by_source.get(source, set()))

    def inventory(selected):
        counters = {key: Counter() for key in ["kind", "country", "language", "year", "raw_present_genre", "declared_subject"]}
        missing = Counter()
        for work, kind, binary in selected:
            data = json.loads(binary)
            counters["kind"][kind or "unknown"] += 1
            for key, value in [("country", data.get("region")), ("language", data.get("original_language"))]:
                values = value if isinstance(value, list) else [value] if value else []
                if not values:
                    missing[key] += 1
                counters[key].update(set(values))
            counters["year"][year_group(data.get("first_publication_year"))] += 1
            if data.get("first_publication_year") is None:
                missing["year"] += 1
            genres = genre_by_work.get(work, set())
            counters["raw_present_genre"].update(genres)
            if not genres:
                missing["raw_present_genre"] += 1
            subjects = subjects_by_work.get(work, set())
            counters["declared_subject"].update(subjects)
            if not subjects:
                missing["declared_subject"] += 1
        n = len(selected)
        return {"works": n, "counts": {key: dict(sorted(value.items())) for key, value in counters.items()},
                "missing": {key: {"n": missing[key], "fraction": missing[key] / n} for key in
                            ["country", "language", "year", "raw_present_genre", "declared_subject"]}}

    pool = inventory(rows)
    novels = inventory([row for row in rows if row[1] in {"novel", "light_novel"}])
    # Receipt from disk is the expected digest, not a digest invented by this run.
    command = manifest["execution_context"]["version_observation"]["argv"][:-1]
    expected = manifest["execution_context"]["executable_sha256"]
    paths = [Path(command[0])] + ([Path(command[1])] if len(command) > 1 else [])
    hash_started = time.perf_counter()
    for _ in range(manifest["cli_invocations"]):
        observed = {str(path.resolve(strict=True)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        if observed != expected:
            raise ValueError("Current executable differs from saved receipt; benchmark must not bypass this")
    repeated_seconds = time.perf_counter() - hash_started
    hash_started = time.perf_counter()
    snapshot = verify_command_receipts(command, [expected] * manifest["cli_invocations"])
    snapshot_seconds = time.perf_counter() - hash_started

    artifact_started = time.perf_counter()
    attempt_stats = []
    read_bytes = 0
    per_attempt_usage = []
    for path in sorted((RUN / "attempts").glob("*.protocol-attempt.json")):
        attempt = load(path)
        per_attempt_usage.append(attempt["usage"])
        stem = str(path)[:-len(".protocol-attempt.json")]
        files = [Path(stem + suffix) for suffix in [".stdin.utf8", ".request.json", ".stdout.jsonl", ".stderr.txt", ".final.json"]]
        item = {"attempt": path.name, "seconds": attempt["seconds"], "exit_code": attempt["actual_exit_code"],
                "job_count": len(attempt["job_keys"]), "artifact_bytes": 0, "artifact_files": 0}
        for artifact in files:
            if artifact.exists():
                binary = artifact.read_bytes()
                read_bytes += len(binary)
                item["artifact_bytes"] += len(binary)
                item["artifact_files"] += 1
        attempt_stats.append(item)
    artifact_seconds = time.perf_counter() - artifact_started
    run_files = [p for p in RUN.rglob("*") if p.is_file()]
    storage = sum(p.stat().st_size for p in run_files)
    # Review decisions are reused exactly; no semantic review is re-executed.
    target_composition = {}
    for target in [10000, 30000, 100000]:
        ln_fraction = 0.15 if target == 10000 else 0.20
        kind_targets = {"novel": int(target * (1 - ln_fraction)), "light_novel": int(target * ln_fraction)}
        def scaled(fractions):
            return {key: round(target * fraction) for key, fraction in fractions.items()}
        target_composition[str(target)] = {
            "kind": kind_targets,
            "kind_gap_from_current_upper_pool": {key: max(0, count - novels["counts"]["kind"].get(key, 0)) for key, count in kind_targets.items()},
            "language": scaled({"ja": 0.35, "en": 0.25, "other_known_languages": 0.40}),
            "country_region": scaled({"Japan": 0.30, "English_primary_regions": 0.25,
                "continental_Europe": 0.20, "East_Asia_excluding_Japan": 0.10, "other_known_regions": 0.15}),
            "year": scaled({"before_1900": 0.20, "1900_1945": 0.15, "1946_1989": 0.25, "1990_2009": 0.20, "2010_or_later": 0.20}),
            "genre_overlapping_minima": scaled({"genre.literary": 0.20, "genre.mystery": 0.15,
                "genre.fantasy": 0.15, "genre.science_fiction": 0.10, "genre.historical": 0.10,
                "genre.romance": 0.10, "genre.horror": 0.05, "genre.adventure": 0.05}),
            "counting": "planning quotas only; overlapping genre/country marginal counts are not summed; unknown/unconfirmed kind/edition/short-story/series excluded",
            "missingness_ceiling": {"country": 0.05, "language": 0.02, "year": 0.10},
            "unknown_policy": "missingness above ceiling stops promotion; unknown is retained and cannot satisfy known-stratum quota"}
    report = {
        "version": "scaling-readiness-v1", "decision": "NO_GO_10000_bulk_processing",
        "decision_scope": "Readiness decision only; no 10000-work acquisition/classification/adoption launched",
        "new_model_calls": 0, "new_expert_gold": 0, "new_user_work": 0, "new_paid_spend_yen": 0,
        "external_contacts": 0, "free_codex_remaining_quota": "unknown_not_verified",
        "eligible_inventory": pool, "kind_eligible_novel_and_light_novel_inventory": novels,
        "target_composition": target_composition,
        "excluded_inventory_rows": excluded, "classification_input_rows": classifier_n,
        "inventory_limitations": ["Bibliographic eligibility is not independent identity precision or full-purpose authorization",
            "Country is source region (multivalue), not author citizenship; counts overlap",
            "Genre counts are raw present outputs, include unreviewed/uncertain labels, and are not adopted gold",
            "Missing genre means no observed present genre; it does not mean absent",
            "Declared subjects are literal source metadata terms, not normalized genre or independent gold"],
        "database_observation": {"path": str(DB.relative_to(ROOT)), "bytes": before.st_size,
            "read_only": True, "stat_unchanged": True, "full_database_hashed": False,
            "selected_sql_seconds": database_seconds, "full_database_regenerated": False},
        "historical_58_work_run": {key: manifest[key] for key in ["selected", "completed", "failed", "cli_invocations", "elapsed_seconds", "usage", "initial_concurrency", "final_concurrency"]},
        "historical_adoption": {"works": adopted, "denominator": 58, "fraction": adopted / 58,
            "source": "data/autonomous-next-v3/database/phase3-personal-poc-with-expansion-v1/verification.json and manifest.json (observed manifest SHA matched)",
            "independent_accuracy": "unevaluated", "review_work_status": review["work_status_counts"],
            "seconds_per_adopted_model_stage_only": manifest["elapsed_seconds"] / adopted,
            "input_tokens_per_adopted": manifest["usage"]["input_tokens"] / adopted,
            "output_tokens_per_adopted": manifest["usage"]["output_tokens"] / adopted,
            "historical_model_stage_adopted_per_hour": adopted * 3600 / manifest["elapsed_seconds"]},
        "attempt_measurements": attempt_stats,
        "current_selected_saved_artifact_read": {"bytes": read_bytes, "seconds": artifact_seconds,
            "meaning": "Read selected saved artifacts once; not rerun, semantic review, proof approval or write throughput"},
        "storage": {"run_directory_bytes": storage, "run_file_count": len(run_files),
            "run_bytes_per_selected": storage / 58, "run_bytes_per_adopted": storage / adopted,
            "main_database_bytes": before.st_size,
            "omitted": ["raw sources outside run", "shared ledger", "supplement DB", "review/artifact duplication", "future indexes and backups"]},
        "hash_benchmark": {"saved_receipt_matched_current_bytes": True, "repetitions": manifest["cli_invocations"],
            "repeated_seconds": repeated_seconds, "snapshot_seconds": snapshot_seconds,
            "repeated_content_reads": len(paths) * manifest["cli_invocations"],
            "repeated_bytes_read_and_hashed": sum(p.stat().st_size for p in paths) * manifest["cli_invocations"],
            "snapshot": snapshot, "order": "repeated then snapshot; warm filesystem cache; single observation",
            "meaning": "Executable primitive only; frozen v3 verifier/runner not changed or bypassed; no native result adopted"},
        "future_gates": ["Acquire and authorize at least 10000 distinct novel/light-novel candidates: current upper pool 1353; gap 8647",
            "Freeze future batch recipe integrating snapshot and keep original v3 verifier bytes untouched",
            "Confirm available free Codex quota; unknown quota is not unlimited capacity",
            "Measure acquisition, per-purpose rights, identity/scope, review, adoption, display and recovery on new bounded <=100-work sample",
            "Pass per-label and identity quality only where matched independent gold exists; other strata remain unevaluated",
            "Complete time and disk bounds including raw, derivatives and backup; historical model/run storage omit them"],
        "throughput_policy": {"concurrency": 2, "works_per_call_maximum": 5, "calls_per_bounded_resume_maximum": 20,
            "selected_works_per_resume_maximum": 100, "retries_per_identical_failed_input": 0,
            "paid_fallback": False, "automatic_10000_launch": False,
            "stop": ["quota/rate-limit or nonzero exit", "source/receipt/recipe mismatch", "rights/scope uncertainty before LLM", "unexpected paid endpoint", "missing durable output or process exit evidence"],
            "adoption_gate": "Only usage-authorized same-work/scope evidence and verified terminal outputs; unresolved conflict/unknown remains held",
            "resume_gate": "Record stop cause, retain originals, change only failed/new inputs after new evidence; publish neither uncertain features nor private history",
            "10000_to_30000": "Re-evaluate all gates using complete observed 10000-stage costs, coverage, holds and quality bounds",
            "30000_to_100000": "Re-evaluate complete 30000-stage measurements, free quota, storage/recovery and strata; do not extrapolate 58-work timing"},
        "latest_display_reused": current["audit"],
        "selected_source_files": source_reads,
        "measurement_total_seconds": time.perf_counter() - started,
    }
    with (output / "report.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(output / "report.json"), "decision": report["decision"],
        "pool": pool["works"], "novel_pool": novels["works"], "hash_benchmark": report["hash_benchmark"],
        "total_seconds": report["measurement_total_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
