import pytest

from recommend_book.normalization import normalize_isbn, normalize_text


def test_unicode_normalization_keeps_volume_and_subtitle():
    assert normalize_text("　雪の国　２：帰還  ") == "雪の国 2:帰還"
    assert normalize_text("雪の国 1") != normalize_text("雪の国 2")


@pytest.mark.parametrize("raw,expected", [("978-0-306-40615-7", "9780306406157"), ("０－３０６－４０６１５－２", "9780306406157"), ("0-8044-2957-X", "9780804429573")])
def test_isbn_validates_and_converts(raw, expected):
    assert normalize_isbn(raw) == expected


@pytest.mark.parametrize("raw", ["9780306406158", "0306406153", "123", "9790306406157", "ISBN 9780306406157", "9780306406157junk"])
def test_invalid_isbn_not_silently_accepted(raw):
    assert normalize_isbn(raw) is None
