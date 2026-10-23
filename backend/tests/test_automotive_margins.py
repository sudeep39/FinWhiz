"""Offline regression against the recovered, original Tesla filing tables."""
import base64
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from finwhiz.automotive_margins import answer, has_inputs, _quarter_values
from finwhiz.lexical import LexicalIndex
from finwhiz.research import research
from finwhiz.scope import resolve_scope
from finwhiz.fiscal import catalog_documents
from finwhiz.config import AIConfig

QUESTION = ('How did automotive regulatory credits affect Tesla’s Q2 automotive gross margin '
            'in 2026 versus 2025? Calculate margins including and excluding credit revenue, '
            'keeping reported automotive costs unchanged.')


@pytest.fixture(scope='module')
def library():
    bundle = json.loads(gzip.decompress((Path(__file__).resolve().parents[2] /
                         'data/recovery/replit-library-v1.json.gz').read_bytes()))

    class Store:
        def json(self, key, default=None):
            digest = bundle['keys'].get(key)
            return json.loads(base64.b64decode(bundle['objects'][digest])) if digest else default

    return bundle['jobs'][:1], Store()


def test_comparison_uses_local_tables_without_provider_calls(library, monkeypatch):
    jobs, store = library
    config = AIConfig.model_validate(jobs[0]['aiConfig'])
    queries = []

    def load(store, key):
        rows = store.json(key)
        return rows, LexicalIndex(rows).counts

    monkeypatch.setattr('finwhiz.retrieval.CORPORA', SimpleNamespace(load=load))

    class Provider:
        def embed(self, texts, purpose):
            pytest.fail('Complete local calculations must not call embeddings')

        def pinecone(self, path, body):
            queries.append(body['filter']['report_date']['$eq'])
            return {'matches': []}  # Required tables must survive even a dense miss.

        def structured(self, system, payload, schema, model=None):
            pytest.fail('Complete local calculations must not call any LLM')

    provider = Provider()
    provider.config = config
    scope = resolve_scope(QUESTION, catalog_documents(jobs, store), ['TSLA'], [], [])
    assert scope['filingDates'] == ['2025-06-30', '2026-06-30']
    result = research(store, jobs, provider, QUESTION, ['TSLA'], scope_result=scope)
    assert result['status'] == 'answered'
    assert not queries
    tables = [s for s in result['sources'] if has_inputs(s['excerpt'])]
    assert {s['report_date'] for s in tables} == {'2026-06-30'}
    assert result['metrics']['calculationMethod'] == 'automotive-credit-table'
    assert len(result['sources']) == 1
    assert result['metrics']['evidence'] == 1
    assert result['metrics']['retrievedEvidence'] >= 2
    assert set(result['citation_ids']) == {s['id'] for s in result['sources']}
    calculations = result['calculations']
    assert [c['values'] for c in calculations] == [
        [16661, 13795], [16661, 13795, 439],
        [20516, 17053], [20516, 17053, 146],
    ]
    assert [c['result'] for c in calculations] == pytest.approx([
        (16661-13795)/16661*100, (16661-439-13795)/(16661-439)*100,
        (20516-17053)/20516*100, (20516-146-17053)/(20516-146)*100,
    ])


def test_missing_conflicting_or_wrong_period_inputs_do_not_invent_answers(library):
    jobs, store = library
    sources = []
    for i, doc in enumerate(jobs[0]['documents']):
        parents = store.json(f"parents/{jobs[0]['id']}/{doc['id']}", [])
        for parent in parents:
            if has_inputs(parent['text']):
                sources.append(dict(id=f'S{i}', ticker='TSLA', report_date=doc['reportDate'], text=parent['text']))
                break
    # The end-to-end regression above checks the exact real filing rows. These
    # guards check that incomplete requests fall back to grounded generation.
    assert answer(QUESTION, []) is None
    assert answer(QUESTION, sources, {'requestedPeriods': ['2026-Q2']}) is None
    assert answer(QUESTION, sources) is not None
    incomplete = [s for s in sources if s['report_date'] != '2025-06-30']
    assert answer(QUESTION, incomplete) is not None  # Prior year is in the comparative columns.
    assert answer(QUESTION, [s for s in sources if s['report_date'] < '2026-01-01']) is None
    original = next(s for s in sources if s['report_date'] == '2026-06-30')
    conflicting = {**original, 'id': 'conflict', 'text': original['text'].replace('20,516', '20,517')}
    assert answer(QUESTION, sources + [conflicting]) is None
    assert _quarter_values('Total automotive revenues | 123', '2026') is None
