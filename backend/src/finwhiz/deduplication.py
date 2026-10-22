"""Preview, back up, retire and delete a fully redundant ingestion job."""
import gzip
import json
import re
import time
from urllib.parse import urlencode

from .config import AIConfig, ROOT, AppError, compact, digest
from .storage import now


def _source(store, file):
    if file.get('samplePath'):
        path = (ROOT / file['samplePath']).resolve()
        if not path.is_relative_to(ROOT / 'data'):
            raise AppError('Invalid source path.', 409)
        return path.read_bytes()
    data = store.get(file.get('key', ''))
    if data is None:
        raise AppError('Original source bytes are missing; cleanup cannot proceed.', 409)
    return data


def preview(jobs, remove_id, keep_id):
    if remove_id == keep_id:
        raise AppError('Choose two different jobs.', 400)
    registered = jobs.all()
    by_id = {job['id']: job for job in registered}
    remove, keep = by_id.get(remove_id), by_id.get(keep_id)
    if not remove or not keep:
        raise AppError('Job not found.', 404)
    if any(job['status'] in ('queued', 'processing') for job in registered):
        raise AppError('Finish queued/processing ingestion before deduplicating.', 409)
    if remove.get('deduplicatedInto'):
        if remove['deduplicatedInto'] != keep_id:
            raise AppError('This job was already merged into a different job.', 409)
        operation = jobs.store.json('deduplication/' + remove['deduplicationToken'])
        if not operation:
            raise AppError('Cleanup manifest is missing; restore it before resuming.', 409)
        return {**operation['plan'], 'status': operation['status']}
    if remove['status'] != 'ready' or keep['status'] != 'ready':
        raise AppError('Both jobs must be ready.', 409)
    if not remove.get('aiConfig') or not keep.get('aiConfig'):
        raise AppError('Both jobs need verified embedding configuration.', 409)
    config = AIConfig.model_validate(remove['aiConfig'])
    if config.fingerprint != AIConfig.model_validate(keep['aiConfig']).fingerprint:
        raise AppError('Embedding configurations differ.', 409)
    if sorted(remove.get('strategies', [])) != sorted(keep.get('strategies', [])):
        raise AppError('Chunking strategies differ.', 409)
    if not remove.get('documents') or len(remove['documents']) != len(remove['files']):
        raise AppError('The duplicate job must contain only complete duplicate documents.', 409)
    aliases = []
    for doc in remove['documents']:
        candidates = [item for item in keep.get('documents', []) if item.get('checksum') == doc.get('checksum') and
                      all(item.get(key) == doc.get(key) for key in ('ticker', 'form', 'reportDate'))]
        if not re.fullmatch(r'[a-f0-9]{64}', doc.get('checksum', '')) or not candidates:
            raise AppError('The kept job does not contain every identical source document.', 409)
        old_file = next((f for f in remove['files'] if f['id'] == doc['id']), None)
        new_file = next((f for f in keep['files'] if f['id'] == candidates[0]['id']), None)
        if not old_file or not new_file or digest(_source(jobs.store, old_file)) != doc['checksum'] or digest(_source(jobs.store, new_file)) != doc['checksum']:
            raise AppError('Source identity verification failed.', 409)
        aliases.append(dict(sourceId=old_file['id'], canonicalSourceId=new_file['id'], checksum=doc['checksum']))
    namespaces = []
    for strategy in remove['strategies']:
        namespace, canonical = remove.get('namespaces', {}).get(strategy), keep.get('namespaces', {}).get(strategy)
        if not namespace or not canonical or namespace == canonical:
            raise AppError('Distinct, verified namespaces are required.', 409)
        if any(namespace in job.get('namespaces', {}).values() for job in registered if job['id'] != remove_id):
            raise AppError('Another job references a namespace selected for deletion.', 409)
        rows = jobs.store.json(f'chunks/{remove_id}/{strategy}', [])
        kept = jobs.store.json(f'chunks/{keep_id}/{strategy}', [])
        versions = {row['metadata'].get('parser_version') for row in rows}
        if not rows or not kept or len(versions) != 1 or None in versions or versions != {row['metadata'].get('parser_version') for row in kept}:
            raise AppError('Chunk artifacts or matching parser versions are missing.', 409)
        canonical_ids = {a['canonicalSourceId'] for a in aliases}
        protected = sorted({row['id'] for row in kept if row['metadata'].get('doc_id') in canonical_ids})
        if not protected:
            raise AppError('Canonical document chunks are missing.', 409)
        namespaces.append(dict(strategy=strategy, namespace=namespace, ids=sorted({row['id'] for row in rows}),
                               canonicalNamespace=canonical, canonicalIds=protected, canonicalCount=len(kept)))
    if not namespaces:
        raise AppError('No namespaces are available for cleanup.', 409)
    plan = dict(removeJobId=remove_id, keepJobId=keep_id, index=config.indexName, provider=config.provider,
                sourceAliases=aliases, namespaces=namespaces, deleteVectorCount=sum(len(n['ids']) for n in namespaces),
                removeFingerprint=digest(remove), keepFingerprint=digest(keep))
    return {**plan, 'confirmToken': digest(plan), 'status': 'preview'}


def public_plan(plan):
    return {key: value for key, value in plan.items() if key not in ('removeFingerprint', 'keepFingerprint', 'namespaces')} | {
        'namespaces': [{key: value for key, value in entry.items() if key not in ('ids', 'canonicalIds')}
                       | {'deleteVectorCount': len(entry['ids'])} for entry in plan['namespaces']]}


