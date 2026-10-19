import { readFile, mkdir, writeFile, appendFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { aiConfigured, enterAIProvider } from '../lib/ai-config.mjs';
enterAIProvider(process.argv.find(a=>a.startsWith('--provider='))?.slice(11));
import { Storage } from '../lib/storage.mjs';
import { runBenchmark, validateSuite, comparisonReport } from '../lib/benchmark.mjs';
import { automatedAnswerReview } from '../lib/answer-review.mjs';
const argument=key=>process.argv.find(a=>a.startsWith(`--${key}=`))?.slice(key.length+3);
const path=argument('queries')||'evaluation/benchmark.json';
const suite=JSON.parse(await readFile(resolve(path),'utf8'));
validateSuite(suite);
console.log(`Validated ${suite.length} questions; ${suite.filter(q=>q.reviewed).length} source-reviewed.`);
if(!process.argv.includes('--apply')){console.log('No provider calls. --apply runs retrieval; add --answers for generation, --judge for optional automated claim review.');process.exit(0);}
if(suite.some(q=>q.reviewed!==true))throw new Error('Review every gold source/policy expectation before a measured benchmark.');
if(!aiConfigured())throw new Error('Configure both provider credentials in server environment variables or .env.');
const answers=process.argv.includes('--answers');if(process.argv.includes('--judge')&&!answers)throw new Error('--judge requires --answers.');
const directory=resolve('artifacts/evaluation',new Date().toISOString().replace(/[:.]/g,'-'));
await mkdir(directory,{recursive:true});
const store=await new Storage().init();const {jobs}=await store.json('state',{jobs:[]});
try{
  const run=await runBenchmark({suite,jobs,store,answers,judge:process.argv.includes('--judge')?automatedAnswerReview:null,repetitions:Number(argument('repetitions')||1),onResult:async(row,provenance)=>{
    await writeFile(join(directory,'provenance.json'),JSON.stringify(provenance,null,2));
    await appendFile(join(directory,'trials.jsonl'),JSON.stringify(row)+'\n');
    console.log(`${row.queryId} ${row.strategy} rerank=${row.rerank} ${row.success?'completed':'FAILED'}`);
  }});
  await writeFile(join(directory,'results.json'),JSON.stringify(run,null,2));
  await writeFile(join(directory,'COMPARISON_REPORT.md'),comparisonReport(run));
  const review=run.results.map(r=>({queryId:r.queryId,strategy:r.strategy,rerank:r.rerank,repeat:r.repeat,reviewer:'',reviewed:false,claims:[],numericCorrect:null,unitsCorrect:null,periodsCorrect:null,relevantAnswer:null,notes:''}));
  await writeFile(join(directory,'manual-review.json'),JSON.stringify(review,null,2));
  console.log(`Report and raw evidence saved to ${directory}`);
}finally{await store.close();}
