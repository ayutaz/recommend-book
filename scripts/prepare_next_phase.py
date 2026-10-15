"""Capture immutable v2 evidence and historical code before a new v3 run.

No old artifact is modified. Sources are accepted only when their bytes match
the completed evidence's SHA, including archived raw core copies when parallel
implementers have already changed the working tree.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def prepare(output: Path) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest_path = ROOT / "data/autonomous-poc-v1/evidence-manifest-v1.json"
    receipt_path = ROOT / "data/autonomous-poc-v1/completion-manifest-v1.json"
    baseline_path = ROOT / "data/autonomous-poc-v1/baseline-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if receipt["status"] != "complete_with_explicit_holds_and_unverified_quality":
        raise ValueError("Expected the completed v2 receipt with explicit holds")
    if sha(manifest_path) != receipt["evidence_manifest_sha256"]:
        raise ValueError("Completed evidence manifest SHA differs")

    observations = []
    for relative, expected in manifest["artifact_sha256"].items():
        path = ROOT / relative.replace("\\", "/")
        observed = sha(path)
        observations.append({"path": relative, "sha256": observed})
        if observed != expected:
            raise ValueError(f"Protected historical artifact changed: {relative}")
    baseline_observations = []
    for item in baseline["artifacts"]:
        path = Path(item["path"])
        if not path.is_absolute():
            path = ROOT / path
        observed = sha(path)
        if observed != item["sha256"]:
            raise ValueError(f"Protected baseline artifact changed: {path}")
        baseline_observations.append({"path": str(path), "sha256": observed})

    commit = receipt["completion_docs_commit"]
    command = ["git", "archive", "--format=tar", commit]
    archive = subprocess.run(command, cwd=ROOT, capture_output=True, check=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    sources = {}
    source_transforms = {}
    source_origins = {}
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as history:
        for relative, expected in manifest["source_sha256"].items():
            member = history.getmember(relative.replace("\\", "/"))
            stream = history.extractfile(member)
            if stream is None:
                raise ValueError(f"Not a source file in historical commit: {relative}")
            raw = stream.read()
            transform = "none"
            origin = "historical_git_archive"
            if hashlib.sha256(raw).hexdigest() != expected:
                # `git archive` can apply Windows checkout line endings.
                # Only exact, recorded newline conversion is accepted, and
                # only if it reproduces the already-bound historical SHA.
                raw.decode("utf-8")
                lf = raw.replace(b"\r\n", b"\n")
                for converted, conversion in (
                    (lf, "git_archive_crlf_to_historical_lf"),
                    (lf.replace(b"\n", b"\r\n"), "git_archive_lf_to_historical_crlf"),
                ):
                    if hashlib.sha256(converted).hexdigest() == expected:
                        raw = converted
                        transform = conversion
                        break
                if hashlib.sha256(raw).hexdigest() != expected:
                    # Mixed historical line endings cannot be recreated from
                    # a normalized Git blob. Use unchanged, SHA-bound bytes;
                    # never infer a prior state from today's changed code.
                    for candidate in (
                        ROOT / relative,
                        ROOT / "data/autonomous-poc-v1/silver/blind-v1/artifacts" / relative,
                        ROOT / "data/autonomous-poc-v1/delta-execution-context-v1/artifacts" / relative,
                    ):
                        if candidate.is_file() and sha(candidate) == expected:
                            raw = candidate.read_bytes()
                            transform = "none_exact_sha_bound_copy"
                            origin = str(candidate)
                            break
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError(f"Historical source cannot be recovered exactly: {relative}")
            destination = output / "historical-source" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as target:
                target.write(raw)
            sources[relative] = expected
            source_transforms[relative] = transform
            source_origins[relative] = origin
    result = {
        "schema_version": "next-phase-preparation-v3.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "historical_completion_status": receipt["status"],
        "historical_commit": commit,
        "historical_worktree_clean_observed": receipt["worktree_clean_observed"],
        "receipt_path": str(receipt_path), "receipt_sha256": sha(receipt_path),
        "evidence_manifest_path": str(manifest_path),
        "evidence_manifest_sha256": sha(manifest_path),
        "baseline_manifest_path": str(baseline_path),
        "baseline_manifest_sha256": sha(baseline_path),
        "protected_artifacts": observations,
        "protected_baseline_artifacts": baseline_observations,
        "historical_source_sha256": sources,
        "historical_source_checkout_transform": source_transforms,
        "historical_source_origins": source_origins,
        "historical_source_snapshot_scope": "Exact v2 tested byte hashes; each current observation's origin is recorded. This is not retroactive prelaunch capture.",
        "archive_command": command,
        "archive_exit_code": archive.returncode,
        "new_live_model_runs": 0,
        "new_user_tasks": 0,
        "v3_code_prelaunch_frozen": False,
        "note": "New code and model inputs must be frozen again before each v3 live run.",
    }
    save(output / "manifest.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = prepare(arguments.output)
    print(json.dumps({"historical_source_files": len(result["historical_source_sha256"]),
                      "protected_artifacts": len(result["protected_artifacts"]),
                      "baseline_artifacts": len(result["protected_baseline_artifacts"]),
                      "output": str(arguments.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
