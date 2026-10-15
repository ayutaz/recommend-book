"""Bounded SPARQL discovery and 50-entity batches with factual claim provenance."""
from __future__ import annotations

import json
import re
from typing import Iterable

from recommend_book.models import Contributor, Evidence, Identifier, SourceRecord, Title
from .core import CollectionResult, HTTPFetcher

QUERY_URL = "https://query.wikidata.org/sparql"
ENTITY_URL = "https://www.wikidata.org/w/api.php"
LANGUAGE_CODES = {"Q1860": "en", "Q5287": "ja", "Q150": "fr", "Q188": "de", "Q1321": "es", "Q7737": "ru", "Q7850": "zh", "Q652": "it", "Q5146": "pt", "Q9610": "bn", "Q1568": "hi", "Q13955": "ar", "Q9176": "ko"}


def _qid(value: str) -> str:
    if not re.fullmatch(r"Q[1-9][0-9]*", value):
        raise ValueError(f"invalid Wikidata QID: {value}")
    return value


def claim_values(entity: dict, prop: str) -> list:
    statements = [s for s in entity.get("claims", {}).get(prop, []) if s.get("rank") != "deprecated"]
    preferred = [s for s in statements if s.get("rank") == "preferred"]
    selected = preferred or statements
    return [s["mainsnak"]["datavalue"]["value"] for s in selected if "datavalue" in s.get("mainsnak", {})]


def _label(entity: dict, preferred: tuple[str, ...]) -> tuple[str, str | None]:
    labels = entity.get("labels", {})
    for language in preferred:
        if language in labels:
            return labels[language]["value"], language
    value = next(iter(labels.values()), {})
    return value.get("value", entity["id"]), value.get("language")


