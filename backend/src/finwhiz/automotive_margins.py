"""A calculation handler over reusable source-located financial facts."""
import re
from .finance import calculate, empty_chart
from .financial_facts import complete_tables, table_facts

LABELS = (r'total automotive revenues?', r'total automotive cost of revenues?', r'automotive regulatory credits(?: revenues?)?')
INPUTS = ('automotive_revenue', 'automotive_cost', 'regulatory_credits')


def requested(question):
    return all(re.search(pattern, question, re.I) for pattern in (
        r'\bautomotive\b', r'regulatory credits?|credit revenue', r'\bmargin', r'exclud', r'includ'))


def has_inputs(text):
    return all(re.search(r'^\s*' + label + r'\s*\|', text, re.I | re.M) for label in LABELS)


def table_anchors(store, rows, question):
    return complete_tables(store, rows, has_inputs) if requested(question) else []


def _quarter_values(text, year):
    """Compatibility helper; calculation handlers use explicit period identities."""
    facts = table_facts(dict(id='table', ticker='TSLA', text=text))
    periods = {f.period for f in facts if f.period.startswith(f'{year}-Q')}
    return _inputs(facts, next(iter(periods))) if len(periods) == 1 else None


def _inputs(facts, period):
    result, units = [], set()
    for metric in INPUTS:
        matched = [f for f in facts if f.period == period and f.metric == metric]
        values = {f.value for f in matched}
        if len(values) != 1:
            return None
        units.update(f.unit for f in matched)
        result.append(next(iter(values)))
    # Ratios cancel a table's shared scale; never combine differently scaled rows.
    return result if len(units) == 1 else None


def complete_request(question, sources, scope):
    """An exact handler must understand every requested clause, not just keywords."""
    names = ' '.join(str(s.get('company', '')) + ' ' + s.get('ticker', '') for s in sources)
    allowed = set(('how did do does the a an s automotive regulatory credits credit revenue revenues '
                   'affect impact influence contribute to of tesla q1 q2 q3 q4 in versus vs and '
                   'calculate compute margins margin gross profit including excluding include exclude '
                   'with without keeping keep reported total costs cost unchanged constant held '
                   'compare comparison between for what was were is are percent percentage points '
                   'revenue').split()) | set(re.findall(r'[a-z]+', names.lower()))
    words = set(re.findall(r'[a-z]+\d*', question.lower()))
    return words <= allowed and (not scope or set(scope.get('tickers', [])) == {s['ticker'] for s in sources})


def answer(question, sources, scope=None):
    if not requested(question) or not complete_request(question, sources, scope):
        return None
    quarters = set(re.findall(r'\bQ([1-4])\b', question, re.I))
    years = sorted(set(re.findall(r'\b20\d{2}\b', question)))
    if len(quarters) != 1 or not years:
        return None
    quarter = int(next(iter(quarters)))
    periods = {f'{year}-Q{quarter}' for year in years}
    if scope and set(scope.get('requestedPeriods', [])) != periods:
        return None
    tickers = scope.get('tickers', []) if scope else sorted({s['ticker'] for s in sources})
    if not tickers:
        return None
    parsed = [(s, table_facts(s)) for s in sources]
    # Prefer one recent comparative table when it covers several periods. Any
    # conflicting original/comparative values require general grounded handling.
    parsed.sort(key=lambda item: (-sum(_inputs(item[1], p) is not None for p in periods),
                                  -int(item[0].get('report_date', '0000-00-00').replace('-', ''))))
    calculations, ids, effects = [], [], []
    for ticker in tickers:
        for year in years:
            period = f'{year}-Q{quarter}'
            matched = [(s, _inputs(facts, period)) for s, facts in parsed if s['ticker'] == ticker]
            matched = [(s, values) for s, values in matched if values is not None]
            if not matched or len({tuple(values) for _, values in matched}) != 1:
                return None
            source, (revenue, cost, credits) = matched[0]
            if revenue <= 0 or revenue - credits <= 0 or cost < 0 or credits < 0:
                return None
            ids.append(source['id'])
            company = source.get('company', ticker)
            for operation, values, label in (
                ('gross_margin', [revenue, cost], 'including regulatory credits'),
                ('gross_margin_excluding_revenue', [revenue, cost, credits], ''),
            ):
                calculations.append(dict(label=f'{company} {ticker} automotive Q{quarter} {year} {label}'.strip(),
                                         operation=operation, values=values, source_ids=[source['id']], unit='%'))
            effect = calculate('gross_margin', [revenue, cost]) - calculate('gross_margin_excluding_revenue', [revenue, cost, credits])
            effects.append(f"Credit revenue increased {ticker}'s calculated margin by {effect:.2f} percentage points in Q{quarter} {year} [{source['id']}].")
    return dict(status='answered', answer='With reported total automotive costs unchanged, ' + ' '.join(effects),
                citation_ids=list(dict.fromkeys(ids)), chart=empty_chart(), calculations=calculations)
