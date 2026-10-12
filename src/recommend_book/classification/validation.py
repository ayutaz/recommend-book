"""Schema and semantic verification; this does not prove label correctness."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
from jsonschema import Draft202012Validator
from recommend_book.jobs import canonical_json
from recommend_book.models import WorkInput

ROOT = Path(__file__).resolve().parents[3]
PROMPT_VERSION = "1.0"


def load_taxonomy(path: Path | None=None) -> dict:
    return json.loads((Path(path) if path else ROOT / "config/taxonomy.json").read_text(encoding="utf-8"))


def input_hash(work: WorkInput) -> str:
    # Hash the entire fixed local snapshot, including rights flags. Sending
    # denied text is separately prohibited by prompt serialization.
    return hashlib.sha256(canonical_json(work.model_dump(mode="json")).encode()).hexdigest()


def _normalized(text):
    return re.sub(r"\s+", " ", text).strip()


def target_language(work: WorkInput) -> str | None:
    languages = {e.language for e in work.evidence if e.llm_input_allowed and e.expression_ref == work.expression_id and e.language}
    return next(iter(languages)) if len(languages) == 1 else work.original_language


def validate_result(result: dict, work: WorkInput, taxonomy: dict | None=None) -> dict:
    taxonomy = taxonomy or load_taxonomy()
    schema = json.loads((ROOT / "schemas/classification.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(result))
    if errors:
        raise ValueError("schema: " + "; ".join(error.message for error in errors[:5]))
    for key, expected in {"work_id":work.work_id, "expression_id":work.expression_id,
                          "input_hash":input_hash(work), "taxonomy_version":taxonomy["version"],
                          "prompt_version":PROMPT_VERSION, "target_language":target_language(work)}.items():
        if result[key] != expected:
            raise ValueError(f"Incorrect {key}")
    label_index = {label["label_id"]: label for label in taxonomy["labels"]}
    axes = {axis["axis_id"] for axis in taxonomy["axes"]}
    permitted = {e.evidence_id:e for e in work.evidence if e.llm_input_allowed}
    if len(permitted) != sum(e.llm_input_allowed for e in work.evidence):
        raise ValueError("Duplicate evidence IDs")
    missing = {row["axis"] for row in result["missing_axes"]}
    if len(missing) != len(result["missing_axes"]):
        raise ValueError("Duplicate missing axes")
    seen, represented, known = set(), set(), set()
    for label in result["labels"]:
        label_id = label["label_id"]
        if label_id not in label_index or label_id in seen:
            raise ValueError("Unknown or duplicate label ID")
        seen.add(label_id)
        if label["axis"] != label_index[label_id]["axis"]:
            raise ValueError("Incorrect label axis")
        represented.add(label["axis"])
        if label["calibrated_confidence"] is not None or label["calibration_version"] is not None or label["calibration_status"] != "uncalibrated":
            raise ValueError("Classification must remain uncalibrated")
        if label["decision"] == "unknown":
            if label["relevance"] is not None or not label["missing_reason"]:
                raise ValueError("Unknown needs null relevance and missing reason")
        else:
            known.add(label["axis"])
            if not label["evidence"] or label["relevance"] is None or label["raw_confidence"] is None:
                raise ValueError("Known decisions need evidence, relevance and raw confidence")
        if label["scope"] in ("expression", "translation"):
            if not work.expression_id or label["expression_id"] != work.expression_id:
                raise ValueError("Incorrect expression scope")
        elif label["expression_id"] is not None:
            raise ValueError("Work/original scope cannot name an expression")
        if label["axis"] == "style" and label["decision"] != "unknown" and label["scope"] == "work":
            raise ValueError("Style needs original or explicit expression scope")
        for ref in label["evidence"]:
            evidence = permitted.get(ref["evidence_id"])
            if evidence is None:
                raise ValueError("Unavailable evidence reference")
            if ref["locator"] != evidence.locator:
                raise ValueError("Incorrect evidence locator")
            if ref["quote"] and _normalized(ref["quote"]) not in _normalized(evidence.text):
                raise ValueError("Quote does not occur in evidence")
            if not ref["quote"] and not ref["summary"]:
                raise ValueError("Evidence needs quote or summary")
            if label["axis"] == "style" and label["decision"] != "unknown":
                if evidence.kind != "excerpt":
                    raise ValueError("Style requires excerpt evidence")
                if label["scope"] == "original" and (evidence.expression_ref or evidence.language != work.original_language):
                    raise ValueError("Original style requires original-language expression evidence")
                if label["scope"] in ("expression", "translation") and evidence.expression_ref != work.expression_id:
                    raise ValueError("Style evidence expression mismatch")
    if represented | missing != axes or not missing <= axes:
        raise ValueError("All six axes must be represented or explicitly unknown")
    if missing != axes - known:
        raise ValueError("All axes without supported decisions must be explicitly missing")
    for rule in taxonomy.get("contradictions", []):
        present = {label["label_id"] for label in result["labels"] if label["decision"] == "present"}
        if set(rule["label_ids"]) <= present:
            if rule.get("condition") == "same_evidence_locator":
                matched = [label for label in result["labels"] if label["label_id"] in rule["label_ids"] and label["decision"] == "present"]
                references = [{(e["evidence_id"], e["locator"]) for e in label["evidence"]} for label in matched]
                if set.intersection(*references) and (not result["conflicts"] or not result["needs_human_review"]):
                    raise ValueError("Contradictory descriptors on the same passage need a conflict and review")
            else:
                raise ValueError("Mutually exclusive labels")
    return result


def read_results(output_dir: Path) -> list[dict]:
    """Read the current run's validated result index, never CLI event JSONL."""
    path = Path(output_dir) / "results.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
