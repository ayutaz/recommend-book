"""The revoked full view cannot inherit content features from its prior delivery."""
import importlib.util
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[1] / 'scripts/build_world_pilot_withdrawal_v1.py'
spec = importlib.util.spec_from_file_location('world_withdrawal', path)
withdrawal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(withdrawal)


def test_new_metadata_candidates_cannot_carry_native_or_old_ranking_features():
    metadata = {'work_id': 'public:a', 'title': 'A', 'authors': [], 'features': {},
                'feature_provenance': {}, 'display_title_aliases': [], 'read_status': 'unknown',
                'score': 0.9, 'content_labels': ['genre.fantasy'], 'explanations': [{'secret': 'old'}]}
    candidate = withdrawal.metadata_candidate(metadata)
    assert candidate['features'] == candidate['feature_provenance'] == {}
    assert 'score' not in candidate and 'content_labels' not in candidate and 'explanations' not in candidate
    metadata['features'] = {'genre.fantasy': 1.0}
    with pytest.raises(ValueError, match='Metadata loader returned content features'):
        withdrawal.metadata_candidate(metadata)


def test_source_mutation_refuses_publication_even_when_size_unchanged(tmp_path):
    source = tmp_path / 'source.bin'
    source.write_bytes(b'original')
    pins = {str(source): withdrawal.sha(source)}
    withdrawal.check_unchanged(pins)
    source.write_bytes(b'modified')
    with pytest.raises(ValueError, match='Withdrawal source changed'):
        withdrawal.check_unchanged(pins)
