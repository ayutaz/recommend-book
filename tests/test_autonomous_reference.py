"""Synthetic tests: no private ratings and no network/model invocation."""
import copy
import hashlib
import json
import importlib.util
from pathlib import Path

import pytest

from recommend_book.autonomous_reference import (
    build_reference_input, extract_body, positive_report, strict_json,
)


def fixture():
    metadata = {"idno":"ne0002", "title":"Example", "author.name":"ExampleAuthor",
        "author.name.full":"Example Author", "author.date.death":"1920",
        "author.country":"Spain", "year":"1900", "availability":"free",
        "protagonist.age":"adult", "protagonist.profession":"student"}
    gold = {"reference_id":"ne0002", "work_id":"reference:conssa:ne0002",
        "labels":[{"label_id":"protagonist.adult","decision":"present",
        "native_field":"protagonist.age","native_value":"adult"}]}
    xml = b'''<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc>
    <titleStmt><title type="short">Example</title><author><name ref="#ExampleAuthor"/></author></titleStmt>
    <publicationStmt><availability status="free"><licence target="https://creativecommons.org/licenses/by/4.0/"/></availability><idno type="cligs">ne0002</idno></publicationStmt>
    <sourceDesc><bibl type="print-source"><date when="1900"/></bibl><bibl type="edition-first"><date when="1900"/></bibl></sourceDesc></fileDesc>
    <profileDesc><textClass><keywords scheme="keywords.xml" cert="high"><term type="text.language">Spanish</term>
    <term type="author.date.death">1920</term><term type="author.country">Spain</term>
    <term type="text.characters.protagonist.age">adult</term></keywords></textClass></profileDesc></teiHeader>
    <text><front>SECRET FRONT</front><body><div type="preface">SECRET PREFACE</div>
    <div type="chapter"><head>SECRET TITLE</head><p>He read <seg rend="italic">books</seg> daily.<note>SECRET NOTE</note> Afterwards he slept.</p>
    <p><choice><orig>Original sentence.</orig><reg>SECRET REWRITE</reg><anno>SECRET ANNOTATION</anno></choice></p></div></body><back>SECRET BACK</back></text></TEI>'''
    return metadata, gold, xml


def build(metadata=None, gold=None, xml=None):
    m,g,x=fixture()
    return build_reference_input(xml or x, metadata or m, gold or g,
        source_url="https://raw.githubusercontent.com/cligs/conssa/8fccf669ed1bdbad309ff190f5afb0d6066d70a3/master/master/ne0002.xml",
        as_of_year=2026, window_chars=6000)


def test_answer_header_and_editorial_material_removed():
    _,_,xml=fixture()
    body,audit=extract_body(xml)
    assert body == "He read books daily. Afterwards he slept.\nOriginal sentence."
    assert "SECRET" not in body
    assert audit["excluded_tags"]["note"] == 1


def test_opaque_work_fields_and_separate_provenance():
    work,audit=build()
    assert work.title == "External reference ne0002"
    assert work.authors == [] and work.first_publication_year is None
    assert work.original_language == "es"
    assert len(work.evidence)==1 and work.evidence[0].kind=="excerpt"
    assert work.evidence[0].llm_input_allowed
    assert work.evidence[0].attribution["creator"] == "CLiGS / Jose Calvo Tello"
    assert "native_field" not in work.model_dump_json()
    assert audit["identity_status"]=="pinned_corpus_id_and_native_annotation_matched"
    assert audit["rights"]["worldwide_public_domain_claim"] is False
    assert audit["rights"]["unverified_processing_regions"]


@pytest.mark.parametrize("key,value",[("availability","restricted"),("author.date.death","1975"),
    ("year","1931"),("year",""),("author.country","unknown")])
def test_noneligible_or_unknown_rights_rejected(key,value):
    m,_,_=fixture();m[key]=value
    with pytest.raises(ValueError): build(metadata=m)


@pytest.mark.parametrize("old,new",[(b'ne0002</idno>',b'ne0003</idno>'),
    (b'ref="#ExampleAuthor"',b'ref="#OtherAuthor"'),
    (b'Spanish</term>',b'English</term>'),
    (b'adult</term>',b'child</term>'),
    (b'when="1900"',b'when="1935"'),
    (b'status="free"',b'status="restricted"'),
    (b'</titleStmt>',b'<respStmt><resp>translator</resp><name>Modern translator</name></respStmt></titleStmt>')])
def test_identity_edition_and_annotation_disagreements_rejected(old,new):
    _,_,xml=fixture()
    with pytest.raises(ValueError): build(xml=xml.replace(old,new))


