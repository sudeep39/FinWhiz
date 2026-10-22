// Offline migration oracle. Never loads .env or writes the application runtime.
import { readFile, writeFile, mkdir, mkdtemp, readdir, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { once } from 'node:events';
import { createApplication } from '../lib/web-server.mjs';
import { extractHtml, makeParents, splitParent } from '../lib/chunking.mjs';
import { bm25, fuse, calculate, supportsNumber } from '../lib/retrieval.mjs';
import { embeddingFingerprint, providerConfig } from '../lib/ai-config.mjs';

const root = new URL('../', import.meta.url);
const output = new URL('specs/001-python-backend/baseline/', root);
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const check = process.argv.includes('--check');
async function artifact(name, value) {
  const bytes = JSON.stringify(value, null, 2) + '\n';
  if (check) {
    if (await readFile(new URL(name, output), 'utf8') !== bytes) throw new Error(`Baseline changed: ${name}`);
  } else await writeFile(new URL(name, output), bytes);
}
await mkdir(output, { recursive: true });
// Explicit allowlist excludes credentials, uploads, caches and generated artifacts.
const paths = ['package.json', 'package-lock.json', 'requirements.txt', 'server.mjs', 'app.mjs', 'index.html', 'style.css', 'experiment.json', 'quality-policy.json', 'pinecone/index.json', 'evaluation/benchmark.json'];
for (const directory of ['lib', 'scripts']) {
  for (const name of (await readdir(new URL(directory + '/', root))).sort()) {
    if (/\.(mjs|py)$/.test(name) && name !== 'capture-migration-baseline.mjs') paths.push(`${directory}/${name}`);
  }
}
for (const name of await readdir(root)) if (name.endsWith('.test.mjs')) paths.push(name);
const inventory = [];
for (const path of paths.sort()) inventory.push({ path, sha256: sha(await readFile(new URL(path, root))) });
await artifact('source-manifest.json', inventory);

const html = '<html><body><h2>Item 7. Results</h2><p>Revenue and operating cash flow reflect the fiscal year ended December 31, 2025.</p><table><tr><th>Metric</th><th>2025</th><th>2024</th></tr><tr><td>Revenue</td><td>125</td><td>100</td></tr><tr><td>Capital expenditure</td><td>(20)</td><td>(15)</td></tr></table></body></html>';
const blocks = extractHtml(html), parents = makeParents(blocks), chunks = [];
for (const parent of parents) chunks.push({ parent: parent.index, fixed: await splitParent(parent, 'fixed'), semantic: await splitParent(parent, 'semantic', async texts => texts.map(() => [1, 0])) });
const rows = [{ id: 'a', metadata: { text: 'revenue growth gross margin' } }, { id: 'b', metadata: { text: 'operating cash flow capital expenditure' } }];
await artifact('domain.json', { html, blocks, parents, chunks, rows, bm25: bm25(rows, 'capital expenditure'), fused: fuse([[rows[0], rows[1]], [rows[1]]]), calculations: { percentChange: calculate('percent_change', [125, 100]), cashFlow: calculate('sum', [100, -20]), negativeEvidence: supportsNumber('Capital expenditure (20)', -20) } });

const directory = await mkdtemp(join(tmpdir(), 'finwhiz-baseline-'));
const keys = ['AI_PROVIDER', 'APP_PASSWORD', 'OPENAI_API_KEY', 'NEBIUS_API_KEY', 'PINECONE_API_KEY', 'REPLIT_DEPLOYMENT', 'NODE_ENV', 'NEBIUS_CHAT_MODEL', 'NEBIUS_JUDGE_MODEL', 'NEBIUS_EMBEDDING_MODEL', 'NEBIUS_EMBEDDING_DIMENSIONS', 'PINECONE_NEBIUS_INDEX', 'PINECONE_INDEX', 'OPENAI_CHAT_MODEL', 'OPENAI_JUDGE_MODEL'];
const previous = Object.fromEntries(keys.map(key => [key, process.env[key]]));
let server;
try {
  for (const key of keys) delete process.env[key];
  Object.assign(process.env, { AI_PROVIDER: 'openai', APP_PASSWORD: 'baseline-password' });
  server = await createApplication({ disableWorkers: true, storage: { directory } });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const base = `http://127.0.0.1:${server.address().port}`;
  const cases = [
    ['health', '/api/health', {}],
    ['unauthorized', '/api/state', { headers: { Authorization: '' } }],
    ['empty-state', '/api/state', {}],
    ['nebius-state', '/api/state', { headers: { 'X-AI-Provider': 'nebius' } }],
    ['invalid-provider', '/api/state', { headers: { 'X-AI-Provider': 'invalid' } }],
    ['unknown-endpoint', '/api/missing', {}],
    ['missing-source', '/api/sources/00000000-0000-0000-0000-000000000000', {}],
    ['invalid-question', '/api/chat', { method: 'POST', body: '{}' }],
    ['malformed-json', '/api/chat', { method: 'POST', body: '{' }],
    ['unready-company', '/api/chat', { method: 'POST', body: JSON.stringify({ question: 'What is revenue?', tickers: ['ACME'] }) }],
    ['origin-rejected', '/api/uploads', { method: 'POST', headers: { Origin: 'https://untrusted.example' } }],
    ['upload-unconfigured', '/api/uploads', { method: 'POST' }],
    ['retry-ineligible', '/api/jobs/00000000-0000-0000-0000-000000000000/retry', { method: 'POST' }],
  ];
  const responses = [];
  for (const [name, path, options] of cases) {
    const response = await fetch(base + path, { ...options, headers: { Authorization: 'Bearer baseline-password', ...options.headers } });
    responses.push({ name, request: { path, method: options.method || 'GET', body: options.body ?? null, headers: options.headers || {} }, status: response.status, headers: Object.fromEntries(['content-type', 'cache-control', 'x-content-type-options', 'referrer-policy', 'content-security-policy'].map(key => [key, response.headers.get(key)])), body: await response.json() });
  }
  await artifact('http.json', responses);
  await artifact('embedding-fingerprints.json', ['openai', 'nebius'].map(id => { const config = providerConfig(id); return { config, fingerprint: embeddingFingerprint(config) }; }));
} finally {
  if (server) await new Promise(done => server.close(done));
  for (const key of keys) previous[key] === undefined ? delete process.env[key] : process.env[key] = previous[key];
  if (resolve(directory).startsWith(resolve(tmpdir(), 'finwhiz-baseline-'))) await rm(directory, { recursive: true, force: true });
}
console.log(check ? 'Migration baseline matches.' : 'Migration baseline captured.');
