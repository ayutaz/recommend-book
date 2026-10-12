import importlib.util
import json
from pathlib import Path
import pytest
from recommend_book.models import WorkInput


def benchmark_module():
    script = Path(__file__).resolve().parents[1] / "scripts/benchmark_classification.py"
    spec = importlib.util.spec_from_file_location("classification_benchmark", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_snapshot(tmp_path, count=100):
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text("".join(json.dumps(WorkInput(work_id=f"w{i}",title=f"Title{i}").model_dump())+"\n" for i in range(count)), encoding="utf-8")
    return snapshot


def test_benchmark_reuses_fixed_inputs_but_new_run_outputs(monkeypatch, tmp_path):
    module = benchmark_module()
    calls = []
    def fake(inputs, output_dir, **kwargs):
        calls.append((inputs, output_dir, kwargs))
        return {"selected":100,"completed":100,"failed":0,"attempts":100,"retries":0,
            "elapsed_seconds":100 / kwargs["concurrency"],"p95_seconds":5,
            "usage":{"input_tokens":100}, "stopped":False,"jobs":{}}
    monkeypatch.setattr(module, "run_classification", fake)
    path = make_snapshot(tmp_path)
    report = module.run_benchmark(path, tmp_path / "bench", batch_size=5)
    assert [call[2]["concurrency"] for call in calls] == [2,4,8]
    assert all(call[2]["stop_on_rate_limit"] is True for call in calls)
    assert all(call[0] == calls[0][0] for call in calls)
    assert len({call[1] for call in calls}) == 3
    assert report["human_quality_verified"] is False
    assert report["work_content_accuracy"] is None
    assert len(report["runs"]) == 3
    assert (tmp_path / "bench/benchmark.json").exists()
    with pytest.raises(ValueError, match="exists|empty|new"):
        module.run_benchmark(path, tmp_path / "bench")


def test_benchmark_stops_after_rate_limit(monkeypatch, tmp_path):
    module = benchmark_module()
    calls = []
    def fake(inputs, output_dir, **kwargs):
        calls.append(kwargs["concurrency"])
        return {"selected":100,"completed":7,"failed":0,"pending":93,"elapsed_seconds":10,
            "p95_seconds":2,"attempts":7,"retries":0,"usage":{},"stopped":True,
            "stop_reasons":["rate_limit"], "jobs":{"j":{"error_category":"rate_limit","status":"pending"}}}
    monkeypatch.setattr(module,"run_classification",fake)
    report = module.run_benchmark(make_snapshot(tmp_path),tmp_path / "bench")
    assert calls == [2]
    assert report["status"] == "stopped"
    assert report["recommended_concurrency"] is None


def test_benchmark_requires_exactly_100_unique_works(tmp_path):
    module = benchmark_module()
    with pytest.raises(ValueError,match="100"):
        module.run_benchmark(make_snapshot(tmp_path,99),tmp_path / "bench")


def test_benchmark_rejects_duplicate_work_even_different_expression(tmp_path):
    module=benchmark_module()
    path=make_snapshot(tmp_path)
    records=[json.loads(line) for line in path.read_text().splitlines()]
    records[-1]["work_id"]=records[0]["work_id"]
    records[-1]["expression_id"]="translation"
    path.write_text("".join(json.dumps(record)+"\n" for record in records))
    with pytest.raises(ValueError,match="100"):
        module.run_benchmark(path,tmp_path / "bench")


def test_zero_benchmark_budget_never_invokes_workers(monkeypatch,tmp_path):
    module=benchmark_module()
    monkeypatch.setattr(module,"run_classification",lambda *args,**kwargs: pytest.fail("must not call workers"))
    report=module.run_benchmark(make_snapshot(tmp_path),tmp_path / "bench",max_observed_tokens=0)
    assert report["status"] == "stopped"
    assert report["runs"] == []


def test_comparison_gate_includes_failure_rate_and_latency():
    module=benchmark_module()
    def run(concurrency,completed,speed,p95,failed):
        return {"concurrency":concurrency,"completed":completed,"failed":failed,"pending":0,
            "works_per_second":speed,"p95_seconds":p95,"final_failure_rate":failed/100,"stopped":False}
    runs=[run(2,99,1,10,1),run(4,99,1.2,11,1),run(8,99,1.5,15,1)]
    chosen,comparisons=module.choose_concurrency(runs)
    assert chosen == 4
    assert comparisons[0]["passes_provisional_gate"] is True
    assert comparisons[1]["passes_provisional_gate"] is False


@pytest.mark.parametrize("first_drift",[4,8])
def test_benchmark_configuration_drift_invalidates_comparison(monkeypatch,tmp_path,first_drift):
    module=benchmark_module()
    def fake(inputs,output_dir,**kwargs):
        return {"selected":100,"completed":100,"failed":0,"pending":0,"elapsed_seconds":100,
            "p95_seconds":3,"usage":{},"stopped":False,"jobs":{},
            "config":{"schema_hash":"changed" if kwargs["concurrency"] >= first_drift else "fixed"}}
    monkeypatch.setattr(module,"run_classification",fake)
    report=module.run_benchmark(make_snapshot(tmp_path),tmp_path / "bench")
    assert report["status"] == "stopped"
    assert report["stop_reasons"] == ["configuration_changed"]
    assert report["recommended_concurrency"] is None
