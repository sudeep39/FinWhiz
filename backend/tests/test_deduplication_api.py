import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse
import pytest
from fastapi.testclient import TestClient

from finwhiz.api import create_app
from finwhiz.config import AppError, digest
from finwhiz.storage import Jobs, Storage
from finwhiz.deduplication import preview

REMOVE, KEEP = '1' * 32, '2' * 32
OLD_SOURCE, NEW_SOURCE = 'a' * 32, 'b' * 32
HEADERS = {'Authorization': 'Bearer baseline-password'}


class FakeVectors:
    def __init__(self, settings, config):
        self.settings, self.config = settings, config
        self.records = {'remove': {'old': dict(id='old', values=[1.0], metadata={'text': 'source'})},
                        'keep': {'new': dict(id='new', values=[1.0], metadata={'text': 'source'})}}
        self.deleted = []
        self.fail_backup = self.fail_delete = False
        self.entered = self.release = None

    def pinecone_host(self):
        return 'fixture.pinecone.io'

    def request(self, url, **kwargs):
        query = parse_qs(urlparse(url).query)
        if self.entered:
            self.entered.set()
            assert self.release.wait(5)
        namespace = query['namespace'][0]
        if self.fail_backup and namespace == 'remove':
            return {'vectors': {}}
        return {'vectors': {key: self.records[namespace][key] for key in query['ids'] if key in self.records[namespace]}}

    def pinecone(self, path, body):
        if path == 'describe_index_stats':
            return {'namespaces': {key: {'vectorCount': len(rows)} for key, rows in self.records.items()}}
        assert path == 'vectors/delete'
        if self.fail_delete:
            self.fail_delete = False
            raise AppError('Injected delete failure', 409)
        self.deleted.append(body)
        for key in body['ids']:
            self.records[body['namespace']].pop(key, None)
        return {}

    def close(self):
        pass


def setup(settings):
    store = Storage(settings.finwhiz_data_dir)
    jobs = Jobs(store)
    data = b'The same source document.'
    for job_id, file_id, namespace, chunk_id in [(REMOVE, OLD_SOURCE, 'remove', 'old'), (KEEP, NEW_SOURCE, 'keep', 'new')]:
        key = 'upload/' + file_id
        store.put(key, data)
        jobs.add(dict(id=job_id, status='ready', aiConfig=settings.provider().model_dump(), strategies=['fixed'],
                      namespaces={'fixed': namespace}, summaries=[],
                      files=[dict(id=file_id, key=key, name='source.txt', size=len(data))],
                      documents=[dict(id=file_id, checksum=digest(data), ticker='TSLA', company='Tesla',
                                      form='10-Q', reportDate='2026-06-30', sourceUrl='/api/sources/' + file_id)]))
        store.put_json(f'chunks/{job_id}/fixed', [dict(id=chunk_id, metadata=dict(parser_version='js-v1', doc_id=file_id))])
    provider = FakeVectors(settings, settings.provider())
    app = create_app(settings, disable_workers=True, provider_factory=lambda *args: provider)
    return app, provider


def test_preview_confirm_apply_and_source_alias(settings):
    app, provider = setup(settings)
    path = f'/api/jobs/{REMOVE}/deduplicate'
    with TestClient(app) as client:
        assert client.post(path, json={'keepJobId': KEEP}).status_code == 401
        plan = client.post(path, headers=HEADERS, json={'keepJobId': KEEP}).json()
        assert plan['status'] == 'preview' and plan['deleteVectorCount'] == 1
        assert not provider.deleted and 'ids' not in plan['namespaces'][0]
        assert client.post(path, headers=HEADERS, json={'keepJobId': KEEP, 'apply': True}).status_code == 409
        body = dict(keepJobId=KEEP, apply=True, confirmToken=plan['confirmToken'])
        response = client.post(path, headers=HEADERS, json=body)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'completed'
        assert provider.records['keep'] and not provider.records['remove']
        state = client.get('/api/state', headers=HEADERS).json()
        assert len(state['documents']) == 1
        assert client.get('/api/sources/' + OLD_SOURCE, headers=HEADERS).content == b'The same source document.'
        merged = next(job for job in app.state.jobs.all() if job['id'] == REMOVE)
        assert merged['namespaces'] == {} and merged['files'][0]['key'] == 'upload/' + NEW_SOURCE
        operation = app.state.store.json(response.json()['backupManifestKey'])
        assert operation['originalJob']['documents'] and operation['backups']
        # Applying the same operation again is safe and performs no more deletes.
        assert client.post(path, headers=HEADERS, json=body).json()['status'] == 'completed'
        assert len(provider.deleted) == 1


