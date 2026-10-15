from pathlib import Path

from recommend_book.classification.runner import build_run_config
from recommend_book.classification.validation import load_taxonomy


def test_guarded_run_config_binds_paragraph_selection_and_screening_code():
    config = build_run_config('gpt-6.1-sol', 'codex-cli test', load_taxonomy(), 5,
                              execution_spec='context-v3')
    for dependency in ('src/recommend_book/wikipedia_paragraphs.py',
                       'src/recommend_book/wikipedia_prose.py',
                       'src/recommend_book/autonomous_enrichment.py',
                       'src/recommend_book/ingestion/wikipedia.py'):
        assert dependency in config['dependency_sha256']
        assert len(config['dependency_sha256'][dependency]) == 64
