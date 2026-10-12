"""Offline fake-process scheduler checks; never invokes Codex."""
import importlib.util,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from recommend_book.models import WorkInput,Evidence

@pytest.fixture
def scheduler(monkeypatch,tmp_path):
    repo=next(p for p in Path(__file__).resolve().parents if (p/"config/autonomous-evaluation-v1.json").is_file())
    path=repo/"scripts/run_autonomous_native_reference.py"
    spec=importlib.util.spec_from_file_location("native_scheduler_fixture",path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    assert m.ROOT==repo
    # Runtime paths remain inside a synthetic isolated root; source bytes are fixtures.
    base=tmp_path/"data/autonomous-poc-v1/reference";snap=base/"snapshot";snap.mkdir(parents=True)
    for rel in ["src/recommend_book/autonomous_reference.py","src/recommend_book/classification/runner.py","src/recommend_book/models.py"]:
        p=tmp_path/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((repo/rel).read_bytes())
    (base/"raw").mkdir();(base/"raw/keywords.xml").write_bytes(b"synthetic fixture-only codebook")
    # The parser/fixed-source gate has separate unit coverage. This mock scheduler
    # suite never requires acquired ignored files or calls any source endpoint.
    monkeypatch.setattr(m,"load_native_codebook",lambda raw:{
        "sha256":"2413a5d27d6a1b95e905a961487de9fd63a013af62ea0b6793d1bfbc468c66cc",
        "source_url":"https://example.org/fixture-only","fixture_only":True})
    (base/"native20-v1").mkdir();(base/"native20-v1/native_runner.py").write_bytes(path.read_bytes())
    monkeypatch.setattr(m,"ROOT",tmp_path);monkeypatch.setattr(m,"resolve_executable",lambda:["fake-only"]);monkeypatch.setattr(m,"cli_version",lambda command:"fixture-only")
    works=[WorkInput(work_id=f"fixture-{i}",title="opaque",original_language="es",evidence=[Evidence(evidence_id="fixture",kind="excerpt",text="Only synthetic text.",source_url="https://example.org",rights_policy_id="fixture",llm_input_allowed=True,language="es",locator="fixture")]) for i in range(20)]
    (snap/"work-inputs.jsonl").write_text("".join(w.model_dump_json()+"\n" for w in works),encoding="utf-8")
    m.write(snap/"manifest.json",{"files":{"work-inputs.jsonl":m.sha256((snap/"work-inputs.jsonl").read_bytes())}})
    def fake(argv,*,input,**kwargs):
        assert argv[0]=="fake-only" and "--ephemeral" in argv and kwargs["shell"] is False
        payload=json.loads(input.split("BEGIN_LOCAL_DATA_JSON\n")[1].split("\nEND_LOCAL_DATA_JSON")[0]);rows=[]
        for w in payload["works"]:
            rows.append({**w["output_identity"],"protagonist_name":None,"age":{"value":"unknown","evidence":[],"reason":"Synthetic insufficient"},"profession":{"value":None,"evidence":[],"reason":"Synthetic insufficient"}})
        Path(argv[argv.index("-o")+1]).write_text(json.dumps({"results":rows}),encoding="utf-8")
        return SimpleNamespace(stdout=json.dumps({"type":"turn.completed","usage":{"input_tokens":1}}),stderr="",returncode=0)
    monkeypatch.setattr(m.subprocess,"run",fake)
    return m,snap,base/"run"

def test_native_fake_scheduler_exact_twenty_four_invocations(scheduler):
    m,snap,out=scheduler;r=m.run_native(snap,out)
    assert r["completed"]==20 and r["cli_invocations"]==4 and r["source_bytes_unchanged"]
    assert r["usage"]["input_tokens"]==4 and r["failed"]==0

def test_forbidden_native_tools_hold_without_fabricated_outputs(scheduler,monkeypatch):
    m,snap,out=scheduler
    monkeypatch.setattr(m.subprocess,"run",lambda *args,**kwargs:SimpleNamespace(stdout=json.dumps({"type":"item.completed","item":{"type":"command_execution"}}),stderr="",returncode=0))
    r=m.run_native(snap,out)
    assert r["completed"]==0 and r["failed"]==20 and r["cli_invocations"]==4
    assert (out/"results.jsonl").read_bytes()==b""
