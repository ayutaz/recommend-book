import pytest
from recommend_book.jobs import JobStore, atomic_json, job_key


def test_lease_expiry_and_attempt_budget(tmp_path):
    clock = [100.0]
    store = JobStore(tmp_path, clock=lambda: clock[0])
    key = job_key("input", {"model": "m"})
    assert store.claim(key, lease_seconds=10) is not None
    assert store.claim(key, lease_seconds=10) is None
    clock[0] = 111
    assert store.claim(key, lease_seconds=10) is not None
    store.fail(key, "invalid", "bad", retryable=True)
    assert store.claim(key, lease_seconds=10) is not None
    store.fail(key, "invalid", "bad", retryable=True)
    assert store.claim(key, lease_seconds=10) is None
    assert store.get(key)["attempts"] == 3


def test_completed_job_cannot_be_claimed(tmp_path):
    store = JobStore(tmp_path)
    store.claim("one")
    store.complete("one", "result.json")
    assert store.claim("one") is None
    assert store.get("one")["status"] == "completed"


def test_key_includes_settings_and_atomic_writer(tmp_path):
    assert job_key("x", {"model":"a"}) != job_key("x", {"model":"b"})
    assert job_key("x", {"a":1,"b":2}) == job_key("x", {"b":2,"a":1})
    atomic_json(tmp_path / "state.json", {"status":"completed"})
    assert not list(tmp_path.glob("*.tmp"))


def test_expired_lease_cannot_finish_new_owners_job(tmp_path):
    now = [1.0]
    store = JobStore(tmp_path, clock=lambda: now[0])
    old = store.claim("one", lease_seconds=1)
    now[0] = 3.0
    new = store.claim("one", lease_seconds=1)
    with pytest.raises(ValueError, match="owned"):
        store.complete("one", "old.json", lease_token=old["lease_token"])
    store.complete("one", "new.json", lease_token=new["lease_token"], result={"owner":"new"})
    with pytest.raises(ValueError, match="owned"):
        store.complete("one", "new.json", lease_token=old["lease_token"], result={"owner":"old"})
    import json
    assert json.loads((tmp_path / "new.json").read_text())["owner"] == "new"


def test_two_store_instances_only_one_claims(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    stores = [JobStore(tmp_path), JobStore(tmp_path)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda store: store.claim("same"), stores))
    assert sum(claim is not None for claim in claims) == 1


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows byte-range locks")
def test_claim_waits_when_empty_lock_file_is_already_locked(tmp_path):
    import msvcrt
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    import threading

    store = JobStore(tmp_path)
    started = threading.Event()
    def claim():
        started.set()
        return store.claim("empty")

    with (store.directory / "empty.lock").open("a+b") as holder:
        msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, 1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(claim)
            try:
                assert started.wait(5)
                with pytest.raises(TimeoutError):
                    future.result(timeout=.1)
            finally:
                holder.seek(0)
                msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, 1)
            assert future.result(timeout=5) is not None
