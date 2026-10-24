"""Bounded local reading-state export and deletion.

Only the mutable ``read_status`` table is exposed.  Source records, catalogue
rows, native classification proofs, and other provenance remain outside this
management surface.  The export digest is a confirmation fence for deletion:
the caller must export the current state and explicitly confirm before rows
can be removed.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re


VERSION = 'scale-history-export-v1'
KIND = 'read-status'
CONFIRMATION = 'DELETE_READ_STATUS'
_QID = re.compile(r'^Q[1-9][0-9]*$')
_STATUSES = {'unknown', 'unread', 'reading', 'read'}


def _canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _digest(payload):
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _payload_from_rows(rows):
    records = []
    for row in rows:
        qid, status, updated_at = row['qid'], row['status'], row['updated_at']
        if not isinstance(qid, str) or not _QID.fullmatch(qid):
            raise ValueError('stored reading-state QID is invalid')
        if status not in _STATUSES:
            raise ValueError('stored reading-state status is invalid')
        if not isinstance(updated_at, str) or not updated_at:
            raise ValueError('stored reading-state timestamp is invalid')
        # Validate the representation without rewriting it.  This makes a
        # corrupt local timestamp visible instead of silently exporting it.
        datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
        records.append({'qid': qid, 'status': status, 'updated_at': updated_at})
    return {'version': VERSION, 'kind': KIND, 'records': records,
            'record_count': len(records)}


def _payload(catalogue):
    with catalogue._connect() as db:
        rows = db.execute('SELECT qid,status,updated_at FROM read_status ORDER BY qid').fetchall()
    return _payload_from_rows(rows)


def export_reading_history(catalogue):
    """Return a portable, path-free export and its canonical content digest."""
    payload = _payload(catalogue)
    return {**payload, 'sha256': _digest(payload)}


def delete_reading_history(catalogue, *, export_sha256, confirmation):
    """Delete only read-state rows after a current export confirmation."""
    if confirmation != CONFIRMATION or not isinstance(export_sha256, str):
        raise ValueError('current export and explicit confirmation are required')
    with catalogue._connect() as db:
        # Lock, read, compare, and delete in one transaction.  A second
        # connection cannot commit a read-state update between the digest
        # check and DELETE, so no newer state can be deleted accidentally.
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('SELECT qid,status,updated_at FROM read_status ORDER BY qid').fetchall()
        current = _payload_from_rows(rows)
        if export_sha256 != _digest(current):
            db.rollback()
            raise ValueError('reading state changed since export')
        deleted = db.execute('DELETE FROM read_status').rowcount
    return {'version': VERSION, 'deleted_count': int(deleted),
            'export_sha256': _digest(current)}
