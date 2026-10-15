"""Exact-revision English originals under a narrowly recorded JP/US rights review.

Wikisource renderings can include transcluded pages: a chapter oldid alone does
not freeze them. Raw rendered bytes and dependency revision IDs are therefore
retained. Contributor text conservatively keeps CC BY-SA 4.0 obligations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote

from recommend_book.models import Contributor, Evidence, Identifier, SourceRecord, Title
from recommend_book.rights import RightsRegistry
from .core import HTTPFetcher

API = 'https://en.wikisource.org/w/api.php'
POLICY = 'wikisource-reviewed-original'
OBLIGATIONS = {'per_work_original_rights_checked', 'attribution', 'share_alike', 'mark_changes'}


@dataclass
class OriginalRightsReview:
    author: str
    author_death_year: int
    first_publication_year: int
    edition_year: int
    original_language: str
    language: str
    jurisdictions: list[str]
    author_revision_url: str
    author_fact_quote: str
    edition_revision_url: str
    edition_fact_quote: str
    no_translation: bool
    no_modern_annotations: bool

    def verify(self):
        if (not 1 <= self.author_death_year <= 1900 or
            not 1 <= self.first_publication_year <= self.edition_year <= 1900 or
            self.original_language != 'en' or self.language != 'en' or
            set(self.jurisdictions) != {'JP', 'US'} or
            not self.no_translation or not self.no_modern_annotations):
            raise ValueError('original rights review outside the JP/US historic-English scope')
        for url in (self.author_revision_url, self.edition_revision_url):
            if not re.fullmatch(r'https://en\.wikisource\.org/w/index\.php\?oldid=[1-9][0-9]*', url):
                raise ValueError('immutable primary-source fact citation required')
        if (not self.author or not self.author_fact_quote or not self.edition_fact_quote or
            str(self.author_death_year) not in self.author_fact_quote or
            str(self.edition_year) not in self.edition_fact_quote):
            raise ValueError('author-death and edition-year quoted facts required')
        return self


class _Paragraphs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.current = None
        self.paragraphs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get('class', '').split())
        blocked = bool(classes & {'header', 'headerContainer', 'reflist', 'reference', 'noprint', 'ws-noexport', 'mw-editsection', 'licenseContainer', 'references'}) or tag in {'script','style','nav','sup'}
        if tag not in {'br','img','hr','meta','link','input','wbr'}:
            self.stack.append((tag, blocked or any(x[1] for x in self.stack)))
        if tag == 'p' and not any(x[1] for x in self.stack):
            self.current = []
        if tag == 'br' and self.current is not None:
            self.current.append(' ')

    def handle_endtag(self, tag):
        if tag == 'p' and self.current is not None:
            text = re.sub(r'\s+', ' ', ''.join(self.current)).strip()
            if text:
                self.paragraphs.append(text)
            self.current = None
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.current is not None and not any(x[1] for x in self.stack):
            self.current.append(data)


def chapter_text(html: str) -> str:
    parser = _Paragraphs()
    parser.feed(html)
    return '\n\n'.join(parser.paragraphs)


def make_excerpts(text: str, *, revision_id: int, count: int = 2, size: int = 1600) -> list[dict]:
    if len(text) < size or count not in (1,2):
        raise ValueError('insufficient prose or invalid excerpt count')
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    starts = [0] if count == 1 else [0, max(size, len(text)//3)]
    if starts[-1] + size > len(text):
        raise ValueError('insufficient non-overlapping prose')
    return [{'start':start, 'end':start+size, 'text':text[start:start+size],
             'normalized_sha256':digest,
             'locator':f'revision:{revision_id};normalized-sha256:{digest};chars:{start}-{start+size};offset_unit:unicode_codepoints'} for start in starts]


def fulfilled_for_record(record: SourceRecord) -> set[str]:
    """Recheck recorded scope before fulfilling the common policy's obligations.

    This checks the provenance contract; it is not a legal opinion or a fresh
    independent bibliographic review. Never infer this obligation from a policy
    ID or from llm_input_allowed alone.
    """
    try:
        review=OriginalRightsReview(**record.extra['original_rights_review']).verify()
        if (record.source!='wikisource' or record.rights_policy_id!=POLICY or
            record.original_language!='en' or record.language!='en' or
            record.publication_year!=review.edition_year or
            record.first_publication_year!=review.first_publication_year or
            review.author not in [c.name for c in record.contributors if c.role=='author'] or
            len(record.evidence)!=2 or len(record.extra['chapters'])!=2):
            return set()
        if not OBLIGATIONS <= set(record.extra.get('fulfilled_obligations',[])):
            return set()
        for evidence in record.evidence:
            if (evidence.kind!='excerpt' or evidence.language!='en' or
                evidence.expression_ref is not None or evidence.rights_policy_id!=POLICY or
                not evidence.license_url or not evidence.attribution.get('history_url') or
                not evidence.attribution.get('credit') or not evidence.attribution.get('changes') or
                not OBLIGATIONS <= set(evidence.inherited_restrictions)):
                return set()
        return set(OBLIGATIONS)
    except (KeyError,TypeError,ValueError):
        return set()


class WikisourceAdapter:
    def __init__(self, fetcher: HTTPFetcher):
        self.fetcher = fetcher

    def query(self, **params):
        fetched = self.fetcher.get(API, source='wikisource', params={'format':'json','formatversion':2,'maxlag':5, **params})
        if fetched.status != 'success':
            raise ValueError(fetched.error or 'Wikisource request failed')
        result = json.loads(fetched.content)
        if 'error' in result:
            raise ValueError(f"Wikisource API error: {result['error']}")
        return result, fetched.artifact

    def pages(self, titles: list[str]):
        payload, artifact = self.query(action='query', titles='|'.join(titles), prop='revisions|pageprops', rvprop='ids|timestamp|content', rvslots='main', redirects=1)
        pages = payload['query']['pages']
        if any('missing' in p or not p.get('revisions') for p in pages):
            raise ValueError('Wikisource page missing/suppressed')
        return pages, artifact

    def render(self, revision_id: int):
        payload, artifact = self.query(action='parse', oldid=revision_id, prop='text|links|templates|properties')
        parsed = payload['parse']
        if parsed.get('revid') != revision_id:
            raise ValueError('render revision mismatch')
        html = parsed['text']
        if isinstance(html,dict):
            html = html['*']
        dependencies = [x.get('*',x.get('title')) for x in parsed.get('templates',[]) if x.get('ns') == 104]
        dependency_versions = []
        for index in range(0,len(dependencies),50):
            pages, _ = self.pages(dependencies[index:index+50])
            dependency_versions.extend({'title':p['title'],'revision_id':p['revisions'][0]['revid']} for p in pages)
        return html, artifact, dependency_versions

    def record(self, *, title: str, work_id: str, review: OriginalRightsReview,
               chapter_pages: list[dict], author_text: str, edition_text: str) -> SourceRecord:
        review.verify()
        if review.author_fact_quote not in author_text or review.edition_fact_quote not in edition_text:
            raise ValueError('quoted rights facts are not present in fetched primary-source text')
        if len(chapter_pages) != 2 or chapter_pages[0]['pageid'] == chapter_pages[1]['pageid']:
            raise ValueError('two distinct chapter locations required')
        evidence, captures = [], []
        for page in chapter_pages:
            revision = page['revisions'][0]
            html, artifact, dependencies = self.render(revision['revid'])
            text = chapter_text(html)
            normalized = self.fetcher.store.save(text.encode(),source='wikisource-normalized',url=f"https://en.wikisource.org/w/index.php?oldid={revision['revid']}",metadata={'chapter':page['title'],'changes':'paragraph-only extraction; headers, images, notes and navigation removed; whitespace normalized'})
            excerpt = make_excerpts(text,revision_id=revision['revid'],count=1)[0]
            url = normalized.source_url
            history = f"https://en.wikisource.org/w/index.php?title={quote(page['title'],safe='')}&action=history"
            allowed = RightsRegistry.default().permits(POLICY,'excerpt','external_llm',OBLIGATIONS)
            attribution={'credit':f"Wikisource contributors: {page['title']}",'permalink':url,'history_url':history,'changes':'paragraph extraction; editorial headers/notes/navigation/images excluded; whitespace normalized; excerpt sliced'}
            evidence.append(Evidence(evidence_id=f"ws:en:{revision['revid']}:chars:{excerpt['start']}-{excerpt['end']}",kind='excerpt',text=excerpt['text'],source_url=url,rights_policy_id=POLICY,llm_input_allowed=allowed,language='en',locator=excerpt['locator'],license='CC-BY-SA-4.0 (contributor text); historic original JP/US scoped review',license_url='https://creativecommons.org/licenses/by-sa/4.0/',attribution=attribution,inherited_restrictions=sorted(OBLIGATIONS)))
            captures.append({'chapter':page['title'],'revision_id':revision['revid'],'revision_timestamp':revision['timestamp'],'render_raw_sha256':artifact.sha256,'render_raw_path':artifact.path,'normalized_sha256':normalized.sha256,'normalized_path':normalized.path,'dependency_revisions_after_render':dependencies,'dependency_freeze_limit':'Chapter oldid does not pin transclusions; captured rendering bytes are authoritative. Dependency revisions observed after render are audit context, not proof of atomic snapshot.','attribution':{'credit':f"Wikisource contributors: {page['title']}",'permalink':url,'history_url':history,'license_url':'https://creativecommons.org/licenses/by-sa/4.0/','changes':'paragraph extraction; editorial headers/notes/navigation/images excluded; whitespace normalized; excerpt sliced'}})
        return SourceRecord(source='wikisource',source_id=str(chapter_pages[0]['revisions'][0]['revid']),source_url=review.edition_revision_url,retrieved_at=artifact.retrieved_at,provider_id='wikisource',record_kind='work',titles=[Title(value=title,language='en')],contributors=[Contributor(name=review.author,role='author')],identifiers=[Identifier(source='wikidata' if work_id.startswith('Q') else 'wikisource',value=work_id,scope='work')],original_language='en',language='en',first_publication_year=review.first_publication_year,publication_year=review.edition_year,evidence=evidence,raw_sha256=artifact.sha256,raw_path=artifact.path,rights_policy_id=POLICY,extra={'original_rights_review':asdict(review),'fulfilled_obligations':sorted(OBLIGATIONS),'rights_scope':'Recorded JP and US conditions only; no worldwide public-domain guarantee. Contributor editorial text retains CC BY-SA 4.0. No images, modern annotations or translation rights inherited.','human_gold':False,'rights_review_method':'Primary-source facts inspected and matched by collector; not legal opinion or human label annotation.','chapters':captures})
