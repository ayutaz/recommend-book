"""Private source-bound ratings/content joins; no authority merge or permission shortcut."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Literal

import mwparserfromhell
from pydantic import BaseModel, ConfigDict

from recommend_book import next_database
from recommend_book.models import WorkInput
from recommend_book.rights import RightsRegistry
from recommend_book.usage_guard import UsageGuard, UsageDecision, GUARD_VERSION, METADATA_FIELDS, model_hash, _record, _values
from . import history_binding
from .next_evaluation import _title_key

CONTRACT_VERSION = "private-serialization-profile-contract-v3"
PROFILE_PURPOSE = "private_local_serialization_content_profile_v3"
AUTHORIZATION_BASIS = "explicit_current_user_request_existing_ratings_private_local_content_recommendation"
SERIALIZATION_MATCHER_VERSION = "external-original-serialization-structure-v2"
PROFILE_POLICY = {
    "version": "private-serialization-profile-policy-v3",
    "purpose": PROFILE_PURPOSE,
    "private_material": "Owner HTML-reproduced manual observations only; no API metadata or cached annotations",
    "external_private_history_transmission": False,
    "export": "Aggregate summary only; private engine rows remain local",
    "identity": "Exact owner serial URL and pinned public original-serialization template/context; no canonical merge",
    "scopes": ["abstract_work", "original_serialization"],
    "style_and_expression_features": "Held; never transferred across versions",
    "public_material": "Conjunction: genuine UsageGuard DISPLAY native+feature verification over complete batch origins AND independent literal local_storage=allowed policy checks for every supplied metadata/evidence origin in that same batch",
    "local_storage_not_inferred_from_display": True,
}


def serialization_profile_spec():
    return {
        "version": "private-serialization-profile-spec-v3", "purpose": PROFILE_PURPOSE,
        "owned_observations_n": 335, "identity_trial_limit": 20,
        "rating_rule": "Use original HTML-reproduced owner rating unchanged",
        "feature_rule": "Exact native work-present features with verified immutable source/recipe lineage",
        "unknown_rule": "Unknown stays unknown; no negative imputation",
        "canonical_merges": 0, "new_owner_ratings": 0, "parameter_search_n": 0,
        "fresh_holdout": False, "preference_accuracy_verified": False,
        "public_admission": "display-native-and-features-plus-complete-batch-literal-local-policy-v1",
        "serialization_matcher": SERIALIZATION_MATCHER_VERSION,
    }


class PublicLocalPolicyReceipt(BaseModel):
    """A computed conjunction receipt, explicitly not a UsageDecision."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["public-display-and-literal-local-policy-v1"] = "public-display-and-literal-local-policy-v1"
    public_ledger_sha256: str
    source_registry_sha256: str
    profile_policy_sha256: str
    complete_input_hashes_sha256: str
    complete_batch_input_n: int
    metadata_material_n: int
    evidence_material_n: int
    local_policy_checks_sha256: str
    display_decisions_sha256: list[str]


