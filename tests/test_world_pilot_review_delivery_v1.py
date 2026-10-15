"""Boundary checks for independent review coverage and public delivery projection."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def module(name):
    path = Path(__file__).resolve().parents[1] / 'scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


review = module('review_world_pilot_v1')
delivery = module('build_world_pilot_delivery_v1')


def test_review_refuses_missing_label_and_foreign_finding():
    records = [{'work': SimpleNamespace(work_id='public:a'), 'result': {'labels': [
        {'label_id': 'genre.fantasy'}, {'label_id': 'theme.family'}]}}]
    row = {'work_id': 'public:a', 'reviewed_label_ids': ['genre.fantasy'], 'summary': 'examined', 'findings': []}
    with pytest.raises(ValueError, match='every native label'):
        review.validate_reviews({'reviews': [row]}, records)
    row['reviewed_label_ids'].append('theme.family')
    review.validate_reviews({'reviews': [row]}, records)
    row['findings'] = [{'label_ids': ['theme.foreign'], 'reason_code': 'semantic_entailment_uncertain', 'reason': 'Unsupported'}]
    with pytest.raises(ValueError, match='label/reason'):
        review.validate_reviews({'reviews': [row]}, records)


def test_merge_preserves_old_score_and_refuses_duplicate_work():
    old = [{'work_id': 'old', 'score': 0.25, 'extra': 'preserved'}]
    new = [{'work_id': 'new', 'score': 0.5}]
    merged = delivery.merge_rankings(old, new)
    assert merged[1] == old[0]
    assert old[0]['score'] == 0.25
    with pytest.raises(ValueError, match='Duplicate'):
        delivery.merge_rankings(old, old)


def test_public_credit_allowlist_drops_local_paths_and_source_prose():
    ev = SimpleNamespace(evidence_id='public-evidence', source_url='https://en.wikipedia.org/w/index.php?oldid=1',
        license='CC-BY-SA-4.0', license_url='https://creativecommons.org/licenses/by-sa/4.0/',
        attribution={'credit': 'Wikipedia contributors: Public title', 'raw_path': 'C:/private/source.bin',
                     'identity_proof': {'target_raw_path': 'C:/private/target.bin'}, 'text': 'Source prose'})
    records = delivery.source_credits(SimpleNamespace(evidence=[ev]))
    serialized = str(records)
    assert 'C:/private' not in serialized
    assert 'Source prose' not in serialized
    assert records[0]['permalink'] == ev.source_url
    with pytest.raises(ValueError, match='allowlist'):
        delivery.safe_url('https://attacker.invalid/?private=1')


def test_supplements_allow_identical_metadata_but_require_one_completed_owner():
    metadata = {}
    delivery.join_identical_projection(metadata, 'public:a', {'title': 'A', 'features': {}})
    delivery.join_identical_projection(metadata, 'public:a', {'title': 'A', 'features': {}})
    with pytest.raises(ValueError, match='projection differs'):
        delivery.join_identical_projection(metadata, 'public:a', {'title': 'Wrong', 'features': {}})
    owners = {}
    delivery.claim_completed_work(owners, 'public:a', {'database': 'first10.sqlite'})
    with pytest.raises(ValueError, match='multiple adopted supplement owners'):
        delivery.claim_completed_work(owners, 'public:a', {'database': 'rest26.sqlite'})


def test_selected_owner_review_cannot_be_omitted_by_supplying_another_valid_review():
    owners = {'public:a': {'review_sha256': 'first10-review'}, 'public:b': {'review_sha256': 'rest26-review'}}
    with pytest.raises(ValueError, match='owning database bound review'):
        delivery.require_owner_reviews(owners, ['public:a', 'public:b'], {'first10-review'})
    delivery.require_owner_reviews(owners, ['public:a', 'public:b'], {'first10-review', 'rest26-review'})
