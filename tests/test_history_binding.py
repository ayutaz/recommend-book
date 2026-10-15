from recommend_book.recommendation.history_binding import extract_owner_html_rows, ncode_from_owner_href, recover_owned_pages, sha256
import json
import sqlite3
from types import SimpleNamespace
import pytest


def test_owner_account_link_cannot_become_work_author():
    raw = b'''<a href="https://mypage.syosetu.com/123/">Owner</a>
    <li class="c-up-reaction-item"><div class="c-up-reaction-item__title">
    <a href="https://ncode.syosetu.com/n1234ab/"><b>Fiction &amp; One</b></a></div>
    <input name="score" value="8"></li>'''
    row, = extract_owner_html_rows(raw)
    assert row["ncode"] == "n1234ab"
    assert row["title"] == "Fiction & One"
    assert row["rating"] == 4
    assert row["row_profile_links"] == []
    assert row["author_identity_verified"] is False


@pytest.mark.parametrize("href", [
    "https://ncode.syosetu.com.evil/n1234ab/", "https://ncode.syosetu.com/n1234ab/1/",
    "https://ncode.syosetu.com/n1234ab/?other=1", "https://x@ncode.syosetu.com/n1234ab/",
    "https://ncode.syosetu.com/n1234ab/#edition", "n1234ab",
])
def test_identifier_requires_unambiguous_serial_work_url(href):
    assert ncode_from_owner_href(href) is None


def test_duplicate_title_anchors_and_missing_scores_remain_unknown():
    row, = extract_owner_html_rows(b'''<li class="c-up-reaction-item">
      <p class="c-up-reaction-item__title"><a href="https://ncode.syosetu.com/n1234ab/">One</a>
      <a href="https://ncode.syosetu.com/n5678cd/">Two</a></p></li>''')
    assert row["ncode"] is None
    assert row["rating"] is None


def test_unclosed_owner_row_fails():
    with pytest.raises(ValueError, match="Unclosed"):
        extract_owner_html_rows(b'<li class="c-up-reaction-item">')


def _recovery_fixture(tmp_path, monkeypatch, *, wrong_sha=False, wrong_rating=False):
    raw = b'''<li class="c-up-reaction-item"><p class="c-up-reaction-item__title">
    <a href="https://ncode.syosetu.com/n1234ab/"> One   Fiction </a></p>
    <input name="score" value="8"></li>'''
    database = tmp_path / "owned.sqlite"
    db = sqlite3.connect(database)
    db.executescript('''CREATE TABLE imports(snapshot_id,pages_json);
      CREATE TABLE rating_observations(snapshot_id,ncode,title,my_rating,source_page,row_number,evidence_json);''')
    db.execute("INSERT INTO imports VALUES (?,?)", ("snapshot", json.dumps([{
        "raw_file": "page.html", "sha256": "0" * 64 if wrong_sha else sha256(raw)}])))
    db.execute("INSERT INTO rating_observations VALUES (?,?,?,?,?,?,?)", (
        "snapshot", "N1234AB", "One Fiction", 1 if wrong_rating else 4, "page.html", 1,
        json.dumps({"href": "https://ncode.syosetu.com/n1234ab/"})))
    db.commit()
    db.close()
    def git(command, **kwargs):
        assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
        return SimpleNamespace(returncode=0, stdout=b"data/raw/page.html\0" if command[1] == "ls-tree" else raw)
    monkeypatch.setattr("recommend_book.recommendation.history_binding.subprocess.run", git)
    return database


def test_recovery_binds_raw_html_and_exact_observation(tmp_path, monkeypatch):
    database = _recovery_fixture(tmp_path, monkeypatch)
    before = database.read_bytes()
    result = recover_owned_pages(tmp_path, database, "a" * 40, tmp_path / "recovered")
    assert result["audit"]["verified_observation_n"] == 1
    assert result["audit"]["usage_permission_granted"] is False
    assert result["audit"]["network_calls"] == 0
    assert database.read_bytes() == before
    with pytest.raises(ValueError, match="immutable"):
        recover_owned_pages(tmp_path, database, "a" * 40, tmp_path / "recovered")


@pytest.mark.parametrize("option,error", [("wrong_sha", "SHA"), ("wrong_rating", "observation")])
def test_recovery_rejects_tampered_proof_before_writing(tmp_path, monkeypatch, option, error):
    database = _recovery_fixture(tmp_path, monkeypatch, **{option: True})
    output = tmp_path / "recovered"
    with pytest.raises(ValueError, match=error):
        recover_owned_pages(tmp_path, database, "a" * 40, output)
    assert not output.exists()