def _literal_local_policy(origins, guard, ledger, registry, pins):
    """Check the independent local-storage right, including uncited batch inputs.

    Real DISPLAY verification separately proves raw-source/native grounding and
    conditional obligations. It cannot substitute for these literal local rules.
    Mixed WD records may legitimately contain separately licensed WP evidence;
    evidence uses its exact own policy, never the enclosing record's CC0 policy.
    """
    checks, metadata_n, evidence_n = [], 0, 0
    for origin in origins:
        if origin not in ledger["inputs"] or origin in ledger.get("revoked_input_hashes", []):
            raise ValueError("Complete batch literal local_storage origin unavailable")
        entry = ledger["inputs"][origin]
        work = guard._snapshot(entry)
        if model_hash(work) != origin or work.source_record_ids != entry["source_record_ids"] or set(work.source_record_ids) != set(entry["sources"]):
            raise ValueError("Complete batch literal local_storage source binding differs")
        records = {}
        for ref, binding in entry["sources"].items():
            if ref != binding["source_version_id"]:
                raise ValueError("Literal local_storage source version differs")
            record = _record(binding)
            _pin({"path": record.raw_path, "sha256": record.raw_sha256}, pins)
            source_database = Path(binding["database_path"]).resolve(strict=True)
            if source_database not in pins:
                _pin({"path": str(source_database), "sha256": history_binding.sha256(source_database.read_bytes())}, pins)
            records[ref] = record
        if set(entry["metadata"]) != set(METADATA_FIELDS):
            raise ValueError("Literal local_storage metadata roster differs")
        for field in METADATA_FIELDS:
            value, binding = getattr(work, field), entry["metadata"][field]
            if binding["value_sha256"] != model_hash(value):
                raise ValueError("Literal local_storage metadata value differs")
            if value is None or value == [] or value == "":
                continue
            values = value if isinstance(value, list) else [value]
            if set(binding["origins"]) != {str(i) for i in range(len(values))}:
                raise ValueError("Literal local_storage metadata origin coverage differs")
            for position, item in enumerate(values):
                refs = binding["origins"][str(position)]
                if not refs:
                    raise ValueError("Literal local_storage metadata has no source")
                for ref in refs:
                    record = records[ref]
                    policy = registry.policies.get(record.rights_policy_id)
                    if (item not in _values(record, field) or policy is None or policy.source != record.source
                        or policy.provider_id not in (record.provider_id, "*") or "bibliographic" not in policy.fields
                        or policy.usages.get("local_storage") != "allowed"):
                        raise ValueError("Metadata requires literal local_storage=allowed for every source")
                    checks.append({"origin": origin, "field": field, "position": position, "source_version": ref,
                                   "policy_sha256": model_hash(policy), "value_sha256": model_hash(item)})
                    metadata_n += 1
        if set(entry["evidence"]) != {e.evidence_id for e in work.evidence}:
            raise ValueError("Literal local_storage evidence roster differs")
        for evidence in work.evidence:
            binding = entry["evidence"][evidence.evidence_id]
            refs = binding["source_versions"]
            policy = registry.policies.get(evidence.rights_policy_id)
            if (binding["model_sha256"] != model_hash(evidence) or not refs or policy is None
                or evidence.kind not in policy.fields or policy.usages.get("local_storage") != "allowed"):
                raise ValueError("Evidence requires its own literal local_storage=allowed policy")
            for ref in refs:
                if evidence not in records[ref].evidence:
                    raise ValueError("Literal local_storage evidence source version differs")
                checks.append({"origin": origin, "evidence_sha256": model_hash(evidence), "source_version": ref,
                               "policy_sha256": model_hash(policy)})
                evidence_n += 1
    return checks, metadata_n, evidence_n


def _json(path):
    return json.loads(Path(path).read_bytes())


def _pin(descriptor, pins):
    if set(descriptor) != {"path", "sha256"} or not Path(descriptor["path"]).is_absolute():
        raise ValueError("Exact absolute source descriptor required")
    path = Path(descriptor["path"]).resolve(strict=True)
    raw = path.read_bytes()
    if history_binding.sha256(raw) != descriptor["sha256"]:
        raise ValueError("Pinned source SHA changed")
    if path in pins and pins[path] != descriptor["sha256"]:
        raise ValueError("Conflicting source pins")
    pins[path] = descriptor["sha256"]
    return path


def _verified_owner(artifacts, pins):
    database = _pin(artifacts["owned_database"], pins)
    report_path = _pin(artifacts["recovery_report"], pins)
    report = _json(report_path)
    if report["version"] != "owner-original-html-recovery-v1" or report["database_sha256"] != pins[database]:
        raise ValueError("Owner recovery database binding differs")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        imports = {r["snapshot_id"]: json.loads(r["pages_json"]) for r in connection.execute("SELECT snapshot_id,pages_json FROM imports")}
        observations = [dict(r) for r in connection.execute("SELECT snapshot_id,ncode,title,my_rating,source_page,row_number,evidence_json FROM rating_observations")]
    finally:
        connection.close()
    pages, parsed = {}, {}
    if len(report["pages"]) != sum(len(pages) for pages in imports.values()):
        raise ValueError("Recovery original page roster differs from owner imports")
    for page in report["pages"]:
        key = (page["snapshot_id"], Path(page["git_path"]).name)
        if key in pages or not any(Path(p["raw_file"]).name == key[1] and p["sha256"] == page["page_sha256"] for p in imports[key[0]]):
            raise ValueError("Recovery page is not bound to owner import")
        path = _pin({"path": str(report_path.parent / (page["page_sha256"] + ".html")), "sha256": page["page_sha256"]}, pins)
        raw = path.read_bytes()
        if hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != page["git_blob_sha1"]:
            raise ValueError("Recovered original Git blob differs")
        rows = history_binding.extract_owner_html_rows(raw)
        if len(rows) != page["row_n"]:
            raise ValueError("Recovered original page row count differs")
        pages[key], parsed[key] = page, rows
    verified = {}
    for observation in observations:
        key = (observation["snapshot_id"], Path(observation["source_page"]).name)
        position = observation["row_number"]
        if key not in parsed or type(position) is not int or not 1 <= position <= len(parsed[key]):
            raise ValueError("Owner observation locator unavailable")
        row = parsed[key][position - 1]
        code, rating = observation["ncode"].lower(), observation["my_rating"]
        if (code in verified or row["ncode"] != code or row["title"] != observation["title"]
            or isinstance(rating, bool) or not isinstance(rating, (int, float)) or not math.isfinite(rating) or not 1 <= rating <= 5
            or row["rating"] != rating or row["hrefs"] != [json.loads(observation["evidence_json"])["href"]]):
            raise ValueError("Owner observation does not reproduce original HTML")
        verified[code] = {"observation": observation, "page": pages[key], "href": row["hrefs"][0]}
    if len(verified) != 335 or report["verified_observation_n"] != len(verified):
        raise ValueError("The frozen owner335 roster differs")
    return verified


