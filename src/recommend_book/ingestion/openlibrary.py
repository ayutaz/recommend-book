"""Local streaming adapter for official five-column gzip dumps. No bulk API calls."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import re
from typing import Callable, Iterator

from recommend_book.models import Contributor, Evidence, Identifier, SourceRecord, Title
from recommend_book.rights import RightsRegistry
from .core import RawStore


def year(text: str | None) -> int | None:
    match = re.search(r"(?<!\d)(1\d{3}|20\d{2})(?!\d)", text or "")
    return int(match[0]) if match else None


def iter_openlibrary_dump(path: str | Path, store: RawStore, *, source_url: str,
                          predicate: Callable[[dict], bool] | None = None, limit: int | None = None,
                          author_names: dict[str, str] | None = None,
                          dump_line_numbers: dict[str, int] | None = None,
                          dump_metadata: dict | None = None) -> Iterator[SourceRecord]:
    path = Path(path)
    if limit is not None and limit < 0:
        raise ValueError("limit must be nonnegative")
    opener = gzip.open if path.suffix == ".gz" else open
    accepted = 0
    rights = RightsRegistry.default()
    with opener(path, "rb") as stream:
        for line_number, raw in enumerate(stream, 1):
            if limit is not None and accepted >= limit:
                return
            try:
                fields = raw.rstrip(b"\r\n").split(b"\t", 4)
                if len(fields) != 5:
                    raise ValueError("expected five tab-separated dump columns")
                item = json.loads(fields[4])
                kind = {b"/type/work": "work", b"/type/edition": "edition", b"/type/author": "agent"}.get(fields[0])
                if kind is None or (predicate is not None and not predicate(item)):
                    continue
            except (ValueError, UnicodeError) as exc:
                raise ValueError(f"invalid Open Library dump line {line_number}: {exc}") from exc
            original_line = (dump_line_numbers or {}).get(fields[1].decode(), line_number)
            artifact = store.save(raw, source="openlibrary", url=source_url, metadata={**(dump_metadata or {}), "dump_line": original_line, "key": fields[1].decode(), "revision": fields[2].decode(), "last_modified": fields[3].decode()})
            key = item.get("key", fields[1].decode())
            url = "https://openlibrary.org" + key
            scope = {"work": "work", "edition": "edition", "agent": "agent"}[kind]
            identifiers = [Identifier(source="openlibrary", value=key, scope=scope)]
            for isbn in item.get("isbn_13", []) + item.get("isbn_10", []):
                if kind == "edition":
                    identifiers.append(Identifier(source="isbn", value=isbn, scope="edition"))
            for source, values in item.get("identifiers", {}).items():
                for value in values if isinstance(values, list) else [values]:
                    if source == "wikidata":
                        identifiers.append(Identifier(source="wikidata", value=str(value), scope=scope))
            for source, value in item.get("remote_ids", {}).items():
                if source == "wikidata":
                    identifiers.append(Identifier(source="wikidata", value=value, scope=scope))
            contributors = []
            for author in item.get("authors", []):
                ref = author.get("author", author).get("key")
                if ref:
                    contributors.append(Contributor(name=(author_names or {}).get(ref, ref), identifiers=[Identifier(source="openlibrary", value=ref, scope="agent")]))
            subjects = item.get("subjects", [])
            evidence = []
            if subjects:
                evidence.append(Evidence(evidence_id=f"ol:{key}:subjects:{artifact.sha256[:12]}", kind="subject", text="; ".join(subjects), source_url=url, rights_policy_id="openlibrary-metadata", llm_input_allowed=rights.permits("openlibrary-metadata", "subject", "external_llm"), locator="subjects"))
            description = item.get("description")
            if isinstance(description, dict):
                description = description.get("value")
            if description:
                evidence.append(Evidence(evidence_id=f"ol:{key}:description:{artifact.sha256[:12]}", kind="description", text=str(description), source_url=url, rights_policy_id="openlibrary-prose", locator="description"))
            languages = [x.get("key", "").rsplit("/", 1)[-1] for x in item.get("languages", [])]
            titles = [Title(value=item.get("title") or item.get("name") or key)]
            titles.extend(Title(value=v, kind="alternative") for v in item.get("alternative_names", []) + item.get("other_titles", []))
            yield SourceRecord(source="openlibrary", source_id=key, source_url=url, retrieved_at=artifact.retrieved_at, provider_id="openlibrary", record_kind=kind, titles=titles, contributors=contributors, identifiers=identifiers, work_refs=[Identifier(source="openlibrary", value=w["key"], scope="work") for w in item.get("works", [])], language=languages[0] if len(languages) == 1 else None, first_publication_year=year(item.get("first_publish_date")) if kind == "work" else None, publication_year=year(item.get("publish_date")) if kind == "edition" else None, publisher="; ".join(item.get("publishers", [])) or None, subjects=subjects, evidence=evidence, raw_sha256=artifact.sha256, raw_path=artifact.path, rights_policy_id="openlibrary-metadata", extra={**(dump_metadata or {}), "dump_url": source_url, "dump_line": original_line, "languages": languages, "revision": fields[2].decode()})
            accepted += 1