def test_mismatch_and_stale_confirmation_are_rejected(settings):
    app, provider = setup(settings)
    jobs = app.state.jobs
    token = preview(jobs, REMOVE, KEEP)['confirmToken']
    jobs.update(KEEP, detail='changed since preview')
    with TestClient(app) as client:
        response = client.post(f'/api/jobs/{REMOVE}/deduplicate', headers=HEADERS,
                               json=dict(keepJobId=KEEP, apply=True, confirmToken=token))
        assert response.status_code == 409 and not provider.deleted
    keep = next(job for job in jobs.all() if job['id'] == KEEP)
    keep['documents'][0]['checksum'] = '0' * 64
    jobs.update(KEEP, documents=keep['documents'])
    with pytest.raises(AppError, match='identical'):
        preview(jobs, REMOVE, KEEP)


@pytest.mark.parametrize('failure', ['backup', 'delete'])
def test_failure_safety_and_resume(settings, failure):
    app, provider = setup(settings)
    setattr(provider, 'fail_' + failure, True)
    path = f'/api/jobs/{REMOVE}/deduplicate'
    with TestClient(app) as client:
        plan = client.post(path, headers=HEADERS, json={'keepJobId': KEEP}).json()
        body = dict(keepJobId=KEEP, apply=True, confirmToken=plan['confirmToken'])
        assert client.post(path, headers=HEADERS, json=body).status_code == 409
        assert provider.records['keep'] and provider.records['remove']
        removed = next(job for job in app.state.jobs.all() if job['id'] == REMOVE)
        if failure == 'backup':
            assert removed['documents'] and removed['namespaces']
        else:
            assert not removed['documents'] and not removed['namespaces']
        setattr(provider, 'fail_' + failure, False)
        assert client.post(path, headers=HEADERS, json=body).json()['status'] == 'completed'


def test_concurrent_writes_are_blocked_during_maintenance(settings):
    app, provider = setup(settings)
    provider.entered, provider.release = threading.Event(), threading.Event()
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        path = f'/api/jobs/{REMOVE}/deduplicate'
        plan = client.post(path, headers=HEADERS, json={'keepJobId': KEEP}).json()
        future = pool.submit(client.post, path, headers=HEADERS,
                             json=dict(keepJobId=KEEP, apply=True, confirmToken=plan['confirmToken']))
        try:
            assert provider.entered.wait(5)
            assert client.post('/api/uploads', headers=HEADERS).status_code == 409
            assert client.post('/api/chat', headers=HEADERS, json={}).status_code == 409
        finally:
            provider.release.set()
        assert future.result().status_code == 200


def test_resume_after_registry_commit_before_manifest_phase_update(settings):
    app, provider = setup(settings)
    provider.fail_delete = True
    path = f'/api/jobs/{REMOVE}/deduplicate'
    with TestClient(app) as client:
        plan = client.post(path, headers=HEADERS, json={'keepJobId': KEEP}).json()
        body = dict(keepJobId=KEEP, apply=True, confirmToken=plan['confirmToken'])
        assert client.post(path, headers=HEADERS, json=body).status_code == 409
        key = 'deduplication/' + plan['confirmToken']
        manifest = app.state.store.json(key)
        manifest['status'] = 'backed_up'  # Simulate exit between registry commit and manifest update.
        app.state.store.put_json(key, manifest)
        assert client.post(path, headers=HEADERS, json=body).json()['status'] == 'completed'


def test_corrupted_backup_blocks_resumed_deletion(settings):
    app, provider = setup(settings)
    provider.fail_delete = True
    path = f'/api/jobs/{REMOVE}/deduplicate'
    with TestClient(app) as client:
        plan = client.post(path, headers=HEADERS, json={'keepJobId': KEEP}).json()
        body = dict(keepJobId=KEEP, apply=True, confirmToken=plan['confirmToken'])
        assert client.post(path, headers=HEADERS, json=body).status_code == 409
        manifest = app.state.store.json('deduplication/' + plan['confirmToken'])
        app.state.store.put(manifest['backups'][0]['key'], b'corrupt')
        response = client.post(path, headers=HEADERS, json=body)
        assert response.status_code == 409 and 'integrity' in response.json()['error']
        assert not provider.deleted and provider.records['keep'] and provider.records['remove']


def test_parser_mismatch_and_absent_workspace_password_are_rejected(settings):
    app, provider = setup(settings)
    rows = app.state.store.json(f'chunks/{REMOVE}/fixed')
    rows[0]['metadata']['parser_version'] = 'other-version'
    app.state.store.put_json(f'chunks/{REMOVE}/fixed', rows)
    with pytest.raises(AppError, match='parser'):
        preview(app.state.jobs, REMOVE, KEEP)
    unprotected = settings.model_copy(update={'app_password': ''})
    with TestClient(create_app(unprotected, disable_workers=True)) as client:
        assert client.post(f'/api/jobs/{REMOVE}/deduplicate', json={'keepJobId': KEEP}).status_code == 403
