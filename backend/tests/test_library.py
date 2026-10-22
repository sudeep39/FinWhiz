from concurrent.futures import ThreadPoolExecutor
import uuid

from fastapi.testclient import TestClient
from finwhiz.api import create_app
from finwhiz.config import digest
from finwhiz.library import accept_upload, inventory, compare_inventories
from finwhiz.storage import Jobs, Storage


def upload_job(settings, store, data=b'same document', provider='openai'):
    file_id = str(uuid.uuid4())
    key = f'upload/{file_id}'
    store.put(key, data)
    return dict(id=str(uuid.uuid4()), status='queued', strategies=['fixed', 'semantic'],
                aiConfig=settings.provider(provider).model_dump(), files=[dict(
                    id=file_id, key=key, name='file.txt', size=len(data), checksum=digest(data))])


def test_concurrent_uploads_claim_content_once(settings):
    store = Storage(settings.finwhiz_data_dir)
    jobs = Jobs(store)
    requests = [upload_job(settings, store) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda job: accept_upload(jobs, job), requests))
    assert sum(created for _, _, created in results) == 1
    assert len(jobs.all()) == 1


def test_variants_legacy_reuse_and_failed_retry(settings):
    store = Storage(settings.finwhiz_data_dir)
    jobs = Jobs(store)
    old = upload_job(settings, store)
    old['files'][0].pop('checksum')  # Existing runtimes have no upload checksum.
    jobs.add(old)
    result, duplicates, created = accept_upload(jobs, upload_job(settings, store))
    assert not created and result['id'] == old['id'] and duplicates
    assert result['files'][0]['checksum'] == digest(b'same document')
    jobs.update(old['id'], status='failed')
    result, _, created = accept_upload(jobs, upload_job(settings, store))
    assert not created and result['status'] == 'failed'
    assert jobs.retry(old['id'])['id'] == old['id']
    assert accept_upload(jobs, upload_job(settings, store, provider='nebius'))[2]
    changed = upload_job(settings, store)
    changed['aiConfig']['embeddingModel'] = 'different-model'
    assert accept_upload(jobs, changed)[2]
    report = inventory(store, jobs.all())
    assert len(report['documents']) == 3 and not report['remoteInspected']
    comparison = compare_inventories(report, report)
    assert len(comparison['contentOverlaps']) == 3
    assert not comparison['safeToDelete']


def test_new_parser_is_a_separate_variant(settings):
    store = Storage(settings.finwhiz_data_dir)
    jobs = Jobs(store)
    old = upload_job(settings, store)
    old['ingestionVersion'] = 'different-parser-version'
    jobs.add(old)
    accepted, _, created = accept_upload(jobs, upload_job(settings, store))
    assert created and accepted['files'][0]['documentId'] == digest(b'same document')


def test_mixed_batch_and_repeat_at_full_queue(settings):
    configured = settings.model_copy(update={'openai_api_key': 'offline', 'pinecone_api_key': 'offline'})
    app = create_app(configured, disable_workers=True)
    headers = {'Authorization': 'Bearer baseline-password'}
    with TestClient(app) as client:
        def upload(contents):
            return client.post('/api/uploads', headers=headers,
                               files=[(f'file_{i}', ('filing.txt', data)) for i, data in enumerate(contents)])
        first = upload([b'a', b'a'])
        assert first.status_code == 202
        assert len(first.json()['job']['files']) == 1
        assert len(first.json()['duplicates']) == 1
        mixed = upload([b'a', b'b'])
        assert mixed.status_code == 202 and len(mixed.json()['job']['files']) == 1
        assert upload([b'c']).status_code == 202
        assert upload([b'a']).status_code == 200  # Reuse works even when queue is full.
        before = set(settings.finwhiz_data_dir.iterdir())
        assert upload([b'd']).status_code == 429
        assert set(settings.finwhiz_data_dir.iterdir()) == before
        assert len(app.state.jobs.all()) == 3
