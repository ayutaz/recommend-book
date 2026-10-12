"""Explicit live CLI benchmark for a frozen 100-work snapshot (2 -> 4 -> 8).

Run with uv run python scripts/benchmark_classification.py --input SNAPSHOT
--output NEW_DIRECTORY --batch-size 5. This command consumes the existing
Codex CLI account's usage; no API billing estimate or human accuracy claim is
made. Separate fresh run directories intentionally avoid cached classifications.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

from recommend_book.classification import input_hash, load_taxonomy, read_results, run_classification
from recommend_book.classification.runner import validate_controls
from recommend_book.cli import read_jsonl
from recommend_book.jobs import atomic_json, canonical_json
from recommend_book.models import WorkInput


STOP_CATEGORIES = {"rate_limit","usage_limit","authentication"}


def _event_attempts(manifest):
    """Deduplicate per-work histories that share a CLI batch invocation."""
    attempts = {}
    for key,state in manifest.get("jobs",{}).items():
        for index,event in enumerate(state.get("history",[])):
            identity = event.get("events_path") or f"{key}:{index}"
            attempts[identity] = event
    return list(attempts.values())


def summarize_run(manifest: dict, directory: Path, concurrency: int) -> dict:
    selected = manifest.get("selected",100)
    completed = manifest.get("completed",0)
    seconds = manifest.get("elapsed_seconds",0)
    events = _event_attempts(manifest)
    categories = Counter(e.get("error_category") for e in events if e.get("error_category"))
    results = read_results(directory)
    axes = Counter(label["axis"] for result in results for label in result["labels"] if label["decision"] in ("present","absent"))
    return {
        "concurrency":concurrency,"directory":str(directory),"selected":selected,"completed":completed,
        "pending":manifest.get("pending",0),"failed":manifest.get("failed",0),
        "valid_result_rate":completed/selected if selected else None,
        "final_failure_rate":manifest.get("failed",0)/selected if selected else None,
        "elapsed_seconds":seconds,"works_per_second":completed/seconds if seconds > 0 else None,
        "p50_seconds":manifest.get("p50_seconds"),"p95_seconds":manifest.get("p95_seconds"),
        "latency_basis":"CLI invocation wall time; all works in a batch share that invocation",
        "job_attempts":manifest.get("attempts",0),"cli_invocations":manifest.get("cli_invocations"),
        "job_retries":manifest.get("retries",0),"cli_error_categories":dict(categories),
        "invalid_output_attempt_rate":sum(categories.get(category,0) for category in
            ("invalid_json","invalid_schema","invalid_result","missing_output"))/len(events) if events else None,
        "usage":manifest.get("usage",{}),"stopped":manifest.get("stopped",False),
        "stop_reasons":manifest.get("stop_reasons",[]),
        "observed_supported_labels_by_axis":dict(axes) if results else None,
        "machine_checks":"Schema, exact identities, permitted evidence references and quoted substring/locator checks",
        "input_fidelity_accuracy":None,"work_content_accuracy":None,"human_quality_verified":False,
        "config":manifest.get("config",{}),
    }


def choose_concurrency(runs: list[dict]) -> tuple[int | None,list[dict]]:
    """Provisional throughput gates; no inference about classification quality."""
    comparisons=[]
    if len(runs) != 3 or any(run["stopped"] or run["completed"]+run["failed"] != 100 or run.get("pending",0) for run in runs):
        return None,comparisons
    chosen = 2 if runs[0]["final_failure_rate"] <= .02 else None
    for previous,current in zip(runs,runs[1:]):
        speed_previous,speed_current = previous["works_per_second"],current["works_per_second"]
        p95_previous,p95_current = previous["p95_seconds"],current["p95_seconds"]
        growth = speed_current/speed_previous - 1 if speed_previous and speed_current else None
        worsening = p95_current/p95_previous - 1 if p95_previous and p95_current is not None else None
        passed = growth is not None and worsening is not None and growth >= .15 and worsening <= .25 and current["final_failure_rate"] <= .02
        comparisons.append({"from":previous["concurrency"],"to":current["concurrency"],
            "throughput_growth":growth,"p95_worsening":worsening,"passes_provisional_gate":passed})
        if passed and chosen == previous["concurrency"]:
            chosen = current["concurrency"]
    return chosen,comparisons


def _write_report(report: dict, directory: Path):
    atomic_json(directory / "benchmark.json", report)
    lines=["# 分類CLIの100作品固定試行", "", f"状態: {report['status']}",
        f"固定入力SHA-256: `{report['snapshot_sha256']}`", "",
        "機械検証: Schema、ID、許可済み根拠参照、引用・箇所の整合性。人手による入力忠実性・作品内容の精度は未評価。",
        "", "| 並列数 | 有効/100 | 秒 | 作品/秒 | p95秒 | 最終失敗率 | 停止理由 |", "|---|---|---|---|---|---|---|"]
    for run in report["runs"]:
        speed=f"{run['works_per_second']:.4f}" if run['works_per_second'] is not None else "未測定"
        lines.append(f"| {run['concurrency']} | {run['completed']} | {run['elapsed_seconds']:.2f} | {speed} | {run['p95_seconds']} | {run['final_failure_rate']} | {', '.join(run['stop_reasons']) or '-'} |")
    lines += ["", f"仮の選択並列数: {report['recommended_concurrency'] if report['recommended_concurrency'] is not None else '判断保留'}",
        "", "p95はCLI呼び出しの所要時間であり、小バッチ内の作品は時間を共有する。利用量はCLIが報告したtoken数であり、API課金額ではない。",
        "新規投入を停止した時点で実行中の呼び出しは完了を待つため、時間・token上限を超える場合がある。",
        "レート制限・利用上限・認証エラーで停止した試行の後には、残りの並列度比較を開始しない。",
        "端末CPU・メモリの観測、人手の根拠支持判定、架空引用率の人手監査はこのスクリプトの測定対象外。"]
    (directory / "benchmark.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def run_benchmark(snapshot: Path, output_dir: Path, *, batch_size: int=1,timeout: float=900,
        model: str="gpt-6.1-sol",executable: str | None=None,max_run_seconds: float | None=None,
        max_observed_tokens: int | None=None) -> dict:
    validate_controls(batch_size=batch_size,timeout=timeout,max_run_seconds=max_run_seconds,max_observed_tokens=max_observed_tokens)
    snapshot=Path(snapshot)
    original_bytes=snapshot.read_bytes()
    inputs=[WorkInput.model_validate(value) for value in read_jsonl(snapshot)]
    if len(inputs) != 100 or len({work.work_id for work in inputs}) != 100:
        raise ValueError("Benchmark requires exactly 100 distinct work IDs in a fixed WorkInput snapshot")
    output_dir=Path(output_dir).resolve()
    if output_dir.exists():
        raise ValueError("Benchmark output exists; use a new directory for deliberate reruns")
    output_dir.mkdir(parents=True,exist_ok=False)
    (output_dir / "inputs.jsonl").write_bytes(original_bytes)
    report={"version":"1.0","created_at":datetime.now(timezone.utc).isoformat(),"status":"running",
        "snapshot_sha256":hashlib.sha256(original_bytes).hexdigest(),"work_count":100,
        "input_hashes":{work.work_id:input_hash(work) for work in inputs},
        "taxonomy_sha256":hashlib.sha256(canonical_json(load_taxonomy()).encode()).hexdigest(),
        "settings":{"model":model,"batch_size":batch_size,"timeout":timeout,
            "max_run_seconds":max_run_seconds,"max_observed_tokens":max_observed_tokens,
            "budget_scope":"all three runs combined; in-flight work may exceed bounds"},
        "concurrency_order":[2,4,8],"runs":[],"usage":{},"recommended_concurrency":None,"comparisons":[],
        "human_quality_verified":False,"input_fidelity_accuracy":None,"work_content_accuracy":None,
        "precision_recall_f1":None,"evidence_support_correctness":None,
        "account_usage_source":"existing Codex CLI authentication; no secret reads or API billing estimate",
        "stop_reasons":[]}
    started=time.monotonic()
    try:
        for concurrency in (2,4,8):
            used=report["usage"].get("input_tokens",0)+report["usage"].get("output_tokens",0)
            remaining_seconds=None if max_run_seconds is None else max(0,max_run_seconds-(time.monotonic()-started))
            remaining_tokens=None if max_observed_tokens is None else max(0,max_observed_tokens-used)
            if remaining_seconds == 0 or remaining_tokens == 0:
                report["status"]="stopped"
                report["stop_reasons"].append("benchmark_budget")
                break
            directory=output_dir / f"concurrency-{concurrency}"
            manifest=run_classification(inputs,directory,concurrency=concurrency,model=model,
                executable=executable,batch_size=batch_size,timeout=timeout,max_run_seconds=remaining_seconds,
                max_observed_tokens=remaining_tokens,stop_on_rate_limit=True)
            observed=summarize_run(manifest,directory,concurrency)
            report["runs"].append(observed)
            for key,value in observed["usage"].items():
                report["usage"][key]=report["usage"].get(key,0)+value
            reasons=set(observed["stop_reasons"])
            if len(report["runs"]) > 1 and observed["config"] != report["runs"][0]["config"]:
                reasons.add("configuration_changed")
            reasons |= set(observed["cli_error_categories"]) & STOP_CATEGORIES
            reasons |= {state.get("error_category") for state in manifest.get("jobs",{}).values()} & STOP_CATEGORIES
            if observed["stopped"] or reasons:
                report["status"]="stopped"
                report["stop_reasons"]=sorted(reasons or {"run_stopped"})
                break
            _write_report(report,output_dir)
        else:
            report["status"]="completed"
        if report["status"] == "completed":
            report["recommended_concurrency"],report["comparisons"]=choose_concurrency(report["runs"])
    except BaseException as exc:
        report["status"]="interrupted" if isinstance(exc,KeyboardInterrupt) else "error"
        report["error"]={"type":type(exc).__name__,"message":str(exc)}
        raise
    finally:
        report["elapsed_seconds"]=time.monotonic()-started
        _write_report(report,output_dir)
    return report


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,required=True,help="Exactly 100 distinct work IDs; fixed WorkInput JSONL")
    parser.add_argument("--output",type=Path,required=True,help="New directory; never overwrite/reuse cached benchmark runs")
    parser.add_argument("--batch-size",type=int,default=1)
    parser.add_argument("--timeout",type=float,default=900)
    parser.add_argument("--model",default="gpt-6.1-sol")
    parser.add_argument("--executable",help="Optional native Codex executable; default resolves installed CLI")
    parser.add_argument("--max-run-seconds",type=float)
    parser.add_argument("--max-observed-tokens",type=int)
    arguments=parser.parse_args(argv)
    try:
        report=run_benchmark(arguments.input,arguments.output,batch_size=arguments.batch_size,
            timeout=arguments.timeout,model=arguments.model,executable=arguments.executable,
            max_run_seconds=arguments.max_run_seconds,max_observed_tokens=arguments.max_observed_tokens)
    except (ValueError,OSError,RuntimeError) as exc:
        print(f"Error: {exc}",file=sys.stderr)
        return 1
    print(json.dumps({"status":report["status"],"runs":len(report["runs"]),
        "recommended_concurrency":report["recommended_concurrency"],"output":str(arguments.output)},ensure_ascii=False))
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