def test_short_body_single_window_long_body_three_disjoint_windows():
    m,g,x=fixture()
    x=x.replace(b'He read ',b'He read '+b'A '*12000)
    work,audit=build(xml=x)
    assert len(work.evidence)==3
    assert sum(len(e.text) for e in work.evidence)==18000
    intervals=[json.loads(e.locator)["char_range"] for e in work.evidence]
    assert intervals[0][1]<=intervals[1][0] and intervals[1][1]<=intervals[2][0]
    assert audit["sampling"]["selection_uses_labels"] is False


@pytest.mark.parametrize("raw",['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}'])
def test_strict_json_rejects_ambiguous_or_nonfinite(raw):
    with pytest.raises(ValueError): strict_json(raw)


def test_dtd_rejected():
    with pytest.raises(ValueError): extract_body(b'<!DOCTYPE x [<!ENTITY a "bad">]><x/>')


def test_external_genre_columns_never_used_or_require_unique_values():
    _,_,xml=fixture()
    xml=xml.replace(b'</keywords>',b'<term type="text.genre.subgenre.edit" resp="wikidata">fantastic</term><term type="text.genre.subgenre.edit" resp="amazon">romance</term></keywords>')
    work,_=build(xml=xml)
    assert "fantastic" not in work.model_dump_json()


def test_library_classification_preceding_native_keywords_not_read():
    _,_,xml=fixture()
    xml=xml.replace(b'<textClass>',b'<textClass><keywords scheme="library"><term type="text.language">SECRET</term></keywords>')
    work,_=build(xml=xml)
    assert "SECRET" not in work.model_dump_json()


def test_original_embedded_letters_and_dialogue_retained():
    _,_,xml=fixture()
    xml=xml.replace(b'<p>He read ',b'<floatingText><body><p>A letter.</p></body></floatingText><sp><stage>He stood.</stage><ab>He spoke.</ab></sp><p>He read ')
    body,_=extract_body(xml)
    assert "A letter." in body and "He stood." in body and "He spoke." in body


def test_literal_four_digit_print_date_allowed_when_attribute_absent():
    _,_,xml=fixture()
    xml=xml.replace(b'<bibl type="print-source"><date when="1900"/>',b'<bibl type="print-source"><date>1900</date>')
    _,audit=build(xml=xml)
    assert audit["editions"]["print-source"]["year"]==1900


def test_posthumous_first_publication_conservative_japan_envelope():
    from recommend_book.autonomous_reference import assess_metadata_rights
    m,_,_=fixture();m.update({"author.date.death":"1880","year":"1930"})
    rights=assess_metadata_rights(m)
    assert rights["japan_conservative_expiry_upper_bound_year"]==1992


def test_positive_only_all_planned_positives_preserved():
    _,g,_=fixture()
    other=copy.deepcopy(g);other["work_id"]="reference:conssa:ne0003";other["reference_id"]="ne0003"
    result={"work_id":g["work_id"],"labels":[{"label_id":"protagonist.adult","decision":"present"}]}
    report=positive_report([g,other],[result],{g["work_id"]})
    assert report["positive_detection_recall"]==.5
    assert report["baselines"]["always_present"]["full_planned_recall"]==1.0
    assert report["baselines"]["always_present"]["executable_input_recall"]==1.0
    assert report["baselines"]["always_unknown"]["full_planned_recall"]==0.0
    assert report["missing_result_work_count"]==1
    assert "precision" not in report and report["calibration_permitted"] is False
    assert report["native_positive_detection"]["protagonist.age=adult"]["positive_support"]==2
    assert report["native_positive_detection"]["protagonist.age=adult"]["detected"]==1
    assert report["unexecuted_reference_work_count"]==1
    assert report["input_missing_result_work_count"]==0


@pytest.mark.parametrize("decision",["unknown","absent",None])
def test_unknown_absent_omitted_are_misses(decision):
    _,g,_=fixture()
    labels=[] if decision is None else [{"label_id":"protagonist.adult","decision":decision}]
    report=positive_report([g],[{"work_id":g["work_id"],"labels":labels}],{g["work_id"]})
    assert report["positive_detection_recall"]==0


def test_duplicate_and_foreign_results_rejected():
    _,g,_=fixture();r={"work_id":g["work_id"],"labels":[]}
    with pytest.raises(ValueError):positive_report([g],[r,r],{g["work_id"]})
    r["work_id"]="foreign"
    with pytest.raises(ValueError):positive_report([g],[r],{g["work_id"]})


