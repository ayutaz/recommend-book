"""Extract scoped Wikidata-linked candidates from local complete or partial OL dumps.

No HTTP requests. A .part input is a fixed byte-prefix convenience sample, never
a completed dump. Only newline-terminated five-column rows are adopted. Exact
original rows are preserved in RawStore with the original source line number.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import zlib

from recommend_book.ingestion.core import RawStore, read_jsonl, write_jsonl
from recommend_book.ingestion.openlibrary import iter_openlibrary_dump
from recommend_book.ingestion.wikidata import claim_values


def scan_selected_dump(path: Path | str, keys: set[str], output: Path | str,
                       *, allow_partial: bool = False) -> dict:
    """Scan a fixed initial-size prefix; reject gzip corruption and bad selected JSON.

    Unselected JSON is not parsed. Column structure and gzip CRC are checked for
    the scanned stream; the manifest explicitly records selected-JSON coverage.
    """
    path, output = Path(path), Path(output)
    partial = path.name.endswith(".part")
    if partial and not allow_partial:
        raise ValueError("A .part dump requires allow_partial=True")
    initial = path.stat()
    snapshot_size = initial.st_size
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + f".{os.getpid()}.tmp")
    decompressor = zlib.decompressobj(31)
    pending = bytearray()
    digest = hashlib.sha256()
    read_bytes = 0
    line_number = 0
    matched = {}
    rows_by_key = {}
    compressed_eof = False
    gzip_members = 1
    try:
        with path.open("rb") as stream, temporary.open("wb") as selected:
            while read_bytes < snapshot_size:
                compressed = stream.read(min(1024 * 1024, snapshot_size - read_bytes))
                if not compressed:
                    raise ValueError("dump shrank during fixed-prefix scan")
                read_bytes += len(compressed)
                digest.update(compressed)
                remaining = compressed
                while remaining:
                    if decompressor.eof:
                        decompressor = zlib.decompressobj(31)
                        gzip_members += 1
                    try:
                        chunk = decompressor.decompress(remaining, 4 * 1024 * 1024)
                    except zlib.error as exc:
                        raise ValueError(f"invalid gzip data near compressed byte {read_bytes}: {exc}") from exc
                    remaining = decompressor.unused_data if decompressor.eof else decompressor.unconsumed_tail
                    pending.extend(chunk)
                    # Partition once per output chunk, not once per source row.
                    rows = pending.split(b"\n")
                    pending = bytearray(rows.pop())
                    for row in rows:
                        line_number += 1
                        raw = bytes(row) + b"\n"
                        fields = raw.rstrip(b"\r\n").split(b"\t", 4)
                        if len(fields) != 5:
                            raise ValueError(f"invalid Open Library dump line {line_number}: expected five columns")
                        try:
                            key = fields[1].decode("utf-8")
                        except UnicodeError as exc:
                            raise ValueError(f"invalid Open Library dump line {line_number}: invalid key encoding") from exc
                        if key not in keys:
                            continue
                        try:
                            item = json.loads(fields[4])
                            if not isinstance(item, dict):
                                raise ValueError("selected JSON must be an object")
                            if item.get("key", key) != key:
                                raise ValueError("JSON key conflicts with original dump column key")
                            expected_type = b"/type/work" if key.startswith("/works/") else b"/type/author" if key.startswith("/authors/") else None
                            if expected_type is None or fields[0] != expected_type:
                                raise ValueError("selected key has an inconsistent dump entity type")
                        except (ValueError, UnicodeError) as exc:
                            raise ValueError(f"invalid Open Library dump line {line_number}: {exc}") from exc
                        if key in matched:
                            raise ValueError(f"duplicate selected key on dump line {line_number}: {key}")
                        matched[key] = line_number
                        rows_by_key[key] = item
                        selected.write(raw)
            compressed_eof = decompressor.eof
            if not partial and not compressed_eof:
                raise ValueError("truncated gzip input cannot be a completed dump")
            if not partial and pending:
                raise ValueError("completed dump has an incomplete final line")
            after = path.stat()
            if not partial and (after.st_size != snapshot_size or after.st_mtime_ns != initial.st_mtime_ns):
                raise ValueError("completed dump changed during scan")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "source_path": str(path.resolve()), "source_kind": "partial_sample" if partial else "complete_dump",
        "scan_complete": not partial and compressed_eof, "gzip_eof": compressed_eof,
        "gzip_members": gzip_members, "snapshot_bytes": snapshot_size,
        "compressed_bytes_read": read_bytes, "snapshot_sha256": digest.hexdigest(),
        "source_size_after_scan": path.stat().st_size,
        "complete_lines_scanned": line_number,
        "discarded_incomplete_line_bytes": len(pending),
        "requested_keys": sorted(keys), "matched_keys": sorted(matched),
        "missing_keys": sorted(keys - matched.keys()), "matched_line_numbers": matched,
        "json_validation_scope": "selected rows only", "selected_payloads": rows_by_key,
    }


def _dump_url(path: Path) -> str:
    name = path.name.removesuffix(".part")
    checkpoint = path.with_name(name + ".checkpoint.json")
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        return saved.get("final_url") or saved.get("requested_url") or "https://openlibrary.org/data/" + name
    return "https://openlibrary.org/data/" + name


def _provenance(report: dict) -> dict:
    return {key: report[key] for key in ("source_path", "source_kind", "scan_complete", "gzip_eof", "snapshot_bytes", "snapshot_sha256", "json_validation_scope")}


def _write_manifest(output_dir: Path, manifest: dict) -> None:
    target = output_dir / "manifest.json"
    temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def extract_poc(wikidata_path: Path | str, works_dump: Path | str,
                authors_dump: Path | str, output_dir: Path | str,
                *, allow_partial: bool = False) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run = {"created_at": datetime.now(timezone.utc).isoformat(), "wikidata_input": str(Path(wikidata_path).resolve()), "works_dump": str(Path(works_dump).resolve()), "authors_dump": str(Path(authors_dump).resolve()), "outputs_are_valid": False}
    _write_manifest(output_dir, {**run, "status": "extracting"})
    try:
        return _extract_poc(wikidata_path, works_dump, authors_dump, output_dir, allow_partial=allow_partial)
    except Exception as exc:
        _write_manifest(output_dir, {**run, "status": "failed", "error": f"{type(exc).__name__}: {exc}", "limitations": ["Earlier output files, if present, do not represent this failed run"]})
        raise


def _extract_poc(wikidata_path: Path | str, works_dump: Path | str,
                 authors_dump: Path | str, output_dir: Path | str,
                 *, allow_partial: bool = False) -> dict:
    wikidata_path, works_dump, authors_dump, output_dir = map(Path, (wikidata_path, works_dump, authors_dump, output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    records = list(read_jsonl(wikidata_path))
    work_keys, author_keys = set(), set()
    for record in records:
        if record.record_kind == "work":
            work_keys.update(identifier.value for identifier in [*record.identifiers, *record.work_refs] if identifier.source == "openlibrary" and identifier.scope == "work" and identifier.value.startswith("/works/"))
            if record.source == "wikidata":
                work_keys.update("/works/" + value for value in claim_values({"claims": record.extra.get("claims", {})}, "P648") if isinstance(value, str) and re.fullmatch(r"OL\d+W", value))
        if record.record_kind == "agent":
            author_keys.update(identifier.value for identifier in record.identifiers if identifier.source == "openlibrary" and identifier.scope == "agent" and identifier.value.startswith("/authors/"))
        author_keys.update(identifier.value for contributor in record.contributors for identifier in contributor.identifiers if identifier.source == "openlibrary" and identifier.scope == "agent" and identifier.value.startswith("/authors/"))
    works_txt, authors_txt = output_dir / "selected-works.txt", output_dir / "selected-authors.txt"
    works = scan_selected_dump(works_dump, work_keys, works_txt, allow_partial=allow_partial)
    for item in works["selected_payloads"].values():
        for author in item.get("authors", []):
            ref = author.get("author", author).get("key")
            if ref and ref.startswith("/authors/"):
                author_keys.add(ref)
    authors = scan_selected_dump(authors_dump, author_keys, authors_txt, allow_partial=allow_partial)
    names = {key: item.get("name", key) for key, item in authors["selected_payloads"].items()}
    raw_store = RawStore(output_dir / "raw")
    work_records = list(iter_openlibrary_dump(works_txt, raw_store, source_url=_dump_url(works_dump), author_names=names, dump_line_numbers=works["matched_line_numbers"], dump_metadata=_provenance(works)))
    author_records = list(iter_openlibrary_dump(authors_txt, raw_store, source_url=_dump_url(authors_dump), dump_line_numbers=authors["matched_line_numbers"], dump_metadata=_provenance(authors)))
    write_jsonl(work_records, output_dir / "works.jsonl")
    write_jsonl(author_records, output_dir / "authors.jsonl")
    write_jsonl([*work_records, *author_records], output_dir / "records.jsonl")
    author_aliases = {record.source_id: [title.value for title in record.titles] for record in author_records}
    manifest = {
        "status": "complete_dump_extraction" if works["scan_complete"] and authors["scan_complete"] else "partial_sample",
        "outputs_are_valid": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "selection": "explicit Wikidata Open Library work IDs and linked author IDs; not a world-population sample",
        "wikidata_input": str(wikidata_path.resolve()),
        "wikidata_input_sha256": hashlib.sha256(wikidata_path.read_bytes()).hexdigest(),
        "works": {key: value for key, value in works.items() if key != "selected_payloads"},
        "authors": {key: value for key, value in authors.items() if key != "selected_payloads"},
        "work_records": len(work_records), "author_records": len(author_records),
        "author_aliases": author_aliases,
        "limitations": ["Only selected rows have JSON structure validation", "Partial sample missing IDs are not evidence of absence from the complete dump", "Dump completion and candidate matching coverage are separate", "No API requests or download performed"],
    }
    _write_manifest(output_dir, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wikidata", required=True, type=Path, help="Normalized Wikidata work records JSONL")
    parser.add_argument("--works-dump", required=True, type=Path)
    parser.add_argument("--authors-dump", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--allow-partial", action="store_true", help="Accept fixed prefix .part samples; never report them as completed dumps")
    args = parser.parse_args()
    try:
        result = extract_poc(args.wikidata, args.works_dump, args.authors_dump, args.output_dir, allow_partial=args.allow_partial)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": result["status"], "work_records": result["work_records"], "author_records": result["author_records"], "manifest": str(args.output_dir / "manifest.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
