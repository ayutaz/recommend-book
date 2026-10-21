import json
import importlib.util
from pathlib import Path
from types import SimpleNamespace

_SPEC = importlib.util.spec_from_file_location(
    "repair_scale_linked_metadata_v1", Path(__file__).parents[1] / "scripts" / "repair_scale_linked_metadata_v1.py"
)
repair = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(repair)


class FakeFetcher:
    def __init__(self, result):
        self.result = result
        self.calls = 0
        self.params = []

    def get(self, *args, **kwargs):
        self.calls += 1
        self.params.append(kwargs.get("params", {}))
        return self.result


def test_fetch_stops_on_http_429_without_resending(monkeypatch, tmp_path):
    result = SimpleNamespace(status="error", error="HTTP 429", status_code=429)
    fetcher = FakeFetcher(result)
    monkeypatch.setattr(repair, "OUTPUT", tmp_path)
    monkeypatch.setattr(repair, "HTTPFetcher", lambda *args, **kwargs: fetcher)

    agent_ids = [f"Q{i}" for i in range(1, 52)]
    state, fetched = repair.fetch_missing(agent_ids, {})

    assert fetcher.calls == 1
    assert fetched == {}
    assert state["held"] == sorted(agent_ids[:50])
    assert state["stopped_reason"] == "HTTP 429"
    assert state["errors"][0]["status_code"] == 429
    assert "Q51" not in fetcher.params[0]["ids"]

    resumed = FakeFetcher(result)
    monkeypatch.setattr(repair, "HTTPFetcher", lambda *args, **kwargs: resumed)
    state, fetched = repair.fetch_missing(agent_ids, {})
    assert resumed.calls == 0
    assert state["held"] == sorted(agent_ids[:50])
    assert fetched == {}


def test_fetch_stops_on_http_200_api_error_without_label_errors(monkeypatch, tmp_path):
    result = SimpleNamespace(
        status="success",
        status_code=200,
        content=json.dumps({"error": {"code": "maxlag", "info": "lagged"}}),
    )
    fetcher = FakeFetcher(result)
    monkeypatch.setattr(repair, "OUTPUT", tmp_path)
    monkeypatch.setattr(repair, "HTTPFetcher", lambda *args, **kwargs: fetcher)

    agent_ids = [f"Q{i}" for i in range(1, 52)]
    state, fetched = repair.fetch_missing(agent_ids, {})

    assert fetcher.calls == 1
    assert fetched == {}
    assert state["held"] == sorted(agent_ids[:50])
    assert state["stopped_reason"] == "Wikidata API maxlag"
    assert state["errors"][0]["api_error"]["code"] == "maxlag"
    assert all("qid" not in error for error in state["errors"])
    assert "Q51" not in fetcher.params[0]["ids"]


def test_fetch_stops_on_malformed_json_or_missing_entities(monkeypatch, tmp_path):
    cases = (
        ("not-json", "malformed Wikidata JSON"),
        (json.dumps({"servedby": "test"}), "Wikidata response missing entities"),
        (json.dumps([]), "Wikidata response must be a JSON object"),
        (json.dumps(None), "Wikidata response must be a JSON object"),
    )
    for index, (content, reason) in enumerate(cases):
        result = SimpleNamespace(status="success", status_code=200, content=content)
        fetcher = FakeFetcher(result)
        output = tmp_path / f"{index}-{reason.replace(' ', '-')}"
        monkeypatch.setattr(repair, "OUTPUT", output)
        monkeypatch.setattr(repair, "HTTPFetcher", lambda *args, _fetcher=fetcher, **kwargs: _fetcher)

        agent_ids = [f"Q{i}" for i in range(1, 52)]
        state, fetched = repair.fetch_missing(agent_ids, {})

        assert fetcher.calls == 1
        assert fetched == {}
        assert state["held"] == sorted(agent_ids[:50])
        assert state["stopped_reason"] == reason
        assert "Q51" not in fetcher.params[0]["ids"]
