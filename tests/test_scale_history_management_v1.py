import sqlite3
import threading
from contextlib import contextmanager

import pytest

from recommend_book.scale_history_management_v1 import (
    CONFIRMATION,
    delete_reading_history,
    export_reading_history,
)


class HistoryCatalogue:
    def __init__(self, path):
        self.db_path = path
        with sqlite3.connect(path) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS read_status(
                    qid TEXT PRIMARY KEY,status TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS works(qid TEXT PRIMARY KEY,row_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS source_versions(version_id TEXT PRIMARY KEY,record_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS native_classifications(qid TEXT PRIMARY KEY,labels_json TEXT NOT NULL);
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_state(self, qid, status, updated_at):
        with self._connect() as db:
            db.execute('INSERT OR REPLACE INTO read_status VALUES(?,?,?)', (qid, status, updated_at))


def test_export_survives_reopen_and_keeps_unknown_distinct_from_unread(tmp_path):
    path = tmp_path / 'catalogue.sqlite'
    first = HistoryCatalogue(path)
    first.add_state('Q100', 'unknown', '2026-10-22T00:00:00+00:00')
    first.add_state('Q101', 'unread', '2026-10-22T00:01:00+00:00')
    exported = export_reading_history(HistoryCatalogue(path))
    assert exported['version'] == 'scale-history-export-v1'
    assert exported['kind'] == 'read-status'
    assert exported['record_count'] == 2
    assert exported['records'] == [
        {'qid': 'Q100', 'status': 'unknown', 'updated_at': '2026-10-22T00:00:00+00:00'},
        {'qid': 'Q101', 'status': 'unread', 'updated_at': '2026-10-22T00:01:00+00:00'},
    ]
    reopened = export_reading_history(HistoryCatalogue(path))
    assert reopened == exported
    assert all(str(path) not in str(value) for value in exported.values())


def test_delete_requires_current_export_and_preserves_catalogue_references(tmp_path):
    path = tmp_path / 'catalogue.sqlite'
    catalogue = HistoryCatalogue(path)
    catalogue.add_state('Q100', 'read', '2026-10-22T00:00:00+00:00')
    with catalogue._connect() as db:
        db.execute("INSERT INTO works VALUES('Q100','catalogue-row')")
        db.execute("INSERT INTO source_versions VALUES('v1','source-row')")
        db.execute("INSERT INTO native_classifications VALUES('Q100','native-row')")
    exported = export_reading_history(catalogue)
    with pytest.raises(ValueError, match='changed since export'):
        delete_reading_history(catalogue, export_sha256='0' * 64, confirmation=CONFIRMATION)
    with pytest.raises(ValueError, match='confirmation'):
        delete_reading_history(catalogue, export_sha256=exported['sha256'], confirmation='')
    result = delete_reading_history(catalogue, export_sha256=exported['sha256'], confirmation=CONFIRMATION)
    assert result['deleted_count'] == 1
    with catalogue._connect() as db:
        assert db.execute('SELECT count(*) FROM read_status').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM works').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM source_versions').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM native_classifications').fetchone()[0] == 1


def test_stale_export_rejects_update_from_second_connection_and_keeps_new_state(tmp_path):
    path = tmp_path / 'catalogue.sqlite'
    catalogue = HistoryCatalogue(path)
    catalogue.add_state('Q100', 'unread', '2026-10-22T00:00:00+00:00')
    exported = export_reading_history(catalogue)

    # Simulate another tab/server writer committing after the export.  The
    # delete must compare against this newer state while holding one
    # BEGIN IMMEDIATE transaction, then reject without deleting it.
    with sqlite3.connect(path) as second_connection:
        second_connection.execute(
            "UPDATE read_status SET status=?, updated_at=? WHERE qid=?",
            ('reading', '2026-10-22T00:02:00+00:00', 'Q100'),
        )

    with pytest.raises(ValueError, match='changed since export'):
        delete_reading_history(
            catalogue,
            export_sha256=exported['sha256'],
            confirmation=CONFIRMATION,
        )

    current = export_reading_history(catalogue)
    assert current['records'] == [{
        'qid': 'Q100',
        'status': 'reading',
        'updated_at': '2026-10-22T00:02:00+00:00',
    }]


class _InterleavingConnection:
    def __init__(self, connection, before_delete):
        self._connection = connection
        self._before_delete = before_delete
        self._triggered = False

    def execute(self, statement, parameters=()):
        if (not self._triggered and
                statement.strip().upper().startswith('DELETE FROM READ_STATUS')):
            self._triggered = True
            self._before_delete()
        return self._connection.execute(statement, parameters)

    def __getattr__(self, name):
        return getattr(self._connection, name)


class _DeleteRaceCatalogue(HistoryCatalogue):
    def __init__(self, path):
        super().__init__(path)
        self.writer_started = threading.Event()
        self.writer_done = threading.Event()
        self.writer_errors = []
        self.writer_thread = None

    def _start_writer_before_delete(self):
        def write_new_state():
            try:
                with sqlite3.connect(self.db_path, timeout=5) as db:
                    self.writer_started.set()
                    db.execute(
                        "INSERT OR REPLACE INTO read_status VALUES(?,?,?)",
                        ('Q100', 'reading', '2026-10-22T00:03:00+00:00'),
                    )
            except BaseException as error:  # surfaced by the test thread below
                self.writer_errors.append(error)
            finally:
                self.writer_done.set()

        self.writer_thread = threading.Thread(target=write_new_state)
        self.writer_thread.start()
        assert self.writer_started.wait(1)
        # With the old two-connection implementation the writer commits here,
        # before DELETE.  BEGIN IMMEDIATE correctly keeps it waiting.
        self.writer_done.wait(0.3)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        proxy = _InterleavingConnection(db, self._start_writer_before_delete)
        try:
            yield proxy
        except BaseException:
            db.rollback()
            raise
        else:
            db.commit()
        finally:
            db.close()


def test_delete_race_keeps_writer_state_after_delete_commit(tmp_path):
    catalogue = _DeleteRaceCatalogue(tmp_path / 'catalogue.sqlite')
    catalogue.add_state('Q100', 'unread', '2026-10-22T00:00:00+00:00')
    exported = export_reading_history(catalogue)

    delete_reading_history(
        catalogue,
        export_sha256=exported['sha256'],
        confirmation=CONFIRMATION,
    )

    assert catalogue.writer_thread is not None
    catalogue.writer_thread.join(5)
    assert not catalogue.writer_thread.is_alive()
    assert catalogue.writer_done.is_set()
    assert not catalogue.writer_errors
    current = export_reading_history(catalogue)
    assert current['records'] == [{
        'qid': 'Q100',
        'status': 'reading',
        'updated_at': '2026-10-22T00:03:00+00:00',
    }]


@pytest.mark.parametrize('bad_row', [
    ('not-a-qid', 'read', '2026-10-22T00:00:00+00:00'),
    ('Q100', 'not-a-status', '2026-10-22T00:00:00+00:00'),
    ('Q100', 'read', 'not-a-timestamp'),
])
def test_corrupt_local_state_is_rejected_without_exporting_it(tmp_path, bad_row):
    catalogue = HistoryCatalogue(tmp_path / 'catalogue.sqlite')
    catalogue.add_state(*bad_row)
    with pytest.raises(ValueError):
        export_reading_history(catalogue)