def _original_serial_templates(text, ncode):
    """Match exact serial links only in reviewed original-novel structures.

    External-link direct templates retain the original supported form. Additional
    forms are a direct ライトノベル child heading, or a top-level dl2's 原作 value.
    Recursive traversal is used only to reject ambiguity, never to admit arbitrary
    wrappers or adaptation sections. Relation matching grants no content transfer.
    """
    code = mwparserfromhell.parse(text)
    external = code.get_sections(matches=r"^\s*外部リンク\s*$", flat=False)
    if len(external) != 1:
        return []
    section = external[0]
    root_heading = section.filter_headings(recursive=False)[0]

    def exact(template):
        params = [parameter for parameter in template.params if str(parameter.name).strip() == "1"]
        return (str(template.name).strip() == "小説家になろう作品" and len(params) == 1
                and str(params[0].value).strip().lower() == ncode)

    if sum(exact(t) for t in section.filter_templates(recursive=True)) != 1:
        return []
    matching = []
    for part in section.get_sections(flat=True, include_lead=True):
        headings = part.filter_headings(recursive=False)
        if not headings:
            continue
        heading = headings[0]
        if not (heading is root_heading or
                heading.level == root_heading.level + 1 and str(heading.title).strip() == "ライトノベル"):
            continue
        for template in part.filter_templates(recursive=False):
            if exact(template):
                matching.append(template)
            elif str(template.name).strip().casefold() == "dl2":
                # dl2 alternates label/value positional parameters. Named,
                # duplicated or incomplete parameters are not a reviewed form.
                params = template.params
                if len(params) % 2 or [str(p.name).strip() for p in params] != [str(i) for i in range(1, len(params) + 1)]:
                    continue
                original = [params[i + 1].value for i in range(0, len(params), 2)
                            if str(params[i].value).strip() == "原作"]
                if len(original) == 1:
                    matching.extend(t for t in original[0].filter_templates(recursive=False) if exact(t))
    return matching


