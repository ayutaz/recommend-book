"""Publish a reviewed native-v1 world-novel export through the v4 control.

This adapter deliberately keeps the native classifier and the frozen control
constructor byte-for-byte unchanged.  Its only publication adaptation is the
single, source-hash-guarded eligibility predicate in ``finalize``: a native
row may be displayed when its work is display-authorized even when the source
genre is unknown (the unknown source is retained as an explicit hold).
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from types import ModuleType

from recommend_book import scale20k_native_v1 as state

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/world-21000-delivery-20261026-v1"
POLICY = "novel_content_with_display_permission_source_genre_optional"
_contexts = {}


def emit(value):
    print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)


def observed_command(argv, directory, *, env=None, timeout=None, stdout_name="stdout.txt", request_fields=None):
    """Run one serialized child and retain request/start/exit/stdout witnesses."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    argv = [str(value) for value in argv]
    state.save(directory / "request.json", {"argv": argv, "shell": False, **(request_fields or {})})
    started = time.monotonic()
    timed_out = False
    with (directory / stdout_name).open("xb") as stdout, (directory / "stderr.txt").open("xb") as stderr:
        with subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stdout,
                              stderr=stderr, shell=False, env=env) as process:
            birth = state.process_created_at(process.pid)
            state.save(directory / "process-start.json", {
                "pid": process.pid, "process_created_at": birth,
                "started_at": datetime.now(timezone.utc).isoformat()})
            emit({"stage": "actual_operational_child_started", "pid": process.pid,
                  "process_created_at": birth, "directory": str(directory)})
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                process.wait()
            result = {"pid": process.pid, "actual_exit_code": process.returncode,
                      "process_exit_observed": True, "process_created_at": birth,
                      "elapsed_seconds": time.monotonic() - started,
                      "timed_out": timed_out,
                      "observed_at": datetime.now(timezone.utc).isoformat()}
    state.save(directory / "process-exit.json", result)
    if result["actual_exit_code"] != 0 or timed_out:
        raise ValueError("Actual child failed; preserve outputs and stop without retry: " + str(directory))
    return result


def catalogue_snapshot(database, qids):
    qids = sorted(set(qids))
    if not qids or len(qids) > 100:
        raise ValueError("One bounded actual selected authority set required")
    marks = ",".join("?" for _ in qids)
    uri = Path(database).resolve(strict=True).as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.execute("BEGIN")
        result = {qid: {"version_id": version, "display_allowed": display,
                        "recommendable": recommendable, "genres": [], "native": None}
                  for qid, version, display, recommendable in db.execute(
                      "SELECT qid,version_id,display_allowed,recommendable FROM works WHERE qid IN (" + marks + ")", qids)}
        if set(result) != set(qids):
            raise ValueError("Selected authority absent from catalogue")
        for qid, genre, label in db.execute(
                "SELECT qid,genre_qid,label FROM work_genres WHERE qid IN (" + marks + ") ORDER BY qid,genre_qid,label", qids):
            result[qid]["genres"].append([genre, label])
        for qid, manifest, digest, labels in db.execute(
                "SELECT qid,manifest_path,manifest_sha256,labels_json FROM native_classifications WHERE qid IN (" + marks + ")", qids):
            if result[qid]["native"] is not None:
                raise ValueError("Duplicate native authority row")
            result[qid]["native"] = {"manifest_path": manifest, "manifest_sha256": digest,
                                      "labels": json.loads(labels)}
    return result


def require_new_display_works(snapshot):
    for row in snapshot.values():
        if row["display_allowed"] != 1 or row["native"] is not None:
            raise ValueError("Only new display-authorized authorities may be classified")
        if not row["genres"] and row["recommendable"] != 0:
            raise ValueError("Unknown source genre must remain source recommendable=0")


def genre_counts(snapshot):
    known = sum(bool(row["genres"]) for row in snapshot.values())
    return {"source_genre_known": known, "source_genre_unknown": len(snapshot) - known}


