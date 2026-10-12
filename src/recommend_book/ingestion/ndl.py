"""NDL SRU cutoff awareness and durable OAI-PMH continuation checkpoints."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from recommend_book.models import Contributor, Evidence, Identifier, SourceRecord, Title
from recommend_book.normalization import normalize_isbn
from recommend_book.rights import RightsRegistry
from .core import CollectionResult, HTTPFetcher, RawArtifact, utc_now
from .openlibrary import year

SRU_URL = "https://ndlsearch.ndl.go.jp/api/sru"
OAI_URL = "https://ndlsearch.ndl.go.jp/api/oaipmh"
SRU = "http://www.loc.gov/zing/srw/"
OAI = "http://www.openarchives.org/OAI/2.0/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
DCNDL = "http://ndl.go.jp/dcndl/terms/"
DC = "http://purl.org/dc/elements/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
FOAF = "http://xmlns.com/foaf/0.1/"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _value_text(element: ET.Element) -> str:
    """Read semantic value nodes, never concatenate a title/name with its reading."""
    for tag in (f"{{{RDF}}}value", f"{{{FOAF}}}name", f"{{{RDFS}}}label"):
        node = element.find(".//" + tag)
        if node is not None and node.text:
            return " ".join(node.text.split())
    return " ".join((element.text or "").split())


def _texts(element: ET.Element, name: str) -> list[str]:
    return list(dict.fromkeys(v for child in element.iter() if _local(child.tag) == name and (v := _value_text(child))))


def _role(value: str | None, default: str = "author") -> str:
    value = (value or "").strip()
    return {"著": "author", "著者": "author", "作": "author", "原作": "author", "author": "author", "訳": "translator", "翻訳": "translator", "訳者": "translator", "translator": "translator", "編": "editor", "編集": "editor", "編者": "editor", "editor": "editor", "絵": "illustrator", "イラスト": "illustrator", "illustrator": "illustrator"}.get(value, default)


def _contributor(element: ET.Element, default: str = "author") -> Contributor | None:
    name = _value_text(element)
    if not name:
        return None
    roles = _texts(element, "role")
    role = _role(roles[0], default) if roles else default
    # Literal credit statements explicitly mark roles; retain their verbatim form in extra.
    match = re.match(r"^(.*?)\s*[\[（(]?\s*(著者|著|原作|作|訳者|訳|翻訳|編者|編|編集|絵|イラスト)\s*[\]）)]?$", name)
    if match and match[1].strip():
        name, role = match[1].strip(), _role(match[2], default)
    identifiers = []
    for node in element.iter():
        for key, value in node.attrib.items():
            if _local(key) in {"about", "resource"} and value.startswith(("http://id.ndl.go.jp/auth/", "https://id.ndl.go.jp/auth/")):
                identifiers.append(Identifier(source="ndl-authority", value=value, scope="agent"))
    return Contributor(name=name, role=role, identifiers=identifiers)


def assess_sru_record(record: SourceRecord, *, ndc_prefix: str | None = None,
                      publication_year: int | None = None, title_contains: str | None = None,
                      language: str | None = None, isbn: str | None = None,
                      translation_required: bool = False) -> dict:
    """Admit returned metadata only when the independently checkable constraints match.

    A successful search response does not itself prove that a returned record is a novel,
    Japanese-language, or a translation. Missing checkable values cause quarantine.
    """
    reasons = []
    if title_contains and not any(title_contains.casefold() in t.value.casefold() for t in record.titles):
        reasons.append("title_not_matched")
    if ndc_prefix and not any(v.startswith(ndc_prefix) for v in record.extra.get("ndc_codes", [])):
        reasons.append("ndc_not_matched")
    if publication_year is not None and record.publication_year != publication_year:
        reasons.append("publication_year_not_matched")
    if language and record.language != language:
        reasons.append("language_not_matched")
    if isbn:
        normalized = normalize_isbn(isbn)
        if normalized is None or not any(i.source == "isbn" and normalize_isbn(i.value) == normalized for i in record.identifiers):
            reasons.append("isbn_not_matched")
    if translation_required and not (record.extra.get("translation_source_language") and record.extra["translation_source_language"] != record.language):
        reasons.append("translation_not_explicit")
    return {"accepted": not reasons, "reasons": reasons, "basis": "returned bibliographic fields, not search total or title/model inference", "source_id": record.source_id, "raw_sha256": record.raw_sha256}


class NDLAdapter:
    def __init__(self, fetcher: HTTPFetcher, *, approved_providers: set[str] | None = None):
        self.fetcher = fetcher
        self.rights = RightsRegistry.default()
        self.approved_providers = approved_providers if approved_providers is not None else {"iss-ndl-opac", "iss-ndl-opac-national", "aozora"}

    def _check_provider(self, provider: str) -> None:
        if not re.fullmatch(r"[a-z0-9-]+", provider) or provider not in self.approved_providers or self.rights.for_provider("ndl", provider, "bibliographic") is None:
            raise ValueError(f"provider is not rights-reviewed/approved: {provider}")

    def _normalize(self, element: ET.Element, artifact: RawArtifact, provider: str,
                   *, source_id: str | None = None, datestamp: str | None = None, locator: str = "recordData") -> SourceRecord:
        providers = _texts(element, "dataProvider") + _texts(element, "dataProviderID")
        explicit = [p for p in providers if re.fullmatch(r"[a-z0-9-]+", p)]
        if explicit and any(p != provider for p in explicit):
            raise ValueError(f"response provider mismatch: requested {provider}, saw {explicit}")
        urls = [v for child in element.iter() for k, v in child.attrib.items() if _local(k) == "about" and v.startswith("https://ndlsearch.ndl.go.jp/books/")]
        identifiers_text = _texts(element, "identifier")
        url = (urls[0] if urls else next((v for v in identifiers_text if v.startswith("https://ndlsearch.ndl.go.jp")), artifact.source_url)).split("#", 1)[0]
        source_id = source_id or (url.rsplit("/", 1)[-1] if urls else None)
        if not source_id:
            raise ValueError("NDL record lacks stable identifier; not inventing a record ID")
        expected_repository = {"iss-ndl-opac": "R100000002", "iss-ndl-opac-national": "R100000002", "aozora": "R000000014"}.get(provider)
        actual_repository = re.search(r"R[0-9]{9}", source_id)
        if expected_repository and actual_repository and actual_repository[0] != expected_repository:
            raise ValueError(f"record repository mismatch: provider {provider} requires {expected_repository}, got {actual_repository[0]}")
        policy = self.rights.for_provider("ndl", provider, "bibliographic")
        # Bibliographic values belong to the main BibResource. Administrative creators
        # identify cataloguing institutions and must not become the book's authors.
        resources = [node for node in element.iter() if node.tag == f"{{{DCNDL}}}BibResource" and any(_local(c.tag) in {"title", "creator", "publisher", "subject", "identifier"} for c in node)]
        fallback = next((node for node in element.iter() if any(_local(c.tag) == "title" for c in node)), element)
        bib = resources[0] if resources else fallback
        titles = _texts(bib, "title")
        contributor_nodes = [child for child in list(bib) if _local(child.tag) in {"creator", "contributor"}]
        contributors = []
        for node in contributor_nodes:
            contributor = _contributor(node, "other" if _local(node.tag) == "contributor" else "author")
            if contributor and not any(c.name == contributor.name and c.role == contributor.role for c in contributors):
                contributors.append(contributor)
        identifiers = [Identifier(source="ndl", value=source_id, scope="edition")]
        for child in bib.iter():
            if _local(child.tag) == "identifier":
                datatype = " ".join(child.attrib.values())
                value = "".join(child.itertext()).strip()
                if "ISBN" in datatype.upper() or (not datatype and re.fullmatch(r"[0-9Xx -]{10,17}", value)):
                    identifiers.append(Identifier(source="isbn", value=value, scope="edition"))
        subjects = _texts(bib, "subject")
        ndc_codes = []
        for node in bib.iter():
            if _local(node.tag) != "subject":
                continue
            for attribute in node.attrib.values():
                if "/class/ndc" in attribute:
                    ndc_codes.append(attribute.rsplit("/", 1)[-1])
                elif "NDC" in attribute.upper() and _value_text(node):
                    ndc_codes.append(_value_text(node))
            # Structured classification values may be accompanied by an NDC URI in a child.
            for child in node.iter():
                for attribute in child.attrib.values():
                    if "/class/ndc" in attribute:
                        ndc_codes.append(attribute.rsplit("/", 1)[-1])
        ndc_codes = list(dict.fromkeys(ndc_codes))
        evidence = []
        if subjects:
            evidence.append(Evidence(evidence_id=f"ndl:{source_id}:subject:{artifact.sha256[:12]}", kind="subject", text="; ".join(subjects), source_url=url, rights_policy_id=policy.policy_id, llm_input_allowed=self.rights.permits(policy.policy_id, "subject", "external_llm", {"attribution"}), locator=locator + "/subject"))
        for name, kind in [("abstract", "synopsis"), ("description", "note")]:
            for index, text in enumerate(_texts(bib, name)):
                evidence.append(Evidence(evidence_id=f"ndl:{source_id}:{name}:{index}:{artifact.sha256[:12]}", kind=kind, text=text, source_url=url, rights_policy_id="ndl-prose-unresolved", locator=locator + "/" + name))
        languages = _texts(bib, "language")
        original_languages = _texts(bib, "originalLanguage")
        dates = _texts(bib, "issued") or _texts(bib, "date")
        title_readings = list(dict.fromkeys(value for node in bib if _local(node.tag) == "title" for value in _texts(node, "transcription")))
        return SourceRecord(source="ndl", source_id=source_id, source_url=url, retrieved_at=artifact.retrieved_at, provider_id=provider, record_kind="edition", titles=[Title(value=t) for t in titles], contributors=contributors, identifiers=identifiers, language=languages[0] if len(languages) == 1 else None, publication_year=year(dates[0]) if dates else None, publisher="; ".join(_texts(bib, "publisher")) or None, series="; ".join(_texts(bib, "seriesTitle")) or None, volume="; ".join(_texts(bib, "volume")) or None, subjects=subjects, evidence=evidence, raw_sha256=artifact.sha256, raw_path=artifact.path, rights_policy_id=policy.policy_id, extra={"provider_basis": "explicit record field" if explicit else "provider-restricted request/header setSpec", "provider_values": providers, "oai_datestamp": datestamp, "raw_locator": locator, "ndc_codes": ndc_codes, "translation_source_language": original_languages[0] if len(original_languages) == 1 else None, "creator_statements": [_value_text(n) for n in contributor_nodes], "title_readings": title_readings, "attribution": {"provider": provider, "credit": policy.attribution, "source_url": url, "license_url": "https://creativecommons.org/licenses/by/4.0/", "changes": "bibliographic normalization"}})

    def search_sru(self, query: str, *, provider: str, max_records: int = 100, start_record: int = 1) -> CollectionResult:
        self._check_provider(provider)
        # The PoC only accepts an AND conjunction. It cannot be broadened through
        # another provider clause or an OR branch whose precedence defeats filtering.
        if re.search(r"\b(?:dpid|dpgroupid)\s*(?:=|any|all)", query, re.IGNORECASE):
            raise ValueError("provider conditions are controlled by the adapter")
        without_literals = re.sub(r'"(?:[^"\\]|\\.)*"', '""', query)
        if re.search(r"\b(?:OR|NOT)\b", without_literals, re.IGNORECASE):
            raise ValueError("only AND query conjunctions are supported")
        if not 1 <= start_record <= 500 or not 1 <= max_records <= 500:
            raise ValueError("NDL SRU result positions are limited to 1..500")
        capacity = min(max_records, 501 - start_record)
        fetched = self.fetcher.get(SRU_URL, source="ndl", params={"operation": "searchRetrieve", "version": "1.2", "recordSchema": "dcndl", "recordPacking": "xml", "query": f'{query} AND dpid="{provider}"', "maximumRecords": capacity, "startRecord": start_record})
        if fetched.status != "success":
            return CollectionResult(status="failed", errors=[fetched.error or "SRU failed"])
        try:
            root = ET.fromstring(fetched.content)
            diagnostics = ["; ".join(_texts(d, "uri") + _texts(d, "details") + _texts(d, "message")) for d in root.iter() if _local(d.tag) == "diagnostic"]
            if diagnostics:
                return CollectionResult(status="failed", errors=diagnostics)
            total = int(root.findtext(f"{{{SRU}}}numberOfRecords", "0"))
            records = []
            for index, element in enumerate(root.findall(f".//{{{SRU}}}recordData")):
                if len(element) == 0 and element.text and element.text.lstrip().startswith("<"):
                    element = ET.fromstring(element.text)
                records.append(self._normalize(element, fetched.artifact, provider, locator=f"records[{start_record + index}]/recordData"))
            consumed = start_record - 1 + len(records)
            return CollectionResult(records=records, status="truncated" if consumed < total else "complete", total_available=total, metadata={"query": query, "provider": provider, "sru_limit": 500, "cutoff_reached": total > 500, "next_start_record": consumed + 1 if consumed < min(total, 500) else None})
        except (ET.ParseError, ValueError) as exc:
            return CollectionResult(status="failed", errors=[f"invalid SRU: {exc}"])

    def harvest_oai(self, *, provider: str, checkpoint: str | Path, max_pages: int = 1,
                    from_date: str | None = None, until_date: str | None = None,
                    metadata_prefix: str = "dcndl_v3") -> CollectionResult:
        self._check_provider(provider)
        if max_pages < 1 or metadata_prefix not in {"dcndl", "dcndl_v3", "oai_dc"}:
            raise ValueError("invalid OAI-PMH settings")
        checkpoint = Path(checkpoint)
        state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
        if state and (state.get("provider") != provider or state.get("metadata_prefix") != metadata_prefix):
            raise ValueError("checkpoint provider/format differs from this request")
        if state.get("complete"):
            return CollectionResult(metadata={"resumed_completed_checkpoint": True})
        from_date = from_date or state.get("from_date") or date.today().isoformat()
        until_date = until_date or state.get("until_date")
        start = date.fromisoformat(from_date[:10])
        end = date.fromisoformat(until_date[:10]) if until_date else None
        if end and (end < start or (end - start).days > 366):
            raise ValueError("OAI-PMH date window must be nonnegative and at most one year")
        if state and (state.get("from_date") != from_date or state.get("until_date") != until_date):
            raise ValueError("checkpoint date window differs from this request")
        token = state.get("next_token")
        collection = CollectionResult()
        for page in range(max_pages):
            params = {"verb": "ListRecords", "resumptionToken": token} if token else {"verb": "ListRecords", "metadataPrefix": metadata_prefix, "set": provider, "from": from_date}
            if not token and until_date:
                params["until"] = until_date
            fetched = self.fetcher.get(OAI_URL, source="ndl", params=params)
            if fetched.status != "success":
                collection.status = "failed"
                collection.errors.append(fetched.error or "OAI-PMH failed")
                collection.next_token = token
                break
            try:
                root = ET.fromstring(fetched.content)
                errors = root.findall(f"{{{OAI}}}error")
                if errors and not all(e.get("code") == "noRecordsMatch" for e in errors):
                    raise ValueError("; ".join((e.get("code", "") + ": " + (e.text or "")) for e in errors))
                page_records = []
                deleted = []
                for index, element in enumerate(root.findall(f".//{{{OAI}}}ListRecords/{{{OAI}}}record")):
                    header = element.find(f"{{{OAI}}}header")
                    if header is None:
                        raise ValueError("OAI record missing header")
                    source_id = header.findtext(f"{{{OAI}}}identifier")
                    stamp = header.findtext(f"{{{OAI}}}datestamp")
                    sets = [e.text for e in header.findall(f"{{{OAI}}}setSpec")]
                    # The requested set may be a superset; preserve all setSpecs and reject unreviewed providers.
                    actual = next((s for s in sets if self.rights.for_provider("ndl", s or "", "bibliographic")), provider)
                    self._check_provider(actual)
                    if actual != provider and provider not in sets:
                        raise ValueError(f"unexpected OAI provider {actual}")
                    if header.get("status") == "deleted":
                        deleted.append({"identifier": source_id, "datestamp": stamp, "set_specs": sets})
                        continue
                    metadata = element.find(f"{{{OAI}}}metadata")
                    if metadata is None:
                        raise ValueError("OAI record missing metadata")
                    record = self._normalize(metadata, fetched.artifact, actual, source_id=source_id, datestamp=stamp, locator=f"ListRecords/record[{index}]/metadata")
                    record.extra["set_specs"] = sets
                    page_records.append(record)
                token_node = root.find(f".//{{{OAI}}}resumptionToken")
                next_token = token_node.text if token_node is not None and token_node.text else None
                if token and next_token == token:
                    raise ValueError("OAI server returned repeated resumptionToken")
                # Durable page outputs precede checkpoint advancement, making restart replays safe.
                checkpoint.parent.mkdir(parents=True, exist_ok=True)
                page_path = checkpoint.parent / (checkpoint.stem + ".pages.jsonl")
                with page_path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps({"raw_path": fetched.artifact.path, "raw_sha256": fetched.artifact.sha256, "records": [r.model_dump(mode="json") for r in page_records], "deleted": deleted, "next_token": next_token}, ensure_ascii=False) + "\n")
                state = {"provider": provider, "metadata_prefix": metadata_prefix, "from_date": from_date, "until_date": until_date, "next_token": next_token, "complete": not next_token, "updated_at": utc_now(), "last_raw_path": fetched.artifact.path, "page_log": str(page_path)}
                temp = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
                temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                temp.replace(checkpoint)
                collection.records.extend(page_records)
                collection.metadata.setdefault("deleted", []).extend(deleted)
                token = next_token
                if not token:
                    break
            except (ET.ParseError, ValueError) as exc:
                collection.status = "failed"
                collection.errors.append(f"invalid OAI-PMH: {exc}")
                break
        if collection.status != "failed" and token:
            collection.status = "truncated"
        collection.next_token = token
        collection.metadata["checkpoint"] = str(checkpoint)
        return collection
