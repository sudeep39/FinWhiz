import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { once } from 'node:events';
import { Worker } from 'node:worker_threads';
import { createApplication } from './lib/web-server.mjs';
import { Storage } from './lib/storage.mjs';
import { extractHtml, makeParents, splitParent, tokenCount } from './lib/chunking.mjs';
import { bm25, fuse, calculate, supportsNumber, research } from './lib/retrieval.mjs';
import { createProviderFixture } from './scripts/provider-fixture.mjs';
import { htmlMetadata, textMetadata, mergeMetadata } from './lib/filing-metadata.mjs';

test('ticker metadata comes from document contents without a company allowlist',()=>{
  const detected=htmlMetadata('<html><body><ix:nonNumeric name="dei:TradingSymbol">ADBE</ix:nonNumeric><ix:nonNumeric name="dei:EntityRegistrantName">Adobe Inc.</ix:nonNumeric><ix:nonNumeric name="dei:DocumentType">10-Q</ix:nonNumeric><ix:nonNumeric name="dei:DocumentPeriodEndDate">March 31, 2025</ix:nonNumeric></body></html>');
  assert.equal(detected.ticker,'ADBE');assert.equal(detected.company,'Adobe Inc.');assert.equal(detected.reportDate,'2025-03-31');
  assert.equal(mergeMetadata({ticker:'WRONG'},detected).ticker,'ADBE');
  assert.equal(textMetadata('Ticker: ACME\n10-K\nFiscal period end: 2025-12-31').ticker,'ACME');
});

