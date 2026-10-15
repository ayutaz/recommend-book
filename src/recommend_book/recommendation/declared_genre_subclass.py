"""Pure, bounded P136/P279 projection to the unchanged thirteen direct endpoints.

This issues no permission or adoption decision. Every supplied ontology document
is hash/identity checked and retained in the provenance requirements. Paths are
source declarations, not strict72 truth, confidence, relevance or centrality.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from recommend_book.models import SourceRecord
from recommend_book.recommendation.declared_genre import (
    DECLARED_GENRE_MAPPING, project_declared_genres,
)


VERSION = "source-declared-genre-subclass-projection-v1"
BASIS = "source_declared_P136_P279_max2_fixed13_v1"
MAX_HOPS = 2
# Frozen exclusions established by the bounded feasibility review. No name tests.
EXCLUDED_NODE_QIDS = frozenset({"Q53911753", "Q119429258", "Q15637301"})
_QID = re.compile(r"Q[1-9][0-9]*")
_STATEMENT_ID = re.compile(r"([Qq][1-9][0-9]*)\$[A-Za-z0-9][A-Za-z0-9-]*")


@dataclass(frozen=True)
class OntologySource:
    """Caller-supplied cached entity bytes; raw_path is a provenance locator only.

    No filesystem read is performed. The caller must bind these documents to
    actual usage/rights receipts separately, including every ontology source.
    """

    entity_id: str
    source_url: str
    raw_path: str
    raw_sha256: str
    raw_bytes: bytes


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _target(snak: dict) -> str | None:
    datavalue = snak.get("datavalue", {})
    if not isinstance(datavalue, dict):
        raise ValueError("item datavalue must preserve its raw object structure")
    if (snak.get("snaktype") != "value" or snak.get("datatype") != "wikibase-item" or
            datavalue.get("type") != "wikibase-entityid"):
        return None
    value = datavalue.get("value")
    if not isinstance(value, dict) or value.get("entity-type") != "item":
        return None
    qid = value.get("id")
    if not isinstance(qid, str) or not _QID.fullmatch(qid):
        return None
    if "numeric-id" in value and (type(value["numeric-id"]) is not int or
                                  value["numeric-id"] != int(qid[1:])):
        raise ValueError("item numeric-id differs from its canonical QID")
    return qid


def _assertions(entity: dict, qid: str, prop: str, artifact: dict) -> list[dict]:
    statements = entity["claims"].get(prop, [])
    if not isinstance(statements, list):
        raise ValueError(f"raw {prop} statements must be a list")
    if any(not isinstance(s, dict) or s.get("rank") not in {"normal", "preferred", "deprecated"}
           for s in statements):
        raise ValueError(f"invalid {prop} statement or rank")
    preferred = any(s["rank"] == "preferred" for s in statements)
    assertions = []
    seen_ids = set()
    for index, statement in enumerate(statements):
        snak = statement.get("mainsnak")
        if (statement.get("type") != "statement" or not isinstance(snak, dict) or
                snak.get("property") != prop):
            raise ValueError(f"invalid {prop} statement property/type")
        statement_id = statement.get("id")
        match = _STATEMENT_ID.fullmatch(statement_id) if isinstance(statement_id, str) else None
        # Wikibase statement owners can use lowercase q. Only that letter's case
        # is normalized; the complete decimal identity must still match exactly.
        if match is None or "Q" + match[1][1:] != qid:
            raise ValueError(f"{prop} statement identity differs from the raw entity")
        if statement_id in seen_ids:
            raise ValueError(f"duplicate {prop} statement identity")
        seen_ids.add(statement_id)
        qualifiers = statement.get("qualifiers", {})
        references = statement.get("references", [])
        order = statement.get("qualifiers-order", [])
        if not isinstance(qualifiers, dict) or not isinstance(references, list) or not isinstance(order, list):
            raise ValueError("qualifiers, qualifier order and references must preserve raw structures")
        rank = statement["rank"]
        selected = rank == "preferred" if preferred else rank != "deprecated"
        target = _target(snak)
        if not selected:
            status = "deprecated" if rank == "deprecated" else "superseded_by_preferred"
        elif target is None:
            status = "unknown_value"
        elif qualifiers:
            status = "held_qualifiers"
        else:
            status = "selected_unqualified"
        assertions.append({
            "source_qid": qid, "target_qid": target, "property": prop,
            "statement_id": statement_id, "statement_owner_normalized": qid,
            "locator": f"entities.{qid}.claims.{prop}[{index}]",
            "rank": rank, "selected_by_rank": selected, "snaktype": snak.get("snaktype"),
            "qualifiers": deepcopy(qualifiers), "qualifiers_order": deepcopy(order),
            "references": deepcopy(references), "raw_statement": deepcopy(statement),
            "raw_statement_sha256": _digest(statement), "selection_status": status,
            "source_artifact": deepcopy(artifact),
        })
    return assertions


def _ontology(source: OntologySource) -> tuple[dict, list[dict]]:
    if (not isinstance(source, OntologySource) or not isinstance(source.entity_id, str) or
            not _QID.fullmatch(source.entity_id) or
            source.source_url != f"https://www.wikidata.org/wiki/{source.entity_id}" or
            not isinstance(source.raw_path, str) or not source.raw_path.strip()):
        raise ValueError("ontology source requires canonical entity identity and raw locator")
    if (not isinstance(source.raw_bytes, bytes) or
            hashlib.sha256(source.raw_bytes).hexdigest() != source.raw_sha256):
        raise ValueError("ontology raw SHA256 mismatch")
    payload = json.loads(source.raw_bytes)
    if not isinstance(payload, dict) or not isinstance(payload.get("entities"), dict):
        raise ValueError("ontology raw requires an entities object")
    entity = payload["entities"].get(source.entity_id)
    if (not isinstance(entity, dict) or entity.get("id") != source.entity_id or
            entity.get("type", "item") != "item" or "missing" in entity or
            not isinstance(entity.get("claims"), dict)):
        raise ValueError("ontology raw entity identity/claims mismatch")
    artifact = {"entity_id": source.entity_id, "source": "wikidata",
                "source_url": source.source_url, "raw_path": source.raw_path,
                "raw_sha256": source.raw_sha256, "entity_locator": f"entities.{source.entity_id}",
                "lastrevid": entity.get("lastrevid")}
    return artifact, _assertions(entity, source.entity_id, "P279", artifact)


def project_declared_genre_subclasses(
    raw_work: bytes,
    source_record: SourceRecord,
    scope_decision: Mapping[str, Any],
    ontology_sources: Sequence[OntologySource],
) -> dict:
    """Return a new packet with direct13 plus explicit <=2-hop P279 features.

    The unchanged direct projector verifies the work bytes, original novel kind,
    every title, exact claims and root-bound scope review first. Only its selected
    unqualified unsupported P136 item assertions start subclass paths. P31, P921,
    names, aliases and inferred endpoint mappings never supply graph edges.

    Every supplied ontology descriptor must bind exact cached bytes; duplicates
    are rejected. Missing sources, cycles, exclusions and depth limits yield
    explicit holds, never guessed paths. Endpoints need no separate raw document
    unless their outgoing P279 statements are followed. Raw statements remain
    intact, including nonselected, unknown, deprecated and qualified assertions.
    All supplied documents appear in source_artifacts even when no path succeeds.
    No input is mutated; no IO, rights issuance, model or adoption occurs.
    """
    direct = project_declared_genres(raw_work, source_record, DECLARED_GENRE_MAPPING, scope_decision)
    work_entity = json.loads(raw_work)["entities"][source_record.source_id]
    # The legacy verifier does not check optional numeric-id consistency.
    for statement in work_entity["claims"].get("P136", []):
        _target(statement["mainsnak"])
    work_artifact = {"entity_id": source_record.source_id, "source": "wikidata",
                     "source_url": source_record.source_url, "raw_path": source_record.raw_path,
                     "raw_sha256": source_record.raw_sha256,
                     "entity_locator": f"entities.{source_record.source_id}",
                     "lastrevid": work_entity.get("lastrevid")}
    if not isinstance(ontology_sources, Sequence) or isinstance(ontology_sources, (str, bytes)):
        raise ValueError("ontology_sources must be an explicit sequence of OntologySource")
    reviews, artifacts, by_qid = [], [work_artifact], {}
    for source in ontology_sources:
        artifact, assertions = _ontology(source)
        if source.entity_id in by_qid or source.entity_id == source_record.source_id:
            raise ValueError("duplicate ontology/work entity identity")
        by_qid[source.entity_id] = assertions
        artifacts.append(artifact)
        reviews.append({"source_artifact": artifact, "assertions": assertions})

    result = deepcopy(direct)
    paths, holds = [], []

    def hold(reason: str, nodes: list[str], assertion: dict, edges: list[dict]) -> None:
        holds.append({"reason": reason, "node_qids": list(nodes),
                      "work_statement_id": assertion["statement_id"],
                      "work_locator": assertion["locator"],
                      "ontology_statement_ids": [e["statement_id"] for e in edges]})

    def walk(nodes: list[str], edges: list[dict], assertion: dict) -> None:
        node = nodes[-1]
        if node in EXCLUDED_NODE_QIDS:
            hold("excluded_isekai_or_medium", nodes, assertion, edges)
            return
        if edges and node in DECLARED_GENRE_MAPPING:
            feature = DECLARED_GENRE_MAPPING[node]
            path = {"node_qids": list(nodes), "hop_count": len(edges), "endpoint_qid": node,
                    "feature_id": feature, "basis": BASIS,
                    "work_assertion": deepcopy(assertion),
                    "work_source_artifact": deepcopy(work_artifact),
                    "ontology_assertions": deepcopy(edges)}
            paths.append(path)
            result["provisional_recommendation_features"][feature] = 1.0
            result["feature_provenance"].setdefault(feature, []).append({
                "basis": BASIS, "subclass_path_index": len(paths) - 1,
                "genre_qid": assertion["genre_qid"], "endpoint_qid": node,
                "statement_id": assertion["statement_id"], "locator": assertion["locator"],
                "raw_confidence": None, "calibrated_confidence": None})
        if len(edges) == MAX_HOPS:
            if node not in DECLARED_GENRE_MAPPING:
                hold("max_hops", nodes, assertion, edges)
            return
        if node not in by_qid:
            if node not in DECLARED_GENRE_MAPPING:
                hold("missing_ontology_source", nodes, assertion, edges)
            return
        selected = [e for e in by_qid[node] if e["selection_status"] == "selected_unqualified"]
        if not selected:
            hold("no_selected_unqualified_P279", nodes, assertion, edges)
        for edge in selected:
            target = edge["target_qid"]
            if target in nodes:
                hold("cycle", nodes + [target], assertion, edges + [edge])
            else:
                walk(nodes + [target], edges + [edge], assertion)

    for assertion in direct["source_declared_genres"]:
        if assertion["projection_status"] == "unsupported_qid":
            walk([assertion["genre_qid"]], [], assertion)
    result.update(version=VERSION, basis=BASIS, llm_input_allowed=False,
                  subclass_paths=paths, path_holds=holds, ontology_reviews=reviews)
    result["provenance"].update({
        "direct_projection_version": direct["version"], "direct_projection_basis": direct["basis"],
        "source_artifacts": deepcopy(artifacts), "source_artifacts_sha256": _digest(artifacts),
        "max_P279_hops": MAX_HOPS, "excluded_node_qids": sorted(EXCLUDED_NODE_QIDS),
        "selection_basis": "best rank independently at P136 and each P279; unqualified item values only",
        "usage_requirements": "caller must obtain actual purpose-specific UsageGuard and literal policy checks for the work AND ALL ontology source_artifacts; no permission is issued here",
    })
    return result
