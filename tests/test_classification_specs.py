import json

import pytest

from recommend_book.classification import build_prompt, load_taxonomy
from recommend_book.classification.runner import build_run_config
from recommend_book.jobs import canonical_json
from test_classification_guarded import local_work, unknown_result


def test_only_two_experiment_candidate_families():
    from recommend_book.classification.specs import get_spec, EXPERIMENT_CANDIDATES
    assert EXPERIMENT_CANDIDATES == ("definitions-v3", "context-v3")
    with pytest.raises(ValueError, match="Unknown"):
        get_spec("arbitrary-third-candidate")


def test_new_prompt_keeps_safe_work_and_uses_distinct_version():
    from recommend_book.classification.specs import get_spec
    work = local_work()
    prompt = get_spec("definitions-v3").build_prompt(work, model="gpt-6.1-sol", cli_version="fake-1", taxonomy=load_taxonomy())
    payload = json.loads(prompt.split("\nBEGIN_LOCAL_DATA_JSON\n")[1].split("\nEND_LOCAL_DATA_JSON")[0])
    assert payload["work"] == json.loads(build_prompt(work).split("\nBEGIN_LOCAL_DATA_JSON\n")[1].split("\nEND_LOCAL_DATA_JSON")[0])["work"]
    assert payload["output_identity"]["prompt_version"] == "3.0-definitions"
    assert "narrator" in prompt.lower()
    assert "no tools" in prompt.lower()


def test_new_validator_preserves_raw_result_values_and_exact_spec():
    from recommend_book.classification.specs import get_spec
    work = local_work()
    result = unknown_result(work)
    result["prompt_version"] = "3.0-definitions"
    original = canonical_json(result)
    assert get_spec("definitions-v3").validate_result(result, work, load_taxonomy()) is result
    assert canonical_json(result) == original
    with pytest.raises(ValueError, match="prompt_version"):
        get_spec("context-v3").validate_result(result, work, load_taxonomy())


def test_offline_historical_config_recipe_has_no_guard_or_candidate_keys():
    historical = build_run_config("gpt-6.1-sol", "saved-cli", load_taxonomy(), 5)
    assert "execution_spec" not in historical
    assert "usage_guard_hash" not in historical
    new = build_run_config("gpt-6.1-sol", "saved-cli", load_taxonomy(), 5, execution_spec="definitions-v3")
    assert new["prompt_version"] == "3.0-definitions"
    assert new["execution_spec"] == "definitions-v3"
    assert new["usage_guard_version"]
    assert new["schema_hash"] != historical["schema_hash"]


def native_result(work, profile="native-definitions-v3"):
    from recommend_book.classification.specs import get_spec
    from recommend_book.classification import input_hash
    return {"work_id":work.work_id,"expression_id":work.expression_id,"input_hash":input_hash(work),
        "prompt_version":get_spec(profile).prompt_version,"model":"gpt-6.1-sol","cli_version":"fake-1",
        "task_version":"native-protagonist-v3",
        "protagonist":{"state":"unknown","name":None,"evidence":[],"unknown_reason":"No single protagonist supplied"},
        "age":{"state":"unknown","value":None,"evidence":[],"unknown_reason":"No age support"},
        "profession":{"state":"unknown","value":None,"evidence":[],"unknown_reason":"No occupation support"}}


def test_native_same_two_families_distinct_task_and_blind_prompt():
    from recommend_book.classification.specs import get_spec
    work = local_work()
    for name in ("native-definitions-v3", "native-context-v3"):
        spec = get_spec(name)
        text = spec.build_prompt(work, model="gpt-6.1-sol", cli_version="fake-1", taxonomy=load_taxonomy())
        payload = json.loads(text.split("\nBEGIN_LOCAL_DATA_JSON\n")[1].split("\nEND_LOCAL_DATA_JSON")[0])
        assert "taxonomy" not in payload
        assert payload["output_identity"]["task_version"] == "native-protagonist-v3"
        assert "one complete chapter" in text
        assert "no numerical" in text
        assert spec.validate_result(native_result(work, name), work, load_taxonomy())


@pytest.mark.parametrize("change", ["bad_age", "unresolved_person", "invented_quote", "wrong_locator", "unknown_value", "foreign_id"])
def test_native_schema_and_grounding_reject_unsupported_state(change):
    from recommend_book.classification.specs import get_spec
    work = local_work()
    work.evidence[0].kind='excerpt';work.evidence[0].language='en'
    result = native_result(work)
    ref = {"evidence_id":work.evidence[0].evidence_id,"locator":work.evidence[0].locator,"quote":work.evidence[0].text}
    result["protagonist"] = {"state":"known","name":"single person","evidence":[ref],"unknown_reason":None}
    result["age"] = {"state":"known","value":"child","evidence":[ref.copy()],"unknown_reason":None}
    if change == "bad_age":result["age"]["value"] = "?"
    elif change == "unresolved_person":result["protagonist"] = native_result(work)["protagonist"]
    elif change == "invented_quote":result["age"]["evidence"][0]["quote"] = "Invented quote not in input"
    elif change == "wrong_locator":result["age"]["evidence"][0]["locator"] = "different chapter"
    elif change == "unknown_value":result["profession"]["value"] = "teacher"
    else:result["work_id"] = "foreign"
    with pytest.raises(ValueError):get_spec("native-definitions-v3").validate_result(result, work, load_taxonomy())


def test_native_profession_preserves_exact_free_string_without_synonyms():
    from recommend_book.classification.specs import get_spec
    work = local_work()
    work.evidence[0].kind='excerpt';work.evidence[0].language='en'
    result = native_result(work)
    ref = {"evidence_id":work.evidence[0].evidence_id,"locator":work.evidence[0].locator,"quote":work.evidence[0].text}
    result["protagonist"] = {"state":"known","name":"single person","evidence":[ref],"unknown_reason":None}
    result["profession"] = {"state":"known","value":"Unnormalized exact occupation","evidence":[ref],"unknown_reason":None}
    before = canonical_json(result)
    assert get_spec("native-definitions-v3").validate_result(result, work, load_taxonomy()) is result
    assert canonical_json(result) == before


@pytest.mark.parametrize('wrong',['synopsis','expression','language'])
def test_native_known_needs_primary_excerpt_and_exact_expression(wrong):
    from recommend_book.classification.specs import get_spec
    work=local_work();work.evidence[0].kind='excerpt';work.evidence[0].language='en'
    if wrong=='synopsis':work.evidence[0].kind='synopsis'
    elif wrong=='expression':work.expression_id='edition-a';work.evidence[0].expression_ref='edition-b'
    else:work.evidence[0].language='fr'
    result=native_result(work)
    result['protagonist']={'state':'known','name':'detective','evidence':[{
        'evidence_id':work.evidence[0].evidence_id,'locator':work.evidence[0].locator,'quote':work.evidence[0].text}],
        'unknown_reason':None}
    with pytest.raises(ValueError,match='excerpt|expression|language'):
        get_spec('native-definitions-v3').validate_result(result,work,load_taxonomy())
