import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { ensureIndex } from './scripts/create-pinecone-index.mjs';
const config = JSON.parse(await readFile(new URL('./pinecone/index.json', import.meta.url)));
const ready = { ...config, host: 'example.svc.pinecone.io', status: { ready: true } };
const response = (status, body) => new Response(JSON.stringify(body), { status });
const options = { apiKey: 'mock-test-key', sleep: async () => {}, log: () => {} };

test('creates a missing index and waits for readiness', async () => {
  const replies = [response(404, {}), response(201, {}), response(200, { ...ready, status: { ready: false, state: 'Initializing' } }), response(200, ready)];
  const calls = [];
  const index = await ensureIndex(config, { ...options, fetchImpl: async (url, init) => { calls.push({ url, ...init }); return replies.shift(); } });
  assert.equal(index.host, ready.host);
  assert.equal(calls[1].method, 'POST');
  assert.deepEqual(JSON.parse(calls[1].body), config);
  assert.equal(calls[1].headers['X-Pinecone-Api-Version'], '2025-10');
  assert.equal(calls.length, 4);
});
test('reuses a matching index without mutation', async () => {
  await ensureIndex(config, { ...options, fetchImpl: async (_url, init) => { assert.equal(init.method, 'GET'); return response(200, ready); } });
});
test('rejects an incompatible existing index without mutation', async () => {
  await assert.rejects(ensureIndex(config, { ...options, fetchImpl: async (_url, init) => { assert.equal(init.method, 'GET'); return response(200, { ...ready, dimension: 3072 }); } }), /dimension/);
});
test('requires a key and stops on authentication failures', async () => {
  await assert.rejects(ensureIndex(config, {}), /PINECONE_API_KEY/);
  await assert.rejects(ensureIndex(config, { ...options, fetchImpl: async () => response(401, {}) }), /HTTP 401/);
});
test('reports a provisioning timeout', async () => {
  await assert.rejects(ensureIndex(config, { ...options, attempts: 1, fetchImpl: async () => response(200, { ...ready, status: { ready: false } }) }), /polling window/);
});