class WikidataAdapter:
    def __init__(self, fetcher: HTTPFetcher):
        self.fetcher = fetcher

    def query_work_ids(self, *, limit: int = 100, offset: int = 0,
                       language_qids: Iterable[str] | None = None,
                       instance_qids: Iterable[str] | None = None) -> tuple[list[str], CollectionResult]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("small SPARQL pages require limit 1..500 and nonnegative offset")
        languages = list(language_qids or [])
        clause = ""
        if languages:
            clause = "VALUES ?language { " + " ".join("wd:" + _qid(v) for v in languages) + " } ?work wdt:P407 ?language."
        # Current book modelling uses P31=literary work with P7937=novel/form.
        # Preserve legacy direct P31 forms while excluding explicit edition records.
        instances = list(instance_qids) if instance_qids is not None else ["Q8261"]
        if not instances:
            raise ValueError("instance_qids must not be empty")
        types = "VALUES ?type { " + " ".join("wd:" + _qid(v) for v in instances) + " }"
        query = "SELECT DISTINCT ?work WHERE { " + types + " { ?work wdt:P7937 ?type. } UNION { ?work wdt:P31 ?type. } " + clause + " FILTER NOT EXISTS { ?work wdt:P629 ?editionWork. } FILTER NOT EXISTS { ?work wdt:P31 wd:Q3331189. } } ORDER BY ?work LIMIT " + str(limit) + " OFFSET " + str(offset)
        result = self.fetcher.get(QUERY_URL, source="wikidata", params={"query": query, "format": "json"}, headers={"Accept": "application/sparql-results+json"})
        if result.status != "success":
            return [], CollectionResult(status="failed", errors=[result.error or "SPARQL failed"])
        try:
            values = json.loads(result.content)["results"]["bindings"]
            ids = [_qid(row["work"]["value"].rsplit("/", 1)[-1]) for row in values]
        except (ValueError, KeyError, TypeError) as exc:
            return [], CollectionResult(status="failed", errors=[f"invalid SPARQL response: {exc}"])
        return ids, CollectionResult(status="truncated" if len(ids) == limit else "complete", metadata={"query": query, "raw_path": result.artifact.path, "raw_sha256": result.artifact.sha256, "offset": offset, "next_offset": offset + len(ids), "instance_qids": instances, "selection": "P7937 forms or legacy direct P31 classes; explicit P629 editions excluded; convenience sample, not world-population representative"})

    def query_edition_ids(self, work_ids: Iterable[str], *, limit: int = 500,
                          offset: int = 0) -> tuple[list[str], CollectionResult]:
        """Find only P629 versions/editions/translations of a bounded work-ID set.

        LIMIT and OFFSET apply to relation rows, not unique editions. P629 by itself
        is broader than a published manifestation; preserve the relation for review.
        """
        works = list(dict.fromkeys(_qid(value) for value in work_ids))
        if not 1 <= len(works) <= 100 or not 1 <= limit <= 500 or offset < 0:
            raise ValueError("edition discovery requires 1..100 work IDs, limit 1..500, nonnegative offset")
        query = "SELECT DISTINCT ?edition ?work WHERE { VALUES ?work { " + " ".join("wd:" + value for value in works) + " } ?edition wdt:P629 ?work. } ORDER BY ?edition ?work LIMIT " + str(limit) + " OFFSET " + str(offset)
        fetched = self.fetcher.get(QUERY_URL, source="wikidata", params={"query": query, "format": "json"}, headers={"Accept": "application/sparql-results+json"})
        if fetched.status != "success":
            return [], CollectionResult(status="failed", errors=[fetched.error or "edition discovery failed"])
        try:
            rows = json.loads(fetched.content)["results"]["bindings"]
            relations = [{"edition": _qid(row["edition"]["value"].rsplit("/", 1)[-1]), "work": _qid(row["work"]["value"].rsplit("/", 1)[-1])} for row in rows]
            if any(relation["work"] not in works for relation in relations):
                raise ValueError("SPARQL relation refers to a work outside the requested frame")
            ids = list(dict.fromkeys(relation["edition"] for relation in relations))
        except (ValueError, KeyError, TypeError) as exc:
            return [], CollectionResult(status="failed", errors=[f"invalid edition discovery response: {exc}"])
        return ids, CollectionResult(status="truncated" if len(rows) == limit else "complete", metadata={"query": query, "work_ids": works, "relations": relations, "relation_rows": len(rows), "offset": offset, "next_offset": offset + len(rows), "raw_path": fetched.artifact.path, "raw_sha256": fetched.artifact.sha256, "selection": "P629 version, edition or translation of; fixed candidate works only"})

    def fetch_entities(self, ids: Iterable[str], *, languages: tuple[str, ...] = ("en", "ja"),
                       kind: str = "work", linked_labels: dict[str, str] | None = None) -> CollectionResult:
        if kind not in {"work", "edition", "agent"}:
            raise ValueError("invalid entity kind")
        ids = list(dict.fromkeys(_qid(v) for v in ids))
        collection = CollectionResult()
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            fetched = self.fetcher.get(ENTITY_URL, source="wikidata", params={"action": "wbgetentities", "ids": "|".join(batch), "props": "labels|aliases|descriptions|claims|sitelinks", "languages": "|".join(languages), "format": "json"})
            if fetched.status != "success":
                collection.status = "failed"
                collection.errors.append(fetched.error or "entity batch failed")
                break
            try:
                payload = json.loads(fetched.content)
                if "error" in payload:
                    raise ValueError(payload["error"])
                entities = payload["entities"]
                for qid in batch:
                    entity = entities.get(qid)
                    if not entity or "missing" in entity:
                        collection.errors.append(f"missing entity: {qid}")
                        collection.status = "failed"
                        continue
                    if kind == "work":
                        # Literary form does not establish work scope. Preserve
                        # conflicting normal claims even beside preferred ones.
                        assertions = []
                        for prop in ("P31", "P629"):
                            for index, statement in enumerate(entity.get("claims", {}).get(prop, [])):
                                if statement.get("rank") == "deprecated":
                                    continue
                                value = statement.get("mainsnak", {}).get("datavalue", {}).get("value")
                                if isinstance(value, dict) and (
                                    (prop == "P31" and value.get("id") == "Q3331189") or
                                    (prop == "P629" and re.fullmatch(r"Q[1-9][0-9]*", str(value.get("id", ""))))
                                ):
                                    assertions.append({"locator": f"claims.{prop}[{index}]", "statement": statement})
                        if assertions:
                            collection.metadata.setdefault("scope_holds", []).append({
                                "source_id": qid, "requested_kind": kind,
                                "reason": "Explicit edition assertion conflicts with requested work scope",
                                "raw_path": fetched.artifact.path, "raw_sha256": fetched.artifact.sha256,
                                "assertion_locators": [a["locator"] for a in assertions],
                                "assertions": assertions,
                            })
                            collection.errors.append(f"scope hold: {qid} has an explicit edition assertion")
                            collection.status = "failed"
                            continue
                    title, title_lang = _label(entity, languages)
                    url = f"https://www.wikidata.org/wiki/{qid}"
                    identifiers = [Identifier(source="wikidata", value=qid, scope=kind)]
                    if kind == "edition":
                        for prop in ("P212", "P957"):
                            identifiers.extend(Identifier(source="isbn", value=str(value), scope="edition") for value in claim_values(entity, prop) if isinstance(value, str))
                    for value in claim_values(entity, "P648"):
                        # OL identifiers encode type; do not coerce an edition into a work ID.
                        match = re.fullmatch(r"OL\d+([WMA])", str(value))
                        if match:
                            directory, scope = {"W": ("works", "work"), "M": ("books", "edition"), "A": ("authors", "agent")}[match[1]]
                            if scope == kind:
                                identifiers.append(Identifier(source="openlibrary", value=f"/{directory}/{value}", scope=scope))
                    contributors = []
                    for prop, role in [("P50", "author"), ("P655", "translator"), ("P98", "editor"), ("P110", "illustrator")]:
                        for val in claim_values(entity, prop):
                            if isinstance(val, dict) and "id" in val:
                                author = val["id"]
                                label = (linked_labels or {}).get(author)
                                if label is None and author in entities:
                                    label = _label(entities[author], languages)[0]
                                contributors.append(Contributor(name=label or author, role=role, identifiers=[Identifier(source="wikidata", value=author, scope="agent")]))
                    evidence = []
                    for language, description in entity.get("descriptions", {}).items():
                        evidence.append(Evidence(evidence_id=f"wd:{qid}:description:{language}:{fetched.artifact.sha256[:12]}", kind="description", text=description["value"], source_url=url, rights_policy_id="wikidata-cc0", llm_input_allowed=True, language=language, locator=f"descriptions.{language}"))
                    dates = claim_values(entity, "P577")
                    years = []
                    for value in dates:
                        if isinstance(value, dict) and value.get("precision", 0) >= 9:
                            match = re.match(r"\+(\d+)-", value.get("time", ""))
                            if match:
                                years.append(int(match[1]))
                    lang_refs = [v["id"] for v in claim_values(entity, "P407") if isinstance(v, dict) and "id" in v]
                    regions = [v["id"] for v in claim_values(entity, "P495") if isinstance(v, dict) and "id" in v]
                    genres = [v["id"] for v in claim_values(entity, "P136") if isinstance(v, dict) and "id" in v]
                    topics = [v["id"] for v in claim_values(entity, "P921") if isinstance(v, dict) and "id" in v]
                    characters = [v["id"] for v in claim_values(entity, "P674") if isinstance(v, dict) and "id" in v]
                    places = [v["id"] for v in claim_values(entity, "P840") if isinstance(v, dict) and "id" in v]
                    series_ids = [v["id"] for v in claim_values(entity, "P179") if isinstance(v, dict) and "id" in v]
                    subjects = [(linked_labels or {}).get(v, v) for v in genres + topics]
                    if subjects:
                        evidence.append(Evidence(evidence_id=f"wd:{qid}:subjects:{fetched.artifact.sha256[:12]}", kind="subject", text="; ".join(subjects), source_url=url, rights_policy_id="wikidata-cc0", llm_input_allowed=True, locator="claims.P136;claims.P921"))
                    if characters:
                        evidence.append(Evidence(evidence_id=f"wd:{qid}:characters:{fetched.artifact.sha256[:12]}", kind="bibliographic", text="Characters (not necessarily protagonists or narrators): " + "; ".join((linked_labels or {}).get(v, v) for v in characters), source_url=url, rights_policy_id="wikidata-cc0", llm_input_allowed=True, locator="claims.P674"))
                    if places:
                        evidence.append(Evidence(evidence_id=f"wd:{qid}:places:{fetched.artifact.sha256[:12]}", kind="bibliographic", text="Narrative locations (not origin regions): " + "; ".join((linked_labels or {}).get(v, v) for v in places), source_url=url, rights_policy_id="wikidata-cc0", llm_input_allowed=True, locator="claims.P840"))
                    if series_ids:
                        evidence.append(Evidence(evidence_id=f"wd:{qid}:series:{fetched.artifact.sha256[:12]}", kind="bibliographic", text="Series: " + "; ".join((linked_labels or {}).get(v, v) for v in series_ids), source_url=url, rights_policy_id="wikidata-cc0", llm_input_allowed=True, locator="claims.P179"))
                    language = LANGUAGE_CODES.get(lang_refs[0], lang_refs[0]) if len(lang_refs) == 1 else None
                    bibliographic = f"Title: {title}; contributors: {'; '.join(c.name for c in contributors)}; Wikidata instance IDs: " + ", ".join(v.get("id", "") for v in claim_values(entity, "P31") if isinstance(v, dict)) + "; Creative work form IDs: " + ", ".join(v.get("id", "") for v in claim_values(entity, "P7937") if isinstance(v, dict))
                    evidence.append(Evidence(evidence_id=f"wd:{qid}:bibliographic:{fetched.artifact.sha256[:12]}", kind="bibliographic", text=bibliographic, source_url=url, rights_policy_id="wikidata-cc0", llm_input_allowed=True, locator="labels;claims.P50;claims.P31;claims.P7937"))
                    titles = [Title(value=title, language=title_lang)]
                    title_assertions = []
                    # P1476 is a published name. Neither its text language nor
                    # its rank proves original/translated title scope. Retain
                    # all nondeprecated candidates, including normal titles
                    # beside preferred, without changing claim_values().
                    for index, statement in enumerate(entity.get("claims", {}).get("P1476", [])):
                        value = statement.get("mainsnak", {}).get("datavalue", {}).get("value")
                        title_index = None
                        if statement.get("rank") != "deprecated" and isinstance(value, dict) and value.get("text"):
                            title_index = len(titles)
                            titles.append(Title(value=value["text"], language=value.get("language"), kind="alternative"))
                        title_assertions.append({"locator": f"claims.P1476[{index}]", "statement": statement,
                                                 "title_index": title_index,
                                                 "title_kind": "alternative" if title_index is not None else None,
                                                 "raw_sha256": fetched.artifact.sha256, "raw_path": fetched.artifact.path,
                                                 "source_url": url, "source_id": qid, "record_kind": kind})
                    titles.extend(Title(value=v["value"], language=lang, kind="alternative") for lang, vals in entity.get("aliases", {}).items() for v in vals)
                    type_ids = [v["id"] for p in ("P7937", "P31") for v in claim_values(entity, p) if isinstance(v, dict) and "id" in v]
                    work_kind = next((name for q, name in [("Q747381", "light_novel"), ("Q49084", "short_story"), ("Q8261", "novel")] if q in type_ids), None)
                    record = SourceRecord(source="wikidata", source_id=qid, source_url=url, retrieved_at=fetched.artifact.retrieved_at, provider_id="wikidata", record_kind=kind, titles=titles, contributors=contributors, identifiers=identifiers, work_refs=[Identifier(source="wikidata", value=v["id"], scope="work") for v in claim_values(entity, "P629") if isinstance(v, dict) and "id" in v], original_language=language if kind == "work" else None, language=language if kind == "edition" else None, first_publication_year=min(years) if years and kind == "work" else None, publication_year=min(years) if years and kind == "edition" else None, work_kind=work_kind, region=regions, subjects=subjects, series="; ".join((linked_labels or {}).get(v, v) for v in series_ids) or None, evidence=evidence, raw_sha256=fetched.artifact.sha256, raw_path=fetched.artifact.path, rights_policy_id="wikidata-cc0", extra={"claims": entity.get("claims", {}), "sitelinks": entity.get("sitelinks", {}), "original_language_qids": lang_refs if kind == "work" else [], "language_qids": lang_refs if kind == "edition" else [], "publication_date_claims": dates, "lastrevid": entity.get("lastrevid"), "region_basis": "P495 country of origin", "subject_qids": genres + topics, "character_qids": characters, "series_qids": series_ids, "narrative_place_qids": places, "linked_label_qids": sorted(set(genres + topics + characters + places + series_ids + [i.value for c in contributors for i in c.identifiers]) & set(linked_labels or {}))})
                    record.extra.update({"title_assertions": title_assertions,
                                         "title_assertion_policy": "published_title_candidates_v1; existing label retained; all nondeprecated P1476 values retained as alternative; no original/translation-language inference"})
                    if kind == "edition":
                        record.extra.update({"relationship_basis": "P629 version, edition or translation of", "edition_instance_qids": [v["id"] for v in claim_values(entity, "P31") if isinstance(v, dict) and "id" in v], "isbn_claims": {p: claim_values(entity, p) for p in ("P212", "P957")}, "author_source": "edition entity claims.P50 only; no copy from work", "edition_entity_review_required": "P629 may identify a version or translation rather than a published edition"})
                        evidence.append(Evidence(evidence_id=f"wd:{qid}:edition:{fetched.artifact.sha256[:12]}", kind="bibliographic", text="Edition/version/translation relationships: " + ", ".join(i.value for i in record.work_refs) + "; ISBN: " + ", ".join(i.value for i in identifiers if i.source == "isbn") + "; edition language: " + str(language) + "; edition publication year: " + str(record.publication_year) + "; translators: " + ", ".join(c.name for c in contributors if c.role == "translator"), source_url=url, rights_policy_id="wikidata-cc0", llm_input_allowed=True, language=language, locator="claims.P629;claims.P212;claims.P957;claims.P407;claims.P577;claims.P655"))
                        record.evidence = evidence
                    collection.records.append(record)
            except (ValueError, KeyError, TypeError) as exc:
                collection.status = "failed"
                collection.errors.append(f"invalid entity response: {exc}")
                break
        return collection