def apply(jobs, plan, provider):
    token = plan['confirmToken']
    operation_key = 'deduplication/' + token
    operation = jobs.store.json(operation_key, {})
    if operation.get('status') == 'backed_up' and any(job.get('deduplicationToken') == token for job in jobs.all()):
        operation['status'] = 'registry_retired'
        jobs.store.put_json(operation_key, operation)
    if operation.get('status') == 'completed':
        return public_plan({**plan, 'status': 'completed'}) | {'backupManifestKey': operation_key}
    if provider.config.indexName != plan['index']:
        raise AppError('Provider/index mismatch.', 409)
    host = provider.pinecone_host()
    headers = {'Api-Key': provider.settings.pinecone_api_key, 'X-Pinecone-Api-Version': '2025-10'}

    def fetch(namespace, ids):
        records = {}
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            query = urlencode([('namespace', namespace)] + [('ids', key) for key in batch])
            result = provider.request(f'https://{host}/vectors/fetch?{query}', headers=headers, method='GET')
            vectors = result.get('vectors', {})
            if set(vectors) != set(batch) or any(not row.get('values') for row in vectors.values()):
                raise AppError('Vector backup or canonical visibility verification failed; no deletion performed.', 409)
            records.update(vectors)
        return records

    if not operation or operation.get('status') == 'backed_up':
        stats = provider.pinecone('describe_index_stats', {}).get('namespaces', {})
        backups = []
        for entry in plan['namespaces']:
            if stats.get(entry['namespace'], {}).get('vectorCount') != len(entry['ids']):
                raise AppError('Remote vector counts changed; inspect before cleanup.', 409)
            if stats.get(entry['canonicalNamespace'], {}).get('vectorCount') != entry['canonicalCount']:
                raise AppError('Canonical namespace counts changed.', 409)
            fetch(entry['canonicalNamespace'], entry['canonicalIds'])
            vectors = fetch(entry['namespace'], entry['ids'])
            key = f'deduplication-backup/{token}/{entry["strategy"]}'
            payload = gzip.compress(compact(vectors).encode(), compresslevel=1, mtime=0)
            jobs.store.put(key, payload)
            restored = json.loads(gzip.decompress(jobs.store.get(key)))
            if restored != vectors:
                raise AppError('Backup readback failed.', 409)
            backups.append(dict(namespace=entry['namespace'], key=key, sha256=digest(payload)))
        original = next(job for job in jobs.all() if job['id'] == plan['removeJobId'])
        operation = dict(status='backed_up', plan=plan, originalJob=original, backups=backups, createdAt=now())
        jobs.store.put_json(operation_key, operation)
        # Retire all searchable references atomically before touching remote vectors.
        with jobs.connection() as db:
            registered = [json.loads(row[0]) for row in db.execute('SELECT data FROM jobs')]
            by_id = {job['id']: job for job in registered}
            remove, keep = by_id[plan['removeJobId']], by_id[plan['keepJobId']]
            if digest(remove) != plan['removeFingerprint'] or digest(keep) != plan['keepFingerprint'] or any(job['status'] in ('queued', 'processing') for job in registered):
                raise AppError('Registry changed; cleanup stopped before deletion.', 409)
            files = {file['id']: file for file in keep['files']}
            aliases = {alias['sourceId']: alias['canonicalSourceId'] for alias in plan['sourceAliases']}
            sources = []
            for file in remove['files']:
                canonical = files[aliases[file['id']]]
                source = {key: value for key, value in file.items() if key not in ('key', 'samplePath', 'known')}
                source.update({key: canonical[key] for key in ('key', 'samplePath', 'known') if key in canonical})
                sources.append(source)
            remove.update(documents=[], namespaces={}, strategies=[], summaries=[], files=sources,
                          detail='Duplicate merged; original source links retained.',
                          deduplicatedInto=keep['id'], deduplicationToken=token)
            db.execute('UPDATE jobs SET data=? WHERE id=?', (compact(remove), remove['id']))
        operation['status'] = 'registry_retired'
        jobs.store.put_json(operation_key, operation)
    # Recheck durable archives and canonical records even on a resumed deletion.
    for archive in operation['backups']:
        payload = jobs.store.get(archive['key'])
        if payload is None or digest(payload) != archive['sha256']:
            raise AppError('Backup integrity check failed; restore the backup before resuming.', 409)
    for entry in plan['namespaces']:
        fetch(entry['canonicalNamespace'], entry['canonicalIds'])
        for start in range(0, len(entry['ids']), 1000):
            provider.pinecone('vectors/delete', dict(namespace=entry['namespace'], ids=entry['ids'][start:start + 1000]))
    for attempt in range(10):
        stats = provider.pinecone('describe_index_stats', {}).get('namespaces', {})
        if all(stats.get(entry['namespace'], {}).get('vectorCount', 0) == 0 for entry in plan['namespaces']):
            break
        time.sleep(1)
    else:
        raise AppError('Deletion visibility is pending. Retry the same confirmed operation.', 409)
    if any(stats.get(entry['canonicalNamespace'], {}).get('vectorCount') != entry['canonicalCount'] for entry in plan['namespaces']):
        raise AppError('Canonical counts changed; inspect the cleanup manifest.', 409)
    operation.update(status='completed', completedAt=now())
    jobs.store.put_json(operation_key, operation)
    return public_plan({**plan, 'status': 'completed'}) | {'backupManifestKey': operation_key}
