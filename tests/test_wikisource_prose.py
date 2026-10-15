import hashlib
import pytest
from recommend_book.ingestion.wikisource import OriginalRightsReview, chapter_text, make_excerpts, fulfilled_for_record
from recommend_book.rights import RightsRegistry
import httpx
from recommend_book.ingestion.core import HTTPFetcher, RawStore
from recommend_book.ingestion.wikisource import WikisourceAdapter


def review(**updates):
    values = dict(author='Jane Austen', author_death_year=1817, first_publication_year=1813,
                  edition_year=1813, original_language='en', language='en', jurisdictions=['JP','US'],
                  author_revision_url='https://en.wikisource.org/w/index.php?oldid=1',
                  author_fact_quote='Jane Austen (1775–1817)',
                  edition_revision_url='https://en.wikisource.org/w/index.php?oldid=2',
                  edition_fact_quote='Jane Austen, Pride and Prejudice (1st ed, 1813, vol 1).pdf',
                  no_translation=True, no_modern_annotations=True)
    return OriginalRightsReview(**(values | updates))


@pytest.mark.parametrize('updates', [{'author_death_year':1901}, {'edition_year':1923}, {'first_publication_year':1901},
    {'original_language':'fr'}, {'no_translation':False}, {'no_modern_annotations':False},
    {'jurisdictions':['JP','US','FR']}, {'author_fact_quote':''}, {'edition_fact_quote':''}])
def test_per_work_review_rejects_unverified_or_out_of_scope(updates):
    with pytest.raises(ValueError):
        review(**updates).verify()


def test_default_policy_is_conditional_not_blanket_permission():
    registry = RightsRegistry.default()
    assert not registry.permits('wikisource-reviewed-original','excerpt','external_llm')
    fulfilled = {'per_work_original_rights_checked','attribution','share_alike','mark_changes'}
    assert registry.permits('wikisource-reviewed-original','excerpt','external_llm',fulfilled)
    assert not registry.permits('wikisource-reviewed-original','synopsis','external_llm',fulfilled)


def test_chapter_extraction_excludes_editorial_header_and_footnotes():
    html = '<div class="mw-parser-output"><div class="header"><p>Modern description</p></div><p>It is a truth <sup class="reference">[1]</sup> universally acknowledged.</p><div class="reflist"><p>Modern annotation</p></div><p>Second paragraph.</p></div>'
    assert chapter_text(html) == 'It is a truth universally acknowledged.\n\nSecond paragraph.'


def test_excerpts_have_verifiable_exact_character_locators():
    text = 'A paragraph about a family.\n\n' * 150
    excerpts = make_excerpts(text, revision_id=42, count=2, size=1200)
    assert len(excerpts) == 2
    for item in excerpts:
        start,end = item['start'],item['end']
        assert item['text'] == text[start:end]
        assert item['normalized_sha256'] == hashlib.sha256(text.encode()).hexdigest()
        assert f'chars:{start}-{end}' in item['locator']


def test_captured_chapters_keep_exact_hash_license_and_dependency_audit(tmp_path):
    prose='The family returned to the old house and found a letter. ' * 60
    def respond(request):
        if request.url.params['action']=='parse':
            rev=int(request.url.params['oldid'])
            return httpx.Response(200,json={'parse':{'revid':rev,'text':'<div class="mw-parser-output"><p>'+prose+'</p></div>','templates':[{'ns':104,'title':'Page:Historic.djvu/8'}]}})
        return httpx.Response(200,json={'query':{'pages':[{'title':'Page:Historic.djvu/8','pageid':7,'revisions':[{'revid':999,'timestamp':'2026-01-01','slots':{'main':{'content':prose}}}]}]}})
    adapter=WikisourceAdapter(HTTPFetcher(RawStore(tmp_path),client=httpx.Client(transport=httpx.MockTransport(respond)),min_interval=0))
    chapters=[{'title':f'Novel/Chapter {n}','pageid':n,'revisions':[{'revid':30+n,'timestamp':'2026-01-01'}]} for n in (1,2)]
    record=adapter.record(title='Novel',work_id='ws:1',review=review(),chapter_pages=chapters,author_text='Jane Austen (1775–1817)',edition_text='Jane Austen, Pride and Prejudice (1st ed, 1813, vol 1).pdf')
    assert len(record.evidence)==2
    assert all(e.llm_input_allowed and e.license_url and e.attribution['history_url'] for e in record.evidence)
    assert all('share_alike' in e.inherited_restrictions for e in record.evidence)
    for capture,evidence in zip(record.extra['chapters'],record.evidence):
        from pathlib import Path
        text=Path(capture['normalized_path']).read_text(encoding='utf8')
        assert hashlib.sha256(text.encode()).hexdigest()==capture['normalized_sha256']
        assert evidence.text==text[:1600]
        assert capture['dependency_revisions_after_render']==[{'title':'Page:Historic.djvu/8','revision_id':999}]
    assert 'per_work_original_rights_checked' in fulfilled_for_record(record)
    record.extra['original_rights_review']['author_death_year']=1950
    assert not fulfilled_for_record(record)


def test_missing_source_fact_is_rejected_before_network():
    adapter=WikisourceAdapter(None)
    with pytest.raises(ValueError,match='quoted rights facts'):
        adapter.record(title='Novel',work_id='ws:1',review=review(),chapter_pages=[],author_text='unverified',edition_text='unverified')
