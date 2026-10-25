import json
import pytest
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work
from test_light_novel_ichijinsha_references_v1 import frame


def test_selected_iris_import_public_search_and_idempotency(frame):
    path, sha, proof, *_ = frame
    cat = LightNovelCatalogue(path.parent / 'catalogue.sqlite')
    ids = proof['reference_ids']
    result = cat.import_ichijinsha_iris_publisher_references(path, sha, reference_ids=ids)
    assert result['inserted'] == 1 and result['updated'] == 0
    entry = result['reference_entries'][ids[0]]
    row = cat.work_detail(entry)
    assert row['entry_kind'] == 'publisher_reference' and row['publisher_reference_kind'] == 'ichijinsha_iris_product_reference'
    assert row['isbn'] == ['9784758047678']
    assert row['authors'] == [] and row['members'] == [] and row['read_status'] == 'unknown'
    assert all(row[k] is False for k in ('light_novel_confirmed', 'light_novel_identity_asserted', 'edition_identity_asserted', 'story_series_identity_asserted', 'original_work_identity_asserted', 'recommendable'))
    public = public_work(row)
    assert public['publisher_reference_kind'] == 'ichijinsha_iris_product_reference'
    assert public['publisher_product_reference']['publisher_reference_id'] == ids[0]
    assert public['publisher_product_reference']['credit_literals'] == [{'role_literal': '著', 'name': '架空 著者'}, {'role_literal': '監修', 'name': '未確認 氏'}]
    assert 'PRIVATE_PROSE' not in json.dumps(public) and str(path.parent) not in json.dumps(public)
    for query in ('完全な題', '架空 著者', '未確認 氏'):
        found = cat.search_catalogue(query, entry_kind='publisher_reference')
        assert found['total'] == 1
    again = cat.import_ichijinsha_iris_publisher_references(path, sha, reference_ids=ids)
    assert again['inserted'] == again['updated'] == 0 and again['unchanged'] == 1
    assert again['reference_entries'] == result['reference_entries']
    assert cat.similar_works(entry)['mode'] == 'insufficient_evidence'


def test_iris_explicit_selection_validation_and_atomic_source_failure(frame):
    path, sha, proof, *_ = frame
    cat = LightNovelCatalogue(path.parent / 'catalogue.sqlite')
    ids = proof['reference_ids']
    with pytest.raises(TypeError): cat.import_ichijinsha_iris_publisher_references(path, sha)
    for selection in (None, [], ids[0], ids * 2, ['publisher-book:wrong']):
        with pytest.raises(ValueError): cat.import_ichijinsha_iris_publisher_references(path, sha, reference_ids=selection)
    (path.parent / 'detail.html').write_bytes(b'changed detail')
    with pytest.raises(ValueError): cat.import_ichijinsha_iris_publisher_references(path, sha, reference_ids=ids)
    with cat._connect() as db:
        assert db.execute('SELECT count(*) FROM entries').fetchone()[0] == 0
