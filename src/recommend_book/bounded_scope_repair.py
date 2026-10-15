"""Bounded saved-metadata scope diagnosis; no model runner or prose export."""
from __future__ import annotations

import json
from pathlib import Path

from .diagnostic_preparation import (
    ArtifactLedger, OUTPUT as PREPARATION, _database, canonical_hash, file_hash,
)

VERSION = "bounded-scope-repair-v1"
PIN = "deea5b26f2c4fe4579abcc592bc8acda2892b35b75019ac3aa49478d1bd2b3dd"
OUTPUT = Path("data/autonomous-next-v3/evaluation/phase6-bounded-scope-repair-v1")
PROPERTIES = ("P31", "P136", "P921", "P50", "P179", "P144", "P747", "P629", "P7937", "P407")
# These are case-specific prerequisites, not inferred class names or label decisions.
PREREQUISITES = {
    "Q1314114": "Novel-specific genre assertion; do not inherit medium-qualified Q15637301/Q15712918.",
    "Q11310834": "Identify single-volume work versus the saved series subject; bind genre to that volume.",
    "Q136649775": "Volume-specific novel evidence; Q15637301 and P179 membership do not supply it.",
    "Q12614896": "Resolve saved disambiguation-description versus literary P31 conflict; verify revision 39150286 against the intended short story.",
    "Q136649875": "Volume-specific novel evidence; Q15637301 and P179 membership do not supply it.",
    "Q136649803": "Volume-specific novel evidence; Q15637301 and P179 membership do not supply it.",
    "Q10374276": "Separate the saved author/work collision; assign P50 assertions and revision 1378689739 to an explicit original work.",
    "Q136649921": "Volume-specific novel evidence; Q15637301 and P179 membership do not supply it.",
    "Q113159272": "Resolve saved series descriptions versus single-work target; identify the volume before genre inheritance.",
    "Q113547689": "Resolve P31 Q1667921/Q7725634 at original-work versus series/adaptation scope.",
    "Q11443124": "Bind revision 1359283232 Plot to original novel versus adaptation; P31 Q7725634 alone does not locate the section's version.",
    "Q11647645": "Bind revision 111254132 section to original novel versus film; resolve saved descriptions before work inheritance.",
    "Q11430142": "Bind revision 110190000 section to original novel versus adaptation; resolve saved descriptions before work inheritance.",
    "Q120336260": "Resolve saved film/novel description disagreement; P747 Q132673016 is an edition assertion, not proof that every genre assertion describes the original novel.",
    "Q11442816": "Bind revision 102858371 section to original novel versus adaptation; resolve saved descriptions before work inheritance.",
    "Q11271131": "Distinguish the two revision 110406523 synopsis sections by novel/adaptation scope before selecting either for work inheritance.",
    "Q11087771": "Replace or split the novel binding to P31 Q5398426 using explicit original-work/adaptation authority and section scope; never relabel the television subject as novel by title alone.",
    "Q120333101": "Separate P31 Q11424/Q7725634 and bind revision 109599419 section to the novel version.",
    "Q136695603": "Resolve P31 Q1667921/Q277759 and saved manhua description against the novel binding; identify original versus adaptation/series before genre transfer.",
    "Q11636436": "Separate P31 Q11424/Q7725634/Q5398426 and bind revision 1355250691 Plot to the novel version.",
}


def entity_metadata(raw, qid):
    entity = raw.get("entities", {}).get(qid)
    if not entity or entity.get("id") != qid:
        raise ValueError("Exact entity absent or mismatched")
    claims = {}
    for prop in PROPERTIES:
        claims[prop] = [
            {"locator": f"entities.{qid}.claims.{prop}[{n}]", "claim_id": claim.get("id"),
             "rank": claim.get("rank"),
             "target": claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id"),
             "value_sha256": canonical_hash(claim)}
            for n, claim in enumerate(entity.get("claims", {}).get(prop, []))
        ]
    return {"qid": qid, "locator": f"entities.{qid}.id", "claims": claims}


def page_metadata(raw, revision):
    matches = [(n, page) for n, page in enumerate(raw.get("query", {}).get("pages", []))
               if any(str(r.get("revid")) == str(revision) for r in page.get("revisions", []))]
    if len(matches) != 1:
        raise ValueError("Exact revision absent or ambiguous")
    n, page = matches[0]
    return {"page_id": page.get("pageid"), "revision_id": str(revision),
            "qid": page.get("pageprops", {}).get("wikibase_item"),
            "locator": f"query.pages[{n}].pageprops.wikibase_item"}