test('JavaScript HTML extraction preserves visible facts and table period headers',()=>{
  const blocks=extractHtml('<html><head><style>hiddenstyle</style></head><body><ix:header>HIDDEN</ix:header><div style="display:none">SECRET</div><div id="results">Item 7. Management Discussion</div><p>Revenue was <ix:nonFraction>12,345</ix:nonFraction> million. This is visible financial context for the reporting period.</p><table><tr><th>Metric</th><th>2026</th><th>2025</th></tr><tr><td>Revenue</td><td>12,345</td><td>10,000</td></tr></table></body></html>');
  assert.ok(blocks.some(b=>b.kind==='table'&&b.text.includes('2026 | 2025')));
  const text=blocks.map(b=>b.text).join(' ');assert.match(text,/12,345 million/);assert.doesNotMatch(text,/SECRET|HIDDEN|hiddenstyle/);
});
test('fixed and semantic chunking are bounded and semantic decisions use embeddings',async()=>{
  const text='Revenue grew substantially in this reporting period. '.repeat(120)+'Regulatory litigation creates material operational risks. '.repeat(120);
  const parent=makeParents([{kind:'narrative',text,section:'Results',anchor:'',id:'b0'}])[0];
  const fixed=await splitParent(parent,'fixed');let embedded=0;
  const semantic=await splitParent(parent,'semantic',async texts=>{embedded+=texts.length;return texts.map(t=>t.startsWith('Regulatory')?[0,1]:[1,0]);});
  assert.ok(embedded>0);assert.ok(fixed.length>1);assert.ok(semantic.length>1);
  assert.ok([...fixed,...semantic].every(t=>tokenCount(t)<=1000));
  assert.ok(semantic.some(t=>t.includes('Regulatory')));
});
test('BM25 exact terms and rank fusion deduplicate candidates',()=>{
  const rows=[{id:'a',metadata:{text:'NVIDIA gross margin improved revenue'}},{id:'b',metadata:{text:'Tesla automotive revenue and free cash flow'}}];
  assert.equal(bm25(rows,'automotive free cash')[0].id,'b');
  const result=fuse([[rows[0],rows[1]],[rows[1]]]);assert.equal(result.length,2);assert.equal(result[0].id,'b');
});
test('financial calculations are deterministic and reject zero denominators',()=>{
  assert.equal(calculate('percent_change',[125,100]),25);assert.equal(calculate('difference',[100,30]),70);assert.equal(calculate('ratio',[50,100]),.5);assert.equal(calculate('sum',[1,2,3]),6);
  assert.throws(()=>calculate('ratio',[5,0]));assert.throws(()=>calculate('sum',[Infinity]));
  assert.equal(supportsNumber('Revenue 12,345; loss (200)',12345),true);assert.equal(supportsNumber('loss (200)',-200),true);assert.equal(supportsNumber('Revenue 12,345',12.345),false);
});
test('upload acceptance, durable job state, authentication and unready chat',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'finwhiz-web-'));
  const envKeys=['APP_PASSWORD','OPENAI_API_KEY','PINECONE_API_KEY','REPLIT_DEPLOYMENT'];const previous=Object.fromEntries(envKeys.map(k=>[k,process.env[k]]));
  let server;
  const start=async()=>{server=await createApplication({disableWorkers:true,storage:{directory}});server.listen(0,'127.0.0.1');await once(server,'listening');return `http://127.0.0.1:${server.address().port}`;};
  const stop=async()=>{await new Promise(r=>server.close(r));server=null;};
  try{
    process.env.APP_PASSWORD='offline-test-password';process.env.OPENAI_API_KEY='offline-key';process.env.PINECONE_API_KEY='offline-key';delete process.env.REPLIT_DEPLOYMENT;
    let base=await start();const headers={Authorization:'Bearer offline-test-password'};
    assert.equal((await fetch(base+'/api/state')).status,401);
    let state=await (await fetch(base+'/api/state',{headers})).json();assert.equal(state.config.storageBackend,'filesystem');assert.deepEqual(state.companies,[]);assert.deepEqual(state.documents,[]);
    const form=new FormData();form.append('details',JSON.stringify([{ticker:'NVDA',reportDate:'2025-01-26',form:'10-K'}]));form.append('file_0',new Blob(['A readable financial document for the offline upload test. '.repeat(10)]),'filing.txt');
    const upload=await fetch(base+'/api/uploads',{method:'POST',headers,body:form});assert.equal(upload.status,202);const job=(await upload.json()).job;assert.equal(job.status,'queued');assert.equal(job.files.length,1);assert.equal(job.files[0].key,undefined);
    assert.deepEqual((await (await fetch(base+'/api/state',{headers})).json()).companies,[]);
    assert.equal((await fetch(base+'/api/chat',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({question:'What is revenue?',tickers:['NVDA']})})).status,409);
    assert.equal((await fetch(base+'/api/samples',{method:'POST',headers:{...headers,Origin:'https://untrusted.example'},body:'{}'})).status,403);
    await stop();base=await start();state=await (await fetch(base+'/api/state',{headers})).json();assert.equal(state.jobs[0].id,job.id);assert.equal(state.jobs[0].status,'queued');
    const download=await fetch(base+`/api/sources/${job.files[0].id}`,{headers});assert.equal(download.status,200);assert.match(await download.text(),/offline upload test/);
    const bad=new FormData();bad.append('file_0',new Blob(['not a document']),'program.exe');assert.equal((await fetch(base+'/api/uploads',{method:'POST',headers,body:bad})).status,400);
    await stop();
    const disk=await new Storage({directory}).init();const persisted=await disk.json('state');
    Object.assign(persisted.jobs[0],{status:'ready',namespaces:{fixed:'fixture'},summaries:[],documents:[{id:job.files[0].id,ticker:'ADBE',company:'Adobe Inc.',reportDate:'2025-03-31',form:'10-Q',sourceUrl:'/api/sources/'+job.files[0].id}]});
    await disk.putJson('state',persisted);await disk.close();base=await start();
    state=await (await fetch(base+'/api/state',{headers})).json();
    assert.deepEqual(state.companies.map(c=>c.ticker),['ADBE']);assert.equal(state.companies[0].periods.length,1);assert.equal(state.companies[0].periods[0].reportDate,'2025-03-31');
  }finally{
    if(server)await stop();for(const key of envKeys)previous[key]===undefined?delete process.env[key]:process.env[key]=previous[key];
    if(resolve(directory).startsWith(resolve(tmpdir(),'finwhiz-web-')))await rm(directory,{recursive:true,force:true});
  }
});
test('local storage preserves byte buffers and structured state',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'finwhiz-web-'));const store=await new Storage({directory}).init();
  try{await store.put('upload',Buffer.from([0,255,10]));assert.deepEqual(await store.get('upload'),Buffer.from([0,255,10]));await store.putJson('state',{phase:'embedding'});assert.deepEqual(await store.json('state'),{phase:'embedding'});}finally{await store.close();if(resolve(directory).startsWith(resolve(tmpdir(),'finwhiz-web-')))await rm(directory,{recursive:true,force:true});}
});
test('real background worker completes both strategies and retrieval uses mocked providers only',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'finwhiz-web-'));const store=await new Storage({directory}).init();
  const originalFetch=globalThis.fetch,oldKey=process.env.OPENAI_API_KEY,oldPinecone=process.env.PINECONE_API_KEY;
  let worker;
  try{
    process.env.OPENAI_API_KEY='offline-test';process.env.PINECONE_API_KEY='offline-test';
    const file={id:'test-doc',key:'upload/test-doc',name:'filing.txt',details:{ticker:'NVDA',company:'NVIDIA',form:'10-Q',reportDate:'2026-07-26'}};
    await store.put(file.key,'Revenue was 125 million in this synthetic offline integration fixture. Operating income was 50 million. This text exists only in a test and is not actual company financial data. '.repeat(12));
    const job={id:'test-corpus',strategies:['fixed','semantic'],files:[file]};
    worker=new Worker(new URL('./lib/ingest.worker.mjs',import.meta.url),{workerData:{job,storage:{directory},offlineTest:true},execArgv:['--import',new URL('./scripts/provider-fixture.mjs',import.meta.url).href]});
    const complete=await new Promise((resolve,reject)=>{worker.on('message',m=>{if(m.type==='complete')resolve(m);if(m.type==='failed')reject(new Error(m.error));});worker.on('error',reject);worker.on('exit',code=>{if(code)reject(new Error(`Worker exit ${code}`));});});
    assert.equal(complete.summaries.length,2);assert.equal(complete.documents[0].ticker,'NVDA');
    const fixture=createProviderFixture();
    for(const strategy of job.strategies){const records=await store.json(`chunks/${job.id}/${strategy}`);assert.ok(records.length>0);fixture.namespaces.set(complete.namespaces[strategy],new Map(records.map(r=>[r.id,r])));}
    globalThis.fetch=fixture.fetch;
    const answer=await research({store,jobs:[{...job,...complete,status:'ready'}],question:'What was revenue?',tickers:['NVDA'],strategy:'fixed',rerank:true});
    assert.equal(answer.status,'answered');assert.ok(answer.sources.length>0);assert.match(answer.answer,/\[S1\]/);assert.ok(fixture.calls.some(c=>c.endsWith('/query')));assert.ok(fixture.calls.filter(c=>c.endsWith('/chat/completions')).length===2);
  }finally{
    globalThis.fetch=originalFetch;oldKey===undefined?delete process.env.OPENAI_API_KEY:process.env.OPENAI_API_KEY=oldKey;oldPinecone===undefined?delete process.env.PINECONE_API_KEY:process.env.PINECONE_API_KEY=oldPinecone;
    await worker?.terminate();await store.close();if(resolve(directory).startsWith(resolve(tmpdir(),'finwhiz-web-')))await rm(directory,{recursive:true,force:true});
  }
});
