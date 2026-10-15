"""Order unchanged frozen inputs for a balanced 1,000 -> 2,000 work run.

This prepares files only. It never invokes an LLM or fills human annotations.
Review/pilot works are included in stage one without moving review partitions.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
from pathlib import Path

from recommend_book.classification.validation import input_hash
from recommend_book.models import WorkInput


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def coarse(work):
    return work.original_language if work.original_language in {'ja', 'en'} else 'other'


def verify_context(frozen, repo):
    context = read_json(frozen / 'processing-context.json')
    for relative, proof in context.items():
        if sha((frozen / proof['snapshot']).read_bytes()) != proof['sha256']:
            raise ValueError(f'frozen context SHA mismatch: {relative}')
        if sha((repo / relative).read_bytes()) != proof['sha256']:
            raise ValueError(f'context drift: {relative}; new processing version required')
    return context


def review_ids(path, input_index):
    pack = read_json(path)
    payload = {k: v for k, v in pack.items() if k != 'pack_sha256'}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    if sha(encoded) != pack['pack_sha256']:
        raise ValueError('review pack SHA mismatch')
    ids = set()
    for row in pack['works']:
        work = WorkInput.model_validate(row)
        if work.work_id in ids or work.work_id not in input_index or input_hash(work) != input_hash(input_index[work.work_id]):
            raise ValueError('review work differs from frozen input or is duplicated')
        ids.add(work.work_id)
    return ids


def order_inputs(inputs, priority_ids, *, stage_size=1000, seed=1729):
    if not isinstance(stage_size, int) or isinstance(stage_size, bool) or stage_size <= 0 or stage_size > len(inputs):
        raise ValueError('stage size must be positive and within frozen input count')
    index = {w.work_id: w for w in inputs}
    if len(index) != len(inputs) or any(w.expression_id is not None for w in inputs):
        raise ValueError('requires distinct work-level inputs')
    priority_ids = set(priority_ids)
    if not priority_ids <= index.keys():
        raise ValueError('unknown priority work ID')
    quotas = {'ja': round(stage_size * .35), 'en': round(stage_size * .35)}
    quotas['other'] = stage_size - sum(quotas.values())
    order = lambda w: sha(f'{seed}:{w.work_id}'.encode())
    selected = []
    for group, quota in quotas.items():
        priority = [w for w in inputs if w.work_id in priority_ids and coarse(w) == group]
        if len(priority) > quota:
            raise ValueError(f'priority works exceed {group} quota')
        selected.extend(priority)
        pools = defaultdict(list)
        for work in inputs:
            if coarse(work) == group and work.work_id not in priority_ids:
                pools[work.original_language or 'unknown'].append(work)
        queues = {language: deque(sorted(works, key=order)) for language, works in pools.items()}
        languages = sorted(queues, key=lambda language: sha(f'{seed}:language:{language}'.encode()))
        remaining = quota - len(priority)
        while remaining and any(queues.values()):
            for language in languages:
                if queues[language] and remaining:
                    selected.append(queues[language].popleft())
                    remaining -= 1
        if remaining:
            raise ValueError(f'insufficient frozen works for {group} quota')
    selected_ids = {w.work_id for w in selected}
    ordered = sorted(selected, key=order) + sorted((w for w in inputs if w.work_id not in selected_ids), key=order)
    return ordered, quotas


def prepare(frozen, review_pack, output, *, stage_size=1000, seed=1729):
    frozen, review_pack, output = map(Path, (frozen, review_pack, output))
    if output.exists():
        raise ValueError('production input output must be new')
    repo = Path(__file__).resolve().parents[1]
    context = verify_context(frozen, repo)
    manifest = read_json(frozen / 'manifest.json')
    raw = (frozen / 'work-inputs.jsonl').read_bytes()
    if sha(raw) != manifest['work_inputs_sha256']:
        raise ValueError('frozen work inputs SHA mismatch')
    lines = [line for line in raw.splitlines() if line.strip()]
    inputs = [WorkInput.model_validate_json(line) for line in lines]
    if len(inputs) != manifest['work_count']:
        raise ValueError('frozen work count mismatch')
    index = {w.work_id: w for w in inputs}
    review = review_ids(review_pack, index)
    pilot_raw = (frozen / 'pilot100.jsonl').read_bytes()
    if sha(pilot_raw) != manifest['pilot_inputs_sha256']:
        raise ValueError('frozen pilot SHA mismatch')
    pilot = [WorkInput.model_validate_json(line) for line in pilot_raw.splitlines() if line.strip()]
    for work in pilot:
        if work.work_id not in index or input_hash(work) != input_hash(index[work.work_id]):
            raise ValueError('pilot differs from frozen input')
    if len(pilot) != manifest['pilot_count'] or len({w.work_id for w in pilot}) != len(pilot):
        raise ValueError('pilot work count mismatch')
    priorities = review | {w.work_id for w in pilot}
    ordered, quotas = order_inputs(inputs, priorities, stage_size=stage_size, seed=seed)
    raw_index = {work.work_id: line for work, line in zip(inputs, lines)}
    ordered_bytes = b''.join(raw_index[work.work_id] + b'\n' for work in ordered)
    report = {
        'frozen_directory': str(frozen.resolve()), 'source_sha256': sha(raw),
        'review_pack_sha256': read_json(review_pack)['pack_sha256'],
        'pilot_sha256': sha(pilot_raw), 'seed': seed, 'stage_size': stage_size,
        'stage_coarse_language_quotas': quotas,
        'stage_original_languages': dict(Counter(w.original_language or 'unknown' for w in ordered[:stage_size])),
        'work_count': len(ordered), 'priority_work_count': len(priorities),
        'review_work_count': len(review), 'all_priority_in_stage_one': priorities <= {w.work_id for w in ordered[:stage_size]},
        'ordered_inputs_sha256': sha(ordered_bytes), 'processing_context': context,
        'human_quality_verified': False, 'human_annotations_created': 0,
        'definition': 'Operational ordering only; per-work frozen bytes unchanged, review partitions unchanged. Not a new evaluation sample.',
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / 'ordered-inputs.jsonl').write_bytes(ordered_bytes)
    (output / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    parser.add_argument('--review-pack', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stage-size', type=int, default=1000)
    args = parser.parse_args()
    print(json.dumps(prepare(args.frozen, args.review_pack, args.output, stage_size=args.stage_size), ensure_ascii=False, indent=2))
