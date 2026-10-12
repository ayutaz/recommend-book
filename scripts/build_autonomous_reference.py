"""Prepare official pinned label-blind inputs; score only observed terminal runs.

This command never invokes a classifier. Classification requires root's GO and
uses the existing recommend-book classify command in a separate run directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from recommend_book.autonomous_reference import (
    COMMIT,GOLD_SHA,METADATA_SHA,TAXONOMY_SHA,VERSION,RIGHTS_SOURCES,
    assess_metadata_rights,build_reference_input,positive_report,sha256,strict_json,
)
from recommend_book.models import WorkInput

UA="recommend-book-independent-reference/1.0 (literary research; https://github.com/ayutaz)"


def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")


def write_jsonl(path,rows):
    path.write_text("".join(json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False)+"\n" for row in rows),encoding="utf-8")


def fetch(url):
    """Official sources only, 1.1s minimum, bounded retries and Retry-After."""
    if not url.startswith((f"https://raw.githubusercontent.com/cligs/conssa/{COMMIT}/",
                           f"https://api.github.com/repos/cligs/conssa/git/trees/{COMMIT}")):
        raise ValueError("unapproved acquisition endpoint")
    for attempt in range(3):
        time.sleep(1.1)
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":UA}),timeout=60) as response:
                if response.geturl()!=url: raise ValueError("unexpected source redirect")
                data=response.read(15_000_001)
                if len(data)>15_000_000: raise ValueError("source exceeds reviewed maximum bytes")
                return data
        except urllib.error.HTTPError as exc:
            if exc.code not in {429,500,502,503,504} or attempt==2: raise
            retry=exc.headers.get("Retry-After")
            if retry:
                from email.utils import parsedate_to_datetime
                try: delay=float(retry)
                except ValueError: delay=(parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds()
                if delay>60: raise RuntimeError("provider Retry-After exceeds this bounded acquisition; defer without retry") from exc
            else: delay=2**attempt
            time.sleep(max(1.1,delay))


def fixed_sources():
    paths={"metadata":ROOT/"data/reference/conssa-metadata.tsv",
           "gold":ROOT/"data/reference/conssa-curated-v1/gold.jsonl",
           "taxonomy":ROOT/"config/taxonomy.json"}
    expected={"metadata":METADATA_SHA,"gold":GOLD_SHA,"taxonomy":TAXONOMY_SHA}
    baseline=strict_json((ROOT/"data/autonomous-poc-v1/baseline-manifest.json").read_bytes())
    baseline_artifacts={row["path"]:row for row in baseline["artifacts"]}
    for key,path in paths.items():
        if sha256(path.read_bytes())!=expected[key]: raise ValueError(f"fixed {key} source changed")
        if baseline_artifacts[path.relative_to(ROOT).as_posix()]["sha256"]!=expected[key]:
            raise ValueError(f"baseline {key} differs")
    config_path=ROOT/"config/autonomous-evaluation-v1.json"
    if sha256(config_path.read_bytes())!=baseline_artifacts["config/autonomous-evaluation-v1.json"]["sha256"]:
        raise ValueError("autonomous config differs from baseline")
    config=strict_json(config_path.read_bytes())
    if config["reference"]["pilot_works"]!=20 or config["reference"]["target_works"]!=100:
        raise ValueError("unsupported frozen reference plan")
    meta=list(csv.DictReader(paths["metadata"].open(encoding="utf-8"),delimiter="\t"))
    metadata={row["idno"]:row for row in meta}
    if len(metadata)!=len(meta): raise ValueError("duplicate metadata ID")
    gold=[strict_json(line) for line in paths["gold"].read_bytes().splitlines() if line.strip()]
    if len(gold)!=180 or sum(len(g["labels"]) for g in gold)!=200: raise ValueError("fixed gold support differs")
    return metadata,gold,config,paths


def prepare(output:Path,target:int):
    if type(target) is not int or not 1<=target<=100: raise ValueError("target outside fixed range")
    metadata,gold,config,source_paths=fixed_sources()
    output=output.resolve()
    base=(ROOT/"data/autonomous-poc-v1/reference").resolve()
    if not output.is_relative_to(base) or output==base: raise ValueError("output outside owned new snapshot namespace")
    if output.exists() and any(output.iterdir()): raise ValueError("refuse nonempty output snapshot")
    output.mkdir(parents=True,exist_ok=True)
    rawdir=base/"raw";rawdir.mkdir(parents=True,exist_ok=True)
    treepath=rawdir/"official-tree.json"
    if not treepath.exists(): treepath.write_bytes(fetch(f"https://api.github.com/repos/cligs/conssa/git/trees/{COMMIT}?recursive=1"))
    tree=strict_json(treepath.read_bytes())
    if tree.get("truncated") is not False: raise ValueError("incomplete official source tree")
    entries={x["path"]:x for x in tree["tree"] if x.get("type")=="blob"}
    inputs=[];audits=[];ledger=[];selected_gold=[]
    for g in sorted(gold,key=lambda x:x["reference_id"]):
        ident=g["reference_id"];m=metadata.get(ident)
        row={"reference_id":ident,"work_id":g["work_id"],"positive_support":len(g["labels"]),
            "provenance":"curated_reference","source_metadata_sha256":METADATA_SHA,"gold_sha256":GOLD_SHA,
            "source_author_country":m.get("author.country") if m else None,
            "source_author_death_year":m.get("author.date.death") if m else None,
            "source_publication_year":m.get("year") if m else None,
            "source_availability":m.get("availability") if m else None,
            "source_commit":COMMIT,"human_annotation_generated":False,"evidence_urls":RIGHTS_SOURCES}
        try:
            if m is None: raise ValueError("metadata identity absent")
            assess_metadata_rights(m)
            if len(inputs)>=target:
                row.update(status="not_selected",reason="pilot/target prefix filled; rights metadata rule passed, TEI not yet inspected")
                ledger.append(row);continue
            path=f"master/master/{ident}.xml"
            if path not in entries: raise ValueError("official original TEI unavailable")
            url=f"https://raw.githubusercontent.com/cligs/conssa/{COMMIT}/{path}"
            rawpath=rawdir/f"{ident}.xml"
            if not rawpath.exists(): rawpath.write_bytes(fetch(url))
            raw=rawpath.read_bytes()
            entry=entries[path]
            gitsha=hashlib.sha1(f"blob {len(raw)}\0".encode()+raw).hexdigest()
            if gitsha!=entry["sha"] or len(raw)!=entry["size"]: raise ValueError("official pinned Git blob mismatch")
            work,audit=build_reference_input(raw,m,g,source_url=url)
            audit.update(raw_path=rawpath.relative_to(ROOT).as_posix(),git_blob_sha1=gitsha,
                original_author=m["author.name.full"],source_metadata_sha256=METADATA_SHA,
                source_annotation_certainty=g["source_annotation_certainty"],
                attribution=g["attribution"],changes="Original TEI body only; fixed label-blind body windows; metadata omitted from model input")
            inputs.append(work.model_dump(mode="json"));audits.append(audit);selected_gold.append(g)
            row.update(status="input_ready",reason="pinned native/TEI/edition/right scopes checked",
                raw_sha256=audit["raw_sha256"],raw_path=audit["raw_path"],rights=audit["rights"])
        except (ValueError,ETParseError,urllib.error.URLError,RuntimeError,OSError) as exc:
            row.update(status="hold",reason=str(exc))
        ledger.append(row)
    write_jsonl(output/"work-inputs.jsonl",inputs)
    write_jsonl(output/"input-audit.jsonl",audits)
    write_jsonl(output/"eligibility-ledger.jsonl",ledger)
    # Answer files stay outside the classifier's workspace and never enter prompt payloads.
    write_jsonl(output/"selected-reference.jsonl",selected_gold)
    write_json(output/"rights-policy.json",{"version":VERSION,"evidence_urls":RIGHTS_SOURCES,
        "no_worldwide_clearance":True,"no_provider_geography_assertion":True,
        "attribution":"CLiGS / Jose Calvo Tello; source URLs and CC BY 4.0 retained",
        "purpose":"Independent research classification of extracted original text only",
        "policy_id":"autonomous-conssa-original-reviewed-v1"})
    fixed_sources() # verify frozen bytes again after acquisition and preparation
    manifest={"version":VERSION,"created_at":datetime.now(timezone.utc).isoformat(),
        "target":target,"input_work_count":len(inputs),"planned_reference_works":180,"planned_positive_support":200,
        "selected_positive_support":sum(len(g["labels"]) for g in selected_gold),
        "status_counts":dict(Counter(row["status"] for row in ledger)),
        "hold_reasons":dict(Counter(row["reason"] for row in ledger if row["status"]=="hold")),
        "source_commit":COMMIT,"source_metadata_sha256":METADATA_SHA,"gold_sha256":GOLD_SHA,
        "taxonomy_sha256":TAXONOMY_SHA,"tree_sha256":sha256(treepath.read_bytes()),
        "files":{p.name:sha256(p.read_bytes()) for p in sorted(output.iterdir()) if p.is_file()},
        "classifier_started":False,"human_annotation_generated":False,
        "selection":"ascending pinned corpus ID after scope/rights/identity checks; no model outcomes used",
        "limits":{"reference_target":100,"classifier_concurrency_maximum":2,"batch_size":5},
        "metric_scope":"positive-only excerpt detection; not full-work quality, precision, negatives or calibration"}
    write_json(output/"manifest.json",manifest)
    print(json.dumps({"output":str(output),"count":len(inputs),"status_counts":manifest["status_counts"],"hold_reasons":manifest["hold_reasons"]}))
    return manifest


def score(snapshot:Path,run:Path,proof_path:Path,output:Path):
    from recommend_book.classification.validation import load_taxonomy,validate_result,input_hash
    from recommend_book.classification.runner import build_run_config
    from recommend_book.jobs import job_key
    fixed_sources()
    snapshot=snapshot.resolve();run=run.resolve();output=output.resolve()
    base=(ROOT/"data/autonomous-poc-v1/reference").resolve()
    if not all(p.is_relative_to(base) for p in (snapshot,run,output,proof_path.resolve())): raise ValueError("paths outside owned reference namespace")
    if output.exists(): raise ValueError("refuse existing report")
    sm=strict_json((snapshot/"manifest.json").read_bytes())
    for name,expected in sm["files"].items():
        if Path(name).name!=name or sha256((snapshot/name).read_bytes())!=expected: raise ValueError("snapshot bytes changed")
    manifest=strict_json((run/"manifest.json").read_bytes());proof=strict_json(proof_path.read_bytes())
    if proof.get("schema_version")!="classification-terminal-proof-v1" or proof.get("process_exit_observed") is not True:
        raise ValueError("observed process exit proof required")
    if type(proof.get("exit_code")) is not int or proof["exit_code"]!=0: raise ValueError("successful observed process exit required")
    if type(proof.get("session_id")) is not int or proof["session_id"]<1: raise ValueError("observed execution session required")
    observed=datetime.fromisoformat(proof.get("observed_at",""))
    if observed.tzinfo is None or observed.utcoffset().total_seconds()!=0: raise ValueError("UTC exit observation required")
    if proof.get("run_id")!=manifest["run_id"]: raise ValueError("proof run identity mismatch")
    for key,path in (("manifest_sha256",run/"manifest.json"),("results_sha256",run/"results.jsonl"),
                     ("ordered_inputs_sha256",snapshot/"work-inputs.jsonl"),
                     ("reference_snapshot_sha256",snapshot/"manifest.json"),
                     ("execution_context_manifest_sha256",run/"execution-context.json")):
        if proof.get(key)!=sha256(path.read_bytes()): raise ValueError("terminal proof bytes differ")
    context=strict_json((run/"execution-context.json").read_bytes())
    if context["input_sha256"]!=proof["ordered_inputs_sha256"] or context["reference_snapshot_sha256"]!=proof["reference_snapshot_sha256"]:
        raise ValueError("prepared context input identity differs")
    artifacts=context["execution_context"]["artifact_sha256"]
    from importlib.util import spec_from_file_location,module_from_spec
    fs=spec_from_file_location("reference_freeze_contract",ROOT/"scripts/freeze_classification_execution.py")
    freeze=module_from_spec(fs);fs.loader.exec_module(freeze)
    if set(artifacts)!=set(freeze.ARTIFACTS): raise ValueError("prepared code artifact set differs")
    for name,expected in artifacts.items():
        if sha256((ROOT/name).read_bytes())!=expected: raise ValueError("classifier code bytes differ")
    after=strict_json((run/"after-observation.json").read_bytes())
    if after.get("source_artifacts_unchanged") is not True or after.get("input_unchanged") is not True or after.get("artifact_sha256")!=artifacts:
        raise ValueError("source before/after verification differs")
    if after.get("execution_context_manifest_sha256")!=proof["execution_context_manifest_sha256"]: raise ValueError("prepared profile changed")
    for field in ("selected","completed","failed","pending"):
        if type(manifest.get(field)) is not int or manifest[field]<0: raise ValueError("invalid terminal count")
    if manifest["pending"] or manifest.get("stopped") is not False: raise ValueError("partial/stopped run cannot be scored")
    inputs=[WorkInput.model_validate(strict_json(x)) for x in (snapshot/"work-inputs.jsonl").read_bytes().splitlines() if x]
    results=[strict_json(x) for x in (run/"results.jsonl").read_bytes().splitlines() if x]
    config=manifest["config"];taxonomy=load_taxonomy()
    if config!=build_run_config("gpt-6.1-sol",config["cli_version"],taxonomy,5): raise ValueError("classification execution context differs")
    if config!=context["execution_context"]["run_config"] or context["execution_context"]["controls"]!={"concurrency":2,"batch_size":5,"timeout":900}:
        raise ValueError("prepared run configuration/controls differ")
    if manifest["initial_concurrency"]!=2 or manifest["limits"]["timeout"]!=900: raise ValueError("actual run controls differ")
    if manifest["selected"]!=len(inputs) or manifest["completed"]!=len(results) or manifest["selected"]!=manifest["completed"]+manifest["failed"]:
        raise ValueError("terminal selected/completed/failed mismatch")
    jobs=manifest["jobs"]
    expected_keys={job_key(input_hash(w),config) for w in inputs}
    if set(jobs)!=expected_keys: raise ValueError("terminal jobs differ from fixed inputs")
    index={r["work_id"]:r for r in results}
    if len(index)!=len(results): raise ValueError("duplicate result identity")
    held=0
    for work in inputs:
        key=job_key(input_hash(work),config);job=jobs[key]
        if job.get("work_id")!=work.work_id or job.get("expression_id")!=work.expression_id: raise ValueError("job identity mismatch")
        if strict_json((run/"jobs"/f"{key}.json").read_bytes())!=job: raise ValueError("durable job state differs")
        if job["status"]=="completed":
            result=index.get(work.work_id)
            if result is None: raise ValueError("completed result missing")
            validate_result(result,work,taxonomy)
            if result["model"]!=config["model"] or result["cli_version"]!=config["cli_version"]: raise ValueError("result execution context differs")
            relative=job.get("result_path","").replace("\\","/")
            from pathlib import PurePosixPath,PureWindowsPath
            path=PurePosixPath(relative)
            if path.is_absolute() or PureWindowsPath(relative).drive or any(x in {"..","."} for x in relative.split("/")) or path.as_posix()!=f"results/{key}.json":
                raise ValueError("unsafe/mismatched durable result path")
            if strict_json((run/"results"/f"{key}.json").read_bytes())!=result: raise ValueError("durable result mismatch")
        elif job["status"]=="held":
            held+=1
            if work.work_id in index: raise ValueError("held job has result")
        else: raise ValueError("unsupported/nonterminal job state")
    if held!=manifest["failed"]: raise ValueError("held count mismatch")
    _,gold,_,_=fixed_sources()
    report=positive_report(gold,results,{w.work_id for w in inputs})
    report.update(run_id=manifest["run_id"],held_work_count=held,
        reference_snapshot_sha256=sha256((snapshot/"manifest.json").read_bytes()),
        terminal_proof_sha256=sha256(proof_path.read_bytes()),
        results_sha256=sha256((run/"results.jsonl").read_bytes()),
        input_sha256=sha256((snapshot/"work-inputs.jsonl").read_bytes()),
        usage=manifest["usage"],elapsed_seconds=manifest["elapsed_seconds"])
    output.parent.mkdir(parents=True,exist_ok=True);write_json(output,report)
    print(json.dumps({k:report[k] for k in ("planned_work_count","input_work_count","positive_support","detected_positives","positive_detection_recall")}))
    return report


# Alias avoids treating XML parse failures as an unbounded acquisition retry.
from xml.etree.ElementTree import ParseError as ETParseError

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest="mode",required=True)
    p=subs.add_parser("prepare");p.add_argument("--output",type=Path,required=True);p.add_argument("--target",type=int,default=20)
    p=subs.add_parser("score");p.add_argument("--snapshot",type=Path,required=True);p.add_argument("--run",type=Path,required=True);p.add_argument("--proof",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=parser.parse_args()
    if a.mode=="prepare":prepare(a.output,a.target)
    else:score(a.snapshot,a.run,a.proof,a.output)


if __name__=="__main__":main()