def cited_evidence(packet):
    refs = {e["evidence_id"]: e for e in packet["evidence_refs"]}
    if len(refs) != len(packet["evidence_refs"]):
        raise ValueError("Duplicate evidence")
    out = []
    for label in packet["labels"]:
        for cite in label["evidence_refs"]:
            evidence = refs.get(cite["evidence_id"])
            if evidence is None or evidence["locator"] != cite["locator"]:
                raise ValueError("Citation is unbound or locator changed")
            out.append({"label_id": label["label_id"], "evidence_id": cite["evidence_id"],
                        "locator": cite["locator"], "evidence_value_sha256": evidence["evidence_value_sha256"]})
    return out


def diagnose(root=Path.cwd(), output=OUTPUT):
    root = Path(root).resolve()
    out = (root / output).resolve()
    if not out.is_relative_to(root) or out.exists():
        raise ValueError("Output must be a new workspace directory")
    ledger = ArtifactLedger(root)

    def load(path, expected=None):
        name = ledger.add(path, expected)
        return json.loads((root / name).read_text(encoding="utf-8"))

    # The full original verifier was executed once before this diagnosis. Recheck
    # frozen packet bytes against its external pin; never trust an updated digest.
    manifest = load(PREPARATION / "manifest.json", PIN)
    cp = load(PREPARATION / "classification-task-packets.json", manifest["output_sha256"]["classification-task-packets.json"])
    ip = load(PREPARATION / "identity-task-packets.json", manifest["output_sha256"]["identity-task-packets.json"])
    if len(cp) != 20 or len(ip) != 3 or sum(len(p["labels"]) for p in cp) != 74:
        raise ValueError("Frozen case counts changed")

    def resolve_ref(ref):
        value = load(ref["path"])
        for token in ref["json_pointer"].strip("/").split("/"):
            value = value[int(token)] if isinstance(value, list) else value[token]
        if canonical_hash(value) != ref["value_sha256"]:
            raise ValueError("Saved finding changed")
        return value

    def source_metadata(s):
        raw = load(s["raw_path"], s["raw_sha256"])
        meta = (entity_metadata(raw, s["source_id"]) if s["source"] == "wikidata"
                else page_metadata(raw, s["source_id"].split(":")[1]))
        return {"source_version_id": s["version_id"], "source_id": s["source_id"],
                "raw_path": s["raw_path"], "raw_sha256": s["raw_sha256"],
                "current_work_nodes": s["current_work_nodes"], **meta}

    policy = load("config/quality-evaluation-v1.json")
    db = _database(root, policy["candidate_inventory"]["database_path"])
    cases, row_bindings = [], []
    try:
        for packet in cp:
            finding = resolve_ref(packet["old_finding"])
            observation = resolve_ref(packet["old_observation"])
            rows = db.execute('SELECT * FROM "next_classification_inputs" WHERE "job_key"=?', (packet["job_key"],)).fetchall()
            if len(rows) != 1:
                raise ValueError("Exact classification row absent")
            row = dict(rows[0])
            inp, result = json.loads(row["input_json"]), json.loads(row["result_json"])
            if (canonical_hash(inp) != packet["input_hash"] or row["work_id"] != packet["work_id"]
                    or canonical_hash(result) != finding["native_result_value_sha256"]):
                raise ValueError("Classification row changed")
            row_bindings.append({"job_key": packet["job_key"], "row_sha256": canonical_hash(row)})
            for e in packet["evidence_refs"]:
                index = int(e["input_pointer"].split("/")[-1])
                if canonical_hash(inp["evidence"][index]) != e["evidence_value_sha256"]:
                    raise ValueError("Evidence changed")
            sources = [source_metadata(s) for s in packet["source_bindings"]]
            wikidata = [s for s in sources if s["source_id"].startswith("Q")]
            if len(wikidata) != 1 or wikidata[0]["qid"] not in PREREQUISITES:
                raise ValueError("Unexpected bounded subject")
            pages = []
            for e in packet["evidence_refs"]:
                if e["evidence_id"].startswith("wp:"):
                    revision = e["evidence_id"].split(":")[2]
                    if not e["raw_path"]:
                        raise ValueError("Wikipedia evidence raw missing")
                    pages.append({"evidence_id": e["evidence_id"], "raw_path": e["raw_path"],
                                  "raw_sha256": e["raw_sha256"],
                                  **page_metadata(load(e["raw_path"], e["raw_sha256"]), revision)})
            labels = []
            for label in packet["labels"]:
                native = [x for x in result["labels"] if x["label_id"] == label["label_id"]]
                saved = [x for x in observation["label_observations"] if x["label_id"] == label["label_id"]]
                if len(native) != 1 or len(saved) != 1 or canonical_hash(native[0]) != label["original_label_value_sha256"]:
                    raise ValueError("Label/finding mismatch")
                labels.append({"label_id": label["label_id"], "native_decision_preserved": label["decision"],
                               "old_status": saved[0]["assessment"], "new_status": saved[0]["assessment"],
                               "containing_work_status": "held",
                               "reason_codes_preserved": saved[0]["reason_codes"],
                               "scope": label["scope"], "expression_id": label["expression_id"],
                               "original_label_value_sha256": label["original_label_value_sha256"]})
            cases.append({"case_id": packet["case_id"], "work_id": packet["work_id"],
                          "job_key": packet["job_key"], "old_finding": packet["old_finding"],
                          "old_observation": packet["old_observation"], "old_status": "held", "new_status": "held",
                          "diagnosis_status": "completed_saved_metadata_audit", "sources": sources,
                          "cited_evidence": cited_evidence(packet), "synopsis_identity_bindings": pages,
                          "structural_binding_matches": (all(p["qid"] == wikidata[0]["qid"] for p in pages) if pages else None),
                          "structural_match_resolves_content_scope": False,
                          "labels": labels, "missing_axes_preserved_sha256": canonical_hash(result["missing_axes"]),
                          "minimum_new_proof": PREREQUISITES[wikidata[0]["qid"]],
                          "accepted_substantive_repair": False,
                          "regression": "Reject work inheritance until the exact subject and section/version proof is bound; retain every saved uncertainty and unknown."})
    finally:
        db.close()
    identities = []
    for packet in ip:
        resolve_ref(packet["old_finding"])
        sources = [source_metadata(s) for s in packet["source_bindings"]]
        qids = [s["qid"] for s in sources]
        relations = [{"source_version_id": s["source_version_id"], "property": prop, **claim}
                     for s in sources for prop in ("P144", "P629", "P747")
                     for claim in s.get("claims", {}).get(prop, [])]
        cross = [x for x in relations if x["target"] in qids]
        identities.append({"case_id": packet["case_id"], "side_versions": packet["side_versions"],
                           "old_finding": packet["old_finding"], "sources": sources,
                           "old_status": "held", "new_status": "held", "exact_qids": qids,
                           "distinct_exact_qids": len(set(qids)) > 1, "scope_relation_claims": relations,
                           "explicit_cross_relation_candidates": cross,
                           "diagnosis_status": "completed_saved_metadata_audit", "accepted_substantive_repair": False,
                           "minimum_new_proof": "Explicit authority identity/equivalence between these exact QIDs with original-work versus expression/edition scope; a shared edition or adaptation edge alone cannot prove same work.",
                           "regression": "Exact revision/QID mismatch and distinct QIDs stay held even with matching author, date, title or AI same."})
    for path in ("src/recommend_book/bounded_scope_repair.py", "scripts/diagnose_bounded_scope_repairs.py", "tests/test_bounded_scope_repair.py"):
        ledger.add(path)
    ledger.verify()
    report = {"version": VERSION, "status": "completed_saved_metadata_audit", "preparation_manifest_sha256": PIN,
              "provenance": "llm_or_silver", "actor_kind": "mechanical", "human_gold": False,
              "semantic_re_review_performed": False, "external_llm_admission": False,
              "production_adoption_behavior_changed": False,
              "verification_scope": {"frozen_manifest_and_two_packet_hashes": True,
                                     "selected_classification_job_rows": 20,
                                     "referenced_raw_file_hashes": True,
                                     "full_preparation_verifier_executed_by_this_script": False,
                                     "all_112_preparation_row_bindings_rechecked_by_this_script": False},
              "classification_work_n": len(cases), "classification_label_n": sum(len(c["labels"]) for c in cases),
              "identity_pair_n": len(identities), "accepted_substantive_repairs": 0,
              "held_classification_work_n": len(cases), "held_identity_pair_n": len(identities),
              "new_model_calls": 0, "external_acquisitions": 0, "database_mutations": 0,
              "source_text_copied": False, "private_history_accessed": False,
              "user_annotation_required": 0, "independent_accuracy_measured": False,
              "classification_cases": cases, "identity_cases": identities}
    # Additive containment only. This does not change native labels or certify a repair.
    overlay = {"version": VERSION, "semantics": "containing_work_scope_quarantine_only", "database_mutations": 0,
               "applied_to_display_or_ranking": False, "production_adoption_behavior_changed": False,
               "label_error_inferred_from_work_hold": False,
               "labels": [{"work_id": c["work_id"], "job_key": c["job_key"], **label,
                           "eligible_for_work_inheritance": False} for c in cases for label in c["labels"]],
               "identity": [{"case_id": c["case_id"], "eligible_for_merge": False} for c in identities]}
    out.mkdir(parents=True)
    for name, value in (("report.json", report), ("scope-quarantine-overlay.json", overlay),
                        ("source-ledger.json", {"files": ledger.files, "selected_classification_rows": row_bindings})):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps({"version": VERSION, "output_sha256": {
        p.name: file_hash(p) for p in out.iterdir() if p.is_file()}}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report
