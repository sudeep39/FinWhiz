"""Content identity, atomic upload reuse, and offline inventory."""
import json
from .config import AIConfig, ROOT, AppError, compact, digest, job_matches
from .documents import PARSER_VERSION


def checksum(file, store):
    if file.get('checksum'):
        return file['checksum']
    if file.get('known', {}).get('sha256'):
        return file['known']['sha256']
    if file.get('samplePath'):
        path = (ROOT / file['samplePath']).resolve()
        if not path.is_relative_to(ROOT / 'data'):
            return None
        data = path.read_bytes() if path.is_file() else None
    else:
        data = store.get(file['key']) if file.get('key') else None
    return digest(data) if data is not None else None


def accept_upload(jobs, job):
    """One transaction covers duplicate detection and insertion, including races.

    Keep historical source IDs/namespaces so existing citations remain valid.
    Failed copies are returned for explicit retry, never automatically reindexed.
    """
    config = AIConfig.model_validate(job['aiConfig'])
    with jobs.connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS documents (checksum TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE)')
        existing = [json.loads(row[0]) for row in db.execute('SELECT data FROM jobs ORDER BY ordinal')]
        known = {}
        for previous in existing:
            if not job_matches(previous, config):
                continue
            if sorted(previous.get('strategies', [])) != sorted(job['strategies']):
                continue
            if previous.get('ingestionVersion', PARSER_VERSION) != PARSER_VERSION:
                continue
            changed = False
            for file in previous.get('files', []):
                value = checksum(file, jobs.store)
                if value:
                    if file.get('checksum') != value:
                        file['checksum'] = value
                        changed = True
                    db.execute('INSERT OR IGNORE INTO documents VALUES (?,?)', (value, value))
                    priority = {'ready': 4, 'processing': 3, 'queued': 2, 'failed': 1}
                    if value not in known or priority.get(previous['status'], 0) > priority.get(known[value]['status'], 0):
                        known[value] = previous
            if changed:
                db.execute('UPDATE jobs SET data=? WHERE id=?', (compact(previous), previous['id']))
        kept, duplicates = [], []
        for file in job['files']:
            value = file['checksum']
            db.execute('INSERT OR IGNORE INTO documents VALUES (?,?)', (value, value))
            file['documentId'] = value
            if value in known:
                previous = known[value]
                duplicates.append(dict(name=file['name'], documentId=value, jobId=previous['id'], status=previous['status']))
            else:
                kept.append(file)
                known[value] = job
        if kept:
            if sum(j['status'] in ('queued', 'processing') for j in existing) >= 3:
                raise AppError('Upload queue is full. Please try again later.', 429)
            job = {**job, 'files': kept, 'ingestionVersion': PARSER_VERSION}
            ordinal = db.execute('SELECT COALESCE(MAX(ordinal),0)+1 FROM jobs').fetchone()[0]
            db.execute('INSERT INTO jobs VALUES (?,?,?)', (job['id'], compact(job), ordinal))
        else:
            job = next(j for j in existing if j['id'] == duplicates[0]['jobId'])
        return job, duplicates, bool(kept)


def inventory(store, jobs):
    """Read local artifacts only; does not infer that any namespace is deletable."""
    documents = []
    for job in jobs:
        config = AIConfig.model_validate(job['aiConfig']) if job.get('aiConfig') else None
        for file in job.get('files', []):
            documents.append(dict(
                checksum=checksum(file, store), name=file['name'], fileId=file['id'],
                jobId=job['id'], status=job['status'],
                provider=config.provider if config else job.get('provider', 'openai'),
                embeddingFingerprint=config.fingerprint if config else None,
                namespaces=job.get('namespaces', {}), strategies=job.get('strategies', []),
                ingestionVersion=job.get('ingestionVersion', 'legacy/unspecified'),
            ))
    return dict(schemaVersion=1, remoteInspected=False, documents=documents,
                message='Compare both runtime inventories before inspecting or deleting Pinecone records.')


def compare_inventories(local, other):
    """Identify content overlap without treating an entire batch as redundant."""
    if other.get('schemaVersion') != 1 or not isinstance(other.get('documents'), list):
        raise AppError('Expected a schemaVersion=1 library inventory.')
    overlaps = []
    for document in local['documents']:
        value = document['checksum']
        if not value:
            continue
        matches = [item for item in other['documents'] if item.get('checksum') == value]
        if matches:
            overlaps.append(dict(
                checksum=value, local=document, other=matches,
                sameEmbeddingCandidates=[item for item in matches if document['embeddingFingerprint'] and
                                         item.get('embeddingFingerprint') == document['embeddingFingerprint']],
            ))
    return dict(schemaVersion=1, contentOverlaps=overlaps, remoteInspected=False,
                safeToDelete=[], message='Overlap is not a deletion plan. Inspect parser/strategy variants and all namespace members first.')
