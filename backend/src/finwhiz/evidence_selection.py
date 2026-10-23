"""Evidence expansion and token packing with explicit filing coverage."""
import copy
from .config import compact
from .documents import token_count
from .financial_facts import facts_payload


def group(row):
    return row['metadata']['ticker'], row['metadata']['report_date']


def pack_sources(store, rows, required, budget, optional_limit=5):
    sources, cache, seen_parents, represented = [], {}, set(), set()
    # Required groups come first; optional context consumes only remaining budget.
    ordered, rest = [], []
    for row in rows:
        if group(row) not in represented:
            ordered.append(row)
            represented.add(group(row))
        else:
            rest.append(row)
    represented = set()
    used = 0
    for row in ordered + rest:
        mandatory = group(row) in required and group(row) not in represented
        if not mandatory and len(sources) >= max(optional_limit, len(required)):
            continue
        m = copy.deepcopy(row['metadata'])
        identity = m.get('corpus_id'), m.get('doc_id'), m.get('parent_id')
        if m.get('chunk_type') == 'table':
            if identity in seen_parents:
                continue
            if not m.get('complete_table_attached'):
                key = f'parents/{m["corpus_id"]}/{m["doc_id"]}'
                if key not in cache:
                    cache[key] = store.json(key, [])
                try:
                    index = int(m['parent_id'].rsplit(':', 1)[-1])
                except (KeyError, ValueError):
                    index = -1
                if 0 <= index < len(cache[key]) and cache[key][index].get('kind') == 'table':
                    m['text'] += '\n\nComplete source table:\n' + cache[key][index]['text']
        source = {'id': f'S{len(sources)+1}', 'chunkId': row['id'], **m}
        size = token_count(compact(source)) + token_count(compact(facts_payload([source]))) + 2
        if used + size > budget:
            continue
        sources.append(source)
        used += size
        represented.add(group(row))
        if m.get('chunk_type') == 'table':
            seen_parents.add(identity)
    missing = sorted(required - represented)
    return sources, missing