def _expected_rows(document):
    rows = document.get("verified_projection", {}).get("rows")
    if not isinstance(rows, list) or not rows or len({row.get("qid") for row in rows}) != len(rows):
        raise ValueError("Unique actual selected mapping required")
    if any(not isinstance(row.get("qid"), str) or not row["qid"] for row in rows):
        raise ValueError("Malformed admitted authority identity")
    return rows


def validate_import_observation(before, after, expected_rows, export, expected_sha):
    if set(before) != set(after):
        raise ValueError("Actual selected authority set changed during import")
    expected = {row["qid"]: row["admitted_labels"] for row in expected_rows}
    require_new_display_works(before)
    if set(expected) != set(before):
        raise ValueError("Admitted authority set differs from selected works")
    for qid, original in before.items():
        current = after[qid]
        if {k: v for k, v in original.items() if k != "native"} != {k: v for k, v in current.items() if k != "native"}:
            raise ValueError("Source metadata changed during native import")
        wanted = {"manifest_path": str(Path(export).resolve()), "manifest_sha256": expected_sha,
                  "labels": expected[qid]}
        if current["native"] != wanted:
            raise ValueError("Actual imported manifest or labels differ from authentic projection")


def _replace_sql(function, before, after):
    count = 0
    for node in ast.walk(function):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and before in node.value:
            if node.value.count(before) != 1:
                raise ValueError("Exact eligibility SQL boundary differs")
            node.value = node.value.replace(before, after)
            count += 1
    if count != 1:
        raise ValueError("Exactly one fixed eligibility SQL required")


def _policy_path(export, base):
    return Path(base) / "native-eligibility" / Path(export).parent.name / "policy-observation.json"


def _write_policy(export, digest, database, base, before, after, expected_rows, import_report, result):
    policy_path = _policy_path(export, base)
    policy = {"version": "world-novel-native-display-eligibility-v1", "policy": POLICY,
              "database_path": str(Path(database).resolve()),
              "classification_manifest_path": str(Path(export).resolve()),
              "classification_manifest_sha256": digest, "before_import": before,
              "after_import": after, "source_genre_counts": genre_counts(before),
              "selected": len(before), "admitted": len(expected_rows),
              "admission_qids": sorted(row["qid"] for row in expected_rows),
              "admission_rows": [{"qid": row["qid"], "admitted_labels": row["admitted_labels"]}
                                 for row in sorted(expected_rows, key=lambda item: item["qid"])],
              "display_policy_sha256": result.get("display_policy_sha256"),
              "import_report_path": str(Path(import_report).resolve()),
              "full_public_native_verifier_required": True, "metadata_mutations": 0,
              "source_record_flags_unchanged": True,
              "publisher_path": str(Path(__file__).resolve()),
              "publisher_sha256": state.sha(__file__),
              "observed_at": datetime.now(timezone.utc).isoformat()}
    state.save(policy_path, policy)
    return policy_path