def _verified_binding(packet, owners, pins):
    owner = owners.get(packet["owned_ncode"])
    if owner is None:
        raise ValueError("Private binding owner subject is absent")
    observation, pageproof = owner["observation"], owner["page"]
    if (packet["snapshot_id"] != observation["snapshot_id"] or packet["row_number"] != observation["row_number"]
        or packet["page_sha256"] != pageproof["page_sha256"] or packet["git_commit"] != pageproof["git_commit"]
        or packet["git_blob_sha1"] != pageproof["git_blob_sha1"] or packet["owner_href"] != owner["href"]
        or packet["owned_title_sha256"] != history_binding.sha256(observation["title"].encode())):
        raise ValueError("Private owner row/proof/ncode binding differs")
    source = packet["public_article_source"]
    path = _pin({"path": source["raw_path"], "sha256": source["raw_sha256"]}, pins)
    receipt_path = _pin({"path": source["source_receipt_path"], "sha256": source["source_receipt_sha256"]}, pins)
    receipt = _json(receipt_path)
    if Path(receipt["path"]).resolve() != path or receipt["sha256"] != pins[path]:
        raise ValueError("Public article receipt differs")
    selected = [p for p in _json(path)["query"]["pages"] if p["pageid"] == source["pageid"] == packet["public_pageid"]]
    if len(selected) != 1:
        raise ValueError("Public article subject not unique")
    page = selected[0]
    # This checks the original bounded candidate's subject alongside its URL;
    # title equality alone never establishes the work relation.
    if _title_key(page["title"]) != _title_key(observation["title"]):
        raise ValueError("Public serialization candidate title subject differs")
    revisions = [r for r in page["revisions"] if r["revid"] == source["revision_id"]]
    if len(revisions) != 1 or page["pageprops"]["wikibase_item"] != source["wikibase_item"]:
        raise ValueError("Public article revision/QID subject differs")
    text = revisions[0]["slots"]["main"]["content"]
    if history_binding.sha256(text.encode()) != source["revision_text_sha256"]:
        raise ValueError("Public revision text differs")
    matching = _original_serial_templates(text, packet["owned_ncode"])
    contexts = [line for line in text.splitlines() if "小説家になろう" in line and ("連載" in line or "掲載された" in line)
                and not line.startswith(("*", ":", "|"))]
    if len(matching) != 1 or not contexts:
        raise ValueError("Explicit original serialization template/context absent")
    return owner, source, page


def _claim_ids(entity, property_id):
    """Recorded nondeprecated Wikidata item assertions, without name inference."""
    identifiers = set()
    for claim in entity.get("claims", {}).get(property_id, []):
        snak = claim.get("mainsnak", {})
        value = snak.get("datavalue", {}).get("value", {})
        if claim.get("rank") == "deprecated" or snak.get("snaktype") != "value" or not isinstance(value, dict):
            continue
        identity = value.get("id", "")
        if snak.get("property") == property_id and snak.get("datatype") == "wikibase-item" and value.get("entity-type") == "item" and re.fullmatch(r"Q[1-9][0-9]*", identity):
            identifiers.add("wikidata:" + identity)
    return sorted(identifiers)


