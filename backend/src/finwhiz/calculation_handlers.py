"""Small calculation registry shared by local lookup and general research."""
import re
from .automotive_margins import answer as automotive_answer, table_anchors
from .financial_facts import complete_tables
from .finance import cash_flow_answer


def try_calculation(question, sources, scope=None):
    if scope and set(scope.get('tickers', [])) != {s['ticker'] for s in sources}:
        return None, None
    result = automotive_answer(question, sources, scope)
    if result:
        return result, 'automotive-credit-table'
    result = cash_flow_answer(question, sources)
    if result:
        # A handler may explain its calculation, but must not swallow another
        # requested metric or an open-ended causal analysis.
        allowed = set(re.findall(r'[a-z]+\d*', result['answer'].lower())) | set((
            'what was were is are the a an and for in of how calculate compute compare '
            'between versus vs first six months half h1 operating cash flow free cash '
            'minus purchases property equipment excluding finance leases net sales '
            'adjustment deducted added reconciliation did it affect impact').split())
        if set(re.findall(r'[a-z]+\d*', question.lower())) <= allowed:
            return result, 'cash-flow-table'
    return None, None


def local_tables(store, rows, question):
    anchors = table_anchors(store, rows, question)
    if not anchors and re.search(r'operating cash flow', question, re.I):
        anchors = complete_tables(store, rows, lambda text: bool(re.search(
            r'Cash Flows from Operating Activities', text, re.I)))
    return anchors
