"""Dedicated bounded native task. No world-classification files are changed.

Source frozen before execution. Uses the verified pilot input only; never reads
reference answers until predictions have been written and process exit observed.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib,json,subprocess,time,sys
from pathlib import Path

ROOT=next(p for p in Path(__file__).resolve().parents
          if (p/"config/autonomous-evaluation-v1.json").is_file() and (p/"pyproject.toml").is_file())
sys.path.insert(0,str(ROOT/"src"))
from recommend_book.autonomous_reference import (
    strict_json,sha256,NATIVE_VERSION,load_native_codebook,native_schema,
    build_native_prompt,validate_native_result,
)
from recommend_book.classification.runner import resolve_executable,cli_version,parse_events
from recommend_book.classification.validation import input_hash
from recommend_book.models import WorkInput


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")


def run_native(snapshot,output,*,concurrency=2):
    if type(concurrency) is not int or not 1<=concurrency<=2:raise ValueError("native max two slots")
    snapshot=Path(snapshot).resolve();output=Path(output).resolve()
    base=(ROOT/"data/autonomous-poc-v1/reference").resolve()
    if not snapshot.is_relative_to(base) or not output.is_relative_to(base):raise ValueError("outside reference namespace")
    if output.exists():raise ValueError("refuse existing native run")
    sm=strict_json((snapshot/"manifest.json").read_bytes())
    for name,expected in sm["files"].items():
        if Path(name).name!=name or sha256((snapshot/name).read_bytes())!=expected:raise ValueError("snapshot changed")
    raw=(snapshot/"work-inputs.jsonl").read_bytes()
    works=[WorkInput.model_validate(strict_json(x)) for x in raw.splitlines() if x]
    if len(works)!=20 or len({w.work_id for w in works})!=20:raise ValueError("native pilot requires exact20")
    codebook_path=base/"raw/keywords.xml";codebook=load_native_codebook(codebook_path.read_bytes())
    if codebook["sha256"]!="2413a5d27d6a1b95e905a961487de9fd63a013af62ea0b6793d1bfbc468c66cc":raise ValueError("fixed codebook changed")
    command=resolve_executable();version=cli_version(command)
    paths={"inputs":snapshot/"work-inputs.jsonl","snapshot":snapshot/"manifest.json","codebook":codebook_path,
        "module":ROOT/"src/recommend_book/autonomous_reference.py","scheduler":Path(__file__).resolve(),
        "scheduler_ignored_copy":ROOT/"data/autonomous-poc-v1/reference/native20-v1/native_runner.py",
        "runner":ROOT/"src/recommend_book/classification/runner.py","models":ROOT/"src/recommend_book/models.py"}
    before={name:sha256(p.read_bytes()) for name,p in paths.items()}
    if before["scheduler"]!=before["scheduler_ignored_copy"]:raise ValueError("native scheduler copies differ")
    output.mkdir(parents=True);(output/"workspace").mkdir();(output/"attempts").mkdir();(output/"results").mkdir()
    schema=native_schema();schema_path=output/"native.schema.json";write(schema_path,schema)
    context={"version":NATIVE_VERSION,"created_at":datetime.now(timezone.utc).isoformat(),"model":"gpt-6.1-sol",
        "cli_version":version,"schema_sha256":sha256(schema_path.read_bytes()),"source_sha256":before,
        "controls":{"concurrency":concurrency,"batch_size":5,"timeout":900,"max_attempts":3,
            "sandbox":"read-only","reasoning_effort":"medium","web_search":"disabled","ignore_user_config":True},
        "codebook":codebook,"single_protagonist_scope":True,"world_taxonomy_used":False,"answers_in_prompt":False}
    write(output/"execution-context.json",context)
    predictions=[];states=[];attempts=[];started=time.monotonic()
    def process(batch_number,batch):
        prompt=build_native_prompt(batch,codebook,cli_version=version)
        attempts_local=[]
        for attempt in range(1,4):
            stem=output/"attempts"/f"batch-{batch_number}-attempt-{attempt}"
            final=stem.with_suffix(".final.json")
            argv=[*command,"exec","--model","gpt-6.1-sol","--sandbox","read-only","--ephemeral",
                "--ignore-user-config","--skip-git-repo-check","--json","--color","never",
                "-c",'model_reasoning_effort="medium"',"-c",'web_search="disabled"',
                "-c","features.multi_agent=false","--cd",str(output/"workspace"),
                "--output-schema",str(schema_path),"-o",str(final),"-"]
            error=None;stdout="";stderr="";returncode=None;tick=time.monotonic();values=[]
            try:
                p=subprocess.run(argv,input=prompt,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=900,shell=False)
                stdout,stderr,returncode=p.stdout,p.stderr,p.returncode
            except subprocess.TimeoutExpired as exc:
                stdout=exc.stdout.decode("utf-8","replace") if isinstance(exc.stdout,bytes) else exc.stdout or ""
                stderr=exc.stderr.decode("utf-8","replace") if isinstance(exc.stderr,bytes) else exc.stderr or ""
                error="timeout"
            except OSError:error="launch_error"
            stem.with_suffix(".stdout.jsonl").write_text(stdout,encoding="utf-8")
            stem.with_suffix(".stderr.txt").write_text(stderr,encoding="utf-8")
            events=parse_events(stdout)
            if error is None and (returncode!=0 or events["event_errors"]):error="cli_error"
            if events["tool_calls"]:error="forbidden_tool_use"
            if error is None:
                try:
                    decoded=strict_json(final.read_text(encoding="utf-8-sig"))
                    if set(decoded)!={"results"} or not isinstance(decoded["results"],list):raise ValueError("native batch shape")
                    values=decoded["results"];idx={r["work_id"]:r for r in values}
                    if len(values)!=len(batch) or set(idx)!={w.work_id for w in batch}:raise ValueError("native batch identity")
                    for w in batch:validate_native_result(idx[w.work_id],w,version)
                except (ValueError,KeyError,TypeError,OSError):error="invalid_native_result"
            metric={"batch":batch_number,"attempt":attempt,"returncode":returncode,"error":error,
                "seconds":time.monotonic()-tick,"usage":events["usage"],"prompt_sha256":sha256(prompt.encode()),
                "final_path":str(final.relative_to(output)),"tool_call_count":len(events["tool_calls"])}
            attempts_local.append(metric)
            if error is None:
                for w in batch:
                    key=sha256((NATIVE_VERSION+input_hash(w)).encode());write(output/"results"/f"{key}.json",idx[w.work_id])
                return values,[{"work_id":w.work_id,"input_hash":input_hash(w),"status":"completed"} for w in batch],attempts_local
            if error in {"forbidden_tool_use","launch_error"} or any(s in (stdout+stderr).lower() for s in ("usage limit","quota exceeded","unauthorized","rate limit","too many requests")):
                break
            if attempt<3:time.sleep(2**attempt)
        return [],[{"work_id":w.work_id,"input_hash":input_hash(w),"status":"held","reason":error} for w in batch],attempts_local
    batches=[works[i:i+5] for i in range(0,len(works),5)]
    for offset in range(0,len(batches),concurrency):
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures=[pool.submit(process,i+1,batches[i]) for i in range(offset,min(offset+concurrency,len(batches)))]
            for f in futures:
                results,jobs,metrics=f.result();predictions.extend(results);states.extend(jobs);attempts.extend(metrics)
    after={name:sha256(p.read_bytes()) for name,p in paths.items()}
    (output/"results.jsonl").write_text("".join(json.dumps(r,ensure_ascii=False,sort_keys=True,allow_nan=False)+"\n" for r in predictions),encoding="utf-8")
    usage={}
    for a in attempts:
        for key,value in a["usage"].items():usage[key]=usage.get(key,0)+value
    manifest={"version":NATIVE_VERSION,"run_id":sha256((str(output)+str(started)).encode()),
        "selected":20,"completed":len(predictions),"failed":20-len(predictions),"pending":0,"stopped":False,
        "states":states,"attempts":attempts,"cli_invocations":len(attempts),"usage":usage,
        "elapsed_seconds":time.monotonic()-started,"context_sha256":sha256((output/"execution-context.json").read_bytes()),
        "input_sha256":sha256(raw),"source_sha256_before":before,"source_sha256_after":after,
        "source_bytes_unchanged":before==after,"human_annotation_generated":False}
    write(output/"manifest.json",manifest)
    if before!=after:raise ValueError("native source bytes changed during execution")
    print(json.dumps({k:manifest[k] for k in ("run_id","selected","completed","failed","usage","elapsed_seconds")}))
    return manifest


if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--snapshot",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();run_native(a.snapshot,a.output)
