import importlib.util
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location('link_scale_history_v1', Path(__file__).parents[1] / 'scripts/link_scale_history_v1.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def work(qid, title, author='Writer', genre='Q9'):
    return {'qid': qid, 'title': title, 'aliases': [], 'authors': [{'qid': 'Q8', 'name': author}],
            'genres': [{'qid': genre, 'label': 'Genre'}], 'read_status': 'unknown', 'record_kind': 'work'}


def history(code, title, **kwargs):
    return {'ncode': code, 'title': title, 'my_rating': 4, 'record_kind': 'work', **kwargs}


def test_strong_ids_title_author_and_ambiguity_do_not_promote_content_links():
    rows = [work('Q1', 'ID match'), work('Q2', 'Alias match'), work('Q3', 'No author'),
            work('Q4', 'Series volume 1'), work('Q5', 'Duplicate'), work('Q6', 'Duplicate')]
    rows[1]['aliases'] = ['Alias private']
    owners = [history('n0001aa', 'Owner private', provider_work_ids=[{'source': 'wikidata', 'value': 'Q1', 'scope': 'work'}], identity_scope_verified=True),
              history('n0002aa', 'Alias private', author_names=['Writer'], author_identity_verified=True, identity_scope_verified=True),
              history('n0003aa', 'No author'),
              history('n0004aa', 'Series volume 1', author_names=['Writer'], author_identity_verified=True, identity_scope_verified=True),
              history('n0005aa', 'Duplicate', author_names=['Writer'], author_identity_verified=True, identity_scope_verified=True),
              history('n0006aa', 'Missing')]
    result = module.audit_history(owners, rows, [])
    report = result['report']
    assert report['strong_metadata_history_links'] == 2
    assert report['ambiguous_history_rows'] == 3
    assert report['unmatched_history_rows'] == 1
    assert report['scale_content_history_links'] == 0
    assert report['existing_content_history_links'] == 0
    assert report['read_state_changes'] == 0
    assert all(v['scope'] == 'same_work' for v in result['private_links'] if v['kind'] == 'strong_metadata')
    assert 'Owner private' not in json.dumps(result)
    assert 'Alias private' not in json.dumps(result)


def test_serialization_and_unverified_ids_cannot_mark_book_or_series_read():
    rows = [work('Q1', 'Same title')]
    owners = [history('n0001aa', 'Same title', record_kind='serialization',
                      provider_work_ids=[{'source': 'wikidata', 'value': 'Q1', 'scope': 'work'}],
                      identity_scope_verified=True, author_identity_verified=True, author_names=['Writer']),
              history('n0002aa', 'Same title', provider_work_ids=[{'source': 'wikidata', 'value': 'Q1', 'scope': 'work'}])]
    result = module.audit_history(owners, rows, [])
    assert result['report']['strong_metadata_history_links'] == 0
    assert result['report']['ambiguous_history_rows'] == 2
    assert result['read_status_plan'] == []


def test_conflicting_stored_provider_ids_stay_ambiguous_when_one_is_outside_catalogue():
    row = history('n0001aa', 'Same title', identity_scope_verified=True,
                  provider_work_ids=[{'source': 'wikidata', 'scope': 'work', 'value': 'Q1'},
                                     {'source': 'wikidata', 'scope': 'work', 'value': 'Q999'}])
    result = module.audit_history([row], [work('Q1', 'Same title')], [])
    assert result['report']['strong_metadata_history_links'] == 0
    assert result['report']['ambiguous_history_rows'] == 1


def test_existing_content_stays_separate_from_catalogue_and_aggregate_preferences_need_support():
    owner = history('n0001aa', 'Old title', record_kind='serialization')
    existing = [{'external_ids': [{'source': 'narou', 'value': 'n0001aa'}],
                 'features': [{'label_id': 'genre.fantasy'}], 'rating': 4}]
    result = module.audit_history([owner], [work('Q1', 'Old title')], existing)
    assert result['report']['existing_content_history_links'] == 1
    assert result['report']['scale_content_history_links'] == 0
    assert result['report']['strong_metadata_history_links'] == 0
    assert result['genre_preferences'] == []
    assert result['private_links'][0]['kind'] == 'existing_content'


def test_only_three_strong_distinct_works_create_coarse_genre_aggregate_and_reversible_plan():
    rows = [work(f'Q{i}', f'Title {i}') for i in range(1, 4)]
    owners = [history(f'n{i:04}aa', f'Title {i}', provider_work_ids=[{'source': 'wikidata', 'value': f'Q{i}', 'scope': 'work'}],
                      identity_scope_verified=True, read_status='read') for i in range(1, 4)]
    result = module.audit_history(owners, rows, [])
    assert result['genre_preferences'] == [{'genre_qid': 'Q9', 'support': 3, 'mean_centered_rating': 0.25,
                                           'basis': 'source_declared_metadata', 'calibrated': False}]
    assert all(v['before'] == 'unknown' and v['after'] == 'read' for v in result['read_status_plan'])
    assert result['report']['read_state_changes'] == 0


def test_normalization_retains_volume_subtitle_and_punctuation_distinctions():
    assert module.exact_text('Ａ  Book') == module.exact_text('A Book')
    assert module.exact_text('Book 1') != module.exact_text('Book')
    assert module.exact_text('Book: subtitle') != module.exact_text('Book')


def test_cli_writes_only_aggregate_report_preserves_originals_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    import pytest
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    owners = [history('n0001aa', 'Private unique title', record_kind='serialization')]
    roster = tmp_path / 'owned.jsonl'
    roster.write_text(json.dumps(owners[0]), encoding='utf-8')
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'private_rows': []}), encoding='utf-8')
    before = roster.read_bytes()
    class Backend:
        def work_detail(self, qid):
            return work(qid, 'Private unique title')
    monkeypatch.setattr(module, 'load_catalogue', lambda _: ([work('Q1', 'Private unique title')], Backend()))
    output = tmp_path / 'data' / 'scale-v1' / 'history'
    args = ['--database', str(tmp_path / 'catalogue.sqlite'), '--output', str(output),
            '--history', str(roster), '--existing-profile', str(profile), '--expected-history-count', '1']
    module.main(args)
    assert roster.read_bytes() == before
    for path in output.iterdir():
        text = path.read_text(encoding='utf-8')
        assert 'Private unique title' not in text and 'n0001aa' not in text and 'my_rating' not in text
    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
    assert report['ambiguous_history_rows'] == 1 and report['read_state_changes'] == 0
    assert 'Private unique title' not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        module.main(args)