def observed_import(path, expected_sha, *, database=None, base=BASE):
    """Import one reviewed export with a serialized, witnessed child process."""
    path = Path(path).resolve(strict=True)
    database = Path(database or state.DATABASE).resolve(strict=True)
    if state.sha(path) != expected_sha:
        raise ValueError("Public export changed before actual import")
    document = state.read(path)
    if document.get("version") != "scale-content-native-classification-export-v3":
        raise ValueError("Actual rights3 public export required")
    rows = _expected_rows(document)
    qids = [row["qid"] for row in rows]
    report = Path(base) / "catalogue-imports" / path.parent.name / "manifest.json"
    if report.parent.exists():
        raise FileExistsError("Import already started; no automatic import retry")
    with state.writer_lock(purpose="world_novel_authentic_native_import"):
        before = catalogue_snapshot(database, qids)
        require_new_display_works(before)
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            marks = ",".join("?" for _ in qids)
            existing = db.execute("SELECT count(*) FROM native_classifications WHERE qid IN (" + marks + ")", qids).fetchone()[0]
        if existing:
            raise ValueError("Same-QID native overwrite is forbidden")
        code = (
            'import json,sys; from recommend_book.scale_content_control_v4 import display_policy_sha256; '
            'before=display_policy_sha256(); from recommend_book.scale_guard_context_v1 import enable_process_cache; '
            '_cache=enable_process_cache(); from recommend_book.scale_catalogue_v1 import ScaleCatalogue; '
            'result=ScaleCatalogue(sys.argv[1]).import_classifications(sys.argv[2],sys.argv[3]); '
            'after=display_policy_sha256();\n'
            'if before!=after: raise ValueError("Display policy changed during actual import")\n'
            'result["display_policy_sha256"]=before; print(json.dumps(result,ensure_ascii=True))')
        actual = observed_command([sys.executable, "-c", code, str(database), str(path), expected_sha],
                                  report.parent, stdout_name="stdout.json",
                                  request_fields={"classification_manifest_sha256": expected_sha,
                                                  "full_public_native_verifier_required": True,
                                                  "source_genre_optional": True})
        result = state.read(report.parent / "stdout.json")
        after = catalogue_snapshot(database, qids)
        validate_import_observation(before, after, rows, path, expected_sha)
        policy_path = _write_policy(path, expected_sha, database, base, before, after, rows, report, result)
        value = {"stage": "actual_catalogue_import_complete" if result and not result.get("failed") else "actual_import_error",
                 "classification_manifest": str(path), "classification_manifest_sha256": expected_sha,
                 "actual_process": actual, "result": result, "before_import": before,
                 "after_import": after, "admission_policy": str(policy_path),
                 "display_policy_sha256": result.get("display_policy_sha256"),
                 "full_public_native_verifier_used": True, "model_outputs_unchanged": True,
                 "observed_at": datetime.now(timezone.utc).isoformat()}
        state.save(report, value)
    emit(value)
    if value["stage"] != "actual_catalogue_import_complete":
        raise ValueError("Actual public import failed; keep outputs without retry")
    _contexts[path.parent.name] = {"policy_path": policy_path, "import_report": report}
    return report, value


def load_publication_tail(database, observed=None, *, base=BASE):
    """Load frozen v4 finalization after the one guarded unknown-genre edit."""
    path = state.ROOT / "data/autonomous-next-v3/scale-30000-v1/content-operational-driver-v2.py"
    if state.sha(path) != state.OPERATION_SHA:
        raise ValueError("Fixed genuine control constructor changed")
    tree = ast.parse(path.read_bytes())
    finalize = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "finalize")
    _replace_sql(finalize, " AND w.display_allowed=1 AND w.recommendable=1", " AND w.display_allowed=1")
    module = ModuleType("world_novel_publication_receipts")
    module.__file__ = str(path)
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), module.__dict__)
    module.ROOT, module.BASE, module.DATABASE, module.LOCK = ROOT, Path(base), Path(database), state.WRITER_LOCK
    callback = observed or (lambda export, digest: observed_import(export, digest, database=database, base=base))
    module.observed_import = callback
    original_save = module.save

    def save_control(destination, document):
        if document.get("version") == "scale-content-window-control-v4":
            export_pin = document.get("control_artifacts", {}).get("export", {}).get("path")
            context = _contexts.get(Path(export_pin).parent.name if export_pin else Path(destination).stem)
            if context:
                extra = {"native_eligibility_policy": context["policy_path"],
                         "native_eligibility_import_report": context["import_report"],
                         "native_eligibility_publisher": Path(__file__).resolve()}
                document["control_artifacts"].update({name: {"path": str(Path(value).resolve()),
                    "sha256": state.sha(value)} for name, value in extra.items()})
        original_save(destination, document)
    module.save = save_control
    return module


