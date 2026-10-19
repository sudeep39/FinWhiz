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
  const fixture=createProviderFixture(),row={id:'evidence',metadata:{text:'Revenue 125 million. Cost of revenue 50 million. Six months operating cash flow: 2026 8,634; 2025 4,696. Purchases of property and equipment: 2026 (8,282); 2025 (3,886). All amounts in millions.',ticker:'ACME',report_date:'2025-12-31',chunk_type:'narrative'}};
  fixture.namespaces.set('fixed-fixture',new Map([[row.id,row]]));
  const store={json:async()=>[row]};
  const valid={status:'answered',answer:'Revenue was 125 million [S1].',citation_ids:['S1'],chart:{type:'none',title:'',unit:'',points:[]},calculations:[]};
  try{
    for(const key of keys)process.env[key]='offline-test';
    let answerCalls=0;
    globalThis.fetch=async(url,options)=>{
      if(!String(url).endsWith('/chat/completions'))return fixture.fetch(url,options);
      answerCalls++;
      if(answerCalls===2){
        const request=JSON.parse(options.body),schema=request.response_format.json_schema.schema;
        assert.ok(schema.properties.calculations.items.properties.values.items.enum.includes(-8282));
        assert.ok(!schema.properties.calculations.items.properties.values.items.enum.includes(8282));
        assert.ok(!schema.properties.calculations.items.properties.values.items.enum.includes(352));
        assert.equal(JSON.parse(request.messages[1].content).previousAnswer,undefined);
      }
      const reply=answerCalls===1?{...valid,answer:'Revenue was 125 million.',citation_ids:[]}:{sentences:[{text:'Revenue was 125 million',source_ids:['S1']}],calculations:[{label:'FCF 2026',operation:'sum',unit:'USD millions',values:[8634,-8282],source_ids:['S1']},{label:'FCF 2025',operation:'sum',unit:'USD millions',values:[4696,-3886],source_ids:['S1']}]};
      return new Response(JSON.stringify({choices:[{finish_reason:'stop',message:{content:JSON.stringify(reply)}}]}),{status:200});
    };
    const repaired=await research({store,jobs,question:'What revenue is disclosed?',tickers:['ACME'],rerank:false});
    assert.equal(answerCalls,2);assert.equal(repaired.answer,'Revenue was 125 million [S1]');
    assert.deepEqual(repaired.calculations.map(c=>c.result),[352,810]);
    answerCalls=0;
    globalThis.fetch=async(url,options)=>{
      if(!String(url).endsWith('/chat/completions'))return fixture.fetch(url,options);
      answerCalls++;
      return new Response(JSON.stringify({choices:[{finish_reason:'stop',message:{content:JSON.stringify({...valid,answer:'Revenue was 125 million [S1, S1].'})}}]}),{status:200});
    };
    const grouped=await research({store,jobs,question:'What revenue is disclosed?',tickers:['ACME'],rerank:false});
    assert.equal(answerCalls,1);assert.match(grouped.answer,/\[S1\] \[S1\]/);
    // A derived numerator is not a filing literal: repair it into raw-input margin operations.
    row.metadata.text+=' Automotive revenue 20,516 and 16,661; costs 17,053 and 13,795; credits 146 and 439.';
    answerCalls=0;
    globalThis.fetch=async(url,options)=>{
      if(!String(url).endsWith('/chat/completions'))return fixture.fetch(url,options);
      answerCalls++;
      const reply=answerCalls===1?{...valid,calculations:[{label:'Adjusted margin',operation:'percentage',unit:'%',values:[3317,20370],source_ids:['S1']}]}:
        {sentences:[{text:'See the automotive margins calculated from reported revenue, costs and credits.',source_ids:['S1']}],calculations:[
          {label:'2026 including credits',operation:'gross_margin',unit:'%',values:[20516,17053],source_ids:['S1']},
          {label:'2026 excluding credits',operation:'gross_margin_excluding_revenue',unit:'%',values:[20516,17053,146],source_ids:['S1']},
          {label:'2025 including credits',operation:'gross_margin',unit:'%',values:[16661,13795],source_ids:['S1']},
          {label:'2025 excluding credits',operation:'gross_margin_excluding_revenue',unit:'%',values:[16661,13795,439],source_ids:['S1']}
        ]};
      return new Response(JSON.stringify({choices:[{finish_reason:'stop',message:{content:JSON.stringify(reply)}}]}),{status:200});
    };
    const margins=await research({store,jobs,question:'Calculate automotive gross margin with and without regulatory credits.',tickers:['ACME'],rerank:false});
    assert.equal(answerCalls,2);
    assert.deepEqual(margins.calculations.map(c=>c.result.toFixed(1)),['16.9','16.3','17.2','15.0']);
    row.metadata.text+=' Energy revenue: 2026 3,139; 2025 2,789. Energy cost of revenue: 2026 2,499; 2025 1,943.';
    answerCalls=0;
    globalThis.fetch=async(url,options)=>{
      if(!String(url).endsWith('/chat/completions'))return fixture.fetch(url,options);
      answerCalls++;
      if(answerCalls===2)assert.match(JSON.parse(JSON.parse(options.body).messages[1].content).validationErrors.join(' '),/4 inputs/);
      const reply=answerCalls===1?{...valid,calculations:[{label:'Energy gross profit',operation:'difference',unit:'USD millions',values:[3139,2499,2789,1943],source_ids:['S1']}]}:
        {sentences:[{text:'Energy gross profit declined despite higher revenue; see the calculations.',source_ids:['S1']}],calculations:[
          {label:'Q2 2026 gross profit',operation:'difference',unit:'USD millions',values:[3139,2499],source_ids:['S1']},
          {label:'Q2 2025 gross profit',operation:'difference',unit:'USD millions',values:[2789,1943],source_ids:['S1']}
        ]};
      return new Response(JSON.stringify({choices:[{finish_reason:'stop',message:{content:JSON.stringify(reply)}}]}),{status:200});
    };
    const profits=await research({store,jobs,question:'Energy revenue increased in Q2 2026. Did its gross profit improve?',tickers:['ACME'],rerank:false});
    assert.equal(answerCalls,2);assert.deepEqual(profits.calculations.map(c=>c.result),[640,846]);
    for(const [reply,pattern] of [
      [{...valid,answer:'Invented claim [S999].'},/citation|schema validation/],
      [{...valid,chart:{...valid.chart,type:'bar',points:[{label:'Invented',value:999,source_ids:['S1']}]}},/Chart values/],
      [{...valid,calculations:[{label:'Margin',operation:'ratio',unit:'%',values:[50,125],source_ids:['S1']}]},/percentage operation/]
    ]){
      globalThis.fetch=async(url,options)=>String(url).endsWith('/chat/completions')?new Response(JSON.stringify({choices:[{finish_reason:'stop',message:{content:JSON.stringify(JSON.parse(options.body).response_format.json_schema.name==='repair_financial_answer'?{sentences:[{text:'Invented claim',source_ids:['S999']}],calculations:[]}:reply)}}]}),{status:200}):fixture.fetch(url,options);
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
