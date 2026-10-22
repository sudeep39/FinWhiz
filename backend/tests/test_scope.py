"""Scope resolution must reject ambiguity before any provider call."""
import pytest
from fastapi.testclient import TestClient
from finwhiz.api import create_app
from finwhiz.fiscal import fiscal_metadata, catalog_documents
from finwhiz.scope import resolve_scope
from finwhiz.storage import Storage, Jobs
from finwhiz.ingestion import ingest
from finwhiz.research import research
from test_workflows import setup_job


def document(ticker='TSLA', day='2026-06-30', year=2026, quarter=2, **extra):
    return dict(id=ticker + day, ticker=ticker, company='Tesla' if ticker == 'TSLA' else 'NVIDIA',
                reportDate=day, fiscal_year=year, fiscal_quarter=quarter,
                form='10-K' if quarter == 4 else '10-Q',
                period_type='annual' if quarter == 4 else 'quarterly', **extra)


@pytest.mark.parametrize('question', [
    'Why did Tesla energy gross margin decline in Q2 2026?',
    'Using Tesla Q2 2026 filing, what were Q1 2026 revenue and common stockholder income, and how did Q2 compare?',
    'Show Tesla results in the second quarter of 2026.',
    'Show Tesla results for the first half of 2026.',
    'Show Tesla results for the quarter ended June 30, 2026.',
    'Show Tesla Q2 FY2026 revenue.',
    'Show Tesla 2026-Q2 revenue.',
])
def test_tesla_questions_target_q2_filing(question):
    result = resolve_scope(question, [document(), document(day='2025-06-30', year=2025)], ['TSLA'])
    assert result['status'] == 'resolved' and result['filingDates'] == ['2026-06-30']


def test_company_and_period_conflicts():
    docs = [document(), document('NVDA', '2026-07-26', quarter=2), document(day='2025-06-30', year=2025)]
    assert resolve_scope('Tesla Q2 2026', docs, ['NVDA'])['status'] == 'needs_clarification'
    assert resolve_scope('Tesla Q2 2026', docs, ['TSLA'], ['2025-06-30'])['status'] == 'needs_clarification'
    assert resolve_scope('Tesla Q2', docs, ['TSLA'])['status'] == 'needs_clarification'
    assert resolve_scope('Tesla Q2 2027', docs, ['TSLA'])['status'] == 'insufficient_evidence'
    assert resolve_scope('What about next quarter?', docs, ['TSLA'])['status'] == 'needs_clarification'
    narrowed = resolve_scope('Tesla Q2', docs, ['TSLA'], ['2026-06-30'])
    assert narrowed['status'] == 'resolved' and narrowed['requestedPeriods'] == ['2026-Q2']


def test_fiscal_calendar_and_year_pairings_are_not_calendar_guesses():
    docs = [document('NVDA', '2025-07-27', year=2026), document('NVDA', '2024-04-28', year=2025, quarter=1)]
    result = resolve_scope('NVIDIA Q2 2026 versus Q1 2025', docs, ['NVDA'])
    assert result['status'] == 'resolved' and result['filingDates'] == ['2024-04-28', '2025-07-27']
    unknown = dict(id='x', ticker='TSLA', company='Tesla', reportDate='2026-06-30', form='10-Q')
    assert resolve_scope('Tesla Q2 2026', [unknown], ['TSLA'])['status'] == 'needs_clarification'
    assert resolve_scope('Tesla 2026-06-30', [unknown], ['TSLA'])['status'] == 'resolved'
    both_years = resolve_scope('Tesla Q2 2026 versus 2025', [document(), document(day='2025-06-30', year=2025)], ['TSLA'])
    assert both_years['status'] == 'resolved' and len(both_years['filingDates']) == 2


def test_q4_and_second_half_require_ytd_inputs():
    annual = document(day='2026-12-31', quarter=4)
    assert resolve_scope('Tesla Q4 2026', [annual], ['TSLA'])['status'] == 'insufficient_evidence'
    q3 = document(day='2026-09-30', quarter=3)
    result = resolve_scope('Tesla Q4 2026', [annual, q3], ['TSLA'])
    assert result['status'] == 'resolved' and len(result['filings']) == 2
    assert resolve_scope('Tesla second half 2026', [annual, document()], ['TSLA'])['status'] == 'resolved'


def test_q2_source_selection_can_derive_q1_even_when_q1_filing_exists():
    docs = [document(), document(day='2026-03-31', quarter=1)]
    question = 'Using Tesla Q2 2026 filing, what were Q1 2026 revenue and common stockholder income, and how did Q2 compare?'
    for periods in ([], ['2026-06-30']):
        result = resolve_scope(question, docs, ['TSLA'], periods)
        assert result['status'] == 'resolved' and result['filingDates'] == ['2026-06-30']


def test_followup_uses_previous_resolved_company_period_and_detects_conflicts():
    docs = [document(), document(day='2025-06-30', year=2025)]
    prior = resolve_scope('Tesla Q2', docs, ['TSLA'], ['2026-06-30'])
    history = [dict(role='user', content='Tesla Q2', scope=prior)]
    result = resolve_scope('Why did it change that quarter?', docs, ['TSLA'], history=history)
    assert result['status'] == 'resolved' and result['filingDates'] == ['2026-06-30']


