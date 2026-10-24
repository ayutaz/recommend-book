import hashlib

import pytest

from recommend_book import scale_catalogue_v1 as module


def test_native_proof_replays_archived_codex_without_weakening_other_pins(tmp_path, monkeypatch):
    logical = tmp_path / 'codex.exe'
    logical.write_bytes(b'new cli')
    expected = hashlib.sha256(b'old cli').hexdigest()
    archive = tmp_path / 'archive'
    (archive / expected).mkdir(parents=True)
    (archive / expected / 'codex.exe').write_bytes(b'old cli')
    monkeypatch.setattr(module, '_CLI_ARCHIVE_ROOT', archive)
    catalogue = object.__new__(module.ScaleCatalogue)
    catalogue._native_proof_artifact(logical, expected)
    source = tmp_path / 'source.json'
    source.write_bytes(b'new cli')
    with pytest.raises(ValueError, match='hash changed'):
        catalogue._native_proof_artifact(source, expected)
    (archive / expected / 'codex.exe').write_bytes(b'bad cli')
    with pytest.raises(ValueError, match='hash'):
        catalogue._native_proof_artifact(logical, expected)
