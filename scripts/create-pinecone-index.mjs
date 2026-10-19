import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

import { aiIndexConfig, providerConfig } from '../lib/ai-config.mjs';
export const apiVersion = '2025-10';
export function validateExisting(index, config) {
  for (const field of ['name', 'vector_type', 'dimension', 'metric']) {
    if (index[field] !== config[field]) throw new Error(`Existing index ${field} does not match configuration. Choose another index name; no changes were made.`);
  }
  for (const field of ['cloud', 'region']) {
    if (index.spec?.serverless?.[field] !== config.spec.serverless[field]) throw new Error(`Existing index ${field} does not match configuration. Choose another index name; no changes were made.`);
  }
  if (index.tags?.['embedding-model'] !== config.tags['embedding-model']) throw new Error('Existing index embedding-model tag does not match. Review its embedding provenance; no changes were made.');
  if (index.tags?.['embedding-provider'] && index.tags['embedding-provider'] !== config.tags['embedding-provider']) throw new Error('Existing index embedding-provider tag does not match; no changes were made.');
  if (index.deletion_protection !== config.deletion_protection) throw new Error('Existing index deletion protection differs. Review it in Pinecone; no changes were made.');
}

export async function ensureIndex(config, { apiKey, fetchImpl = fetch, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)), attempts = 60, log = console.log } = {}) {
  if (!apiKey?.trim()) throw new Error('Set PINECONE_API_KEY in your environment before using --apply.');
  const headers = { 'Api-Key': apiKey, 'Content-Type': 'application/json', 'X-Pinecone-Api-Version': apiVersion };
  const endpoint = `https://api.pinecone.io/indexes/${encodeURIComponent(config.name)}`;
  async function request(url, method = 'GET', body) {
    const response = await fetchImpl(url, { method, headers, ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(30000), redirect: 'error' });
    // Do not print request headers, API keys, or arbitrary error-response bodies.
    if (!response.ok && response.status !== 404 && response.status !== 409) throw new Error(`Pinecone ${method} returned HTTP ${response.status}. Check project permissions, quotas, and region availability. Rerun to inspect any index already created.`);
    return response;
  }
  let response = await request(endpoint);
  if (response.status === 404) {
    log(`Creating ${config.name}...`);
    const created = await request('https://api.pinecone.io/indexes', 'POST', config);
    if (!created.ok && created.status !== 409) throw new Error(`Index creation returned HTTP ${created.status}.`);
    // A 409 may mean another process just created it. Describe and validate it.
  } else if (response.ok) {
    validateExisting(await response.json(), config);
    log(`Reusing matching index ${config.name}.`);
  } else throw new Error(`Index lookup returned HTTP ${response.status}.`);
  for (let attempt = 0; attempt < attempts; attempt++) {
    response = await request(endpoint);
    if (response.ok) {
      const index = await response.json();
      validateExisting(index, config);
      if (index.status?.ready) {
        if (!index.host) throw new Error('Ready index response is missing its data-plane host.');
        return index;
      }
      log(`Waiting: ${index.status?.state || 'initializing'}`);
    } else if (response.status !== 404) throw new Error(`Index lookup returned HTTP ${response.status}.`);
    await sleep(5000);
  }
  throw new Error('Index did not become ready within the polling window. It may still be provisioning; rerun to check it.');
}

async function main() {
  const args = process.argv.slice(2);
  if (args.some(arg => !['--apply', '--dry-run'].includes(arg)&&!arg.startsWith('--provider=')) || (args.includes('--apply') && args.includes('--dry-run'))) throw new Error('Usage: node scripts/create-pinecone-index.mjs [--dry-run | --apply]');
  const config = aiIndexConfig(providerConfig(args.find(a=>a.startsWith('--provider='))?.slice(11)));
  if (!args.includes('--apply')) {
    console.log(`Dry run: POST https://api.pinecone.io/indexes (API ${apiVersion})`);
    console.log(JSON.stringify(config, null, 2));
    console.log('No network requests made. Use --apply to create or validate the index.');
    return;
  }
  const index = await ensureIndex(config, { apiKey: process.env.PINECONE_API_KEY });
  console.log(JSON.stringify({ name: index.name, host: index.host, ready: index.status.ready, dimension: index.dimension, metric: index.metric, plannedNamespace: 'sec-filings-v1' }, null, 2));
  console.log('Index ready. The namespace is created on first upsert; no vectors have been uploaded.');
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main().catch(error => { console.error(error.message); process.exitCode = 1; });
