import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { planQuestion } from './lib/query-policy.mjs';
import { operationalStatus } from './lib/operations.mjs';
import { Storage } from './lib/storage.mjs';
import { snapshotRuntime, restoreRuntime } from './lib/recovery.mjs';
import { runBenchmark, validateSuite, comparisonReport, verifyCorpus } from './lib/benchmark.mjs';
import { scoreManualReviews } from './lib/manual-scoring.mjs';
import { research } from './lib/retrieval.mjs';
import { requestJson } from './lib/providers.mjs';
import { createProviderFixture } from './scripts/provider-fixture.mjs';

test('ambiguous, unsupported and follow-up questions have explicit policies',()=>{
  assert.equal(planQuestion('What is profit?').status,'needs_clarification');
  assert.equal(planQuestion('What will the NVIDIA stock price be next Friday?').status,'insufficient_evidence');
  assert.equal(planQuestion('What did executives say on the earnings call?').status,'insufficient_evidence');
  assert.match(planQuestion('And for the prior quarter?',[{role:'user',content:'What is operating cash flow?'}]).retrievalQuestion,/operating cash flow/);
  assert.equal(planQuestion('What is total revenue?').status,undefined);
});
test('clarification and absent corpora do not incur provider calls',async()=>{
  const original=globalThis.fetch;globalThis.fetch=()=>{throw new Error('Unexpected provider call');};
  try{
    assert.equal((await research({jobs:[],question:'What is profit?',tickers:['ACME']})).status,'needs_clarification');
    assert.equal((await research({jobs:[],question:'Revenue?',tickers:['ACME']})).status,'insufficient_evidence');
  }finally{globalThis.fetch=original;}
});
test('freshness and queue SLA distinguish overdue data from recent checks',()=>{
  const now=Date.parse('2026-10-18T12:00:00Z');
  const result=operationalStatus([{id:'old',status:'queued',createdAt:'2026-10-18T11:00:00Z'},{status:'ready',documents:[{ticker:'ACME',reportDate:'2025-01-01'}]}],{checkedAt:'2026-10-17T00:00:00Z'},now);
  assert.equal(result.freshness.reviewDue,false);assert.equal(result.freshness.companies[0].stale,true);assert.equal(result.sla.breaches[0].jobId,'old');
  assert.equal(operationalStatus([],null,now).freshness.reviewDue,true);
});
test('runtime recovery verifies integrity and never overwrites populated destinations',async()=>{
  const root=await mkdtemp(join(tmpdir(),'finwhiz-recovery-'));
  const store=await new Storage({directory:join(root,'runtime')}).init();
  try{
    await store.putJson('state',{jobs:[{status:'processing'}]});
    await assert.rejects(snapshotRuntime(store.directory,join(root,'backup')),/queue/);
    await store.putJson('state',{jobs:[]});await store.put('upload/doc','financial source');
    const snapshot=await snapshotRuntime(store.directory,join(root,'backup'));
    await restoreRuntime(join(root,'backup'),join(root,'restored'));
    const restored=await new Storage({directory:join(root,'restored')}).init();assert.equal((await restored.get('upload/doc')).toString(),'financial source');
    await assert.rejects(restoreRuntime(join(root,'backup'),join(root,'restored')),/empty/);
    await writeFile(join(root,'backup',snapshot.files[0].name),'corrupt');
    await assert.rejects(restoreRuntime(join(root,'backup'),join(root,'broken')),/checksum/);
  }finally{await rm(root,{recursive:true,force:true});}
});
const docs=[{ticker:'ACME',reportDate:'2025-12-31',checksum:'source-hash',accession:'accession'}];
const jobs=[{status:'ready',documents:docs,namespaces:{fixed:'fixed-fixture',semantic:'semantic-fixture'}}];
const suite=[{id:'T1',question:'Revenue?',tickers:['ACME'],periods:['2025-12-31'],expected:'answered',labels:[{ticker:'ACME',reportDate:'2025-12-31',contains:['Revenue','125']}]},{id:'T2',question:'What is profit?',tickers:['ACME'],periods:[],expected:'needs_clarification',labels:[]}];
test('benchmark compares all four conditions, counterbalances order and retains failures',async()=>{
  const calls=[];
  const run=await runBenchmark({suite,jobs,answers:true,researchFn:async options=>{
    calls.push(`${options.strategy}/${options.rerank}`);
    if(options.question==='Revenue?'&&options.strategy==='fixed'&&!options.rerank)throw new Error('Controlled provider outage');
    return {status:options.question==='Revenue?'?'answered':'needs_clarification',ranking:[{ticker:'ACME',report_date:'2025-12-31',text:'Revenue 125'}],answer:'Revenue 125 [S1].',sources:[]};
  }});
  assert.equal(run.results.length,8);assert.equal(run.summary.groups['fixed/baseline'].recallAt5,0);
  assert.equal(run.summary.groups['fixed/baseline'].statusAccuracy,.5);assert.equal(run.summary.groups['fixed/reranked'].recallAt5,1);
  assert.equal(run.summary.rerankingDeltas.fixed.recallAt5,1);assert.notEqual(calls[0],calls[4]);
  assert.match(comparisonReport(run),/Controlled provider outage|Failed requests/);
  assert.equal(scoreManualReviews(run,[]).groups['semantic/reranked'].acceptance,'pending');
  assert.throws(()=>scoreManualReviews(run,[{queryId:'wrong'}]),/belong/);
});
test('benchmark rejects missing gold and mismatched strategy corpora',()=>{
  assert.throws(()=>validateSuite([{...suite[0],labels:[]}]),/gold/);
  assert.throws(()=>verifyCorpus(suite,[{...jobs[0],namespaces:{fixed:'only'}}]),/identical/);
  assert.throws(()=>verifyCorpus([{...suite[0],labels:[{...suite[0].labels[0],sourceSha256:'changed'}]}],jobs),/changed/);
});

