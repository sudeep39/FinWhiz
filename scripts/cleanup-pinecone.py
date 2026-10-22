"""Back up and delete only audited non-Replit vector IDs; default is dry-run."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))
from finwhiz.config import Settings, compact, digest


def protected_namespaces(states, namespaces):
    protected, job_ids = set(), set()
    for state in states:
        jobs = state.get('jobs')
        if not isinstance(jobs, list) or not jobs:
            raise RuntimeError('An authenticated, nonempty Replit registry is required.')
        for job in jobs:
            if job['status'] in ('queued', 'processing'):
                raise RuntimeError('Replit ingestion is active. Finish it before cleanup.')
            job_ids.add(job['id'])
            protected.update(job.get('namespaces', {}).values())
    return protected | {namespace for namespace in namespaces if any(namespace.startswith('web-' + job_id + '-') for job_id in job_ids)}


def build_plan(audit):
    states = []
    for provider in ('openai', 'nebius'):
        entry = audit['remote'][provider]
        if entry['status'] != 200:
            raise RuntimeError('Replit authentication failed; deletion is forbidden.')
        states.append(entry['state'])
    plan = dict(origin=audit['replitOrigin'], retain=[], delete=[])
    for provider, index in audit['indexes'].items():
        namespaces = index['stats'].get('namespaces', {})
        keep = protected_namespaces(states, namespaces)
        if namespaces and not set(namespaces).intersection(keep):
            raise RuntimeError('No Replit-owned namespace found in a nonempty index.')
        for namespace, stats in sorted(namespaces.items()):
            record = dict(provider=provider, index=index['index'], host=index['host'],
                          namespace=namespace, vectorCount=stats['vectorCount'])
            if namespace in keep:
                plan['retain'].append(record)
            elif namespace.startswith('web-'):
                plan['delete'].append(record)
            else:
                raise RuntimeError('Unrecognized namespace requires review: ' + namespace)
    return plan


def run(args):
    import httpx
    settings = Settings(_env_file=ROOT / '.env')
    audit = json.loads(args.audit.read_bytes())
    plan = build_plan(audit)
    print(json.dumps(dict(retain=plan['retain'], delete=plan['delete'],
                         deleteVectors=sum(row['vectorCount'] for row in plan['delete'])), indent=2), flush=True)
    if not args.apply:
        return
    if plan['origin'] != 'https://fin-whiz.replit.app':
        raise RuntimeError('Unexpected authoritative origin; review the cleanup tool before changing it.')
    headers = {'Api-Key': settings.pinecone_api_key, 'X-Pinecone-Api-Version': '2025-10'}
    password = settings.finwhiz_shared_backend_password or settings.app_password
    backup = ROOT / '.backups' / ('pinecone-cleanup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True, exist_ok=False)
    with httpx.Client(timeout=60) as client:
        def request(method, url, **kwargs):
            response = client.request(method, url, **kwargs)
            if not response.is_success:
                raise RuntimeError(f'Remote operation returned HTTP {response.status_code}; no response body logged.')
            return response.json()

        def verify_registry():
            states = [request('GET', plan['origin'] + '/api/state', headers={
                'Authorization': 'Bearer ' + password, 'X-AI-Provider': provider}) for provider in ('openai', 'nebius')]
            names = {row['namespace'] for row in plan['retain'] + plan['delete']}
            protected = protected_namespaces(states, names)
            if {row['namespace'] for row in plan['delete']} & protected:
                raise RuntimeError('Replit now references a deletion candidate. Stopping.')
            if not {row['namespace'] for row in plan['retain']}.issubset(protected):
                raise RuntimeError('Replit ownership changed. Re-audit before cleanup.')

        def stats(host):
            return request('POST', 'https://' + host + '/describe_index_stats', headers=headers, json={}).get('namespaces', {})

        for entry in plan['retain'] + plan['delete']:
            configured = settings.provider(entry['provider'])
            actual = request('GET', 'https://api.pinecone.io/indexes/' + configured.indexName, headers=headers)
            if configured.indexName != entry['index'] or actual['host'] != entry['host'] or not entry['host'].endswith('.pinecone.io'):
                raise RuntimeError('Index identity changed. Re-audit before cleanup.')
        verify_registry()
        saved = []
        for number, entry in enumerate(plan['delete']):
            host, namespace = entry['host'], entry['namespace']
            if stats(host).get(namespace, {}).get('vectorCount', 0) != entry['vectorCount']:
                raise RuntimeError('Vector count changed. Re-audit before cleanup.')
            ids, pagination = set(), None
            while True:
                params = {'namespace': namespace, 'limit': 100}
                if pagination:
                    params['paginationToken'] = pagination
                result = request('GET', 'https://' + host + '/vectors/list', headers=headers, params=params)
                ids.update(vector['id'] for vector in result.get('vectors', []))
                pagination = result.get('pagination', {}).get('next')
                if not pagination:
                    break
            if len(ids) != entry['vectorCount']:
                raise RuntimeError('Listed vector count does not match the audited namespace.')
            ordered = sorted(ids)
            target = backup / f'vectors-{number}.jsonl.gz'
            def fetch(batch):
                result = request('GET', 'https://' + host + '/vectors/fetch', headers=headers,
                                 params=[('namespace', namespace)] + [('ids', value) for value in batch])
                if set(result.get('vectors', {})) != set(batch):
                    raise RuntimeError('Backup fetch was incomplete. No deletion allowed.')
                return result['vectors']
            batches = [ordered[start:start + 100] for start in range(0, len(ordered), 100)]
            count = 0
            with gzip.open(target, 'wt', encoding='utf-8') as stream, ThreadPoolExecutor(max_workers=4) as pool:
                for vectors in pool.map(fetch, batches):
                    for vector in vectors.values():
                        if not vector.get('values'):
                            raise RuntimeError('Backup lacks vector values.')
                        stream.write(compact(vector) + '\n')
                        count += 1
            # Read the completed archive back before authorizing deletion.
            with gzip.open(target, 'rt', encoding='utf-8') as stream:
                restored_ids = {json.loads(line)['id'] for line in stream}
            if restored_ids != ids:
                raise RuntimeError('Backup readback failed.')
            saved.append(dict(**entry, archive=target.name, archiveSha256=digest(target.read_bytes()), ids=ordered))
            print(f'Backed up and verified {count} vectors: {namespace}', flush=True)
        manifest = dict(plan=plan, archives=saved, completed=False)
        manifest_path = backup / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        verify_registry()
        for entry in saved:
            if stats(entry['host']).get(entry['namespace'], {}).get('vectorCount', 0) != entry['vectorCount']:
                raise RuntimeError('Namespace changed during backup. No deletion allowed.')
        for entry in saved:
            # Delete exact backed-up IDs rather than clearing arbitrary namespaces.
            for start in range(0, len(entry['ids']), 1000):
                request('POST', 'https://' + entry['host'] + '/vectors/delete', headers=headers,
                        json={'namespace': entry['namespace'], 'ids': entry['ids'][start:start + 1000]})
            print('Deleted backed-up IDs from ' + entry['namespace'], flush=True)
        for attempt in range(30):
            current = {host: stats(host) for host in {entry['host'] for entry in saved + plan['retain']}}
            if all(current[entry['host']].get(entry['namespace'], {}).get('vectorCount', 0) == 0 for entry in saved):
                break
            time.sleep(1)
        else:
            raise RuntimeError('Deletion visibility is pending; inspect the saved manifest before retrying.')
        for entry in plan['retain']:
            if current[entry['host']].get(entry['namespace'], {}).get('vectorCount', 0) != entry['vectorCount']:
                raise RuntimeError('Retained namespace count changed; inspect remote state immediately.')
        verify_registry()
        manifest.update(completed=True, remaining=current)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        print('Cleanup verified. Backup: ' + str(backup), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, default=ROOT / '.cache/shared-library-audit.json')
    parser.add_argument('--apply', action='store_true')
    try:
        run(parser.parse_args())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
