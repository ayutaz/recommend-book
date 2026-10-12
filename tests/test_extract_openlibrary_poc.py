import gzip
import hashlib
import json
from pathlib import Path
import sys
import zlib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.extract_openlibrary_poc import extract_poc, scan_selected_dump
from recommend_book.ingestion.core import read_jsonl


def dump_line(kind, key, payload):
    return b"\t".join([f"/type/{kind}".encode(), key.encode(), b"4", b"2026-09-30T00:00:00Z", json.dumps({"key": key, **payload}, ensure_ascii=False).encode()]) + b"\n"


def test_complete_dump_selection_reports_original_line_and_exact_bytes(tmp_path):
    first = dump_line("work", "/works/OL1W", {"title": "Other"})
    chosen = dump_line("work", "/works/OL2W", {"title": "Chosen"})
    source = tmp_path / "works.gz"
    source.write_bytes(gzip.compress(first + chosen))
    target = tmp_path / "filtered.txt"
    report = scan_selected_dump(source, {"/works/OL2W"}, target)
    assert report["source_kind"] == "complete_dump"
    assert report["gzip_eof"] is True
    assert report["scan_complete"] is True
    assert report["matched_line_numbers"] == {"/works/OL2W": 2}
    assert target.read_bytes() == chosen


def test_partial_requires_explicit_flag_even_if_gzip_is_complete(tmp_path):
    source = tmp_path / "works.gz.part"
    source.write_bytes(gzip.compress(dump_line("work", "/works/OL1W", {"title": "Other"})))
    with pytest.raises(ValueError, match="allow_partial"):
        scan_selected_dump(source, set(), tmp_path / "out.txt")
    report = scan_selected_dump(source, set(), tmp_path / "out.txt", allow_partial=True)
    assert report["source_kind"] == "partial_sample"
    assert report["scan_complete"] is False


def test_partial_footer_eof_discards_last_unterminated_line(tmp_path):
    complete = dump_line("work", "/works/OL1W", {"title": "Complete"})
    partial = dump_line("work", "/works/OL2W", {"title": "No newline"}).rstrip(b"\n")
    source = tmp_path / "works.gz.part"
    source.write_bytes(gzip.compress(complete + partial)[:-8])
    target = tmp_path / "filtered.txt"
    report = scan_selected_dump(source, {"/works/OL1W", "/works/OL2W"}, target, allow_partial=True)
    assert report["gzip_eof"] is False
    assert report["discarded_incomplete_line_bytes"] == len(partial)
    assert report["missing_keys"] == ["/works/OL2W"]
    assert target.read_bytes() == complete


def test_truncated_gzip_is_never_accepted_as_completed_dump(tmp_path):
    source = tmp_path / "works.gz"
    source.write_bytes(gzip.compress(dump_line("work", "/works/OL1W", {"title": "Complete"}))[:-8])
    with pytest.raises(ValueError, match="truncated"):
        scan_selected_dump(source, {"/works/OL1W"}, tmp_path / "filtered.txt")


def test_invalid_selected_json_fails_without_publishing_output(tmp_path):
    source = tmp_path / "works.gz.part"
    source.write_bytes(gzip.compress(b"/type/work\t/works/OL1W\t4\tdate\t{broken}\n")[:-8])
    target = tmp_path / "filtered.txt"
    with pytest.raises(ValueError, match="line 1"):
        scan_selected_dump(source, {"/works/OL1W"}, target, allow_partial=True)
    assert not target.exists()