def test_preflight_and_chat_ambiguity_do_not_construct_provider(settings):
    store = Storage(settings.finwhiz_data_dir)
    jobs = Jobs(store)
    jobs.add(dict(id='tesla', status='ready', files=[], provider='openai', strategies=['fixed'],
                  namespaces={'fixed': 'fixture'}, documents=[document(), document(day='2025-06-30', year=2025)]))
    def forbidden(*args):
        raise AssertionError('Ambiguous scope must not create a provider')
    with TestClient(create_app(settings, disable_workers=True, provider_factory=forbidden)) as client:
        headers = {'Authorization': 'Bearer baseline-password'}
        request = dict(question='Tesla Q2', tickers=['TSLA'])
        assert client.post('/api/scope', headers=headers, json=request).json()['status'] == 'needs_clarification'
        result = client.post('/api/chat', headers=headers, json=request)
        assert result.status_code == 200 and result.json()['status'] == 'needs_clarification'


def test_fiscal_dei_facts_are_enriched_without_calendar_inference():
    html = '<ix:nonNumeric name="dei:DocumentFiscalYearFocus">2026</ix:nonNumeric><ix:nonNumeric name="dei:DocumentFiscalPeriodFocus">Q2</ix:nonNumeric>'
    assert fiscal_metadata(html)['fiscal_quarter'] == 2
    assert fiscal_metadata(html)['fiscal_year'] == 2026
    assert fiscal_metadata('Report ended June 30, 2026') == {}


def test_both_retrieval_methods_and_model_receive_resolved_scope(settings):
    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    original = provider.structured
    payloads = []
    def structured(system, payload, schema, model=None):
        if schema.__name__ == 'Answer':
            payloads.append(payload)
        return original(system, payload, schema, model)
    provider.structured = structured
    result = research(store, jobs.all(), provider, 'What revenue was reported on 2025-03-31?', ['ACME'])
    assert result['scope']['filingDates'] == ['2025-03-31']
    assert payloads[0]['resolvedScope']['filings'] == [dict(ticker='ACME', reportDate='2025-03-31')]


def test_keyword_and_vector_scope_preserve_company_date_pairs(settings):
    from finwhiz.retrieval import retrieve
    store = Storage(settings.finwhiz_data_dir)
    rows = [dict(id=str(i), metadata=dict(ticker=ticker, report_date=day,
                  text='Revenue growth', chunk_type='text'))
            for i, (ticker, day) in enumerate([('TSLA', '2026-06-30'), ('TSLA', '2025-06-30'),
                                              ('NVDA', '2026-06-30'), ('NVDA', '2025-06-30')])]
    store.put_json('chunks/job/fixed', rows)
    queries = []
    class Provider:
        config = settings.provider()
        def embed(self, *args, **kwargs):
            return [[1.0]]
        def pinecone(self, path, body):
            queries.append(body['filter'])
            return {'matches': [dict(id=row['id'], score=0.9) for row in rows]}
    result = retrieve(store, [dict(id='job', status='ready', namespaces={'fixed': 'fixture'})],
                      Provider(), 'Revenue', ['TSLA', 'NVDA'], [], 'fixed', False,
                      filing_scope={('TSLA', '2026-06-30'), ('NVDA', '2025-06-30')})
    assert {row['chunkId'] for row in result['ranking']} == {'0', '3'}
    assert len(queries) == 2
    assert {(q['ticker']['$eq'], q['report_date']['$eq']) for q in queries} == {('TSLA', '2026-06-30'), ('NVDA', '2025-06-30')}


def test_metadata_backfill_is_plan_only_and_preserves_ids(settings):
    from finwhiz.fiscal import metadata_backfill_plan
    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    documents = jobs.all()[0]['documents']
    documents[0].update(fiscal_year=2025, fiscal_quarter=1, period_type='quarterly')
    jobs.update(job['id'], documents=documents)
    plan = metadata_backfill_plan(jobs.all(), store)
    assert plan['dryRun'] and not plan['remoteInspected'] and plan['updateCount'] > 0
    original = store.json('chunks/job/fixed')
    assert 'fiscal_year' not in original[0]['metadata']
    assert original[0]['id'] in {update['id'] for update in plan['updates']}


def test_new_ingestion_publishes_verified_fiscal_metadata(settings):
    store, jobs, job, provider = setup_job(settings)
    html = store.get('upload/file').decode('utf-8')
    html += '<ix:nonNumeric name="dei:DocumentFiscalYearFocus">2025</ix:nonNumeric><ix:nonNumeric name="dei:DocumentFiscalPeriodFocus">Q1</ix:nonNumeric>'
    store.put('upload/file', html)
    ingest(job, store, jobs, provider)
    doc = jobs.all()[0]['documents'][0]
    assert doc['fiscal_year'] == 2025 and doc['fiscal_quarter'] == 1
    for records in provider.records.values():
        for row in records.values():
            assert row['metadata']['fiscal_year'] == 2025
            assert row['metadata']['period_type'] == 'quarterly'
