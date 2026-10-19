import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { Worker } from 'node:worker_threads';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { activeAI, withAIProvider, providerConfig, embeddingFingerprint, jobMatchesAI } from './lib/ai-config.mjs';
import { embed, structured, validateSchema, upsertVectors, pineconeHost } from './lib/providers.mjs';
import { Storage } from './lib/storage.mjs';
import { createApplication } from './lib/web-server.mjs';
import { createProviderFixture } from './scripts/provider-fixture.mjs';
import { research } from './lib/retrieval.mjs';

const envKeys=['AI_PROVIDER','NEBIUS_API_KEY','OPENAI_API_KEY','PINECONE_API_KEY','APP_PASSWORD','NODE_ENV','REPLIT_DEPLOYMENT'];
function fakeKeys(){const saved=Object.fromEntries(envKeys.map(k=>[k,process.env[k]]));process.env.OPENAI_API_KEY='offline-openai';process.env.NEBIUS_API_KEY='offline-nebius';process.env.PINECONE_API_KEY='offline-pinecone';delete process.env.AI_PROVIDER;delete process.env.APP_PASSWORD;delete process.env.REPLIT_DEPLOYMENT;process.env.NODE_ENV='test';return ()=>{for(const k of envKeys)saved[k]===undefined?delete process.env[k]:process.env[k]=saved[k];};}
test('provider scopes stay isolated across concurrent requests and fingerprints',async()=>{
  const restore=fakeKeys();try{
    await Promise.all(['openai','nebius'].map(id=>withAIProvider(id,async()=>{await new Promise(r=>setTimeout(r,10));assert.equal(activeAI().provider,id);})));
    assert.notEqual(embeddingFingerprint(providerConfig('openai')),embeddingFingerprint(providerConfig('nebius')));
    assert.equal(jobMatchesAI({aiConfig:providerConfig('openai')},providerConfig('nebius')),false);
    assert.equal(jobMatchesAI({},providerConfig('nebius')),false);
    assert.throws(()=>withAIProvider('untrusted',()=>{}),/Choose/);
  }finally{restore();}
});
test('both providers route embeddings and JSON generation; missing keys never fall back',async()=>{
  const restore=fakeKeys(),original=globalThis.fetch,fixture=createProviderFixture();globalThis.fetch=fixture.fetch;
  try{
    for(const id of ['openai','nebius'])await withAIProvider(id,async()=>{
      const [v]=await embed(['revenue'],{purpose:'query'});assert.equal(v.length,id==='nebius'?4096:1536);
      await structured('Return evidence order',JSON.stringify({candidates:[{id:'a',text:'test'}]}),'rank_evidence',{type:'object',properties:{ids:{type:'array',items:{type:'string'}}},required:['ids'],additionalProperties:false});
    });
    const neb=fixture.requests.filter(r=>r.url.startsWith('https://api.tokenfactory.nebius.com/'));
    assert.equal(neb.length,2);assert.match(neb[0].body.input[0],/^Instruct:/);assert.equal(neb[1].body.model,providerConfig('nebius').chatModel);
    delete process.env.NEBIUS_API_KEY;await assert.rejects(withAIProvider('nebius',()=>embed(['test'])),/NEBIUS_API_KEY/);assert.equal(fixture.requests.length,4);
    assert.throws(()=>validateSchema({ids:[2]},{type:'object',properties:{ids:{type:'array',items:{type:'string'}}},required:['ids']}),/schema/);
  }finally{globalThis.fetch=original;restore();}
});
test('HTTP provider selection pins uploads and isolates visible corpora without exposing keys',async()=>{
  const restore=fakeKeys(),directory=await mkdtemp(join(tmpdir(),'finwhiz-provider-'));let server;
  try{
    server=await createApplication({disableWorkers:true,storage:{directory}});server.listen(0,'127.0.0.1');await once(server,'listening');const base=`http://127.0.0.1:${server.address().port}`;
    const headers={'X-AI-Provider':'nebius'};
    const initial=await (await fetch(base+'/api/state',{headers})).json();assert.equal(initial.config.aiProvider,'nebius');assert.equal(initial.config.providersConfigured,true);assert.deepEqual(initial.companies,[]);assert.doesNotMatch(JSON.stringify(initial),/offline-nebius|offline-openai/);
    assert.equal((await fetch(base+'/api/state',{headers:{'X-AI-Provider':'unknown'}})).status,400);
    const form=new FormData();form.append('file_0',new Blob(['financial fixture '.repeat(20)]),'test.txt');form.append('details',JSON.stringify([{ticker:'ACME',form:'10-Q',reportDate:'2025-03-31'}]));
    const result=await fetch(base+'/api/uploads',{method:'POST',headers,body:form});assert.equal(result.status,202);const job=(await result.json()).job;assert.equal(job.aiConfig.provider,'nebius');assert.equal(job.aiConfig.dimension,4096);
    await new Promise(r=>server.close(r));server=null;
    const store=await new Storage({directory}).init(),state=await store.json('state');Object.assign(state.jobs[0],{status:'ready',documents:[{ticker:'ACME',company:'Example',reportDate:'2025-03-31',form:'10-Q'}]});await store.putJson('state',state);
    server=await createApplication({disableWorkers:true,storage:{directory}});server.listen(0,'127.0.0.1');await once(server,'listening');const next=`http://127.0.0.1:${server.address().port}`;
    assert.equal((await (await fetch(next+'/api/state',{headers})).json()).companies[0].ticker,'ACME');assert.deepEqual((await (await fetch(next+'/api/state')).json()).companies,[]);
    delete process.env.NEBIUS_API_KEY;const missing=await fetch(next+'/api/uploads',{method:'POST',headers,body:new FormData()});assert.equal(missing.status,503);
  }finally{if(server)await new Promise(r=>server.close(r));restore();await rm(directory,{recursive:true,force:true});}
});
test('research rejects an index with another embedding space even when dimensions match',async()=>{
  const restore=fakeKeys(),original=globalThis.fetch;
  try{
    globalThis.fetch=async()=>new Response(JSON.stringify({dimension:1536,metric:'cosine',vector_type:'dense',tags:{'embedding-model':'text-embedding-3-large'},status:{ready:true},host:'fixture.svc.pinecone.io'}),{status:200});
    const config={...providerConfig('nebius'),dimension:1536,indexName:'isolated-incompatible-index'};
    await assert.rejects(withAIProvider(config,()=>pineconeHost()),/provenance/);
  }finally{globalThis.fetch=original;restore();}
});
test('Nebius worker handles both chunkers and research without any OpenAI calls',async()=>{
  const restore=fakeKeys(),directory=await mkdtemp(join(tmpdir(),'finwhiz-nebius-')),store=await new Storage({directory}).init(),original=globalThis.fetch;let worker;
  try{
    const file={id:'doc',key:'upload/doc',name:'test.txt',details:{ticker:'ACME',form:'10-Q',reportDate:'2025-03-31'}};
    await store.put(file.key,'Revenue was 125 million and costs were 50 million. This is a synthetic test document containing financial results only for offline verification. '.repeat(80));
    const job={id:'nebius-corpus',aiConfig:providerConfig('nebius'),strategies:['fixed','semantic'],files:[file]};
    worker=new Worker(new URL('./lib/ingest.worker.mjs',import.meta.url),{workerData:{job,storage:{directory},offlineTest:true},execArgv:['--import',new URL('./scripts/provider-fixture.mjs',import.meta.url).href]});
    const completed=await new Promise((resolve,reject)=>{worker.on('message',m=>{if(m.type==='complete')resolve(m);if(m.type==='failed')reject(new Error(m.error));});worker.on('error',reject);worker.on('exit',code=>{if(code)reject(new Error(`Worker failed: ${code}`));});});
    const fixture=createProviderFixture();for(const strategy of job.strategies){const records=await store.json(`chunks/${job.id}/${strategy}`);assert.ok(records.length);assert.ok(records.every(r=>r.metadata.embedding_provider==='nebius'&&r.metadata.embedding_dimensions===4096));fixture.namespaces.set(completed.namespaces[strategy],new Map(records.map(r=>[r.id,r])));}
    globalThis.fetch=fixture.fetch;
    const answer=await withAIProvider('nebius',()=>research({store,jobs:[{...job,...completed,status:'ready'}],question:'What was revenue?',tickers:['ACME'],rerank:true}));
    assert.equal(answer.status,'answered');assert.equal(answer.metrics.aiProvider,'nebius');assert.ok(fixture.calls.filter(u=>u.includes('/chat/completions')).length===2);assert.ok(fixture.calls.every(u=>!u.startsWith('https://api.openai.com/')));
    await withAIProvider('nebius',()=>upsertVectors('sized',Array.from({length:24},(_,i)=>({id:String(i),values:Array(4096).fill(.12345678912345678),metadata:{text:'x'.repeat(30000)}}))));
    assert.ok(fixture.requests.filter(r=>r.url.endsWith('/vectors/upsert')).every(r=>Buffer.byteLength(JSON.stringify(r.body))<1_800_000));
  }finally{globalThis.fetch=original;await worker?.terminate();restore();await rm(directory,{recursive:true,force:true});}
});
