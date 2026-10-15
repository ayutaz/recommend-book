import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book.classification.specs import SPEC_NAMES, get_spec
from recommend_book.rights import RightsRegistry
from recommend_book.usage_guard import UsageDenied, UsageGuard, model_hash

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_french_runtime_builder', ROOT / 'scripts/prepare_native_french_runtime.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_runtime_patches_only_accept_exact_frozen_bases_and_exactly_once_anchors():
    for relative, expected in builder.BASE_HASHES.items():
        original = (ROOT / relative).read_bytes()
        patched, count = builder.patch_source(relative, original)
        assert builder.sha(original) == expected and patched != original
        assert count == (8 if relative.endswith('specs.py') else 1)
        assert (ROOT / relative).read_bytes() == original
        with pytest.raises(ValueError, match='SHA'):
            builder.patch_source(relative, original + b'\n')
    for value in ('none','anchor anchor'):
        with pytest.raises(ValueError, match='exactly once'):
            builder.replace_once(value,'anchor','replacement')


def test_original_specs_are_not_extended_or_rewritten():
    assert SPEC_NAMES == ('guarded-v3-baseline','definitions-v3','context-v3','native-definitions-v3','native-context-v3')
    with pytest.raises(ValueError, match='Unknown'):
        get_spec('native-french-genre-v1')
    assert builder.sha((ROOT / 'src/recommend_book/classification/specs.py').read_bytes()) == builder.BASE_HASHES['src/recommend_book/classification/specs.py']


def test_unregistered_or_foreign_native_recipe_fails_closed_without_cli(tmp_path):
    registry = RightsRegistry.default()
    ledger = {'version':'usage-ledger-v3','registry_sha256':model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
              'execution_recipes':{}}
    path = tmp_path / 'ledger.json'
    path.write_text(json.dumps(ledger),encoding='utf8')
    with pytest.raises(UsageDenied):
        UsageGuard(path).assert_execution_allowed('0'*64)
    ledger['execution_recipes']['0'*64] = {'verifier_path':str(tmp_path / 'fake.py'),'verifier_sha256':'0'*64}
    path.write_text(json.dumps(ledger),encoding='utf8')
    with pytest.raises(UsageDenied, match='verifier'):
        UsageGuard(path).assert_execution_allowed('0'*64)
