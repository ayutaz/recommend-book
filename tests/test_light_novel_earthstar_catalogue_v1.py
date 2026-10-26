from types import SimpleNamespace
import pytest
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.light_novel_site_backend_v1 import UnifiedCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work
from test_light_novel_publisher_references_v1 import earth_fixture, write_json


def test_earthstar_cards_are_independent_references_not_adaptation_or_series_proof(tmp_path):
    path, manifest, _, _ = earth_fixture(tmp_path)
    sha = write_json(path, manifest)
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    ids = ['earthstar-work:esn001']
    result = cat.import_earthstar_publisher_references(path, sha, reference_ids=ids)
    assert result['inserted'] == 1 and result['held'] == result['updated'] == 0
    identity = result['reference_entries'][ids[0]]
    def forbidden(*a, **kw): pytest.fail('publisher reference must not access the world catalogue')
    unified = UnifiedCatalogue(SimpleNamespace(db_path=tmp_path / 'world.sqlite', work_detail=forbidden), cat)
    row = unified.work_detail(identity)
    shown = public_work(row)
    assert row['publisher_reference_kind'] == 'earthstar_work_reference'
    assert row['authors'] == row['members'] == row['isbn'] == []
    assert row['read_status'] == 'unknown'
    assert all(row[k] is False for k in ('light_novel_confirmed', 'light_novel_identity_asserted',
        'edition_identity_asserted', 'story_series_identity_asserted', 'original_work_identity_asserted', 'recommendable'))
    product = shown['publisher_product_reference']
    assert product['label'] is product['form'] is None
    assert product['credit_literals'] == []
    assert product['credits'] == [{'role': 'author', 'name': '著者名'}, {'role': 'illustrator', 'name': '絵の人'}]
    assert shown['publisher_provider'] == 'earthstar'
    assert shown['edition_identity_asserted'] is False
    for query in ('完全な題', '著者名', '絵の人'):
        assert cat.search_catalogue(query, entry_kind='publisher_reference')['total'] == 1
    again = cat.import_earthstar_publisher_references(path, sha, reference_ids=ids)
    assert again['unchanged'] == 1 and again['inserted'] == again['updated'] == 0
    assert again['reference_entries'] == result['reference_entries']


@pytest.mark.parametrize('selection', [None, [], 'earthstar-work:esn001', ['earthstar-work:missing'],
                                       ['earthstar-work:esn001', 'earthstar-work:esn001']])
def test_earthstar_requires_explicit_unique_known_selection(tmp_path, selection):
    path, manifest, _, _ = earth_fixture(tmp_path)
    sha = write_json(path, manifest)
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    with pytest.raises(ValueError):
        cat.import_earthstar_publisher_references(path, sha, reference_ids=selection)
    with cat._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 0


def test_earthstar_source_corruption_cannot_write_a_reference(tmp_path):
    path, manifest, receipt, _ = earth_fixture(tmp_path)
    sha = write_json(path, manifest)
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    (tmp_path / receipt['raw_path']).write_bytes(b'changed raw')
    with pytest.raises(ValueError):
        cat.import_earthstar_publisher_references(path, sha, reference_ids=['earthstar-work:esn001'])
    with cat._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 0
