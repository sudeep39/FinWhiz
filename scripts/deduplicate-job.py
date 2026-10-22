"""Authenticated preview/apply client for the Python duplicate-merge API."""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))
from finwhiz.config import ROOT, Settings
import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove-job', required=True)
    parser.add_argument('--keep-job', required=True)
    parser.add_argument('--backend')
    parser.add_argument('--provider', choices=['openai', 'nebius'], default='nebius')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-token')
    args = parser.parse_args()
    settings = Settings(_env_file=ROOT / '.env')
    origin = args.backend or settings.finwhiz_backend_url or 'http://127.0.0.1:8000'
    parsed = urlparse(origin)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        parser.error('backend must be an HTTP(S) origin without credentials or a path')
    if any(not re.fullmatch(r'[a-f0-9-]{1,64}', value) for value in (args.remove_job, args.keep_job)):
        parser.error('invalid job ID')
    if args.apply and not re.fullmatch(r'[a-f0-9]{64}', args.confirm_token or ''):
        parser.error('--apply requires --confirm-token from a fresh preview')
    password = settings.finwhiz_shared_backend_password if settings.finwhiz_backend_url and origin.rstrip('/') == settings.finwhiz_backend_url.rstrip('/') else ''
    password = password or settings.app_password
    if not password:
        parser.error('configure the target workspace password in .env before using maintenance APIs')
    body = dict(keepJobId=args.keep_job, apply=args.apply)
    if args.confirm_token:
        body['confirmToken'] = args.confirm_token
    with httpx.Client(timeout=180) as client:
        response = client.post(origin.rstrip('/') + f'/api/jobs/{args.remove_job}/deduplicate',
                               json=body, headers={'Authorization': 'Bearer ' + password,
                                                   'X-AI-Provider': args.provider})
    if response.status_code == 404:
        raise RuntimeError('Endpoint or job not found. Deploy the Python maintenance API to the correct migrated runtime first.')
    if not response.is_success:
        try:
            error = response.json().get('error', 'Request failed')
        except ValueError:
            error = 'Request failed'
        raise RuntimeError(f'HTTP {response.status_code}: {error}')
    print(json.dumps(response.json(), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, httpx.HTTPError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