def verify_policy_control(receipt, public, digest):
    """Rebind the v4 receipt to the import-time admission evidence."""
    artifacts = receipt.get("control_artifacts", {})
    required = {"native_eligibility_policy", "native_eligibility_import_report",
                "native_eligibility_publisher"}
    if not required <= artifacts.keys():
        raise ValueError("v4 control lacks world publication policy pins")
    export_pin = artifacts.get("export")
    if not export_pin or export_pin.get("sha256") != digest:
        raise ValueError("v4 export pin differs from reviewed manifest")
    export_path = Path(export_pin["path"]).resolve(strict=True)
    if state.sha(export_path) != digest:
        raise ValueError("Reviewed manifest changed after publication")
    exported = state.read(export_path)
    if exported != public:
        raise ValueError("Control export document differs from reviewed manifest")
    policy_pin = artifacts["native_eligibility_policy"]
    policy_path = Path(policy_pin["path"]).resolve(strict=True)
    if state.sha(policy_path) != policy_pin["sha256"]:
        raise ValueError("Pinned admission policy changed")
    policy = state.read(policy_path)
    if (policy.get("version") != "world-novel-native-display-eligibility-v1"
            or policy.get("policy") != POLICY
            or policy.get("classification_manifest_sha256") != digest
            or policy.get("classification_manifest_path") != str(export_path)
            or policy.get("publisher_path") != artifacts["native_eligibility_publisher"]["path"]
            or policy.get("publisher_sha256") != artifacts["native_eligibility_publisher"]["sha256"]):
        raise ValueError("World publication admission policy binding differs")
    rows = _expected_rows(public)
    expected_admission = [{"qid": row["qid"], "admitted_labels": row["admitted_labels"]}
                          for row in sorted(rows, key=lambda item: item["qid"])]
    if policy.get("admission_rows") != expected_admission:
        raise ValueError("World publication admitted rows differ from control export")
    validate_import_observation(policy["before_import"], policy["after_import"], rows, export_path, digest)
    report_pin = artifacts["native_eligibility_import_report"]
    report_path = Path(report_pin["path"]).resolve(strict=True)
    if state.sha(report_path) != report_pin["sha256"]:
        raise ValueError("Pinned import report changed")
    report = state.read(report_path)
    if (report.get("admission_policy") != str(policy_path)
            or report.get("classification_manifest") != str(export_path)
            or report.get("classification_manifest_sha256") != digest
            or report.get("before_import") != policy.get("before_import")
            or report.get("after_import") != policy.get("after_import")
            or report.get("display_policy_sha256") != policy.get("display_policy_sha256")
            or report.get("result", {}).get("display_policy_sha256") != policy.get("display_policy_sha256")):
        raise ValueError("Import report and admission policy are not bound")
    return policy


def publish(export, expected_sha, output_v3, *, database=None, base=BASE):
    """Review native-v1 bytes, then finalize them through the v4 control."""
    export = Path(export).resolve(strict=True)
    output_v3 = Path(output_v3).resolve()
    database = Path(database or state.DATABASE).resolve(strict=True)
    base = Path(base).resolve()
    if not output_v3.is_relative_to(base):
        raise ValueError("Reviewed v3 output must stay inside the world publication namespace")
    if state.sha(export) != expected_sha:
        raise ValueError("Native export changed before output review")
    if state.read(export).get("version") != "scale-content-native-classification-export-v1":
        raise ValueError("Native-v1 export required as review input")
    reviewed = create_reviewed_export(export, expected_sha, output_v3)
    public = Path(reviewed.get("manifest", output_v3 / "manifest.json")).resolve()
    if not public.is_relative_to(base) or state.sha(public) != reviewed.get("sha256", state.sha(public)):
        raise ValueError("Reviewed v3 manifest is outside or differs from the output-review witness")
    digest = state.sha(public)
    publication = load_publication_tail(database, base=base)
    receipt = publication.finalize(public, digest)
    verify_policy_control(receipt, state.read(public), digest)
    return receipt


def create_reviewed_export(parent_path, expected_sha, output_dir):
    """Late import keeps synthetic contract tests independent of model deps."""
    from recommend_book.scale_content_classification_v3 import create_reviewed_export as review
    return review(parent_path, expected_sha, output_dir)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output-v3", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=state.DATABASE)
    args = parser.parse_args(argv)
    emit(publish(args.export, args.sha256, args.output_v3, database=args.database))


if __name__ == "__main__":
    main()
