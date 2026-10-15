"""Private owner-HTML evidence recovery; never grants recommendation permission.

Only local pinned Git objects and manual-import SQLite columns are read. API
metadata, annotations and inferred author identities are deliberately absent.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from urllib.parse import urlsplit


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def ncode_from_owner_href(href: str) -> str | None:
    """Accept an explicit serial-work URL, never arbitrary embedded strings."""
    try:
        parsed = urlsplit(href)
        if parsed.scheme not in {"https", "http"} or parsed.hostname not in {
            "ncode.syosetu.com", "novel18.syosetu.com"
        } or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            return None
    except ValueError:
        return None
    match = re.fullmatch(r"/(n\d{4}[a-z]{1,2})/?", parsed.path.lower())
    return match.group(1) if match and not parsed.query and not parsed.fragment else None


class _OwnerRows(HTMLParser):
    _void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.rows = []
        self.row = None
        self.title_depth = None
        self.anchor_depth = None

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        classes = attr.get("class", "").split()
        if tag not in self._void:
            self.stack.append(tag)
        if tag == "li" and "c-up-reaction-item" in classes:
            if self.row is not None:
                raise ValueError("Nested owner observation rows")
            self.row = {"row_number": len(self.rows) + 1, "title": "", "hrefs": [], "score_values": [], "row_profile_links": []}
            self.row_depth = len(self.stack)
        if self.row is None:
            return
        if "c-up-reaction-item__title" in classes:
            self.title_depth = len(self.stack)
        if tag == "a":
            href = attr.get("href", "")
            if self.title_depth is not None:
                self.row["hrefs"].append(href)
                self.anchor_depth = len(self.stack)
            if urlsplit(href).hostname == "mypage.syosetu.com":
                self.row["row_profile_links"].append(href)
        if tag == "input" and attr.get("name") == "score":
            self.row["score_values"].append(attr.get("value"))

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        depth = len(self.stack) - self.stack[::-1].index(tag)
        if self.row is not None:
            if self.anchor_depth is not None and depth <= self.anchor_depth:
                self.anchor_depth = None
            if self.title_depth is not None and depth <= self.title_depth:
                self.title_depth = None
            if tag == "li" and depth == self.row_depth:
                self.rows.append(self.row)
                self.row = None
        del self.stack[depth - 1:]

    def handle_data(self, data):
        if self.row is not None and self.anchor_depth is not None:
            self.row["title"] += " " + data.strip()


def extract_owner_html_rows(raw: bytes) -> list[dict]:
    parser = _OwnerRows()
    parser.feed(raw.decode("utf-8-sig"))
    parser.close()
    if parser.row is not None:
        raise ValueError("Unclosed owner observation row")
    for row in parser.rows:
        # Reproduce the original importer get_text(' ', strip=True)/split rule.
        row["title"] = " ".join(row["title"].split())
        codes = [ncode_from_owner_href(href) for href in row["hrefs"]]
        row["ncode"] = codes[0] if len(codes) == 1 else None
        scores = row["score_values"]
        row["rating"] = int(scores[0]) / 2 if len(scores) == 1 and scores[0] in {"2", "4", "6", "8", "10"} else None
        # A profile URL inside a row is still not an author authority proof.
        row["author_identity_verified"] = False
    return parser.rows


def recover_owned_pages(repository, database, head, output_dir):
    """Recover originals by recorded SHA; retain private proof, never overwrite.

    This is provenance inspection, not a UsageGuard receipt or content-use grant.
    Git lazy fetching is disabled, so unavailable objects fail locally.
    """
    repository = Path(repository).resolve(strict=True)
    database = Path(database).resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Use a new immutable private output namespace")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("A pinned full Git commit is required")
    before = sha256(database.read_bytes())
    connection = sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        imports = [dict(row) for row in connection.execute("SELECT snapshot_id,pages_json FROM imports")]
        observations = [dict(row) for row in connection.execute(
            "SELECT snapshot_id,ncode,title,my_rating,source_page,row_number,evidence_json FROM rating_observations ORDER BY snapshot_id,source_page,row_number"
        )]
    finally:
        connection.close()
    environment = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}

    def git(*args):
        result = subprocess.run(["git", *args], cwd=repository, env=environment, capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError("Pinned local Git object unavailable")
        return result.stdout

    files = git("ls-tree", "-r", "--name-only", "-z", head).decode("utf-8").split("\0")
    originals, proofs, index = {}, [], {}
    for snapshot in imports:
        for page in json.loads(snapshot["pages_json"]):
            basename = Path(page["raw_file"]).name
            matches = []
            for name in files:
                if name == basename or name.endswith("/" + basename):
                    raw = git("show", head + ":" + name)
                    if sha256(raw) == page["sha256"]:
                        matches.append((name, raw))
            if not matches:
                raise ValueError("Original owner page SHA not reproduced")
            name, raw = matches[0]
            originals[page["sha256"]] = raw
            rows = extract_owner_html_rows(raw)
            for row in rows:
                key = (snapshot["snapshot_id"], basename, row["row_number"])
                if key in index:
                    raise ValueError("Ambiguous owner page row locator")
                index[key] = row
            proofs.append({"snapshot_id": snapshot["snapshot_id"], "page_sha256": page["sha256"],
                "git_commit": head, "git_path": name, "git_blob_sha1": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
                "row_n": len(rows), "row_profile_links_n": sum(len(row["row_profile_links"]) for row in rows)})
    verified = []
    for observation in observations:
        key = (observation["snapshot_id"], Path(observation["source_page"]).name, observation["row_number"])
        row = index.get(key)
        evidence = json.loads(observation["evidence_json"])
        if row is None or row["ncode"] != observation["ncode"].lower() or row["title"] != observation["title"] or row["rating"] != observation["my_rating"] or row["hrefs"] != [evidence["href"]]:
            raise ValueError("Original owner row does not reproduce observation")
        verified.append({**observation, "html_row_verified": True})
    if sha256(database.read_bytes()) != before:
        raise ValueError("Owner database changed during recovery")
    output.mkdir(parents=True)
    for digest, raw in originals.items():
        (output / (digest + ".html")).write_bytes(raw)
    document = {"version": "owner-original-html-recovery-v1", "private_local_only": True,
        "database_sha256": before, "git_commit": head, "page_n": len(proofs),
        "verified_observation_n": len(verified), "row_author_profile_links_n": sum(p["row_profile_links_n"] for p in proofs),
        "author_identity_verified_n": 0, "api_tables_read": [], "annotations_read": False,
        "network_calls": 0, "usage_permission_granted": False, "pages": proofs}
    (output / "recovery.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"audit": document, "observations": verified}