def test_extraction_matches_wikidata_work_refs_author_aliases_and_raw_provenance(tmp_path):
    selected = dump_line("work", "/works/OL2W", {"title": "Chosen", "authors": [{"author": {"key": "/authors/OL9A"}}]})
    works = tmp_path / "works.gz.part"
    works.write_bytes(gzip.compress(dump_line("work", "/works/OL1W", {"title": "Other"}) + selected)[:-8])
    chosen_author = dump_line("author", "/authors/OL9A", {"name": "Author", "alternative_names": ["Alias"], "remote_ids": {"wikidata": "Q9"}})
    authors = tmp_path / "authors.gz"
    authors.write_bytes(gzip.compress(chosen_author))
    wikidata = tmp_path / "wikidata.jsonl"
    wikidata.write_text(json.dumps({"source": "wikidata", "source_id": "Q1", "source_url": "https://www.wikidata.org/wiki/Q1", "retrieved_at": "2026-10-12", "provider_id": "wikidata", "record_kind": "work", "titles": [{"value": "Chosen"}], "identifiers": [{"source": "openlibrary", "value": "/works/OL2W", "scope": "work"}], "raw_sha256": "abc", "raw_path": "raw.json", "rights_policy_id": "wikidata-cc0"}) + "\n", encoding="utf-8")
    result = extract_poc(wikidata, works, authors, tmp_path / "out", allow_partial=True)
    assert result["status"] == "partial_sample"
    assert result["works"]["matched_keys"] == ["/works/OL2W"]
    record = list(read_jsonl(tmp_path / "out" / "works.jsonl"))[0]
    assert record.extra["dump_line"] == 2
    assert Path(record.raw_path).read_bytes() == selected
    assert record.raw_sha256 == hashlib.sha256(selected).hexdigest()
    assert record.contributors[0].name == "Author"
    agent = list(read_jsonl(tmp_path / "out" / "authors.jsonl"))[0]
    assert [title.value for title in agent.titles] == ["Author", "Alias"]
    manifest = [json.loads(line) for line in (tmp_path / "out" / "raw" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    work_manifest = next(row for row in manifest if row["sha256"] == record.raw_sha256)
    assert work_manifest["metadata"]["dump_line"] == 2
    assert work_manifest["metadata"]["source_kind"] == "partial_sample"


def test_cut_in_middle_of_gzip_data_keeps_only_prior_complete_rows(tmp_path):
    first = dump_line("work", "/works/OL1W", {"title": "Complete"})
    second = dump_line("work", "/works/OL2W", {"title": "A second work with different text"})
    compressor = zlib.compressobj(wbits=31)
    prefix = compressor.compress(first) + compressor.flush(zlib.Z_SYNC_FLUSH)
    tail = compressor.compress(second) + compressor.flush(zlib.Z_FINISH)
    source = tmp_path / "works.gz.part"
    source.write_bytes(prefix + tail[:max(1, len(tail) // 2)])
    target = tmp_path / "filtered.txt"
    report = scan_selected_dump(source, {"/works/OL1W", "/works/OL2W"}, target, allow_partial=True)
    assert report["gzip_eof"] is False
    assert report["matched_keys"] == ["/works/OL1W"]
    assert target.read_bytes() == first


def test_gzip_crc_corruption_is_not_treated_as_partial_eof(tmp_path):
    source = tmp_path / "works.gz.part"
    compressed = bytearray(gzip.compress(dump_line("work", "/works/OL1W", {"title": "Complete"})))
    compressed[-8] ^= 1
    source.write_bytes(compressed)
    with pytest.raises(ValueError, match="invalid gzip"):
        scan_selected_dump(source, {"/works/OL1W"}, tmp_path / "filtered.txt", allow_partial=True)


def test_growing_partial_is_bounded_to_initial_byte_snapshot(tmp_path, monkeypatch):
    first = dump_line("work", "/works/OL1W", {"title": "Initial"})
    second = dump_line("work", "/works/OL2W", {"title": "Appended"})
    source = tmp_path / "works.gz.part"
    original_bytes = gzip.compress(first)
    source.write_bytes(original_bytes)
    original_open = Path.open

    class GrowingReader:
        def __init__(self, stream):
            self.stream = stream
            self.grown = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, count):
            result = self.stream.read(count)
            if not self.grown:
                with original_open(source, "ab") as writer:
                    writer.write(gzip.compress(second))
                self.grown = True
            return result

    def growing_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return GrowingReader(stream) if path == source and mode == "rb" else stream

    monkeypatch.setattr(Path, "open", growing_open)
    target = tmp_path / "selected.txt"
    report = scan_selected_dump(source, {"/works/OL1W", "/works/OL2W"}, target, allow_partial=True)
    assert report["snapshot_bytes"] == len(original_bytes)
    assert report["source_size_after_scan"] > report["snapshot_bytes"]
    assert report["snapshot_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert report["matched_keys"] == ["/works/OL1W"]


def test_concatenated_complete_gzip_members_preserve_line_numbers(tmp_path):
    first = dump_line("work", "/works/OL1W", {"title": "First"})
    second = dump_line("work", "/works/OL2W", {"title": "Second"})
    source = tmp_path / "works.gz"
    source.write_bytes(gzip.compress(first) + gzip.compress(second))
    report = scan_selected_dump(source, {"/works/OL2W"}, tmp_path / "selected.txt")
    assert report["gzip_members"] == 2
    assert report["matched_line_numbers"] == {"/works/OL2W": 2}


def test_selected_json_key_cannot_conflict_with_dump_key(tmp_path):
    source = tmp_path / "works.gz"
    raw = b"/type/work\t/works/OL1W\t4\tdate\t{\"key\":\"/works/OL2W\",\"title\":\"Mismatch\"}\n"
    source.write_bytes(gzip.compress(raw))
    with pytest.raises(ValueError, match="key conflicts"):
        scan_selected_dump(source, {"/works/OL1W"}, tmp_path / "selected.txt")


def test_saved_p648_work_claim_is_a_scoped_selection_candidate(tmp_path):
    works = tmp_path / "works.gz"
    works.write_bytes(gzip.compress(dump_line("work", "/works/OL2W", {"title": "Chosen"})))
    authors = tmp_path / "authors.gz"
    authors.write_bytes(gzip.compress(b""))
    wikidata = tmp_path / "wikidata.jsonl"
    wikidata.write_text(json.dumps({"source": "wikidata", "source_id": "Q1", "source_url": "https://www.wikidata.org/wiki/Q1", "retrieved_at": "2026-10-12", "provider_id": "wikidata", "record_kind": "work", "titles": [{"value": "Chosen"}], "raw_sha256": "abc", "raw_path": "raw.json", "rights_policy_id": "wikidata-cc0", "extra": {"claims": {"P648": [{"rank": "normal", "mainsnak": {"datavalue": {"value": "OL2W"}}}, {"rank": "deprecated", "mainsnak": {"datavalue": {"value": "OL3W"}}}, {"rank": "normal", "mainsnak": {"datavalue": {"value": "OL4M"}}}]}}}) + "\n", encoding="utf-8")
    report = extract_poc(wikidata, works, authors, tmp_path / "out")
    assert report["status"] == "complete_dump_extraction"
    assert report["works"]["requested_keys"] == ["/works/OL2W"]
    assert report["work_records"] == 1


def test_failed_rerun_cannot_leave_previous_success_manifest(tmp_path):
    works, authors = tmp_path / "works.gz", tmp_path / "authors.gz"
    works.write_bytes(gzip.compress(b""))
    authors.write_bytes(gzip.compress(b""))
    wikidata = tmp_path / "wikidata.jsonl"
    wikidata.write_text("", encoding="utf-8")
    target = tmp_path / "out"
    assert extract_poc(wikidata, works, authors, target)["status"] == "complete_dump_extraction"
    works.write_bytes(b"not gzip")
    with pytest.raises(ValueError):
        extract_poc(wikidata, works, authors, target)
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["outputs_are_valid"] is False
