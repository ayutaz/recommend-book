from pathlib import Path
import httpx
import pytest
from recommend_book.downloads import download_file


def test_resume_validated_range_and_atomic_complete(tmp_path):
    target = tmp_path / "dump.gz"
    partial = Path(str(target) + ".part")
    partial.write_bytes(b"abc")
    seen = []
    def handler(request):
        seen.append(request.headers.get("range"))
        return httpx.Response(206, headers={"Content-Range": "bytes 3-5/6", "Content-Length": "3"}, stream=httpx.ByteStream(b"def"))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        manifest = download_file("https://example.test/dump.gz", target, client=client)
    assert seen == ["bytes=3-"]
    assert target.read_bytes() == b"abcdef"
    assert not partial.exists()
    assert manifest["bytes"] == 6 and len(manifest["sha256"]) == 64


def test_invalid_range_preserves_partial_without_claiming_success(tmp_path):
    target = tmp_path / "dump.gz"
    partial = Path(str(target) + ".part")
    partial.write_bytes(b"abc")
    def handler(request):
        return httpx.Response(206, headers={"Content-Range": "bytes 2-5/6"}, stream=httpx.ByteStream(b"cdef"))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="range"):
            download_file("https://example.test/dump.gz", target, client=client)
    assert partial.read_bytes() == b"abc"
    assert not target.exists()


def test_server_ignoring_resume_restarts_instead_of_appending(tmp_path):
    target = tmp_path / "dump.gz"
    Path(str(target) + ".part").write_bytes(b"abc")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=httpx.ByteStream(b"abcdef")))) as client:
        download_file("https://example.test/dump.gz", target, client=client)
    assert target.read_bytes() == b"abcdef"
