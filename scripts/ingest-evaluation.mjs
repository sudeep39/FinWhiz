import { readFile } from 'node:fs/promises';
import { Worker } from 'node:worker_threads';
import { createHash } from 'node:crypto';
import { Storage } from '../lib/storage.mjs';
import { enterAIProvider, activeAI, embeddingFingerprint } from '../lib/ai-config.mjs';
enterAIProvider(process.argv.find(a=>a.startsWith('--provider='))?.slice(11));
const config=JSON.parse(await readFile(new URL('../experiment.json',import.meta.url)));
const manifest=JSON.parse(await readFile(new URL('../data/filings/manifest.json',import.meta.url)));
const entries=manifest.entries.filter(e=>config.tickers.includes(e.ticker));
if(entries.length!==config.tickers.length*config.periods_per_company)throw new Error('Wrong evaluation corpus size.');
const digest=createHash('sha256').update(embeddingFingerprint()+entries.map(e=>e.sha256).sort().join('')).digest('hex');
if(!process.argv.includes('--apply')){console.log(`Plan: ${entries.length} verified filings, fixed + semantic, corpus ${digest}. --apply uses paid providers.`);process.exit(0);}
const store=await new Storage().init(),state=await store.json('state',{jobs:[]});
if(state.jobs.some(j=>['processing','queued'].includes(j.status)))throw new Error('An ingestion job is active. Stop the web server and let its queue finish before CLI ingestion.');
let job=state.jobs.find(j=>j.id===digest.slice(0,32));
if(job?.status==='ready'){console.log('This evaluation corpus is already indexed.');process.exit(0);}
if(!job){job={aiConfig:activeAI(),id:digest.slice(0,32),createdAt:new Date().toISOString(),strategies:['fixed','semantic'],files:entries.map(e=>({id:e.sha256.slice(0,32),name:e.localPath.split('/').at(-1),samplePath:e.localPath,known:e,size:e.bytes||0}))};state.jobs.unshift(job);}
Object.assign(job,{status:'processing',phase:'validating',startedAt:new Date().toISOString(),error:null});await store.putJson('state',state);
const worker=new Worker(new URL('../lib/ingest.worker.mjs',import.meta.url),{workerData:{job,storage:{directory:store.directory}},execArgv:[],resourceLimits:{maxOldGenerationSizeMb:768}});
let commits=Promise.resolve(),finished=false,lastPhase='',lastPercent=-10;
const save=()=>{commits=commits.catch(()=>{}).then(()=>store.putJson('state',state));return commits;};
const timeout=setTimeout(()=>worker.terminate(),30*60000);timeout.unref();
worker.on('message',m=>{
  if(m.type==='progress'){Object.assign(job,{phase:m.phase,percent:m.percent,detail:m.detail});if(m.phase!==lastPhase||m.percent-lastPercent>=5){console.log(`${m.percent}% ${m.phase}: ${m.detail}`);lastPhase=m.phase;lastPercent=m.percent;}}
  if(m.type==='complete'){finished=true;Object.assign(job,{status:'ready',phase:'ready',percent:100,documents:m.documents,namespaces:m.namespaces,summaries:m.summaries,completedAt:new Date().toISOString()});console.log(JSON.stringify({status:'ready',summaries:m.summaries,documents:m.documents.length}));}
  if(m.type==='failed'){finished=true;Object.assign(job,{status:'failed',error:m.error});console.error(m.error);process.exitCode=1;}
  save().catch(e=>{console.error(e.message);process.exitCode=1;});
});
worker.on('error',error=>{Object.assign(job,{status:'failed',error:error.message});console.error(error.message);process.exitCode=1;});
await new Promise(resolve=>worker.on('exit',resolve));clearTimeout(timeout);
if(!finished){Object.assign(job,{status:'failed',error:job.error||'Worker stopped. Rerun to resume cached work.'});process.exitCode=1;}
await save();await store.close();