def _public_features(row, source, page, guard, artifacts, ledger, registry, ledger_sha, pins):
    subject = {"pageid": source["pageid"], "revision_id": source["revision_id"], "wikibase_item": source["wikibase_item"], "raw_sha256": source["raw_sha256"]}
    if row.get("public_subject") != subject or row.get("work_id") != "public:wd:" + subject["wikibase_item"]:
        raise ValueError("Public classification subject differs from verified serialization")
    if row.get("source_scope") not in PROFILE_POLICY["scopes"] or row.get("rating") is not None:
        raise ValueError("Public row source scope/private rating differs")
    work = WorkInput.model_validate(row["input_json"])
    origin = model_hash(work)
    origins = row["classification_originating_input_hashes"]
    if work.work_id != row["work_id"] or work.expression_id is not None or origin not in origins or not origins or len(set(origins)) != len(origins):
        raise ValueError("Complete public work input subject/origins differ")
    entry = ledger["inputs"][origin]
    if model_hash(guard._snapshot(entry)) != origin or set(work.source_record_ids) != set(entry["sources"]):
        raise ValueError("Public WorkInput is not bound to actual ledger sources")
    matching_sources = []
    for binding in entry["sources"].values():
        record = _record(binding)
        if record.source == "wikidata" and record.source_id == subject["wikibase_item"]:
            path = _pin({"path": record.raw_path, "sha256": record.raw_sha256}, pins)
            entity = _json(path).get("entities", {}).get(subject["wikibase_item"], {})
            if entity.get("id") == subject["wikibase_item"] and entity.get("sitelinks", {}).get("jawiki", {}).get("title") == page["title"]:
                matching_sources.append(entity)
    if len(matching_sources) != 1:
        raise ValueError("Wikidata/article/work subject correspondence unproved")
    groups = {"author_ids": _claim_ids(matching_sources[0], "P50"), "series_ids": _claim_ids(matching_sources[0], "P179")}
    for key, expected_ids in groups.items():
        if key in row and row[key] != expected_ids:
            raise ValueError("Public author/series IDs differ from native source claims")
    features, provenance = row.get("features", {}), row.get("feature_provenance", {})
    if not isinstance(features, dict) or not isinstance(provenance, dict) or set(features) != set(provenance):
        raise ValueError("Exact feature/provenance mapping required")
    for label, strength in features.items():
        proofs = provenance.get(label)
        if label.startswith("style.") or not proofs or any(p.get("axis") == "style" or p.get("scope") != "work" or p.get("expression_id") is not None or p.get("input_hash") != origin or set(p.get("source_record_ids", [])) != set(work.source_record_ids) for p in proofs):
            raise ValueError("Feature style/expression/source scope is not work-level")
        if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or strength < 0:
            raise ValueError("Finite nonnegative feature strength required")
    native, feature = row["classification_derivation"], row["feature_derivation"]
    expected = {"work_id": work.work_id, "features": features, "feature_provenance": provenance}
    result, manifest, recipe = native["payload"], native["source_manifest"], feature["features_recipe"]
    if (native["kind"] != "classification" or feature["kind"] != "classification_features" or result["work_id"] != work.work_id or result["input_hash"] != origin
        or native["originating_input_hashes"] != origins or feature["originating_input_hashes"] != origins
        or native["value_sha256"] != model_hash(result) or feature["payload"] != expected or feature["value_sha256"] != model_hash(expected)
        or feature["source_manifest"] != manifest or feature["native_result"] != result or recipe["native_result_sha256"] != model_hash(result)
        or {k: recipe[k] for k in ("path", "sha256")} != artifacts["feature_recipe"]
        or manifest["config_sha256"] != model_hash(_json(artifacts["classification_spec"]["path"])) or manifest["usage_ledger_sha256"] != ledger_sha):
        raise ValueError("Native classification/feature/recipe/spec lineage differs")
    display_decisions = []
    for payload, extra in ((result, {}), (expected, {"derivation_recipe": recipe})):
        decision = guard.check_derived(payload, origins, "display", source_manifest=manifest, **extra)
        if not isinstance(decision, UsageDecision) or decision.allowed is not True or decision.usage != "display" or decision.guard_version != GUARD_VERSION or decision.ledger_path != str(guard.ledger_path) or decision.ledger_sha256 != ledger_sha or decision.payload_sha256 != model_hash(payload) or decision.input_hashes != origins:
            raise ValueError("Genuine source-bound display decision required")
        display_decisions.append(model_hash(decision))
    checks, metadata_n, evidence_n = _literal_local_policy(origins, guard, ledger, registry, pins)
    receipt = PublicLocalPolicyReceipt(public_ledger_sha256=ledger_sha, source_registry_sha256=artifacts["source_registry"]["sha256"],
        profile_policy_sha256=artifacts["profile_policy"]["sha256"], complete_input_hashes_sha256=model_hash(origins),
        complete_batch_input_n=len(origins), metadata_material_n=metadata_n, evidence_material_n=evidence_n,
        local_policy_checks_sha256=model_hash(checks), display_decisions_sha256=display_decisions)
    return work, deepcopy(features), deepcopy(provenance), groups, receipt


