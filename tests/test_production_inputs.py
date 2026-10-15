"""Synthetic scheduling fixtures are not annotation gold."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book.models import WorkInput

spec = importlib.util.spec_from_file_location('prepare_production_inputs', Path(__file__).resolve().parents[1] / 'scripts/prepare_production_inputs.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture_inputs():
    return [WorkInput(work_id=f'{language}-{i}', title=f'Title {language} {i}', original_language=language)
            for language, count in [('ja', 14), ('en', 14), ('fr', 6), ('de', 6)] for i in range(count)]


def test_stage_preserves_all_priority_work_and_exact_language_counts():
    inputs = fixture_inputs()
    priorities = {'ja-13', 'en-13', 'fr-5', 'de-5'}
    ordered, quotas = module.order_inputs(inputs, priorities, stage_size=20)
    assert len(ordered) == 40 and len({w.work_id for w in ordered}) == 40
    assert priorities <= {w.work_id for w in ordered[:20]}
    assert quotas == {'ja': 7, 'en': 7, 'other': 6}
    assert sum(w.original_language == 'ja' for w in ordered[:20]) == 7
    assert sum(w.original_language == 'en' for w in ordered[:20]) == 7
    assert {w.original_language for w in ordered[:20]} == {'ja', 'en', 'fr', 'de'}
    original = {w.work_id: w for w in inputs}
    assert all(w == original[w.work_id] for w in ordered)
    assert [w.work_id for w in ordered] == [w.work_id for w in module.order_inputs(list(reversed(inputs)), priorities, stage_size=20)[0]]


def test_impossible_priority_quota_is_rejected_without_shrinking():
    with pytest.raises(ValueError, match='priority.*quota'):
        module.order_inputs(fixture_inputs(), {f'ja-{i}' for i in range(8)}, stage_size=20)
    with pytest.raises(ValueError, match='unknown priority'):
        module.order_inputs(fixture_inputs(), {'missing'}, stage_size=20)


def test_review_cannot_replace_frozen_work_or_forge_pack_sha(tmp_path):
    work = fixture_inputs()[0]
    pack = {'works': [work.model_dump(mode='json')], 'predictions': [], 'label_ids': [], 'partitions': {}, 'group_ids': {}}
    pack['pack_sha256'] = hashlib.sha256(json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    path = tmp_path / 'review_pack.json'
    path.write_text(json.dumps(pack), encoding='utf-8')
    assert module.review_ids(path, {work.work_id: work}) == {work.work_id}
    pack['works'][0]['title'] = 'Different input'
    path.write_text(json.dumps(pack), encoding='utf-8')
    with pytest.raises(ValueError, match='pack SHA'):
        module.review_ids(path, {work.work_id: work})
    del pack['pack_sha256']
    pack['pack_sha256'] = hashlib.sha256(json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    path.write_text(json.dumps(pack), encoding='utf-8')
    with pytest.raises(ValueError, match='frozen input'):
        module.review_ids(path, {work.work_id: work})


def test_context_drift_is_rejected(tmp_path):
    frozen = tmp_path / 'frozen'; frozen.mkdir()
    repo = tmp_path / 'repo'; repo.mkdir()
    content = b'original'
    (repo / 'prompt.py').write_bytes(content)
    (frozen / 'prompt.py').write_bytes(content)
    (frozen / 'processing-context.json').write_text(json.dumps({'prompt.py': {'snapshot': 'prompt.py', 'sha256': hashlib.sha256(content).hexdigest()}}))
    module.verify_context(frozen, repo)
    (repo / 'prompt.py').write_bytes(b'changed')
    with pytest.raises(ValueError, match='context drift'):
        module.verify_context(frozen, repo)


def test_prepare_preserves_raw_rows_and_refuses_overwrite_or_bad_source(tmp_path):
    frozen = tmp_path / 'frozen'; frozen.mkdir()
    repo = Path(__file__).resolve().parents[1]
    relative = 'src/recommend_book/classification/prompt.py'
    context_bytes = (repo / relative).read_bytes()
    (frozen / 'prompt.py').write_bytes(context_bytes)
    (frozen / 'processing-context.json').write_text(json.dumps({relative: {'snapshot': 'prompt.py', 'sha256': hashlib.sha256(context_bytes).hexdigest()}}))
    works = fixture_inputs()
    rows = [json.dumps(w.model_dump(mode='json'), ensure_ascii=False).encode() for w in works]
    raw = b'\n'.join(rows) + b'\n'
    pilot_raw = rows[-1] + b'\n'
    (frozen / 'work-inputs.jsonl').write_bytes(raw)
    (frozen / 'pilot100.jsonl').write_bytes(pilot_raw)
    (frozen / 'manifest.json').write_text(json.dumps({'work_inputs_sha256': hashlib.sha256(raw).hexdigest(), 'pilot_inputs_sha256': hashlib.sha256(pilot_raw).hexdigest(), 'work_count': 40, 'pilot_count': 1}))
    payload = {'works': [works[0].model_dump(mode='json')], 'predictions': [], 'label_ids': [], 'partitions': {'ja-0': 'holdout'}, 'group_ids': {}}
    payload['pack_sha256'] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    pack = tmp_path / 'review.json'; pack.write_text(json.dumps(payload))
    before = {p: p.read_bytes() for p in [*frozen.iterdir(), pack]}
    output = tmp_path / 'prepared'
    report = module.prepare(frozen, pack, output, stage_size=20)
    actual_rows = (output / 'ordered-inputs.jsonl').read_bytes().splitlines()
    assert sorted(actual_rows) == sorted(rows)
    assert {'ja-0', 'de-5'} <= {json.loads(row)['work_id'] for row in actual_rows[:20]}
    assert report['all_priority_in_stage_one'] and report['human_annotations_created'] == 0
    assert all(p.read_bytes() == data for p, data in before.items())
    with pytest.raises(ValueError, match='must be new'):
        module.prepare(frozen, pack, output, stage_size=20)
    (frozen / 'work-inputs.jsonl').write_bytes(raw + b' ')
    rejected = tmp_path / 'rejected'
    with pytest.raises(ValueError, match='inputs SHA'):
        module.prepare(frozen, pack, rejected, stage_size=20)
    assert not rejected.exists()
