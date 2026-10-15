"""Opt-in exact-revision article sections with persistent CC BY-SA attribution.

This adapter never treats article text as CC0. Source sections are preserved as
wikitext; inline quotations and imported/fair-use text require separate review.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from recommend_book.models import Evidence, Identifier, SourceRecord, Title
from recommend_book.rights import RightsRegistry
from .core import CollectionResult, HTTPFetcher


def section_text(wikitext: str, section: str) -> str:
    if section == "lead":
        return re.split(r"(?m)^={2,6}\s*[^=\n]+\s*={2,6}\s*$", wikitext, maxsplit=1)[0].strip()
    headings = list(re.finditer(r"(?m)^(={2,6})\s*([^=\n]+?)\s*\1\s*$", wikitext))
    for index, heading in enumerate(headings):
        if heading[2].strip().casefold() == section.casefold():
            level = len(heading[1])
            end = next((h.start() for h in headings[index + 1:] if len(h[1]) <= level), len(wikitext))
            return wikitext[heading.end():end].strip()
    raise ValueError(f"article section missing: {section}")


class WikipediaAdapter:
    def __init__(self, fetcher: HTTPFetcher, *, enabled: bool = False):
        self.fetcher, self.enabled = fetcher, enabled

    def fetch_revision(self, language: str, revision_id: int, *, work_id: str,
                       section: str, reviewed_no_third_party_text: bool = False,
                       allow_licensed_llm: bool = False) -> CollectionResult:
        if not self.enabled:
            raise ValueError("Wikipedia requires configuration opt-in for licensed text")
        if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z]+)?", language) or revision_id < 1 or not re.fullmatch(r"Q[1-9][0-9]*", work_id):
            raise ValueError("invalid language/revision/work reference")
        fetched = self.fetcher.get(f"https://{language}.wikipedia.org/w/api.php", source="wikipedia", params={"action": "query", "prop": "revisions", "revids": revision_id, "rvprop": "ids|timestamp|content", "rvslots": "main", "format": "json", "formatversion": 2})
        if fetched.status != "success":
            return CollectionResult(status="failed", errors=[fetched.error or "Wikipedia failed"])
        try:
            payload = json.loads(fetched.content)
            page = payload["query"]["pages"][0]
            revision = page["revisions"][0]
            if revision["revid"] != revision_id:
                raise ValueError("response revision differs from requested immutable revision")
            wikitext = revision["slots"]["main"].get("content", revision["slots"]["main"].get("*"))
            if not isinstance(wikitext, str):
                raise ValueError("revision text missing/suppressed")
            text = section_text(wikitext, section)
            url = f"https://{language}.wikipedia.org/w/index.php?oldid={revision_id}"
            history = f"https://{language}.wikipedia.org/w/index.php?title={quote(page['title'], safe='')}&action=history"
            fulfilled = {"attribution", "share_alike"}
            if reviewed_no_third_party_text:
                fulfilled.add("reviewed_no_third_party_text")
            allowed = allow_licensed_llm and RightsRegistry.default().permits("wikipedia-text", "note", "external_llm", fulfilled)
            evidence = Evidence(evidence_id=f"wp:{language}:{revision_id}:{section}", kind="note", text=text, source_url=url, rights_policy_id="wikipedia-text", llm_input_allowed=allowed, language=language, locator=f"revision:{revision_id};section:{section}")
            record = SourceRecord(source="wikipedia", source_id=f"{language}:{revision_id}:{section}", source_url=url, retrieved_at=fetched.artifact.retrieved_at, provider_id="wikipedia", record_kind="work", titles=[Title(value=page["title"], language=language)], identifiers=[Identifier(source="wikidata", value=work_id, scope="work")], evidence=[evidence], raw_sha256=fetched.artifact.sha256, raw_path=fetched.artifact.path, rights_policy_id="wikipedia-text", extra={"page_id": page.get("pageid"), "revision_id": revision_id, "revision_timestamp": revision.get("timestamp"), "section": section, "text_format": "wikitext", "text_license": "CC-BY-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/", "attribution": {"permalink": url, "history_url": history, "credit": f"Wikipedia contributors: {page['title']}", "changes": "section extracted; original wikitext retained"}, "reviewed_no_third_party_text": reviewed_no_third_party_text, "inherited_text_restrictions": ["attribution", "share_alike", "mark_changes"], "license_scope": "article prose and adaptations; no CC0 relicense; linked content and quoted/fair-use material excluded"})
            return CollectionResult(records=[record])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return CollectionResult(status="failed", errors=[f"invalid Wikipedia revision: {exc}"])
