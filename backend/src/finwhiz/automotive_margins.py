"""Evidence selection and conservative table arithmetic for credit-adjusted margins."""
import re
from .finance import calculate, empty_chart

LABELS = (r'total automotive revenues?', r'total automotive cost of revenues?', r'automotive regulatory credits')


def requested(question):
    return all(re.search(pattern, question, re.I) for pattern in (
        r'\bautomotive\b', r'regulatory credits?|credit revenue', r'\bmargin', r'exclud', r'includ'))


def has_inputs(text):
    return all(re.search(r'^\s*' + label + r'\s*\|', text, re.I | re.M) for label in LABELS)


def table_anchors(store, rows, question):
    """Reserve a complete input table per company/filing, before global truncation."""
    if not requested(question):
        return []
    anchors, seen, parents_cache = [], set(), {}
    for row in rows:
        m = row['metadata']
        group = m['ticker'], m['report_date']
        if group in seen or m.get('chunk_type') != 'table':
            continue
        key = f'parents/{m["corpus_id"]}/{m["doc_id"]}'
        if key not in parents_cache:
            parents_cache[key] = store.json(key, [])
        index = int(m['parent_id'].rsplit(':', 1)[1])
        parents = parents_cache[key]
        if not 0 <= index < len(parents):
            continue
        parent = parents[index]
        if parent['kind'] != 'table' or len(parent['text']) > 12000 or not has_inputs(parent['text']):
            continue
        prefix = f"{m['company']} | {m['ticker']} | {m['form']} | Period ended {m['report_date']}\n{m['section_path']}\n"
        anchors.append({**row, 'metadata': {**m, 'text': prefix + parent['text'], 'complete_table_attached': True}})
        seen.add(group)
    return anchors


def _quarter_values(text, year):
    """Respect three-month versus six-month column groups and repeated HTML cells."""
    lines = [line.split('|') for line in text.splitlines() if '|' in line]
    lines = [[cell.strip() for cell in line] for line in lines]
    for i, header in enumerate(lines):
        if not any(re.search('Three Months Ended', cell, re.I) for cell in header):
            continue
        years = next((line for line in lines[i + 1:i + 4] if str(year) in line), None)
        if years is None:
            continue
        columns = [col for col, cell in enumerate(years) if cell == str(year) and col < len(header) and re.search('Three Months Ended', header[col], re.I)]
        result = []
        for label in LABELS:
            matches = [line for line in lines[i + 1:] if re.fullmatch(label, line[0], re.I)]
            values = []
            for line in matches:
                numbers = [float(line[col].replace(',', '')) for col in columns if col < len(line) and re.fullmatch(r'\d[\d,]*(?:\.\d+)?', line[col])]
                if numbers and len(set(numbers)) == 1:
                    values.append(numbers[0])
            if not values or len(set(values)) != 1:
                break
            result.append(values[0])
        if len(result) == 3:
            return result
    return None


def answer(question, sources, scope=None):
    if not requested(question):
        return None
    quarters = set(re.findall(r'\bQ([1-4])\b', question, re.I))
    years = sorted(set(re.findall(r'\b20\d{2}\b', question)))
    # This supported table layout is Tesla's calendar-quarter disclosure.
    # Other issuers/ambiguous periods continue through general grounded generation.
    if len(quarters) != 1 or not years or quarters == {'4'}:
        return None
    quarter = int(next(iter(quarters)))
    if scope and set(scope.get('requestedPeriods', [])) != {f'{year}-Q{quarter}' for year in years}:
        return None
    suffix = {1: '03-31', 2: '06-30', 3: '09-30'}[quarter]
    calculations, ids = [], []
    effects = []
    for year in years:
        matched = []
        for source in sources:
            if source.get('ticker') != 'TSLA' or source.get('report_date') != f'{year}-{suffix}':
                continue
            values = _quarter_values(source['text'], year)
            if values:
                matched.append((source, values))
        if not matched or len({tuple(values) for _, values in matched}) != 1:
            return None
        source, (revenue, cost, credits) = matched[0]
        if revenue <= 0 or revenue - credits <= 0 or cost < 0:
            return None
        ids.append(source['id'])
        for operation, values, label in (
            ('gross_margin', [revenue, cost], 'including regulatory credits'),
            ('gross_margin_excluding_revenue', [revenue, cost, credits], ''),
        ):
            calculations.append(dict(label=f'Tesla Q{quarter} {year} {label}'.strip(), operation=operation,
                                     values=values, source_ids=[source['id']], unit='%'))
        effect = calculate('gross_margin', [revenue, cost]) - calculate('gross_margin_excluding_revenue', [revenue, cost, credits])
        effects.append(f"Credit revenue increased the calculated margin by {effect:.2f} percentage points in Q{quarter} {year} [{source['id']}].")
    return dict(status='answered', answer='With reported total automotive costs unchanged, ' + ' '.join(effects),
                citation_ids=list(dict.fromkeys(ids)), chart=empty_chart(), calculations=calculations)