def build_serialization_profile(public_rows, *, contract_path, contract_sha256, public_guard):
    """Return private engine rows and an aggregate-only summary after source replay.

    The new local owner-purpose contract is distinct from the public material's
    genuine DISPLAY decisions plus literal local-storage policy verification.
    Neither is a fixed-bigram permission receipt, and display is not local rights.
    """
    pins = {}
    contract_file = _pin({"path": str(Path(contract_path).resolve()), "sha256": contract_sha256}, pins)
    contract = _json(contract_file)
    if set(contract) != {"version", "purpose", "authorization_basis", "artifacts", "public_rows_sha256"} or contract["version"] != CONTRACT_VERSION or contract["purpose"] != PROFILE_PURPOSE or contract["authorization_basis"] != AUTHORIZATION_BASIS or contract["public_rows_sha256"] != model_hash(public_rows):
        raise ValueError("New private source/recipe/spec purpose contract required")
    artifacts = contract["artifacts"]
    required = {"owned_database", "recovery_report", "serialization_bindings", "public_ledger", "source_registry", "profile_policy", "profile_spec", "classification_spec", "history_recipe", "profile_recipe", "feature_recipe"}
    if set(artifacts) != required:
        raise ValueError("Complete new source/recipe/spec descriptors required")
    for descriptor in artifacts.values():
        _pin(descriptor, pins)
    recipe_paths = {"history_recipe": Path(history_binding.__file__), "profile_recipe": Path(__file__), "feature_recipe": Path(next_database.__file__)}
    for key, implementation in recipe_paths.items():
        if Path(artifacts[key]["path"]).resolve() != implementation.resolve():
            raise ValueError("Production verifier/feature recipe path differs")
    if _json(artifacts["profile_policy"]["path"]) != PROFILE_POLICY or _json(artifacts["profile_spec"]["path"]) != serialization_profile_spec():
        raise ValueError("New private profile policy/spec differs")
    if not isinstance(public_guard, UsageGuard) or public_guard.ledger_path != Path(artifacts["public_ledger"]["path"]).resolve() or public_guard.expected_ledger_sha256 != artifacts["public_ledger"]["sha256"]:
        raise ValueError("Actual frozen public UsageGuard required")
    ledger, registry, ledger_sha = public_guard._load()
    selected_registry = RightsRegistry.from_file(artifacts["source_registry"]["path"])
    if model_hash([p.model_dump(mode="json") for p in registry.policies.values()]) != model_hash([p.model_dump(mode="json") for p in selected_registry.policies.values()]):
        raise ValueError("Public source registry differs")
    owners = _verified_owner(artifacts, pins)
    packets = [json.loads(line) for line in Path(artifacts["serialization_bindings"]["path"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    if not packets or len(packets) > 20 or len({p["owned_ncode"] for p in packets}) != len(packets):
        raise ValueError("Distinct bounded private serialization proofs required")
    index = {}
    for row in public_rows:
        identity = row["public_subject"]["pageid"]
        if identity in index:
            raise ValueError("Duplicate public classification subject")
        index[identity] = row
    private, missing, unknown, material_receipts = [], 0, 0, []
    for packet in packets:
        if packet["source_database_sha256"] != artifacts["owned_database"]["sha256"]:
            raise ValueError("Private binding owner database differs")
        owner, source, page = _verified_binding(packet, owners, pins)
        row = index.get(source["pageid"])
        if row is None:
            missing += 1
            continue
        work, features, provenance, groups, material_receipt = _public_features(row, source, page, public_guard, artifacts, ledger, registry, ledger_sha, pins)
        material_receipts.append(material_receipt.model_dump(mode="json"))
        unknown += sum(label.get("decision") == "unknown" for label in row["classification_derivation"]["payload"].get("labels", []))
        private.append({"work_id": work.work_id, "title": work.title, "rating": owner["observation"]["my_rating"],
            "features": features, "feature_provenance": provenance, "read_status": "unknown", "popularity": None,
            **groups, "external_ids": [{"source": "narou", "value": packet["owned_ncode"]}],
            "private_lineage": {"contract_sha256": contract_sha256, "page_sha256": packet["page_sha256"], "public_revision_id": source["revision_id"], "public_input_hash": model_hash(work)}})
    if len({r["work_id"] for r in private}) != len(private):
        raise ValueError("Multiple owner serials require explicit work-level rating consolidation")
    if any(history_binding.sha256(path.read_bytes()) != digest for path, digest in pins.items()):
        raise ValueError("Profile source changed during computation")
    summary = {"version": "private-serialization-profile-summary-v3", "owned_observation_n": len(owners),
        "verified_serialization_relation_n": len(packets), "bound_reference_n": len(private),
        "supported_reference_n": sum(bool(r["features"]) for r in private), "missing_public_classification_n": missing,
        "reference_author_identity_missing_n": sum(not r["author_ids"] for r in private),
        "reference_series_identity_missing_n": sum(not r["series_ids"] for r in private),
        "unknown_native_label_n": unknown, "canonical_merges": 0, "new_owner_ratings": 0,
        "private_titles_exported": 0, "private_ids_exported": 0, "individual_ratings_exported": 0,
        "author_series_leakage_verified": False, "fresh_holdout": False, "preference_accuracy_verified": False}
    summary.update(public_source_policy_receipt_n=len(material_receipts), local_storage_inferred_from_display=False,
                   guard_local_storage_receipt_claimed=False)
    return {"private_rows": private, "summary": summary,
        "purpose": {"kind": CONTRACT_VERSION, "purpose": PROFILE_PURPOSE, "contract_sha256": contract_sha256,
                    "public_ledger_sha256": ledger_sha, "guard_version": GUARD_VERSION,
                    "public_material_receipts": material_receipts}}
