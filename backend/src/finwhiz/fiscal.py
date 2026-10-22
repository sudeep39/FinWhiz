"""Verified fiscal labels from SEC DEI facts; no calendar-quarter guessing."""
import re
from functools import lru_cache
from html import unescape
from pathlib import Path


def fiscal_metadata(html):
    def fact(name):
        match = re.search(r'<[^>]+\bname=[\"\']dei:' + name + r'[\"\'][^>]*>(.*?)</[^>]+>', html, re.I | re.S)
        return unescape(re.sub(r'<[^>]*>', '', match[1])).strip() if match else ''
    year, period = fact('DocumentFiscalYearFocus'), fact('DocumentFiscalPeriodFocus').upper()
    if not re.fullmatch(r'(?:19|20|21)\d{2}', year) or period not in ('Q1', 'Q2', 'Q3', 'Q4', 'FY'):
        return {}
    return dict(fiscal_year=int(year), fiscal_quarter=4 if period == 'FY' else int(period[1]),
                period_type='annual' if period == 'FY' else 'quarterly', fiscal_metadata_source='dei')


@lru_cache(maxsize=128)
def _read_fiscal(path, modified, size):
    return fiscal_metadata(Path(path).read_text(encoding='utf-8', errors='replace'))


def catalog_documents(jobs, store):
    """Enrich historical catalog entries in memory, without rewriting vectors."""
    from .config import ROOT
    result = []
    for job in jobs:
        if job['status'] != 'ready':
            continue
        files = {file['id']: file for file in job.get('files', [])}
        for doc in job.get('documents', []):
            fields = {}
            file = files.get(doc['id'], {})
            if not doc.get('fiscal_year') and file.get('name', '').lower().endswith(('.html', '.htm')):
                if file.get('samplePath'):
                    path = (ROOT / file['samplePath']).resolve()
                    if not path.is_relative_to(ROOT / 'data'):
                        continue
                else:
                    path = store.path(file['key']) if file.get('key') else None
                if path and path.is_file():
                    info = path.stat()
                    fields = _read_fiscal(str(path), info.st_mtime_ns, info.st_size)
            result.append({**doc, **fields, 'jobId': job['id']})
    return result


def metadata_backfill_plan(jobs, store):
    """Propose metadata-only updates for review; never contact Pinecone."""
    updates, skipped = [], []
    for job in jobs:
        docs = {doc['id']: doc for doc in catalog_documents([job], store)}
        index = job.get('aiConfig', {}).get('indexName')
        for strategy, namespace in job.get('namespaces', {}).items():
            for row in store.json(f"chunks/{job['id']}/{strategy}", []):
                doc = docs.get(row['metadata'].get('doc_id'), {})
                if not index or not all(doc.get(key) for key in ('fiscal_year', 'fiscal_quarter', 'period_type')):
                    continue
                fields = {key: doc[key] for key in ('fiscal_year', 'fiscal_quarter', 'period_type')}
                if any(row['metadata'].get(key) != value for key, value in fields.items()):
                    updates.append(dict(index=index, namespace=namespace, id=row['id'], setMetadata=fields))
        if not index or not docs or any(not doc.get('fiscal_year') for doc in docs.values()):
            skipped.append(job['id'])
    return dict(dryRun=True, remoteInspected=False, updates=updates, updateCount=len(updates),
                jobsRequiringReview=skipped,
                message='Review metadata-only updates against the deployed registry before applying. No vectors were changed or re-embedded.')
