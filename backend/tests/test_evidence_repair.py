"""Offline regression for Tesla energy-margin question and bounded repair."""
import pytest
from finwhiz.answer_validation import validation_issues
from finwhiz.finance import empty_chart
from finwhiz.research import research
from finwhiz.schemas import Answer, Repair
from test_workflows import setup_job
from finwhiz.ingestion import ingest


def answer(text):
    return dict(status='answered', answer=text, citation_ids=['S1'],
                chart=empty_chart(), calculations=[])


@pytest.mark.parametrize('text,valid', [
    ('Revenue was $3.139 billion [S1].', True),
    ('Revenue was $3.14 billion [S1].', False),
    ('Revenue was $3.139 million [S1].', False),
    ('Gross margin was 20.4% [S1].', True),
    ('Gross margin fell 9.9 percentage points [S1].', False),
])
def test_exact_scale_conversion_without_derived_or_rounded_claims(text, valid):
    sources = [dict(id='S1', text='(in millions)\nEnergy revenue | 3,139\nGross margin | 20.4% | 30.3%')]
    assert (not validation_issues(answer(text), sources)) == valid


def test_tesla_question_repairs_with_previous_answer_and_specific_errors(settings, monkeypatch):
    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    evidence = [dict(id='S1', ticker='TSLA', report_date='2026-06-30', text=(
        'Tesla | Period ended 2026-06-30\n'
        'Energy revenue increased $350 million, or 13%. Cost increased $556 million, or 29%. '
        'Gross margin declined from 30.3% to 20.4%. Average cost per MWh increased '
        'due to sales mix and unfavorable warranty adjustments.'))]
    monkeypatch.setattr('finwhiz.research.retrieve', lambda *a, **kw: dict(sources=evidence))
    monkeypatch.setattr('finwhiz.research.catalog_documents', lambda *a: [dict(
        id='tesla', ticker='TSLA', company='Tesla', reportDate='2026-06-30',
        fiscal_year=2026, fiscal_quarter=2, form='10-Q', period_type='quarterly')])
    question = 'Why did Tesla’s energy generation and storage gross margin decline in Q2 2026 despite higher revenue?'
    calls = []
    def structured(system, payload, schema, model=None):
        calls.append(schema)
        if schema is Answer:
            return answer('Margin fell 9.9 percentage points [S1].')
        assert schema is Repair
        assert '9.9' in payload['previousAnswer']['answer']
        assert any('9.9' in issue for issue in payload['validationErrors'])
        return dict(sentences=[dict(text=(
            'Gross margin fell from 30.3% to 20.4% as costs rose faster than revenue, '
            'with higher average cost per MWh driven by sales mix and unfavorable warranty adjustments.'
        ), source_ids=['S1'])], calculations=[])
    provider.structured = structured
    result = research(store, jobs.all(), provider, question, ['TSLA'])
    assert result['status'] == 'answered' and 'warranty' in result['answer']
    assert calls == [Answer, Repair]


def test_repeatedly_unsupported_repair_returns_evidence_limitation(settings):
    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    calls = []
    def unsupported(system, payload, schema, model=None):
        calls.append(schema)
        if schema is Answer:
            return answer('Revenue was 999 million [S1].')
        return dict(sentences=[dict(text='Revenue was 999 million.', source_ids=['S1'])], calculations=[])
    provider.structured = unsupported
    result = research(store, jobs.all(), provider, 'What revenue was reported?', ['ACME'], rerank=False)
    assert result['status'] == 'insufficient_evidence'
    assert '999' not in result['answer'] and result['calculations'] == []
    assert calls.count(Repair) == 1