test('quota exhaustion stops immediately instead of repeating paid-provider requests',async()=>{
  const original=globalThis.fetch;let calls=0;
  globalThis.fetch=async()=>{calls++;return new Response(JSON.stringify({error:{type:'insufficient_quota',code:'credit_balance_exhausted'}}),{status:429});};
  try{await assert.rejects(requestJson('https://fixture.invalid',{},{}),/quota is exhausted/);assert.equal(calls,1);}finally{globalThis.fetch=original;}
});

test('financial generation rejects invented citations, chart numbers and percent scaling',async()=>{
  const original=globalThis.fetch,keys=['OPENAI_API_KEY','PINECONE_API_KEY'],saved=Object.fromEntries(keys.map(k=>[k,process.env[k]]));
  const fixture=createProviderFixture(),row={id:'evidence',metadata:{text:'Revenue 125 million. Cost of revenue 50 million.',ticker:'ACME',report_date:'2025-12-31',chunk_type:'narrative'}};
  fixture.namespaces.set('fixed-fixture',new Map([[row.id,row]]));
  const store={json:async()=>[row]};
  const valid={status:'answered',answer:'Revenue was 125 million [S1].',citation_ids:['S1'],chart:{type:'none',title:'',unit:'',points:[]},calculations:[]};
  try{
    for(const key of keys)process.env[key]='offline-test';
    for(const [reply,pattern] of [
      [{...valid,answer:'Invented claim [S999].'},/citation/],
      [{...valid,chart:{...valid.chart,type:'bar',points:[{label:'Invented',value:999,source_ids:['S1']}]}},/Chart values/],
      [{...valid,calculations:[{label:'Margin',operation:'ratio',unit:'%',values:[50,125],source_ids:['S1']}]},/percentage operation/]
    ]){
      globalThis.fetch=async(url,options)=>String(url).endsWith('/chat/completions')?new Response(JSON.stringify({choices:[{finish_reason:'stop',message:{content:JSON.stringify(reply)}}]}),{status:200}):fixture.fetch(url,options);
      await assert.rejects(research({store,jobs,question:'What revenue is disclosed?',tickers:['ACME'],rerank:false}),pattern);
    }
  }finally{globalThis.fetch=original;for(const key of keys)saved[key]===undefined?delete process.env[key]:process.env[key]=saved[key];}
});

test('manual acceptance requires complete reviewed claims and fails incorrect citations',async()=>{
  const run=await runBenchmark({suite:[suite[0]],jobs,answers:true,researchFn:async()=>({status:'answered',ranking:[{ticker:'ACME',report_date:'2025-12-31',text:'Revenue 125'}]})});
  const reviews=run.results.map(r=>({...r,reviewer:'Independent analyst fixture',reviewed:true,claims:[{text:'Revenue 125',supported:true,citationCorrect:true}],numericCorrect:true,unitsCorrect:true,periodsCorrect:true,relevantAnswer:true}));
  assert.equal(scoreManualReviews(run,reviews).groups['fixed/baseline'].acceptance,'pass');
  reviews.find(r=>r.strategy==='fixed'&&!r.rerank).claims[0].citationCorrect=false;
  assert.equal(scoreManualReviews(run,reviews).groups['fixed/baseline'].acceptance,'fail');
  reviews[0].reviewed=false;assert.equal(scoreManualReviews(run,reviews).groups['fixed/baseline'].acceptance,'pending');
});
