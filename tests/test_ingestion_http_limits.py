import json

import httpx
import pytest

from recommend_book.ingestion import HTTPFetcher, RawStore
from recommend_book.ingestion import core


def test_host_intervals_separate_query_and_entity_api(tmp_path, monkeypatch):
    clock = [1000.0]
    waits = []
    monkeypatch.setattr(core.time, "time", lambda: clock[0])
    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds
    fetcher = HTTPFetcher(RawStore(tmp_path), min_interval=1,
        host_intervals={"query.wikidata.org": 61}, sleep=sleep,
        client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200))))
    fetcher.get("https://query.wikidata.org/sparql", source="wikidata")
    fetcher.get("https://www.wikidata.org/w/api.php", source="wikidata")
    fetcher.get("https://query.wikidata.org/sparql", source="wikidata")
    assert waits == [61]


def test_interrupted_retry_after_is_persisted_and_observed_wait_logged(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(core.time, "time", lambda: clock[0])
    store = RawStore(tmp_path)
    def interrupted_sleep(seconds):
        raise KeyboardInterrupt
    fetcher = HTTPFetcher(store, min_interval=0, sleep=interrupted_sleep,
        client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(429, headers={"Retry-After":"120"}))))
    with pytest.raises(KeyboardInterrupt):
        fetcher.get("https://query.wikidata.org/sparql", source="wikidata")
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds
    resumed = HTTPFetcher(store, min_interval=0, sleep=sleep,
        client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200))))
    assert resumed.get("https://query.wikidata.org/sparql", source="wikidata").status == "success"
    assert waits == [120]
    logs = [json.loads(line) for line in (tmp_path / "http-waits.jsonl").read_text().splitlines()]
    assert any(row["reason"] == "retry_after" and row["requested_seconds"] == 120 for row in logs)
    assert any(row.get("observed_seconds") == 120 for row in logs)
    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert manifest[0]["metadata"]["retry_after"] == "120"


def test_final_failed_attempt_retains_server_cooldown(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(core.time, "time", lambda: clock[0])
    store = RawStore(tmp_path)
    failed = HTTPFetcher(store, min_interval=0, max_attempts=1,
        client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(429, headers={"Retry-After":"120"}))))
    assert failed.get("https://query.wikidata.org/sparql", source="wikidata").status == "failed"
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds
    resumed = HTTPFetcher(store, min_interval=0, sleep=sleep,
        client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200))))
    resumed.get("https://query.wikidata.org/sparql", source="wikidata")
    assert waits == [120]
