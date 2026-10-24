"""Same canonical content admission, with source-genre-independent display scope."""
import ast
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

from recommend_book import scale20k_native_v1 as state

ROOT = Path(__file__).resolve().parents[1]
SOURCE_RECIPE_SHA = '62e71beb20f46a216eff73c26cb3d8986103ea785ec62f84d2dcc062a5ea4da9'
POLICY = 'novel_content_with_display_permission_source_genre_optional'


def load_recipe():
    path = ROOT / 'scripts/prepare_scale_content_sources_v1.py'
    if state.sha(path) != SOURCE_RECIPE_SHA:
        raise ValueError('Fixed canonical content source recipe changed')
    tree, count = ast.parse(path.read_bytes()), 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == 'SELECT qid FROM works WHERE display_allowed=1 AND recommendable=1':
            node.value = 'SELECT qid FROM works WHERE display_allowed=1'
            count += 1
    if count != 1:
        raise ValueError('One exact source eligibility SQL required')
    module = ModuleType('canonical_display_eligible_novel_source')
    module.__file__ = str(Path(__file__).resolve())
    exec(compile(ast.fix_missing_locations(tree), module.__file__, 'exec'), module.__dict__)
    return module


def prepare_native_window(records_path, catalogue, output, official_conditions, **kwargs):
    from run_scale20k_native_v2 import catalogue_snapshot, require_new_display_works, genre_counts
    from recommend_book.models import SourceRecord
    records = [SourceRecord.model_validate_json(line) for line in Path(records_path).read_bytes().splitlines() if line.strip()]
    qids = [record.source_id for record in records]
    if not 1 <= len(qids) <= 100 or len(set(qids)) != len(qids):
        raise ValueError('One distinct actual selected source cohort required')
    before = catalogue_snapshot(catalogue, qids)
    require_new_display_works(before)
    code_sha = state.sha(__file__)
    observer = ROOT / 'scripts/run_scale20k_native_v2.py'
    observer_sha = state.sha(observer)
    result = load_recipe().prepare_native_window(records_path, catalogue, output, official_conditions, **kwargs)
    after = catalogue_snapshot(catalogue, qids)
    if before != after or state.sha(__file__) != code_sha or state.sha(observer) != observer_sha:
        raise ValueError('Source catalogue metadata or source entry changed during actual admission')
    manifest = Path(output).resolve(strict=True) / 'manifest.json'
    fixed = state.read(manifest)
    if result['required_purpose_admissions_allowed'] is not True or fixed['required_purpose_admissions_allowed'] is not True:
        raise ValueError('Actual canonical purpose admission did not pass')
    policy_path = manifest.parent / 'native-eligibility-policy.json'
    state.save(policy_path, {'version': 'scale20k-source-display-eligibility-v2', 'policy': POLICY,
        'source_builder_path': str(Path(__file__).resolve()), 'source_builder_sha256': code_sha,
        'observer_path': str(observer), 'observer_sha256': observer_sha,
        'canonical_recipe_path': str(ROOT / 'scripts/prepare_scale_content_sources_v1.py'),
        'canonical_recipe_sha256': SOURCE_RECIPE_SHA, 'source_manifest_sha256': state.sha(manifest),
        'catalogue_before': before, 'catalogue_after': after, 'source_genre_counts': genre_counts(before),
        'model_calls': 0, 'metadata_mutations': 0, 'observed_at': datetime.now(timezone.utc).isoformat()})
    return {**result, 'eligibility_policy_path': str(policy_path), 'eligibility_policy_sha256': state.sha(policy_path)}


def main():
    module = load_recipe()
    module.prepare_native_window = prepare_native_window
    module.main()


if __name__ == '__main__':
    main()
