import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtemp, rm, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createApplication } from './lib/web-server.mjs';
import { fuse, calculate, completeRanking, finalizeMargins } from './lib/retrieval.mjs';
import { isISODate, validateDetails } from './lib/validation.mjs';
import { tokenCount, windows } from './lib/chunking.mjs';
import { retrievalMetrics } from './lib/evaluation.mjs';
import { cashFlowAnswer, cashFlowAdjustmentAnswer } from './lib/cash-flow.mjs';

test('cash-flow adjustments preserve accounting signs and year columns',()=>{
  const question='What was the SpaceX equity investment unrealized gain adjustment in operating cash flow for the first six months of 2026?';
  const text='(in millions)\n | Six Months Ended June 30,\n | 2026 | 2026 | | 2025 | 2025\nCash Flows from Operating Activities | | | | |\nAdjustments to reconcile net income to net cash provided by operating activities: | | | | |\nSpaceX equity investment unrealized gain | (1,005) | (1,005) | | — | —\nChanges in operating assets and liabilities: | | | | |\nCash Flows from Investing Activities | | | | |\nPurchase of SpaceX equity investment | (2,002) | (2,002) | | — | —';
  const source={id:'S3',ticker:'TSLA',text};
  const answer=cashFlowAdjustmentAnswer(question,[source]);
  assert.match(answer.answer,/−\$1,005 million \(a negative adjustment\)/);
  assert.match(answer.answer,/deducted.*remove the noncash gain/);
  assert.deepEqual(answer.citation_ids,['S3']);assert.doesNotMatch(answer.answer,/2,002/);
  const changed=cashFlowAdjustmentAnswer(question,[{...source,text:text.replaceAll('(1,005)','(900)')}]);
  assert.match(changed.answer,/−\$900 million/);
  const positive=cashFlowAdjustmentAnswer(question,[{...source,text:text.replaceAll('(1,005)','1,005')}]);
  assert.match(positive.answer,/It was added/);assert.doesNotMatch(positive.answer,/negative adjustment/);
  assert.equal(cashFlowAdjustmentAnswer(question.replace('2026','2025'),[source]),null);
  assert.equal(cashFlowAdjustmentAnswer(question,[{...source,text:text.replace('Six Months','Three Months')}]),null);
  assert.equal(cashFlowAdjustmentAnswer(question,[source,{...source,text:text.replaceAll('(1,005)','(900)')}]),null);
  assert.equal(cashFlowAdjustmentAnswer(question.replace('first six months','third quarter'),[source]),null);
});

test('explicit free cash flow uses signed cash-flow rows and matching half-year columns',()=>{
  const question='Define free cash flow as operating cash flow minus purchases of property and equipment excluding finance leases, net of sales. Calculate it for the first six months of 2026 and 2025.';
  const text='(in millions)\n | Six Months Ended June 30,\n | 2026 | 2026 | | 2025 | 2025\nNet cash provided by operating activities | 8,634 | 8,634 | | 4,696 | 4,696\nPurchases of property and equipment excluding finance leases, net of sales | (8,282) | (8,282) | | (3,886) | (3,886)';
  const source={id:'S2',ticker:'TSLA',text};
  const answer=cashFlowAnswer(question,[source]);
  assert.deepEqual(answer.calculations.map(c=>c.result),[352,810]);
  assert.deepEqual(answer.citation_ids,['S2']);assert.match(answer.answer,/decreased by \$458 million/);
  assert.match(answer.answer,/No\. Operating cash flow increased/);
  assert.equal(cashFlowAnswer(question,[{...source,text:text.replace('(8,282)','8,282')}]),null);
  assert.equal(cashFlowAnswer(question,[{...source,text:text.replace('Six Months','Three Months')}]),null);
  assert.equal(cashFlowAnswer(question,[{...source,text:text.replace('(in millions)','(in thousands)')}]),null);
  assert.equal(cashFlowAnswer(question,[source,{...source,id:'S3',text:text.replaceAll('8,634','9,634')}]),null);
  assert.equal(cashFlowAnswer('What was revenue in 2026 and 2025?',[source]),null);
});

test('incomplete reranking preserves evidence and ignores duplicate or invented IDs', () => {
  const rows=['a','b','c'].map(id=>({id}));
  assert.deepEqual(completeRanking(rows,['c','c','unknown']),{candidates:[rows[2],rows[0],rows[1]],fallback:true});
  assert.deepEqual(completeRanking(rows,[]),{candidates:rows,fallback:true});
  assert.deepEqual(completeRanking(rows,['c','b','a']),{candidates:[rows[2],rows[1],rows[0]],fallback:false});
  assert.deepEqual(rows.map(r=>r.id),['a','b','c']);
});

test('evaluation measures content-label coverage and first relevant rank', () => {
  const label={ticker:'ACME',reportDate:'2025-03-31',contains:['revenue','125']};
  const row={ticker:'ACME',report_date:'2025-03-31',text:'Revenue was 125 million'};
  const result=retrievalMetrics([{...row,ticker:'OTHER'},row,row],[label]);
  assert.equal(result.recallAt5,1);assert.equal(result.mrrAt5,0.5);
  assert.equal(retrievalMetrics([],[label]).recallAt5,0);
  assert.throws(()=>retrievalMetrics([],[]));
});