@pytest.fixture
def score_fixture(tmp_path,monkeypatch):
    from recommend_book.classification.validation import load_taxonomy,input_hash,PROMPT_VERSION
    from recommend_book.classification.runner import build_run_config
    from recommend_book.jobs import job_key
    repo=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location("reference_builder_test",repo/"scripts/build_autonomous_reference.py")
    script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    monkeypatch.setattr(script,"ROOT",tmp_path)
    # Synthetic namespace; copied code bytes are only integrity fixtures, never executed as classifier.
    for rel in ("scripts/freeze_classification_execution.py","config/sources.json","config/taxonomy.json",
                "schemas/classification.schema.json","src/recommend_book/classification/prompt.py",
                "src/recommend_book/classification/validation.py","src/recommend_book/classification/runner.py",
                "src/recommend_book/models.py","src/recommend_book/jobs.py","pyproject.toml","uv.lock"):
        p=tmp_path/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((repo/rel).read_bytes())
    base=tmp_path/"data/autonomous-poc-v1/reference";snap=base/"fixture-snapshot";run=base/"fixture-run"
    snap.mkdir(parents=True);(run/"results").mkdir(parents=True);(run/"jobs").mkdir()
    work,_=build();_,g,_=fixture()
    monkeypatch.setattr(script,"fixed_sources",lambda:({},[g],{},{}))
    script.write_jsonl(snap/"work-inputs.jsonl",[work.model_dump(mode="json")])
    script.write_json(snap/"manifest.json",{"files":{"work-inputs.jsonl":script.sha256((snap/"work-inputs.jsonl").read_bytes())}})
    config=build_run_config("gpt-6.1-sol","fixture-only",load_taxonomy(),5)
    key=job_key(input_hash(work),config);e=work.evidence[0]
    result={"work_id":work.work_id,"expression_id":None,"target_language":"es","input_hash":input_hash(work),
        "taxonomy_version":"1.0","prompt_version":PROMPT_VERSION,"model":"gpt-6.1-sol","cli_version":"fixture-only",
        "labels":[{"label_id":"protagonist.adult","axis":"protagonist","decision":"present","scope":"work",
        "expression_id":None,"relevance":None,"raw_confidence":.8,"calibrated_confidence":None,
        "calibration_status":"uncalibrated","calibration_version":None,"evidence":[{"evidence_id":e.evidence_id,
        "locator":e.locator,"quote":"He read books daily.","summary":None,"directness":"direct"}],
        "missing_reason":None,"spoiler":"none"}],"missing_axes":[{"axis":a,"decision":"unknown","reason":"Synthetic insufficient"}
        for a in ["genre","theme","structure","style","atmosphere"]],"conflicts":[],"needs_human_review":True}
    job={"key":key,"work_id":work.work_id,"expression_id":None,"input_hash":input_hash(work),"config":config,
        "status":"completed","result_path":f"results/{key}.json"}
    manifest={"run_id":"fixture-only","config":config,"selected":1,"completed":1,"failed":0,"pending":0,
        "stopped":False,"jobs":{key:job},"initial_concurrency":2,"limits":{"timeout":900},"usage":{},"elapsed_seconds":1}
    artifacts={rel:script.sha256((tmp_path/rel).read_bytes()) for rel in [
        "config/sources.json","config/taxonomy.json","schemas/classification.schema.json",
        "src/recommend_book/classification/prompt.py","src/recommend_book/classification/validation.py",
        "src/recommend_book/classification/runner.py","src/recommend_book/models.py","src/recommend_book/jobs.py","pyproject.toml","uv.lock"]}
    context={"input_sha256":script.sha256((snap/"work-inputs.jsonl").read_bytes()),
        "reference_snapshot_sha256":script.sha256((snap/"manifest.json").read_bytes()),
        "execution_context":{"run_config":config,"artifact_sha256":artifacts,"controls":{"concurrency":2,"batch_size":5,"timeout":900}}}
    script.write_json(run/"execution-context.json",context)
    script.write_json(run/"after-observation.json",{"source_artifacts_unchanged":True,"input_unchanged":True,
        "artifact_sha256":artifacts,"execution_context_manifest_sha256":script.sha256((run/"execution-context.json").read_bytes())})
    def publish():
        script.write_json(run/"manifest.json",manifest)
        script.write_jsonl(run/"results.jsonl",[result] if manifest["completed"] else [])
        script.write_json(run/"jobs"/f"{key}.json",job)
        if manifest["completed"]:script.write_json(run/"results"/f"{key}.json",result)
        proof={"schema_version":"classification-terminal-proof-v1","process_exit_observed":True,
            "exit_code":0,"session_id":1,"observed_at":"2026-10-13T00:00:00+00:00","run_id":manifest["run_id"]}
        for name,p in (("manifest_sha256",run/"manifest.json"),("results_sha256",run/"results.jsonl"),
            ("ordered_inputs_sha256",snap/"work-inputs.jsonl"),("reference_snapshot_sha256",snap/"manifest.json"),
            ("execution_context_manifest_sha256",run/"execution-context.json")):proof[name]=script.sha256(p.read_bytes())
        script.write_json(run/"terminal-proof.json",proof)
        return proof
    publish()
    return script,snap,run,base/"fixture-report.json",manifest,result,job,publish


