import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtemp, rm, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createApplication } from './lib/web-server.mjs';
import { fuse, calculate } from './lib/retrieval.mjs';
import { isISODate, validateDetails } from './lib/validation.mjs';
import { tokenCount, windows } from './lib/chunking.mjs';
import { retrievalMetrics } from './lib/evaluation.mjs';

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
  assert.equal(calculate('percentage',[50,100]),50);
  assert.equal(calculate('ratio',[50,100]),0.5);
  assert.throws(()=>calculate('percentage',[1,0]));
  assert.equal(calculate('percent_change',[-50,-100]),50);
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
