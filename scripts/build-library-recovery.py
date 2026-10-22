"""Export retained Replit vector metadata plus verified original sources; no vector writes."""
import base64
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))
from finwhiz.config import ROOT, Settings, compact, digest
from finwhiz.fiscal import fiscal_metadata
from finwhiz.providers import Providers
from finwhiz.storage import now

JOB_IDS = ['3f963a10-e7d8-4982-8d3f-6b11b2662cfe', '6bfa4ce0-2cd2-4945-baa0-c13fe157fd95']


def main():
    settings = Settings(_env_file=ROOT / '.env')
    config = settings.provider('nebius')
    manifest = json.loads((ROOT / 'data/filings/manifest.json').read_bytes())['entries']
    sources = [entry for entry in manifest if entry.get('ticker') == 'TSLA']
    js = """
import {readFile} from 'node:fs/promises';
import {extractHtml,makeParents} from './lib/chunking.mjs';
let input='';for await(const chunk of process.stdin)input+=chunk;
const result={};for(const source of JSON.parse(input))result[source.sha256]=makeParents(extractHtml(await readFile(source.localPath,'utf8')));
process.stdout.write(JSON.stringify(result));
"""
    parents = json.loads(subprocess.run(['node', '--input-type=module', '-e', js], cwd=ROOT,
                                       input=json.dumps(sources), text=True, encoding='utf-8',
                                       capture_output=True, check=True).stdout)
    objects, keys, jobs, namespace_counts = {}, {}, [], {}

    def put(key, data):
        data = data if isinstance(data, bytes) else compact(data).encode()
        checksum = digest(data)
        objects[checksum] = base64.b64encode(data).decode()
        keys[key] = checksum

    provider = Providers(settings, config)
    try:
        host = provider.pinecone_host()
        headers = {'Api-Key': settings.pinecone_api_key, 'X-Pinecone-Api-Version': '2025-10'}
        stats = provider.pinecone('describe_index_stats', {}).get('namespaces', {})
        for job_id in JOB_IDS:
            records_by_strategy = {}
            for strategy in ('fixed', 'semantic'):
                namespace = f'web-{job_id}-{strategy}'
                ids, cursor = [], None
                while True:
                    params = dict(namespace=namespace, limit=100)
                    if cursor:
                        params['paginationToken'] = cursor
                    result = provider.request(f'https://{host}/vectors/list?' + urlencode(params), headers=headers, method='GET')
                    ids.extend(row['id'] for row in result.get('vectors', []))
                    cursor = result.get('pagination', {}).get('next')
                    if not cursor:
                        break
                ids = sorted(set(ids))
                if not ids or len(ids) != stats.get(namespace, {}).get('vectorCount'):
                    raise RuntimeError('Unexpected or incomplete namespace: ' + namespace)
                def fetch(batch):
                    query = urlencode([('namespace', namespace)] + [('ids', key) for key in batch])
                    vectors = provider.request(f'https://{host}/vectors/fetch?{query}', headers=headers, method='GET').get('vectors', {})
                    if set(vectors) != set(batch):
                        raise RuntimeError('Incomplete vector metadata fetch')
                    return [dict(id=key, metadata=vectors[key]['metadata']) for key in batch]
                batches = [ids[i:i + 50] for i in range(0, len(ids), 50)]
                records = []
                with ThreadPoolExecutor(max_workers=4) as pool:
                    for rows in pool.map(fetch, batches):
                        records.extend(rows)
                for row in records:
                    metadata = row['metadata']
                    if metadata.get('corpus_id') != job_id or metadata.get('strategy') != strategy or metadata.get('embedding_fingerprint') != config.fingerprint:
                        raise RuntimeError('Vector provenance mismatch')
                put(f'chunks/{job_id}/{strategy}', records)
                records_by_strategy[strategy] = records
                namespace_counts[namespace] = len(records)
                print(f'Read {len(records)} existing records from {namespace}', flush=True)
            documents, files = [], []
            groups = {}
            for row in records_by_strategy['fixed']:
                groups.setdefault(row['metadata']['doc_id'], []).append(row)
            for source_id, rows in groups.items():
                metadata = rows[0]['metadata']
                candidates = [source for source in sources if source['reportDate'] == metadata['report_date'] and source['accession'] == metadata['accession']]
                if len(candidates) != 1:
                    raise RuntimeError('Verified original filing is missing')
                source = candidates[0]
                raw = (ROOT / source['localPath']).read_bytes()
                if digest(raw) != source['sha256']:
                    raise RuntimeError('Original source checksum mismatch')
                original_parents = parents[source['sha256']]
                all_rows = [row for records in records_by_strategy.values() for row in records if row['metadata']['doc_id'] == source_id]
                for row in all_rows:
                    m = row['metadata']
                    index = int(m['parent_id'].rsplit(':', 1)[1])
                    if index >= len(original_parents) or original_parents[index]['kind'] != m['chunk_type'] or original_parents[index]['section'] != m['section_path']:
                        raise RuntimeError('Reconstructed legacy parents do not match stored vector provenance')
                key = f'upload/{source_id}'
                put(key, raw)
                put(f'parents/{job_id}/{source_id}', original_parents)
                name = Path(source['localPath']).name
                files.append(dict(id=source_id, key=key, name=name, size=len(raw), checksum=source['sha256'], known=source))
                documents.append(dict(id=source_id, name=name, checksum=source['sha256'],
                                      **{field: source.get(field, '') for field in ('ticker', 'company', 'cik', 'form', 'reportDate', 'filingDate', 'accession')},
                                      sourceUrl=source['url'], **fiscal_metadata(raw.decode('utf-8', errors='replace'))))
            jobs.append(dict(id=job_id, aiConfig=config.model_dump(), createdAt=now(), completedAt=now(),
                             status='ready', phase='ready', percent=100, detail='Recovered existing Replit library; vectors reused.',
                             strategies=['fixed', 'semantic'], files=files, documents=documents,
                             namespaces={strategy: f'web-{job_id}-{strategy}' for strategy in records_by_strategy},
                             summaries=[dict(strategy=strategy, chunks=len(rows)) for strategy, rows in records_by_strategy.items()]))
        bundle = dict(version=1, createdAt=now(), provenance='Retained Replit namespaces and checksum-verified original SEC files',
                      indexes=[dict(provider='nebius', indexName=config.indexName, fingerprint=config.fingerprint, namespaces=namespace_counts)],
                      jobs=jobs, objects=objects, keys=keys)
        output = ROOT / 'data/recovery/replit-library-v1.json.gz'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(gzip.compress(compact(bundle).encode(), compresslevel=6, mtime=0))
        if json.loads(gzip.decompress(output.read_bytes())) != bundle:
            raise RuntimeError('Recovery bundle readback failed')
        print(json.dumps(dict(bundle=str(output), sha256=digest(output.read_bytes()), bytes=output.stat().st_size,
                              jobs=len(jobs), documents=sum(len(job['documents']) for job in jobs), vectors=sum(namespace_counts.values()))))
    finally:
        provider.close()


if __name__ == '__main__':
    main()
