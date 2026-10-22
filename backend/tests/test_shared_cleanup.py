import runpy
import pytest
from finwhiz.config import ROOT, AppError
from finwhiz.api import create_app
from finwhiz.cli import ingest_corpus

cleanup = runpy.run_path(str(ROOT / 'scripts/cleanup-pinecone.py'))


def test_cleanup_protects_replit_and_active_job_prefixes():
    state = {'jobs': [dict(id='kept', status='ready', namespaces={'fixed': 'web-kept-fixed'}),
                      dict(id='failed', status='failed', namespaces={})]}
    namespaces = {'web-kept-fixed', 'web-failed-semantic', 'web-other-fixed'}
    assert cleanup['protected_namespaces']([state], namespaces) == {'web-kept-fixed', 'web-failed-semantic'}
    with pytest.raises(RuntimeError):
        cleanup['protected_namespaces']([{'jobs': [dict(id='active', status='processing')]}], namespaces)


def test_cleanup_plan_requires_authenticated_nonempty_authority():
    audit = dict(replitOrigin='https://fin-whiz.replit.app', indexes={}, remote={
        name: dict(status=401) for name in ('openai', 'nebius')})
    with pytest.raises(RuntimeError):
        cleanup['build_plan'](audit)
    for entry in audit['remote'].values():
        entry.update(status=200, state={'jobs': [dict(id='kept', status='ready', namespaces={'fixed': 'web-kept-fixed'})]})
    audit['indexes']['nebius'] = dict(index='fixture', host='fixture.pinecone.io', stats={'namespaces': {
        'web-kept-fixed': {'vectorCount': 10}, 'web-other-fixed': {'vectorCount': 5}}})
    plan = cleanup['build_plan'](audit)
    assert [row['namespace'] for row in plan['delete']] == ['web-other-fixed']
    assert [row['namespace'] for row in plan['retain']] == ['web-kept-fixed']


def test_shared_configuration_blocks_local_library_writers(settings):
    shared = settings.model_copy(update={'finwhiz_backend_url': 'https://fin-whiz.replit.app'})
    with pytest.raises(AppError, match='shared backend'):
        create_app(shared)
    with pytest.raises(AppError, match='Shared-backend mode'):
        ingest_corpus(shared, shared.provider(), [])
