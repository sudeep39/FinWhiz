"""Evidence checks and deterministic rendering, independent of graph orchestration."""

import re
from .config import AppError
from .finance import calculate, supports_number, finalize_margins, empty_chart
from .fact_validation import bound_value, calculation_bound, numeric_context, ordered_periods
from .financial_facts import table_facts


def referenced_sources(answer, sources):
    """Expose evidence actually referenced by the final rendered response."""
    ids = set(re.findall(r'\[(S\d+)\]', answer['answer']))
    for calculation in answer.get('calculations', []):
        ids.update(calculation.get('source_ids', []))
    if answer.get('chart', {}).get('type') != 'none':
        for point in answer.get('chart', {}).get('points', []):
            ids.update(point.get('source_ids', []))
    return [source for source in sources if source['id'] in ids]


def coverage_issues(answer, sources, scope):
    if not scope or answer['status'] != 'answered':
        return []
    cited = referenced_sources(answer, sources)
    missing_companies = set(scope.get('tickers', [])) - {s.get('ticker') for s in cited}
    issues = [f'The answer omits requested company {ticker}.' for ticker in sorted(missing_companies)]
    for ticker in scope.get('tickers', []):
        company_sources = [s for s in cited if s.get('ticker') == ticker]
        periods = {f.period for s in company_sources for f in table_facts(s)}
        for source in company_sources:
            year, quarter = source.get('fiscal_year'), source.get('fiscal_quarter')
            if year and quarter:
                periods.add(f'{year}-Q{str(quarter).lstrip("Qq")}')
            elif ticker == 'TSLA' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', source.get('report_date', '')):
                day = source['report_date']
                periods.add(f'{day[:4]}-Q{(int(day[5:7])-1)//3+1}')
        for period in scope.get('requestedPeriods', []):
            if period not in periods:
                # Explicit source-column differences may derive Q1/Q4. The
                # operand validator independently checks their period order.
                year, kind = period.split('-', 1)
                inputs = {'Q1': {f'{year}-H1', f'{year}-Q2'},
                          'Q4': {f'{year}-FY', f'{year}-YTD-Q3'}}.get(kind, set())
                derived = bool(inputs) and inputs <= periods and any(
                    c['operation'] == 'difference' and inputs <= set(ordered_periods(c['label']))
                    for c in answer.get('calculations', []))
                if not derived:
                    issues.append(f'The cited evidence does not cover {ticker} {period}.')
    return issues


def supports_narrative_number(sources, ids, value, unit=None, claim='', metric=None):
    """Allow exact unit conversions, never rounding or new arithmetic claims."""
    scales = {'thousand': 1e3, 'million': 1e6, 'billion': 1e9, 'trillion': 1e12}
    bound = bound_value(sources, ids, value, claim, unit, metric)
    if bound is not None:
        return bound
    for source in sources:
        if source['id'] not in ids:
            continue
        text = source['text']
        if not unit:
            if supports_number(text, value):
                return True
            continue
        source_scales = re.findall(r'\bin\s+(thousands|millions|billions|trillions)\b', text, re.I)
        if len(set(s.lower() for s in source_scales)) == 1:
            scale = scales[source_scales[0].lower().rstrip('s')]
            if supports_number(text, value * scales[unit] / scale):
                return True
        # Explicit narrative quantities are also evidence for their own scale.
        for match in re.finditer(r'(?<![\w.])-?\d[\d,]*(?:\.\d+)?\s+(thousand|million|billion|trillion)s?\b', text, re.I):
            if match[1].lower() == unit and supports_number(match[0], value):
                return True
    return False


