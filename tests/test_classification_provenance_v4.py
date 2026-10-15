import json
from jsonschema import Draft202012Validator

from recommend_book.classification import load_taxonomy
from test_classification_guarded import local_work


def test_provenance_v4_is_a_future_only_spec_with_shared_batch_guidance():
    from recommend_book.classification.specs import ALL_SPEC_NAMES, get_spec

    work = local_work()
    spec = get_spec("provenance-v4")
    prompt = spec.build_prompt(
        work, model="gpt-6.1-sol", cli_version="fake-1", taxonomy=load_taxonomy()
    )
    payload = json.loads(
        prompt.split("\nBEGIN_LOCAL_DATA_JSON\n", 1)[1].split(
            "\nEND_LOCAL_DATA_JSON", 1
        )[0]
    )

    assert spec.prompt_version == "4.0-provenance"
    assert "provenance-v4" in ALL_SPEC_NAMES
    assert payload["output_identity"]["prompt_version"] == "4.0-provenance"
    assert "shared, content-addressed" in prompt
    assert "shared raw artifact alone is" in prompt
    assert "page ID" in prompt
    assert "complex interiority" in prompt
    assert "marginal social" in prompt
    assert "supporting character's occupation" in prompt
    assert "ordinary semantic uncertainty" in prompt

    assert spec.expected_prompt(
        [work], model="gpt-6.1-sol", cli_version="fake-1", taxonomy=load_taxonomy()
    ) == prompt
    second = work.model_copy(update={"work_id": "guarded-work-2"})
    batch = spec.build_batch_prompt(
        [work, second], model="gpt-6.1-sol", cli_version="fake-1", taxonomy=load_taxonomy()
    )
    assert spec.expected_prompt(
        [work, second], model="gpt-6.1-sol", cli_version="fake-1", taxonomy=load_taxonomy()
    ) == batch

    old_prompt = get_spec("definitions-v3").build_prompt(
        work, model="gpt-6.1-sol", cli_version="fake-1", taxonomy=load_taxonomy()
    )
    assert "supporting character's occupation" not in old_prompt


def test_historical_and_existing_candidate_versions_do_not_change():
    from recommend_book.classification.specs import get_spec

    assert get_spec("definitions-v3").prompt_version == "3.0-definitions"
    assert get_spec("context-v3").prompt_version == "3.1-context"
    assert "supporting character's occupation" not in get_spec("context-v3").build_prompt(
        local_work(), model="gpt-6.1-sol", cli_version="fake-1", taxonomy=load_taxonomy()
    )


def test_provenance_v4_schema_and_semantic_validator_accept_unknown_result():
    from recommend_book.classification.specs import get_spec
    from test_classification_guarded import unknown_result

    work = local_work()
    result = unknown_result(work)
    result["prompt_version"] = "4.0-provenance"
    with open("schemas/classification-provenance-v4.schema.json", encoding="utf-8") as stream:
        schema = json.load(stream)
    assert list(Draft202012Validator(schema).iter_errors(result)) == []
    assert get_spec("provenance-v4").validate_result(result, work, load_taxonomy()) is result


def test_provenance_v4_run_config_binds_new_schema():
    from recommend_book.classification.runner import build_run_config

    config = build_run_config(
        "gpt-6.1-sol", "fake-1", load_taxonomy(), 5, execution_spec="provenance-v4"
    )
    assert config["prompt_version"] == "4.0-provenance"
    assert "schemas/classification-provenance-v4.schema.json" in config["dependency_sha256"]
