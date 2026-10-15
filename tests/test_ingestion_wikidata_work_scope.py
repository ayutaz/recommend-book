import httpx
import pytest

from recommend_book.ingestion import HTTPFetcher, RawStore
from recommend_book.ingestion.wikidata import WikidataAdapter


def claim(qid, rank='normal'):
    return {'rank': rank, 'mainsnak': {'datavalue': {'value': {'id': qid}}}}


def adapter(tmp_path, entity):
    fetcher = HTTPFetcher(RawStore(tmp_path), min_interval=0,
                          client=httpx.Client(transport=httpx.MockTransport(
                              lambda request: httpx.Response(200, json={'entities': {entity['id']: entity}}))))
    return WikidataAdapter(fetcher)


@pytest.mark.parametrize('claims', [
    {'P31': [claim('Q3331189')], 'P7937': [claim('Q49084')]},
    {'P31': [claim('Q8261', 'preferred'), claim('Q3331189')]},
    {'P31': [claim('Q8261')], 'P629': [claim('Q200')]},
])
def test_explicit_edition_assertion_is_held_in_work_fetch_with_raw_proof(tmp_path, claims):
    result = adapter(tmp_path, {'id': 'Q100', 'claims': claims}).fetch_entities(['Q100'], kind='work')
    assert result.records == []
    assert result.status == 'failed'
    hold = result.metadata['scope_holds'][0]
    assert hold['source_id'] == 'Q100'
    assert hold['requested_kind'] == 'work'
    assert hold['raw_path'] and len(hold['raw_sha256']) == 64
    assert hold['assertion_locators']


def test_deprecated_edition_claim_does_not_block_valid_work_or_edition_fetch(tmp_path):
    entity = {'id': 'Q100', 'claims': {'P31': [claim('Q3331189', 'deprecated'), claim('Q8261')]}}
    assert adapter(tmp_path / 'work', entity).fetch_entities(['Q100'], kind='work').records[0].record_kind == 'work'
    edition = {'id': 'Q100', 'claims': {'P31': [claim('Q3331189')], 'P629': [claim('Q200')]}}
    assert adapter(tmp_path / 'edition', edition).fetch_entities(['Q100'], kind='edition').records[0].record_kind == 'edition'


def test_discovery_excludes_explicit_edition_class_even_without_p629(tmp_path):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'results': {'bindings': []}})
    fetcher = HTTPFetcher(RawStore(tmp_path), min_interval=0,
                          client=httpx.Client(transport=httpx.MockTransport(respond)))
    WikidataAdapter(fetcher).query_work_ids(limit=10)
    query = requests[0].url.params['query']
    assert 'FILTER NOT EXISTS { ?work wdt:P31 wd:Q3331189.' in query
