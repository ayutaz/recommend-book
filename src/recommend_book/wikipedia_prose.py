"""Conservative automatic screening of licensed article prose, not human review.

Only authored prose is selected. Quoted, templated, externally linked and
unrecognized markup paragraphs are withheld. Screening cannot prove the absence
of an unmarked copyright problem; that limitation is retained in the manifest.
"""
from dataclasses import dataclass, field
import html
import re

import mwparserfromhell

SCREEN_VERSION = "wikipedia-prose-v2"
BLOCKED_NOTICES = re.compile(r"copyvio|copyright violation|non.?free|fair.?use|copypaste|copy.?paste|copyrighted", re.I)
QUOTATION_TEMPLATES = re.compile(r"quote|quotation|blockquote|cquote|poem|lyrics|epigraph", re.I)
QUOTES = re.compile(r'["“”«»「」『』]')


@dataclass
class ScreenResult:
    text: str = ""
    accepted_paragraphs: int = 0
    rejected_paragraphs: list[dict] = field(default_factory=list)
    article_blocked: bool = False
    version: str = SCREEN_VERSION


def screen_section(wikitext: str, *, article_wikitext: str | None = None, factual_titles=()) -> ScreenResult:
    result = ScreenResult()
    article = mwparserfromhell.parse(article_wikitext if article_wikitext is not None else wikitext)
    if any(BLOCKED_NOTICES.search(str(t.name)) for t in article.filter_templates(recursive=True)):
        result.article_blocked = True
        result.rejected_paragraphs.append({"index": None, "reason": "article_rights_notice"})
        return result
    accepted = []
    for index, paragraph in enumerate(re.split(r"\n\s*\n", wikitext)):
        code = mwparserfromhell.parse(paragraph)
        # Citations belong to provenance, not to the summary supplied to the LLM.
        for tag in list(code.filter_tags(recursive=True)):
            if str(tag.tag).strip().casefold() in {"ref", "references"}:
                try:
                    code.remove(tag, recursive=True)
                except ValueError:
                    pass  # already removed with its containing reference
        templates = code.filter_templates(recursive=True)
        reason = None
        if any(QUOTATION_TEMPLATES.search(str(t.name)) for t in templates):
            reason = "quotation_template"
        elif templates:
            reason = "template"
        elif code.filter_external_links(recursive=True):
            reason = "external_link"
        elif any(not (str(tag.tag) in {"i", "b"} and tag.wiki_markup in {"''", "'''", "'''''"}) for tag in code.filter_tags(recursive=True)):
            reason = "html_tag"
        elif any(re.match(r"^(?:File|Image|Category|ファイル|画像):", str(link.title), re.I) for link in code.filter_wikilinks(recursive=True)):
            reason = "nonprose_link"
        elif re.search(r"(?m)^\s*(?:[|!*#;:]|\{\|)", str(code)):
            reason = "nonprose_markup"
        plain = html.unescape(code.strip_code(normalize=True, collapse=True)).strip()
        # Confirmed work titles are factual metadata. Other quoted passages stay withheld.
        for title in factual_titles:
            title = str(title).strip()
            if not title or len(title) > 200:
                continue
            for opening, closing in (("『", "』"), ("「", "」"), ('"', '"'), ("“", "”"), ("«", "»")):
                plain = plain.replace(opening + title + closing, title)
        if reason is None and QUOTES.search(plain):
            reason = "quotation_marker"
        if reason is None and any(token in plain for token in ("{{", "}}", "[[", "]]", "<", ">")):
            reason = "unparsed_markup"
        if reason:
            result.rejected_paragraphs.append({"index": index, "reason": reason})
        elif plain:
            accepted.append(plain)
    result.text = "\n\n".join(accepted)
    result.accepted_paragraphs = len(accepted)
    return result