@pytest.mark.parametrize("separator",["/","\\"])
def test_terminal_scoring_accepts_native_safe_relative_result_path(score_fixture,separator):
    s,snap,run,out,m,r,j,publish=score_fixture;j["result_path"]=j["result_path"].replace("/",separator);publish()
    report=s.score(snap,run,run/"terminal-proof.json",out)
    assert report["positive_detection_recall"]==1 and report["human_annotation_generated"] is False


@pytest.mark.parametrize("field,value",[("pending",1),("stopped",True),("selected",True),("completed",False)])
def test_partial_and_boolean_terminal_counts_rejected(score_fixture,field,value):
    s,snap,run,out,m,r,j,publish=score_fixture;m[field]=value;publish()
    with pytest.raises(ValueError):s.score(snap,run,run/"terminal-proof.json",out)


def test_terminal_held_keeps_denominator(score_fixture):
    s,snap,run,out,m,r,j,publish=score_fixture;m.update(completed=0,failed=1);j["status"]="held";publish()
    report=s.score(snap,run,run/"terminal-proof.json",out)
    assert report["held_work_count"]==1 and report["positive_support"]==1 and report["positive_detection_recall"]==0


@pytest.mark.parametrize("path",["../escape.json","C:\\escape.json","/escape.json","results/other.json"])
def test_unsafe_or_wrong_job_path_rejected(score_fixture,path):
    s,snap,run,out,m,r,j,publish=score_fixture;j["result_path"]=path;publish()
    with pytest.raises(ValueError):s.score(snap,run,run/"terminal-proof.json",out)


def test_model_cli_mismatch_rejected_even_valid_result(score_fixture):
    s,snap,run,out,m,r,j,publish=score_fixture;r["cli_version"]="wrong";publish()
    with pytest.raises(ValueError):s.score(snap,run,run/"terminal-proof.json",out)


def test_false_process_observation_rejected(score_fixture):
    s,snap,run,out,m,r,j,publish=score_fixture;proof=publish();proof["process_exit_observed"]=False
    s.write_json(run/"terminal-proof.json",proof)
    with pytest.raises(ValueError):s.score(snap,run,run/"terminal-proof.json",out)


def test_durable_state_mismatch_rejected(score_fixture):
    s,snap,run,out,m,r,j,publish=score_fixture
    key=next(iter(m["jobs"]));s.write_json(run/"jobs"/f"{key}.json",{**j,"input_hash":"wrong"})
    with pytest.raises(ValueError):s.score(snap,run,run/"terminal-proof.json",out)


def test_native_prompt_is_independent_single_protagonist_without_world_taxonomy():
    from recommend_book.autonomous_reference import build_native_prompt
    work,_=build()
    codebook={"age_values":["adult","child","young","mature","?","other","unknown"],
        "profession_scope":"open_nominal_no_categories_or_grouping_definition"}
    prompt=build_native_prompt([work],codebook,cli_version="fixture-only")
    assert "single main protagonist" in prompt and "protagonist.adult" not in prompt
    assert "native_field" not in prompt and "source_annotation_certainty" not in prompt
    assert "Do not invent numeric age boundaries" in prompt


def test_native_unknown_is_not_absence_and_known_requires_exact_support():
    from recommend_book.autonomous_reference import validate_native_result,NATIVE_VERSION
    from recommend_book.classification.validation import input_hash
    work,_=build();e=work.evidence[0]
    r={"work_id":work.work_id,"input_hash":input_hash(work),"model":"gpt-6.1-sol","cli_version":"fixture-only",
       "schema_version":NATIVE_VERSION,"protagonist_name":None,
       "age":{"value":"unknown","evidence":[],"reason":"Ambiguous main protagonist"},
       "profession":{"value":None,"evidence":[],"reason":"Insufficient original text"}}
    assert validate_native_result(r,work,"fixture-only")==r
    r["age"].update(value="adult",reason=None)
    with pytest.raises(ValueError):validate_native_result(r,work,"fixture-only")
    r["age"]["evidence"]=[{"evidence_id":e.evidence_id,"locator":e.locator,"quote":"fabricated"}]
    with pytest.raises(ValueError):validate_native_result(r,work,"fixture-only")


def test_native_profession_exact_detection_without_painter_to_artist_mapping():
    from recommend_book.autonomous_reference import native_positive_report
    _,g,_=fixture();g["labels"]=[{"label_id":"protagonist.artist","decision":"present","native_field":"protagonist.profession","native_value":"artist"}]
    r={"work_id":g["work_id"],"age":{"value":"unknown"},"profession":{"value":"painter"}}
    report=native_positive_report([g],[r],{g["work_id"]})
    assert report["detected_positives"]==0 and report["native_task_scope"]=="dedicated_single_protagonist_native_predictions"
