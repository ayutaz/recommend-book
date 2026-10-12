from recommend_book.wikipedia_prose import screen_section


def test_retains_original_summary_and_removes_refs_and_markup():
    text = "''A Book'' follows [[Ada]], a detective.<ref>{{cite book|quote=Copyright quotation}}</ref>"
    result = screen_section(text)
    assert result.text == "A Book follows Ada, a detective."
    assert result.accepted_paragraphs == 1
    assert not result.rejected_paragraphs


def test_drops_entire_quoted_and_external_paragraphs():
    text = 'Ada investigates a murder.\n\nThe novel says "protected passage".\n\n[https://example.com prose from another source]'
    result = screen_section(text)
    assert result.text == "Ada investigates a murder."
    assert len(result.rejected_paragraphs) == 2


def test_does_not_convert_quotation_template_to_plaintext():
    result = screen_section('Safe summary.\n\n{{blockquote|unlicensed passage}}')
    assert result.text == "Safe summary."
    assert result.rejected_paragraphs[0]["reason"] == "quotation_template"


def test_article_license_notice_blocks_all_sections():
    result = screen_section("A neutral summary.", article_wikitext="{{copyvio|url=https://example.com}}")
    assert not result.text
    assert result.article_blocked


def test_unknown_template_paragraph_is_excluded():
    result = screen_section('Safe summary.\n\n{{unknown prose|do not import}} and text.')
    assert result.text == "Safe summary."
    assert result.rejected_paragraphs[0]["reason"] == "template"


def test_entities_cannot_hide_quotes_or_imported_html():
    result = screen_section('Good summary.\n\nIt contains &quot;copyright prose&quot;.\n\n<blockquote>Imported quotation</blockquote>')
    assert result.text == "Good summary."
    assert len(result.rejected_paragraphs) == 2


def test_only_confirmed_book_title_is_exempted_from_quote_screen():
    result = screen_section('『作品名』は探偵を主人公とする小説。\n\n本文の「著作物の引用」を紹介する。', factual_titles=["作品名"])
    assert result.text == "作品名は探偵を主人公とする小説。"
    assert len(result.rejected_paragraphs) == 1