test('a document has only one vote per retrieval ranking', () => {
  const a={id:'a'}, b={id:'b'};
  assert.deepEqual(fuse([[a,b,a,a],[b]]), fuse([[a,b],[b]]));
});
test('percentage and fraction calculations have distinct scales', () => {
  assert.equal(calculate('gross_margin',[20516,17053]).toFixed(1),'16.9');
  assert.equal(calculate('gross_margin_excluding_revenue',[20516,17053,146]).toFixed(1),'16.3');
  assert.equal(calculate('gross_margin',[16661,13795]).toFixed(1),'17.2');
  assert.equal(calculate('gross_margin_excluding_revenue',[16661,13795,439]).toFixed(1),'15.0');
  assert.throws(()=>calculate('gross_margin',[0,50]));
  assert.throws(()=>calculate('gross_margin_excluding_revenue',[100,50,100]));
  assert.equal(calculate('percentage',[50,100]),50);
  assert.equal(calculate('ratio',[50,100]),0.5);
  assert.throws(()=>calculate('percentage',[1,0]));
  assert.equal(calculate('percent_change',[-50,-100]),50);
});

test('margin prose and missing baseline calculations come from verified arithmetic',()=>{
  const calculations=[{label:'gross_margin_excluding_revenue_Q2_2026',operation:'gross_margin_excluding_revenue',unit:'%',values:[20516,17053,146],source_ids:['S1'],result:calculate('gross_margin_excluding_revenue',[20516,17053,146])}];
  const result=finalizeMargins({status:'answered',answer:'Credit revenue fell [S1]. Gross margin excluding credits was 16.9% [S1].',citation_ids:['S1']},calculations);
  assert.match(result.answer,/Credit revenue fell/);
  assert.doesNotMatch(result.answer,/excluding credits was 16.9/);
  assert.match(result.answer,/16.88% including/);assert.match(result.answer,/16.28% excluding/);
  assert.equal(calculations.length,2);assert.equal(calculations[1].operation,'gross_margin');
});
test('metadata rejects impossible dates and unexpected structured fields', () => {
  assert.equal(isISODate('2025-02-29'),false);
  assert.equal(isISODate('2024-02-29'),true);
  assert.equal(isISODate('2025-13-01'),false);
  for(const value of [null,[null],[{ticker:{value:'TSLA'}}],[{sourceUrl:'https://example.com'}],[{reportDate:'2025-02-31'}]])assert.equal(validateDetails(value),false);
  assert.equal(validateDetails([{ticker:'BRK.B',reportDate:'2025-03-31',form:'10-Q'}]),true);
});
test('literal tokenizer special tokens and Unicode survive chunking', () => {
  const text='Revenue <|endoftext|> 日本語 😀 '.repeat(40);
  assert.ok(tokenCount(text)>0);
  const pieces=windows(text,60,0);
  assert.equal(pieces.join(''),text);
  assert.ok(pieces.every(p=>!p.includes('\uFFFD')));
});
test('invalid requests fail cleanly, rejected uploads leave no source files, production requires authentication', async () => {
  const directory=await mkdtemp(join(tmpdir(),'finwhiz-review-'));
  const keys=['APP_PASSWORD','OPENAI_API_KEY','PINECONE_API_KEY','NODE_ENV','REPLIT_DEPLOYMENT'];
  const previous=Object.fromEntries(keys.map(k=>[k,process.env[k]]));
  let server;
  try{
    delete process.env.APP_PASSWORD;delete process.env.REPLIT_DEPLOYMENT;process.env.NODE_ENV='test';
    process.env.OPENAI_API_KEY='offline-test';process.env.PINECONE_API_KEY='offline-test';
    server=await createApplication({disableWorkers:true,storage:{directory}});
    server.listen(0,'127.0.0.1');await once(server,'listening');
    const base=`http://127.0.0.1:${server.address().port}`;
    for(const body of ['null','[]','{'])assert.equal((await fetch(base+'/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body})).status,400);
    const before=await readdir(directory);
    const bad=new FormData();bad.append('file_0',new Blob(['financial text']),'filing.txt');bad.append('details','[null]');
    assert.equal((await fetch(base+'/api/uploads',{method:'POST',body:bad})).status,400);
    assert.deepEqual(await readdir(directory),before);
    const duplicate=new FormData();duplicate.append('file_0',new Blob(['first']),'a.txt');duplicate.append('file_0',new Blob(['second']),'b.txt');
    assert.equal((await fetch(base+'/api/uploads',{method:'POST',body:duplicate})).status,400);
    assert.deepEqual(await readdir(directory),before);
    process.env.NODE_ENV='production';assert.equal((await fetch(base+'/api/state')).status,503);
    process.env.APP_PASSWORD='offline-review-password';
    const protectedResponse=await fetch(base+'/api/state');
    assert.equal(protectedResponse.status,401);
    assert.match(protectedResponse.headers.get('content-security-policy'),/object-src 'none'/);
    for(let i=1;i<20;i++)assert.equal((await fetch(base+'/api/state')).status,401);
    assert.equal((await fetch(base+'/api/state')).status,429);
  }finally{
    if(server)await new Promise(resolve=>server.close(resolve));
    for(const key of keys)previous[key]===undefined?delete process.env[key]:process.env[key]=previous[key];
    await rm(directory,{recursive:true,force:true});
  }
});
