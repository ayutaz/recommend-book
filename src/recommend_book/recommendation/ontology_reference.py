"""Typed CC0 reference-material checks; these are NOT UsageGuard decisions.

Caller must freeze and bind the real registry policy and official-condition entry
alongside real work-material decisions. Derived features require a separate exact
derived-artifact permit. This function grants neither catalog status nor adoption.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json

from recommend_book.recommendation.declared_genre_subclass import OntologySource, _ontology
from recommend_book.rights import RightsPolicy

VERSION = "wikidata-ontology-reference-purpose-v1"
LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
OFFICIAL_URL = "https://www.wikidata.org/wiki/Wikidata:Licensing"
STRUCTURED_CLAUSE = ("All structured data (i.e. the main, Property, Lexeme, and EntitySchema "
                     "namespaces) is released into the public domain under Creative Commons Zero.")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class _Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.parts, self.links = [], None, []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.suppressed += 1
        if tag == "p" and not self.suppressed:
            self.parts, self.links = [], []
        if tag == "a" and self.parts is not None and not self.suppressed:
            self.links.extend(value for key, value in attrs if key == "href")

    def handle_data(self, data):
        if self.parts is not None and not self.suppressed:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "p" and self.parts is not None and not self.suppressed:
            self.rows.append((" ".join("".join(self.parts).split()), list(self.links)))
            self.parts = None
        if tag in ("script", "style") and self.suppressed:
            self.suppressed -= 1


def admit_ontology_reference(source: OntologySource, usage: str, policy: RightsPolicy,
                             official_conditions: list[dict], official_raw: dict[str, bytes],
                             *, now: datetime) -> dict:
    """Reproduce a distinct reference receipt from exact raw bytes, without IO.

    ``official_conditions`` must be the exact entries from the frozen real work
    ledger. ``official_raw`` maps their raw_path strings to exact bytes. Registry
    and ledger binding remain explicit caller obligations, recorded by hashes.
    Failure raises ValueError; no guessed or historical permission is substituted.
    All raw P279 assertions are retained with rank/qualifier/reference evidence.
    """
    if usage not in ("display", "local_storage", "commercial"):
        raise ValueError("reference purpose unsupported")
    if (policy.policy_id != "wikidata-cc0" or policy.source != "wikidata" or
            policy.provider_id != "wikidata" or policy.license != "CC0-1.0" or
            policy.usages.get(usage) != "allowed" or
            not {"bibliographic", "subject"} <= set(policy.fields) or
            policy.scope != "Structured main-namespace entity data only. Descriptions distinguish entities; they are not synopses. Linked text/images have separate rights." or
            OFFICIAL_URL not in policy.evidence_urls or policy.obligations):
        raise ValueError("literal Wikidata source/field/purpose/scope policy unsupported")
    if now.tzinfo is None or not official_conditions:
        raise ValueError("current timezone-qualified official evidence required")
    official_proofs = []
    for entry in official_conditions:
        if (entry.get("source_url") != OFFICIAL_URL or entry.get("final_url") != OFFICIAL_URL or
                entry.get("status_code") != 200):
            raise ValueError("official primary URL/status differs")
        checked = datetime.fromisoformat(entry["verified_at"].replace("Z", "+00:00"))
        if checked.tzinfo is None or not 0 <= (now - checked).total_seconds() <= 7 * 86400:
            raise ValueError("official primary condition outside strict seven days")
        raw = official_raw.get(entry["raw_path"])
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != entry["raw_sha256"]:
            raise ValueError("official primary raw SHA256 differs")
        parser = _Paragraphs()
        parser.feed(raw.decode("utf8"))
        matches = [(index, text.index(STRUCTURED_CLAUSE))
                   for index, (text, links) in enumerate(parser.rows)
                   if STRUCTURED_CLAUSE in text and LICENSE_URL in links]
        if not matches:
            raise ValueError("full structured main-namespace CC0 clause not reproduced")
        official_proofs.append({"official_entry": deepcopy(entry),
                                "official_entry_sha256": _digest(entry),
                                "structured_scope_clause_sha256": _digest(STRUCTURED_CLAUSE),
                                "clause_locators": [f"HTML.p[{i}].normalized_text[{start}:{start + len(STRUCTURED_CLAUSE)}]" for i, start in matches],
                                "cc0_license_link": LICENSE_URL})
    artifact, assertions = _ontology(source)
    return {"version": VERSION, "receipt_kind": "ontology_reference_material_purpose",
            "source_kind": "ontology_reference", "purpose": usage,
            "reference_material_admitted": True, "actual_usage_guard_decision": False,
            "catalog_work": False, "feature_adoption_authorized": False,
            "external_llm_authorized": False, "checked_at": now.astimezone(timezone.utc).isoformat(),
            "source_artifact": artifact, "P279_assertions": assertions,
            "P279_assertions_sha256": _digest(assertions),
            "policy": policy.model_dump(mode="json"), "policy_sha256": _digest(policy.model_dump(mode="json")),
            "official_conditions": official_proofs,
            "permission_scope": "cached structured main-namespace entity material only; no linked text/images or derived-feature permit",
            "adoption_requirements": "real work UsageGuard decisions AND complete source-bound ontology reference receipts/reproduction AND exact derived-artifact permit"}