def validation_issues(answer, sources, validate_numeric=True):
    allowed = {s["id"] for s in sources}

    def valid(ids):
        return bool(ids) and all(i in allowed for i in ids)

    def cited(ids):
        return "\n".join(s["text"] for s in sources if s["id"] in ids)

    answer["answer"] = re.sub(
        r"\[(S\d+(?:\s*[,;]\s*S\d+)+)\]",
        lambda m: " ".join(f"[{i}]" for i in re.split(r"\s*[,;]\s*", m[1])),
        answer["answer"],
    )
    inline = re.findall(r"\[(S\d+)\]", answer["answer"])
    issues = []
    if answer["status"] == "answered" and validate_numeric:
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z\[])", answer["answer"]):
            ids = re.findall(r"\[(S\d+)\]", sentence)
            if not ids:
                ids = answer["citation_ids"]
            text = re.sub(r"\[S\d+\]", "", sentence)
            for match in re.finditer(r"(?<![A-Za-z\d])\(?-?\d[\d,]*(?:\.\d+)?%?\)?", text):
                raw = match[0]
                # Period labels identify the claim; they are not financial values.
                if re.fullmatch(r'20\d{2}', raw) or (match.start() and text[match.start()-1:match.start()].upper() == 'Q'):
                    continue
                value = float(re.sub(r"[(),%]", "", raw)) * (-1 if raw.startswith("(") else 1)
                suffix = re.match(r'\s+(thousand|million|billion|trillion)s?\b', text[match.end():], re.I)
                unit = suffix[1].lower() if suffix and '%' not in raw else None
                context, metric = numeric_context(text, match.start())
                if not supports_narrative_number(sources, ids, value, unit, context, metric):
                    issues.append(
                        f"Narrative value {raw}{' ' + unit if unit else ''} is not supported by citations {ids}. Use an exact cited value with its original units, or move derived results to the calculation panel."
                    )
    if any(i not in allowed for i in answer["citation_ids"] + inline) or (
        answer["status"] == "answered"
        and (
            not inline
            or not valid(answer["citation_ids"])
            or any(i not in answer["citation_ids"] for i in inline)
        )
    ):
        issues.append("The answer must contain valid evidence citations.")
    for calculation in answer["calculations"]:
        if not valid(calculation["source_ids"]) or not calculation_bound(calculation, sources) or not all(
            supports_number(cited(calculation["source_ids"]), value)
            for value in calculation["values"]
        ):
            issues.append(
                f"{calculation['label']}: inputs must be literal values in the cited evidence."
            )
        try:
            calculate(calculation["operation"], calculation["values"])
        except AppError:
            issues.append(f"{calculation['label']}: invalid operation, input count or denominator.")
        if calculation['operation'] == 'ratio' and re.search(r'%|percent', calculation['unit'], re.I):
            issues.append(f"{calculation['label']}: use the percentage operation for percent units.")
    for point in answer['chart']['points']:
        scale = re.search(r'\b(thousand|million|billion)s?\b', answer['chart'].get('unit', ''), re.I)
        if not valid(point['source_ids']) or bound_value(sources, point['source_ids'], point['value'], point['label'], scale[1].lower() if scale else None) is False or not supports_number(cited(point['source_ids']), point['value']):
            issues.append('Chart values must be literal numbers in their cited evidence; omit an unsupported chart.')
    return list(dict.fromkeys(issues))


def finalize(answer, sources):
    allowed = {s["id"] for s in sources}

    def valid(ids, value):
        return (
            bool(ids)
            and all(i in allowed for i in ids)
            and supports_number("\n".join(s["text"] for s in sources if s["id"] in ids), value)
        )

    for point in answer["chart"]["points"]:
        if not valid(point["source_ids"], point["value"]):
            raise AppError(
                "Chart values could not be verified against the cited evidence. Ask for a specific metric and period."
            )
    for calculation in answer["calculations"]:
        if calculation["operation"] == "ratio" and re.search(
            r"%|percent", calculation["unit"], re.I
        ):
            raise AppError("Use the percentage operation for percent units.")
        calculation["result"] = calculate(calculation["operation"], calculation["values"])
    if answer["status"] != "answered":
        answer["chart"], answer["calculations"] = empty_chart(), []
    return finalize_margins(answer)
