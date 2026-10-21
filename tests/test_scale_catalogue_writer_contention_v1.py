"""Windows lock-file sharing contention must not authorize lock deletion."""
import importlib.util
from pathlib import Path

import pytest

from recommend_book.scale_linked_metadata_v2 import catalogue_writer_lock


spec = importlib.util.spec_from_file_location(
    "qlever_writer_contention", Path(__file__).resolve().parents[1] / "scripts/collect_scale_wikidata_qlever_v1.py"
)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


@pytest.mark.parametrize("acquire", [catalogue_writer_lock, collector.catalogue_writer_lock])
def test_transient_permission_error_waits_and_acquires_its_own_lock(tmp_path, monkeypatch, acquire):
    catalogue = tmp_path / "scale" / "catalogue.sqlite"
    catalogue.parent.mkdir()
    lock = tmp_path / "catalogue-writer.lock"
    original_open = Path.open
    attempts = []

    def contended_open(path, *args, **kwargs):
        if path == lock and args and args[0] == "x":
            attempts.append(path)
            if len(attempts) == 1:
                raise PermissionError(13, "transient sharing contention")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", contended_open)
    with acquire(catalogue, wait_seconds=1, poll_seconds=0):
        assert lock.is_file()
        assert len(attempts) == 2
    assert not lock.exists()


@pytest.mark.parametrize("acquire", [catalogue_writer_lock, collector.catalogue_writer_lock])
def test_permission_timeout_preserves_another_writers_lock(tmp_path, monkeypatch, acquire):
    catalogue = tmp_path / "scale" / "catalogue.sqlite"
    catalogue.parent.mkdir()
    lock = tmp_path / "catalogue-writer.lock"
    lock.write_text("another writer", encoding="utf-8")
    original_open = Path.open

    def contended_open(path, *args, **kwargs):
        if path == lock and args and args[0] == "x":
            raise PermissionError(13, "sharing contention")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", contended_open)
    with pytest.raises(RuntimeError, match="wait expired"):
        with acquire(catalogue, wait_seconds=0, poll_seconds=0):
            pytest.fail("must not enter another writer's lock")
    assert lock.read_text(encoding="utf-8") == "another writer"
