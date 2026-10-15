"""Synthetic source/identity checks; mocked native admission is not real quality."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from recommend_book.models import Evidence, SourceRecord, Title, WorkInput
from recommend_book.rights import RightsRegistry
from recommend_book.usage_guard import UsageGuard, UsageDecision, model_hash, build_input_entry, source_binding
from recommend_book.recommendation import history_binding as hb
from recommend_book.recommendation import serialization_profile as sp
from recommend_book.recommendation import serialization_profile_v3 as sp3
from recommend_book import next_database


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def prepared(tmp_path, monkeypatch, request):
    profile_module = sp3 if getattr(request, "param", None) == "v3" else sp
    recovery = tmp_path / "recovery"
    recovery.mkdir()
    rows = [{"ncode": f"n{i:04d}aa", "title": f"Synthetic Work {i}", "rating": 4} for i in range(335)]
    raw = "".join(f'<li class="c-up-reaction-item"><p class="c-up-reaction-item__title"><a href="https://ncode.syosetu.com/{r["ncode"]}/">{r["title"]}</a></p><input name="score" value="8"></li>' for r in rows).encode()
    digest = hb.sha256(raw)
    (recovery / (digest + ".html")).write_bytes(raw)
    database = tmp_path / "owned.sqlite"
    c = sqlite3.connect(database)
    c.executescript('''CREATE TABLE imports(snapshot_id,pages_json);
      CREATE TABLE rating_observations(snapshot_id,ncode,title,my_rating,source_page,row_number,evidence_json);''')
    c.execute("INSERT INTO imports VALUES (?,?)", ("snapshot", json.dumps([{"raw_file": "page.html", "sha256": digest}])))
    c.executemany("INSERT INTO rating_observations VALUES (?,?,?,?,?,?,?)", [
        ("snapshot", r["ncode"].upper(), r["title"], r["rating"], "page.html", i + 1,
         json.dumps({"href": f'https://ncode.syosetu.com/{r["ncode"]}/'})) for i, r in enumerate(rows)])
    c.commit()
    c.close()
    dbsha = hb.sha256(database.read_bytes())
    report = {"version": "owner-original-html-recovery-v1", "database_sha256": dbsha, "verified_observation_n": 335,
              "pages": [{"snapshot_id": "snapshot", "page_sha256": digest, "git_path": "data/raw/page.html", "git_commit": "a" * 40, "git_blob_sha1": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(), "row_n":335}]}
    recovery_descriptor = _write(recovery / "recovery.json", report)
    text = "{{Infobox animanga/Header|ジャンル=ファンタジー}}\n小説家になろうで連載されたオンライン小説で、書籍版が刊行された。\n== 外部リンク ==\n{{小説家になろう作品|n0000aa}}"
    wp = tmp_path / "wikipedia.json"
    page = {"pageid": 7, "title": "Synthetic Work 0", "pageprops": {"wikibase_item": "Q1"}, "revisions": [{"revid": 9, "slots": {"main": {"content": text}}}]}
    wpdesc = _write(wp, {"query": {"pages": [page]}})
    receipt = _write(tmp_path / "wp-receipt.json", {"path": str(wp.resolve()), "sha256": wpdesc["sha256"]})
    binding = {"public_pageid": 7, "owned_ncode": "n0000aa", "source_database_sha256": dbsha, "snapshot_id": "snapshot", "row_number": 1,
               "page_sha256": digest, "git_commit": "a" * 40, "git_blob_sha1": report["pages"][0]["git_blob_sha1"], "owner_href": "https://ncode.syosetu.com/n0000aa/", "owned_title_sha256": hb.sha256(b"Synthetic Work 0"),
               "public_article_source": {"pageid": 7, "revision_id": 9, "wikibase_item": "Q1", "raw_path": wpdesc["path"], "raw_sha256": wpdesc["sha256"], "revision_text_sha256": hb.sha256(text.encode()), "source_receipt_path": receipt["path"], "source_receipt_sha256": receipt["sha256"]}}
    bindings = tmp_path / "bindings.jsonl"
    bindings.write_text(json.dumps(binding) + "\n", encoding="utf-8")
    wd = tmp_path / "wikidata.json"
    claims = {}
    if getattr(request, "param", None) == "groups":
        claims = {prop: [{"rank": "normal", "mainsnak": {"property": prop, "snaktype": "value", "datatype": "wikibase-item", "datavalue": {"value": {"entity-type": "item", "id": identity}}}}] for prop, identity in [("P50", "Q9"), ("P179", "Q12")]}
    wdsha = _write(wd, {"entities": {"Q1": {"id": "Q1", "sitelinks": {"jawiki": {"title": page["title"]}}, "claims": claims}}})["sha256"]
    record = SourceRecord(source="wikidata", source_id="Q1", source_url="https://www.wikidata.org/wiki/Q1", retrieved_at="2026-10-15T00:00:00Z", provider_id="wikidata", record_kind="work", titles=[Title(value=page["title"])], raw_sha256=wdsha, raw_path=str(wd.resolve()), rights_policy_id="wikidata-cc0")
    source_db = tmp_path / "public.sqlite"
    c = sqlite3.connect(source_db)
    c.execute("CREATE TABLE source_records(version_id,record_json,raw_path,raw_sha256)")
    c.execute("INSERT INTO source_records VALUES (?,?,?,?)", ("version1", record.model_dump_json(), str(wd.resolve()), wdsha))
    c.commit(); c.close()
    work = WorkInput(work_id="public:wd:Q1", title=page["title"], source_record_ids=["version1"])
    origin = model_hash(work)
    registry = RightsRegistry.default()
    registry_desc = _write(tmp_path / "registry.json", {"version": "test", "rights_policies": [p.model_dump(mode="json") for p in registry.policies.values()]})
    ledger = {"version": "usage-ledger-v3", "registry_sha256": model_hash([p.model_dump(mode="json") for p in registry.policies.values()]), "registry_path": registry_desc["path"], "registry_file_sha256": registry_desc["sha256"], "inputs": {origin: build_input_entry(work, {"version1": source_binding(source_db, "version1")})}}
    ledger_desc = _write(tmp_path / "ledger.json", ledger)
    guard = UsageGuard(ledger_desc["path"], registry_path=registry_desc["path"], expected_ledger_sha256=ledger_desc["sha256"])
    calls = []
    def check(payload, origins, usage, **kwargs):
        calls.append((deepcopy(payload), list(origins), usage, kwargs))
        return UsageDecision(allowed=True, usage=usage, ledger_path=str(guard.ledger_path), ledger_sha256=ledger_desc["sha256"], input_hashes=origins, payload_sha256=model_hash(payload))
    monkeypatch.setattr(guard, "check_derived", check)
    config = {"synthetic_config": 1}
    config_desc = _write(tmp_path / "classification-spec.json", config)
    native = {"work_id": work.work_id, "input_hash": origin, "expression_id": None, "labels": []}
    provenance = {"genre.fantasy": [{"scope": "work", "expression_id": None, "input_hash": origin, "source_record_ids": ["version1"]}]}
    feature = {"work_id": work.work_id, "features": {"genre.fantasy": 1.}, "feature_provenance": provenance}
    manifest = {"config_sha256": model_hash(config), "usage_ledger_sha256": ledger_desc["sha256"]}
    recipe = {"path": str(Path(next_database.__file__).resolve()), "sha256": hb.sha256(Path(next_database.__file__).read_bytes()), "extraction": "work-present-relevance-max-v1", "native_result_sha256": model_hash(native)}
    public = {"work_id": work.work_id, "title": work.title, "input_json": work.model_dump(mode="json"), "public_subject": {"pageid": 7, "revision_id": 9, "wikibase_item": "Q1", "raw_sha256": wpdesc["sha256"]}, "source_scope": "abstract_work", "features": feature["features"], "feature_provenance": provenance, "classification_originating_input_hashes": [origin],
              "classification_derivation": {"kind": "classification", "payload": native, "value_sha256": model_hash(native), "originating_input_hashes": [origin], "source_manifest": manifest},
              "feature_derivation": {"kind": "classification_features", "payload": feature, "value_sha256": model_hash(feature), "originating_input_hashes": [origin], "source_manifest": manifest, "features_recipe": recipe, "native_result": native}}
    artifacts = {"owned_database": {"path": str(database.resolve()), "sha256": dbsha}, "recovery_report": recovery_descriptor, "serialization_bindings": {"path": str(bindings.resolve()), "sha256": hb.sha256(bindings.read_bytes())}, "public_ledger": ledger_desc, "source_registry": registry_desc, "classification_spec": config_desc,
                 "profile_policy": _write(tmp_path / "profile-policy.json", profile_module.PROFILE_POLICY), "profile_spec": _write(tmp_path / "profile-spec.json", profile_module.serialization_profile_spec())}
    artifacts.update({key: {"path": str(Path(module.__file__).resolve()), "sha256": hb.sha256(Path(module.__file__).read_bytes())} for key, module in {"history_recipe": hb, "profile_recipe": profile_module, "feature_recipe": next_database}.items()})
    contract = {"version": profile_module.CONTRACT_VERSION, "purpose": profile_module.PROFILE_PURPOSE, "authorization_basis": profile_module.AUTHORIZATION_BASIS, "artifacts": artifacts, "public_rows_sha256": model_hash([public])}
    contractpath = tmp_path / "contract.json"
    desc = _write(contractpath, contract)
    return {"rows": [public], "contract": contract, "contractpath": contractpath, "contractsha": desc["sha256"], "guard": guard, "calls": calls, "binding": binding, "bindingspath": bindings, "wp": wp, "recovery": recovery, "database": database, "profile_module": profile_module}


def _run(fixture):
    return fixture.get("profile_module", sp).build_serialization_profile(fixture["rows"], contract_path=fixture["contractpath"], contract_sha256=fixture["contractsha"], public_guard=fixture["guard"])


def _rebind_contract(fixture):
    fixture["contract"]["public_rows_sha256"] = model_hash(fixture["rows"])
    fixture["contractsha"] = _write(fixture["contractpath"], fixture["contract"])["sha256"]


def test_exact_sources_join_private_rating_without_private_summary(prepared):
    got = _run(prepared)
    row, = got["private_rows"]
    assert row["work_id"] == "public:wd:Q1" and row["rating"] == 4
    assert row["features"] == {"genre.fantasy": 1.}
    assert row["read_status"] == "unknown"
    assert row["series_ids"] == [] and row["author_ids"] == []
    assert got["summary"]["bound_reference_n"] == 1
    assert got["summary"]["canonical_merges"] == 0
    assert "n0000aa" not in json.dumps(got["summary"])
    assert "Synthetic Work" not in json.dumps(got["summary"])
    assert [c[2] for c in prepared["calls"]] == ["display", "display"]
    receipt, = got["purpose"]["public_material_receipts"]
    assert receipt["kind"] == "public-display-and-literal-local-policy-v1"
    assert receipt["complete_batch_input_n"] == 1
    assert receipt["metadata_material_n"] == 1
    assert receipt["evidence_material_n"] == 0
    assert "allowed" not in receipt


@pytest.mark.parametrize("target", ["owner_page", "public_page", "binding", "database"])
def test_changed_raw_or_identity_proof_rejected(prepared, target):
    if target == "owner_page":
        path = next(prepared["recovery"].glob("*.html"));path.write_bytes(path.read_bytes() + b"changed")
    elif target == "public_page":
        prepared["wp"].write_bytes(prepared["wp"].read_bytes() + b" ")
    elif target == "binding":
        changed = deepcopy(prepared["binding"]);changed["owned_ncode"] = "n0001aa"
        prepared["bindingspath"].write_text(json.dumps(changed) + "\n", encoding="utf-8")
        prepared["contract"]["artifacts"]["serialization_bindings"]["sha256"] = hb.sha256(prepared["bindingspath"].read_bytes())
        _rebind_contract(prepared)
    else:
        c = sqlite3.connect(prepared["database"]);c.execute("UPDATE rating_observations SET my_rating=1 WHERE row_number=1");c.commit();c.close()
    with pytest.raises(ValueError):
        _run(prepared)


@pytest.mark.parametrize("scope", ["style", "expression"])
def test_cross_version_style_or_expression_feature_rejected(prepared, scope):
    row = prepared["rows"][0]
    if scope == "style":
        row["features"] = {"style.plain": 1}
        row["feature_provenance"] = {"style.plain": [{"scope": "work", "expression_id": None}]}
    else:
        row["feature_provenance"]["genre.fantasy"][0]["scope"] = "expression"
    feature = {"work_id": row["work_id"], "features": row["features"], "feature_provenance": row["feature_provenance"]}
    row["feature_derivation"].update(payload=feature, value_sha256=model_hash(feature))
    _rebind_contract(prepared)
    with pytest.raises(ValueError, match="scope|style"):
        _run(prepared)


def test_boolean_guard_and_foreign_subject_rejected(prepared, monkeypatch):
    monkeypatch.setattr(prepared["guard"], "check_derived", lambda *a, **k: True)
    with pytest.raises(ValueError, match="decision"):
        _run(prepared)


def test_foreign_work_input_cannot_attach_to_verified_owner_binding(prepared):
    prepared["rows"][0]["public_subject"]["wikibase_item"] = "Q2"
    _rebind_contract(prepared)
    with pytest.raises(ValueError, match="subject"):
        _run(prepared)


def test_old_title_spec_not_reusable_as_new_profile_spec(prepared):
    descriptor = prepared["contract"]["artifacts"]["profile_spec"]
    new = _write(Path(descriptor["path"]), {"version": "owned-title-fixed-v3.3"})
    prepared["contract"]["artifacts"]["profile_spec"] = new
    _rebind_contract(prepared)
    with pytest.raises(ValueError, match="spec"):
        _run(prepared)


def test_detached_feature_values_rejected_even_if_contract_is_resealed(prepared):
    prepared["rows"][0]["features"]["genre.fantasy"] = .5
    _rebind_contract(prepared)
    with pytest.raises(ValueError, match="lineage"):
        _run(prepared)


def test_complete_batch_held_origin_is_not_dropped(prepared, monkeypatch):
    row = prepared["rows"][0]
    origins = row["classification_originating_input_hashes"] + ["f" * 64]
    row["classification_originating_input_hashes"] = origins
    for key in ("classification_derivation", "feature_derivation"):
        row[key]["originating_input_hashes"] = origins
    _rebind_contract(prepared)
    seen = []
    def held(payload, origin_ids, usage, **kwargs):
        seen.append(origin_ids)
        return UsageDecision(allowed=False, usage=usage, ledger_path=str(prepared["guard"].ledger_path), ledger_sha256=prepared["contract"]["artifacts"]["public_ledger"]["sha256"], input_hashes=origin_ids, payload_sha256=model_hash(payload))
    monkeypatch.setattr(prepared["guard"], "check_derived", held)
    with pytest.raises(ValueError, match="decision"):
        _run(prepared)
    assert seen == [origins]


def test_unknown_labels_do_not_create_negative_features(prepared):
    row = prepared["rows"][0]
    native = row["classification_derivation"]["payload"]
    native["labels"] = [{"label_id": "theme.family", "decision": "unknown"}]
    row["classification_derivation"]["value_sha256"] = model_hash(native)
    row["features"], row["feature_provenance"] = {}, {}
    expected = {"work_id": row["work_id"], "features": {}, "feature_provenance": {}}
    feature = row["feature_derivation"]
    feature.update(payload=expected, value_sha256=model_hash(expected), native_result=native)
    feature["features_recipe"]["native_result_sha256"] = model_hash(native)
    _rebind_contract(prepared)
    got = _run(prepared)
    assert got["private_rows"][0]["features"] == {}
    assert got["summary"]["unknown_native_label_n"] == 1
    assert got["summary"]["supported_reference_n"] == 0


def test_wrong_purpose_receipt_rejected(prepared, monkeypatch):
    original = prepared["guard"].check_derived
    def external(*args, **kwargs):
        return original(*args, **kwargs).model_copy(update={"usage": "external_llm"})
    monkeypatch.setattr(prepared["guard"], "check_derived", external)
    with pytest.raises(ValueError, match="decision"):
        _run(prepared)


def test_only_exact_nondeprecated_native_author_series_claims_are_kept():
    def claim(prop, identity, rank="normal"):
        return {"rank": rank, "mainsnak": {"property": prop, "snaktype": "value", "datatype": "wikibase-item", "datavalue": {"value": {"id": identity, "entity-type": "item"}}}}
    entity = {"claims": {"P50": [claim("P50", "Q9"), claim("P50", "Q9"), claim("P50", "Q10", "deprecated"), claim("P179", "Q11")], "P179": [claim("P179", "Q12")]}}
    assert sp._claim_ids(entity, "P50") == ["wikidata:Q9"]
    assert sp._claim_ids(entity, "P179") == ["wikidata:Q12"]


def test_forged_author_id_cannot_enter_private_fold_groups(prepared):
    prepared["rows"][0]["author_ids"] = ["wikidata:Q999"]
    _rebind_contract(prepared)
    with pytest.raises(ValueError, match="author/series"):
        _run(prepared)


@pytest.mark.parametrize("prepared", ["groups"], indirect=True)
def test_native_source_author_series_ids_survive_exact_join(prepared):
    got = _run(prepared)
    assert got["private_rows"][0]["author_ids"] == ["wikidata:Q9"]
    assert got["private_rows"][0]["series_ids"] == ["wikidata:Q12"]
    assert got["summary"]["reference_author_identity_missing_n"] == 0
    assert got["summary"]["reference_series_identity_missing_n"] == 0


@pytest.mark.parametrize("state", ["denied", "unresolved", "conditional"])
def test_display_success_never_substitutes_literal_local_permission(prepared, state):
    artifacts = prepared["contract"]["artifacts"]
    registry_path = Path(artifacts["source_registry"]["path"])
    registry = json.loads(registry_path.read_bytes())
    next(p for p in registry["rights_policies"] if p["policy_id"] == "wikidata-cc0")["usages"]["local_storage"] = state
    artifacts["source_registry"] = _write(registry_path, registry)
    ledger_path = Path(artifacts["public_ledger"]["path"])
    ledger = json.loads(ledger_path.read_bytes())
    ledger["registry_file_sha256"] = artifacts["source_registry"]["sha256"]
    selected = RightsRegistry.from_file(registry_path)
    ledger["registry_sha256"] = model_hash([p.model_dump(mode="json") for p in selected.policies.values()])
    artifacts["public_ledger"] = _write(ledger_path, ledger)
    prepared["guard"].expected_ledger_sha256 = artifacts["public_ledger"]["sha256"]
    for row in prepared["rows"]:
        row["classification_derivation"]["source_manifest"]["usage_ledger_sha256"] = artifacts["public_ledger"]["sha256"]
    def display(payload, origins, usage, **kwargs):
        assert usage == "display"
        return UsageDecision(allowed=True, usage=usage, ledger_path=str(prepared["guard"].ledger_path), ledger_sha256=artifacts["public_ledger"]["sha256"], input_hashes=origins, payload_sha256=model_hash(payload))
    prepared["guard"].check_derived = display
    _rebind_contract(prepared)
    with pytest.raises(ValueError, match="literal local_storage"):
        _run(prepared)


@pytest.mark.parametrize("state", ["allowed", "denied", "unresolved", "conditional"])
def test_uncited_batch_wikipedia_evidence_uses_own_policy_not_enclosing_cc0(prepared, state):
    """The extra origin has no selected feature citation but still constrains use."""
    ledger, registry, _ = prepared["guard"]._load()
    first_origin = prepared["rows"][0]["classification_originating_input_hashes"][0]
    binding = ledger["inputs"][first_origin]["sources"]["version1"]
    source_database = Path(binding["database_path"])
    connection = sqlite3.connect(source_database)
    record = SourceRecord.model_validate_json(connection.execute("SELECT record_json FROM source_records WHERE version_id='version1'").fetchone()[0])
    evidence = Evidence(evidence_id="public-note", kind="note", text="Synthetic independently licensed note", source_url="https://ja.wikipedia.org/w/index.php?oldid=9", rights_policy_id="wikipedia-screened-prose")
    record.evidence.append(evidence)
    connection.execute("UPDATE source_records SET record_json=? WHERE version_id='version1'", (record.model_dump_json(),))
    connection.commit();connection.close()
    fresh_binding = source_binding(source_database, "version1")
    ledger["inputs"][first_origin]["sources"]["version1"] = fresh_binding
    extra_work = WorkInput(work_id="synthetic-extra-batch-work", title=record.titles[0].value, evidence=[evidence], source_record_ids=["version1"])
    extra_origin = model_hash(extra_work)
    ledger["inputs"][extra_origin] = build_input_entry(extra_work, {"version1": fresh_binding})
    registry.policies["wikipedia-screened-prose"].usages["local_storage"] = state
    if state == "allowed":
        checks, metadata_n, evidence_n = sp._literal_local_policy([first_origin, extra_origin], prepared["guard"], ledger, registry, {})
        assert metadata_n == 2 and evidence_n == 1
        assert {entry["origin"] for entry in checks} == {first_origin, extra_origin}
    else:
        with pytest.raises(ValueError, match="Evidence requires its own literal local_storage"):
            sp._literal_local_policy([first_origin, extra_origin], prepared["guard"], ledger, registry, {})


def _replace_public_revision_text(fixture, text):
    page_doc = json.loads(fixture["wp"].read_bytes())
    page_doc["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"] = text
    raw = _write(fixture["wp"], page_doc)
    source = fixture["binding"]["public_article_source"]
    source["raw_sha256"] = raw["sha256"]
    source["revision_text_sha256"] = hb.sha256(text.encode())
    source["source_receipt_sha256"] = _write(Path(source["source_receipt_path"]), raw)["sha256"]
    fixture["bindingspath"].write_text(json.dumps(fixture["binding"]) + "\n", encoding="utf-8")
    fixture["contract"]["artifacts"]["serialization_bindings"]["sha256"] = hb.sha256(fixture["bindingspath"].read_bytes())
    fixture["rows"][0]["public_subject"]["raw_sha256"] = raw["sha256"]
    _rebind_contract(fixture)


@pytest.mark.parametrize("prepared", ["v3"], indirect=True)
@pytest.mark.parametrize("external", [
    "== 外部リンク ==\n=== ライトノベル ===\n* {{小説家になろう作品|n0000aa}}\n=== コミック ===\n* comic link",
    "== 外部リンク ==\n{{dl2\n| 原作 |\n* {{小説家になろう作品|N0000AA}}\n| 漫画 |\n* comic link\n}}",
])
def test_reviewed_child_heading_and_original_dl2_join_without_version_feature_transfer(prepared, external):
    assert prepared["profile_module"] is sp3
    _replace_public_revision_text(prepared, "小説家になろうで連載されたオンライン小説を改稿して書籍化した。\n" + external)
    result = _run(prepared)
    assert result["summary"]["bound_reference_n"] == 1
    assert result["private_rows"][0]["features"] == {"genre.fantasy": 1.}
    assert result["summary"]["canonical_merges"] == 0


@pytest.mark.parametrize("prepared", ["v3"], indirect=True)
@pytest.mark.parametrize("external", [
    "== 外部リンク ==\n=== コミック ===\n* {{小説家になろう作品|n0000aa}}",
    "== 外部リンク ==\n=== TVアニメ ===\n* {{小説家になろう作品|n0000aa}}",
    "== 外部リンク ==\n{{dl2|漫画|{{小説家になろう作品|n0000aa}}}}",
    "== 外部リンク ==\n{{unreviewed|原作|{{小説家になろう作品|n0000aa}}}}",
    "== 外部リンク ==\n{{dl2|原作|{{unreviewed|{{小説家になろう作品|n0000aa}}}}}}",
    "== その他 ==\n=== ライトノベル ===\n{{小説家になろう作品|n0000aa}}",
    "== 外部リンク ==\n=== ライトノベル ===\n{{小説家になろう作品|n0001aa}}",
    "== 外部リンク ==\n{{小説家になろう作品|n0000aa}}\n=== ライトノベル ===\n{{小説家になろう作品|n0000aa}}",
])
def test_other_media_wrappers_wrong_subject_and_ambiguous_links_stay_rejected(prepared, external):
    assert prepared["profile_module"] is sp3
    _replace_public_revision_text(prepared, "小説家になろうで連載されたオンライン小説である。\n" + external)
    with pytest.raises(ValueError, match="serialization template/context"):
        _run(prepared)


@pytest.mark.parametrize("prepared", ["v3"], indirect=True)
@pytest.mark.parametrize("replacement", ["contract", "policy", "recipe"])
def test_v3_rejects_old_contract_policy_and_verifier_identity(prepared, replacement):
    if replacement == "contract":
        prepared["contract"].update(version=sp.CONTRACT_VERSION, purpose=sp.PROFILE_PURPOSE)
    elif replacement == "policy":
        descriptor = prepared["contract"]["artifacts"]["profile_policy"]
        prepared["contract"]["artifacts"]["profile_policy"] = _write(Path(descriptor["path"]), sp.PROFILE_POLICY)
    else:
        path = Path(sp.__file__).resolve()
        prepared["contract"]["artifacts"]["profile_recipe"] = {"path": str(path), "sha256": hb.sha256(path.read_bytes())}
    _rebind_contract(prepared)
    with pytest.raises(ValueError, match="contract|policy/spec|recipe path"):
        _run(prepared)
