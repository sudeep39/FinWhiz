"""Failure-oriented regressions for financial grounding and retrieval coverage."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest

from finwhiz.answer_validation import validation_issues, coverage_issues
from finwhiz.automotive_margins import answer as margins
from finwhiz.config import AppError
from finwhiz.evidence_selection import pack_sources
from finwhiz.finance import empty_chart
from finwhiz.financial_facts import table_facts, number
from finwhiz.retrieval import retrieve
from finwhiz.research import research
from finwhiz.storage import Storage
from finwhiz.ingestion import ingest
from test_workflows import setup_job

QUESTION = ('How did automotive regulatory credits affect Tesla Q2 automotive gross margin in 2026 versus 2025? '
            'Calculate margins including and excluding credit revenue, keeping reported automotive costs unchanged.')
TABLE = '''(Dollars in millions)
Metric | Three Months Ended June 30 | Three Months Ended June 30 | Six Months Ended June 30 | Six Months Ended June 30
Year | 2026 | 2025 | 2026 | 2025
Total automotive revenues | 200 | 100 | 400 | 300
Total automotive cost of revenues | 150 | 80 | 300 | 240
Automotive regulatory credits | 10 | 5 | 20 | 15
Total revenues | 250 | 125 | 500 | 375
Cost of revenues | 175 | 95 | 350 | 285'''


def source(**updates):
    return dict(id='S1', ticker='TSLA', company='Tesla', report_date='2026-06-30', text=TABLE, **updates)


def response(text, calculations=None):
    return dict(status='answered', answer=text, citation_ids=['S1'], calculations=calculations or [], chart=empty_chart())


def test_fact_identities_distinguish_quarter_ytd_metric_and_units():
    facts = table_facts(source())
    assert {(f.period, f.value) for f in facts if f.metric == 'automotive_revenue'} == {
        ('2026-Q2', 200), ('2025-Q2', 100), ('2026-H1', 400), ('2025-H1', 300)}
    assert all(f.unit == 'millions' and f.row >= 3 and f.column >= 1 for f in facts)
    assert number('١٬٢٣٤٫٥') == 1234.5
    assert number('(2.5e3)') == -2500


@pytest.mark.parametrize('text', [
    'Q2 2026 automotive revenue was 100 million [S1].',
    'Q2 2026 automotive revenue was 400 million [S1].',
    'Q2 2026 automotive revenue was 150 million [S1].',
    'Q2 2026 automotive revenue was 200 billion [S1].',
])
def test_wrong_period_metric_duration_and_scale_are_rejected(text):
    assert validation_issues(response(text), [source()])


def test_correct_multiple_claims_and_exact_scale_conversion():
    assert not validation_issues(response('Q2 2026 automotive revenue was 200 million and costs were 150 million [S1].'), [source()])
    assert not validation_issues(response('Q2 2026 automotive revenue was 0.2 billion [S1].'), [source()])


def test_wrong_period_narrative_reproduction():
    evidence = dict(id='S1', text='Q2 2025 Revenue 100. Costs 80. Q2 2026 Revenue 200. Costs 150.')
    assert validation_issues(response('Q2 2026 revenue was 100 [S1].'), [evidence])


def test_comparison_narrative_cannot_swap_years_in_one_sentence():
    assert validation_issues(response('Q2 2026 automotive revenue was 100 million versus 2025 revenue of 200 million [S1].'), [source()])


def test_unknown_table_layout_does_not_validate_by_number_presence():
    evidence = {**source(), 'text': 'Metric | 2026 | 2025\nRevenue | 200 | 100\nCosts | 150 | 80'}
    assert validation_issues(response('Q2 2026 revenue was 100 million [S1].'), [evidence])


def test_table_unit_is_not_inferred_from_another_source():
    evidence = {**source(), 'text': TABLE.replace('(Dollars in millions)', '')}
    assert validation_issues(response('Q2 2026 automotive revenue was 200 billion [S1].'), [evidence])


def test_chart_period_metric_and_scale_are_checked():
    result = response('See the chart [S1].')
    result['chart'] = dict(type='bar', title='Revenue', unit='millions', points=[
        dict(label='TSLA automotive revenue Q2 2026', value=100, source_ids=['S1'])])
    assert validation_issues(result, [source()])


def test_coverage_checks_every_requested_company_and_period():
    scope = dict(tickers=['TSLA', 'NVDA'], requestedPeriods=['2026-Q2', '2025-Q2'])
    assert any('NVDA' in issue for issue in coverage_issues(response('Revenue [S1].'), [source()], scope))
    assert not coverage_issues(response('Revenue [S1].'), [source()], {**scope, 'tickers': ['TSLA']})
    assert coverage_issues(response('Revenue [S1].'), [source()], dict(tickers=['TSLA'], requestedPeriods=['2024-Q2']))


@pytest.mark.parametrize('values', [[100, 80], [200, 80], [200, 300]])
def test_margin_operands_must_share_metric_company_and_period(values):
    calc = dict(label='TSLA automotive Q2 2026', operation='gross_margin', values=values, source_ids=['S1'], unit='%')
    assert validation_issues(response('See the calculation [S1].', [calc]), [source()])


def test_cross_period_operand_order_is_enforced():
    calc = dict(label='TSLA automotive revenue Q2 2026 versus Q2 2025', operation='percent_change',
                values=[200, 100], source_ids=['S1'], unit='%')
    assert not validation_issues(response('See the calculation [S1].', [calc]), [source()])
    calc['values'] = [100, 200]
    assert validation_issues(response('See the calculation [S1].', [calc]), [source()])


def test_specialized_handler_rejects_unhandled_clauses_and_missing_companies():
    scope = dict(tickers=['TSLA'], requestedPeriods=['2026-Q2', '2025-Q2'])
    assert margins(QUESTION, [source()], scope)
    assert margins(QUESTION + ' Also explain net income and production trends.', [source()], scope) is None
    assert margins(QUESTION, [source()], {**scope, 'tickers': ['TSLA', 'NVDA']}) is None
    other = {**source(), 'id': 'S2', 'ticker': 'ACME', 'company': 'Acme', 'fiscal_year': 2026, 'fiscal_quarter': 2}
    result = margins(QUESTION.replace('Tesla', 'Tesla and Acme'), [source(), other],
                     {**scope, 'tickers': ['TSLA', 'ACME']})
    assert result and len(result['calculations']) == 8


def test_fiscal_year_is_not_assumed_to_be_calendar_year():
    other = {**source(), 'ticker': 'NVDA', 'fiscal_year': 2027, 'fiscal_quarter': 2}
    assert {f.period for f in table_facts(other)} == {'2027-Q2', '2026-Q2', '2027-H1', '2026-H1'}


def rows_for(count):
    return [dict(id=str(i), metadata=dict(ticker='TSLA', report_date=f'{2000+i}-06-30',
                text=f'Revenue growth for period {2000+i}.', chunk_type='text')) for i in range(count)]


def test_more_than_sixteen_groups_are_preserved_or_explicitly_reported(tmp_path):
    rows = rows_for(24)
    required = {('TSLA', r['metadata']['report_date']) for r in rows}
    store = Storage(tmp_path)
    sources, missing = pack_sources(store, rows, required, 10000)
    assert len(sources) == 24 and not missing
    smaller, missing = pack_sources(store, rows, required, 120)
    assert missing and len(smaller) + len(missing) == 24


def test_hybrid_retrieval_does_not_truncate_a_twenty_four_filing_comparison(settings):
    store = Storage(settings.finwhiz_data_dir)
    rows = rows_for(24)
    store.put_json('chunks/job/fixed', rows)
    class Provider:
        config = settings.provider()
        def embed(self, *args, **kwargs):
            return [[1.0]]
        def pinecone(self, path, body):
            return {'matches': []}
    result = retrieve(store, [dict(id='job', status='ready', namespaces={'fixed': 'fixture'})],
                      Provider(), 'Revenue growth', ['TSLA'], [], 'fixed', False)
    assert len(result['sources']) == 24 and not result['missingCoverage']


def test_parent_expansion_is_budgeted_before_selection(tmp_path):
    store = Storage(tmp_path)
    store.put_json('parents/job/doc', [dict(kind='table', text='Revenue | 123\n' * 2000)])
    rows = [dict(id='large', metadata=dict(ticker='TSLA', report_date='2026-06-30',
             text='Revenue | 123', chunk_type='table', corpus_id='job', doc_id='doc', parent_id='doc:0'))]
    required = {('TSLA', '2026-06-30')}
    sources, missing = pack_sources(store, rows, required, 500)
    assert not sources and missing == list(required)
    # Another complete, short passage may cover the same filing; never cut a table.
    rows.append(dict(id='short', metadata=dict(ticker='TSLA', report_date='2026-06-30',
                     text='Revenue was 123.', chunk_type='text')))
    sources, missing = pack_sources(store, rows, required, 500)
    assert not missing and [s['chunkId'] for s in sources] == ['short']


@pytest.mark.parametrize('failure', ['embedding', 'vector', 'partial_vector'])
def test_search_failures_retain_keyword_results_and_successful_groups(settings, failure):
    store = Storage(settings.finwhiz_data_dir)
    rows = rows_for(2)
    store.put_json('chunks/job/fixed', rows)
    class Provider:
        config = settings.provider()
        def embed(self, *args, **kwargs):
            if failure == 'embedding':
                raise AppError('Offline provider failure', 502)
            return [[1.0]]
        def pinecone(self, path, body):
            if failure == 'vector' or body['filter']['report_date']['$eq'] == '2000-06-30':
                raise AppError('Offline vector failure', 504)
            return {'matches': [{'id': '1', 'score': 0.9}]}
        def structured(self, *args, **kwargs):
            pytest.fail('Degraded retrieval must not attempt reranking')
    result = retrieve(store, [dict(id='job', status='ready', namespaces={'fixed': 'fixture'})],
                      Provider(), 'Revenue growth', ['TSLA'], [], 'fixed', True)
    assert result['retrievalDegraded'] and not result['missingCoverage']
    assert {s['chunkId'] for s in result['sources']} == {'0', '1'}


def test_compiled_graph_keeps_concurrent_request_dependencies_isolated(settings, monkeypatch):
    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    def unexpected(*args):
        pytest.fail('Pre-resolved scope must not rebuild the catalog')
    monkeypatch.setattr('finwhiz.research.catalog_documents', unexpected)
    scope = dict(status='resolved', tickers=['ACME'], filingDates=['2025-03-31'],
                 filings=[dict(ticker='ACME', reportDate='2025-03-31')], requestedPeriods=[])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(research, store, jobs.all() if i == 0 else [], provider,
                    'What revenue was reported?', ['ACME'], scope_result=deepcopy(scope)) for i in range(2)]
        results = [f.result() for f in futures]
    assert results[0]['status'] == 'answered' and results[1]['status'] == 'insufficient_evidence'


def test_api_registers_explicit_methods_and_validates_typed_research_input(settings):
    from fastapi.testclient import TestClient
    from finwhiz.api import create_app
    app = create_app(settings, disable_workers=True)
    assert set(app.openapi()['paths']['/api/chat']) == {'post'}
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer baseline-password'}
        assert client.get('/api/chat', headers=headers).status_code == 404
        bad = client.post('/api/chat', headers=headers,
                          json=dict(question='Revenue?', tickers=['TSLA'], rerank='yes'))
        assert bad.status_code == 400 and bad.json()['error'] == 'Rerank must be a boolean.'
