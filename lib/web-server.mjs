import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { randomUUID, timingSafeEqual } from 'node:crypto';
import { Worker } from 'node:worker_threads';
import { fileURLToPath } from 'node:url';
import Busboy from 'busboy';
import { Storage } from './storage.mjs';
import { research } from './retrieval.mjs';
import { isISODate, validateDetails } from './validation.mjs';
import { activeAI, withAIProvider, aiConfigured, providerOptions, jobMatchesAI } from './ai-config.mjs';
import { operationalStatus } from './operations.mjs';

const root=new URL('../',import.meta.url);
const experiment=JSON.parse(await readFile(new URL('experiment.json',root)));
const routes={'/':['index.html','text/html'],'/app.mjs':['app.mjs','text/javascript'],'/style.css':['style.css','text/css'],
  '/data/experiment-filings.json':['data/experiment-filings.json','application/json'],'/data/experiment-filings.csv':['data/experiment-filings.csv','text/csv']};
const json=(res,status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
const publicJob=job=>{const {files,...safe}=job;return {...safe,files:files.map(f=>({id:f.id,name:f.name,size:f.size}))};};
async function readJson(req,max=32000){const chunks=[];let n=0;for await(const chunk of req){n+=chunk.length;if(n>max)throw Object.assign(new Error('Request is too large.'),{status:413});chunks.push(chunk);}try{const value=JSON.parse(Buffer.concat(chunks));if(!value||typeof value!=='object'||Array.isArray(value))throw new Error();return value;}catch{throw Object.assign(new Error('Invalid JSON request.'),{status:400});}}
export async function receiveFiles(req,store){
  if(!req.headers['content-type']?.startsWith('multipart/form-data'))throw Object.assign(new Error('Use a multipart file upload.'),{status:400});
  return new Promise((resolveUpload,reject)=>{
    let parser;try{parser=Busboy({headers:req.headers,limits:{files:32,fileSize:20*1024*1024,fields:2,fieldSize:32000,parts:35}});}catch{reject(Object.assign(new Error('Invalid upload request.'),{status:400}));return;}
    const files=[],writes=[],indexes=new Set();let total=0,details=[],problem=null;
    parser.on('field',(name,value,info)=>{if(info.valueTruncated)problem='File details are too large.';if(name==='details'){try{details=JSON.parse(value);if(!validateDetails(details))problem='Invalid file details.';}catch{problem='Invalid file details.';}}});
    parser.on('file',(field,stream,info)=>{
      const name=info.filename.replaceAll('\\','/').split('/').at(-1).replace(/[\x00-\x1f]/g,'').slice(0,180);
      const index=/^file_\d+$/.test(field)?Number(field.slice(5)):NaN;
      if(!/\.(html?|pdf|txt)$/i.test(name)||!Number.isInteger(index)||index<0||index>31||indexes.has(index)){problem='Choose HTML, PDF, or TXT files.';stream.resume();return;}
      indexes.add(index);const chunks=[];let size=0;
      stream.on('limit',()=>{problem='Each file must be smaller than 20 MB.';});
      stream.on('data',chunk=>{total+=chunk.length;size+=chunk.length;if(total>100*1024*1024)problem='Each batch must be smaller than 100 MB.';if(!problem)chunks.push(chunk);});
      const promise=new Promise((done,fail)=>{stream.on('error',fail);stream.on('end',async()=>{try{if(problem){done();return;}const id=randomUUID(),key=`upload/${id}`;await store.put(key,Buffer.concat(chunks));files.push({id,key,name,size,index});done();}catch(error){fail(error);}});});
      writes.push(promise);
    });
    for(const event of ['filesLimit','fieldsLimit','partsLimit'])parser.on(event,()=>{problem='Upload batch limit exceeded (maximum 32 files).';});
    parser.on('error',reject);req.on('aborted',()=>reject(new Error('Upload interrupted.')));
    parser.on('close',async()=>{try{await Promise.all(writes);if(problem)throw Object.assign(new Error(problem),{status:400});if(!files.length)throw Object.assign(new Error('Select at least one readable file.'),{status:400});files.sort((a,b)=>a.index-b.index);resolveUpload(files.map(f=>({...f,details:details[f.index]||{}})));}catch(error){await Promise.allSettled(files.map(f=>store.remove(f.key)));reject(error);}});
    req.pipe(parser);
  });
}
export async function createApplication(options={}){
  const storageOptions={directory:process.env.FINWHIZ_DATA_DIR||fileURLToPath(new URL('.runtime/',root)),...(options.storage||{})};
  const store=await new Storage(storageOptions).init();
  const state=await store.json('state',{jobs:[]});
  for(const job of state.jobs)if(job.status==='processing'){job.status='queued';job.detail='Resuming after server restart';}
  let worker=null,pumpBusy=false,closed=false;
  let commits=Promise.resolve();
  const save=()=>{commits=commits.catch(()=>{}).then(()=>store.putJson('state',state));return commits;};
  async function pump(){
    if(pumpBusy||closed||options.disableWorkers)return;
    const job=[...state.jobs].reverse().find(j=>j.status==='queued');if(!job)return;
    pumpBusy=true;job.status='processing';job.startedAt=new Date().toISOString();await save();
    worker=new Worker(new URL('lib/ingest.worker.mjs',root),{workerData:{job,storage:storageOptions},resourceLimits:{maxOldGenerationSizeMb:768}});
    let finished=false;const deadline=setTimeout(()=>{Object.assign(job,{status:'failed',error:'Processing exceeded 30 minutes. Retry using a smaller batch.'});finished=true;worker?.terminate();save().catch(()=>{});},30*60*1000);deadline.unref();
    worker.on('message',message=>{
      if(message.type==='progress')Object.assign(job,{phase:message.phase,percent:message.percent,detail:message.detail});
      if(message.type==='complete'){finished=true;Object.assign(job,{status:'ready',phase:'ready',percent:100,detail:'Ready for research',namespaces:message.namespaces,documents:message.documents,summaries:message.summaries,completedAt:new Date().toISOString(),error:null});}
      if(message.type==='failed'){finished=true;Object.assign(job,{status:'failed',detail:'Processing stopped',error:message.error});}
      save().catch(error=>console.error('Job state could not be saved:',error.message));
    });
    worker.on('error',error=>{finished=true;Object.assign(job,{status:'failed',error:error.message});save().catch(()=>{});});
    worker.on('exit',()=>{clearTimeout(deadline);if(!finished&&!closed){Object.assign(job,{status:'failed',error:'Processing worker stopped unexpectedly. Retry to resume cached work.'});save().catch(()=>{});}worker=null;pumpBusy=false;if(!closed)setImmediate(()=>pump().catch(e=>console.error(e.message)));});
  }
  const enqueue=async files=>{
    if(state.jobs.filter(j=>['queued','processing'].includes(j.status)).length>=3)throw Object.assign(new Error('Three batches are already queued. Wait for a batch to finish.'),{status:429});
    const job={aiConfig:activeAI(),id:randomUUID(),createdAt:new Date().toISOString(),status:'queued',phase:'queued',percent:0,detail:'Waiting to process',strategies:['fixed','semantic'],files};
    state.jobs.unshift(job);await save();setImmediate(()=>pump().catch(e=>console.error(e.message)));return job;
  };
  const limits=new Map(),authLimits=new Map();let activeChats=0;
  const handle=async(req,res)=>{
    res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','same-origin');
    res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
    const url=new URL(req.url,'http://localhost');
    try{
      if(url.pathname==='/api/health'){json(res,200,{ok:true});return;}
      if(url.pathname.startsWith('/api/')){
        const required=process.env.APP_PASSWORD;
        if((process.env.REPLIT_DEPLOYMENT||process.env.NODE_ENV==='production')&&!required)throw Object.assign(new Error('Set APP_PASSWORD in deployment secrets before enabling this shared workspace.'),{status:503});
        const provided=req.headers.authorization?.replace(/^Bearer /,'')||'';
        const client=req.socket.remoteAddress,now=Date.now();
        for(const [key,value] of authLimits)if(now-value.start>60000)authLimits.delete(key);
        if((authLimits.get(client)?.count||0)>=20)throw Object.assign(new Error('Too many password attempts. Wait a minute.'),{status:429});
        if(required&&(Buffer.byteLength(provided)!==Buffer.byteLength(required)||!timingSafeEqual(Buffer.from(provided),Buffer.from(required)))){const rate=authLimits.get(client)||{start:now,count:0};rate.count++;authLimits.set(client,rate);json(res,401,{error:'Enter your workspace password.'});return;}
        if(req.method==='POST'&&req.headers.origin){const origin=new URL(req.headers.origin);if(origin.host!==req.headers.host&&origin.host!==req.headers['x-forwarded-host'])throw Object.assign(new Error('Origin not allowed.'),{status:403});}
        if(url.pathname==='/api/state'&&req.method==='GET'){
          const documents=state.jobs.filter(j=>j.status==='ready'&&jobMatchesAI(j)).flatMap(j=>j.documents.map(d=>({...d,jobId:j.id,strategies:j.strategies})));
          const companies=[];
          for(const doc of documents)if(!companies.some(c=>c.ticker===doc.ticker))companies.push({name:doc.company,ticker:doc.ticker,periods:[]});
          for(const doc of documents){const company=companies.find(c=>c.ticker===doc.ticker);if(!company.periods.some(p=>p.reportDate===doc.reportDate&&p.form===doc.form))company.periods.push({reportDate:doc.reportDate,form:doc.form,url:doc.sourceUrl,ready:true});}
          json(res,200,{companies,documents,jobs:state.jobs.map(publicJob),config:{providersConfigured:aiConfigured(),aiProvider:activeAI().provider,aiProviders:providerOptions(),storageBackend:'filesystem',authenticated:!!required,asOf:experiment.as_of},capabilities:{hybrid:true,rerank:true,strategies:['fixed','semantic']}});return;
        }
        if(url.pathname==='/api/operations'&&req.method==='GET'){
          json(res,200,operationalStatus(state.jobs,await store.json('freshness-check')));return;
        }
        if(url.pathname==='/api/uploads'&&req.method==='POST'){
          if(!aiConfigured())throw Object.assign(new Error(`Configure ${activeAI().provider==='nebius'?'NEBIUS_API_KEY':'OPENAI_API_KEY'} and PINECONE_API_KEY in server secrets before submitting files.`),{status:503});
          if(state.jobs.filter(j=>['queued','processing'].includes(j.status)).length>=3)throw Object.assign(new Error('Upload queue is full. Please try again later.'),{status:429});
          const files=await receiveFiles(req,store);let job;try{job=await enqueue(files);}catch(error){await Promise.allSettled(files.map(f=>store.remove(f.key)));throw error;}json(res,202,{job:publicJob(job),message:'Your files are uploaded. Cleanup, chunking, embedding, and indexing will continue in the background. You can leave and return later.'});return;
        }
        const retry=url.pathname.match(/^\/api\/jobs\/([a-f0-9-]+)\/retry$/);
        if(retry&&req.method==='POST'){const job=state.jobs.find(j=>j.id===retry[1]);if(!job||job.status!=='failed')throw Object.assign(new Error('This job cannot be retried.'),{status:400});job.status='queued';job.error=null;job.percent=0;await save();setImmediate(()=>pump().catch(e=>console.error(e.message)));json(res,202,{job:publicJob(job)});return;}
        const source=url.pathname.match(/^\/api\/sources\/([a-f0-9-]+)$/);
        if(source&&req.method==='GET'){
          const file=state.jobs.flatMap(j=>j.files).find(f=>f.id===source[1]);if(!file)throw Object.assign(new Error('Source not found.'),{status:404});
          const bytes=file.samplePath?await readFile(new URL(file.samplePath,root)):await store.get(file.key);
          res.writeHead(200,{'Content-Type':'application/octet-stream','Content-Disposition':`attachment; filename="${file.name.replace(/[^a-zA-Z0-9._-]/g,'_')}"`});res.end(bytes);return;
        }
        if(url.pathname==='/api/chat'&&req.method==='POST'){
          const key=req.socket.remoteAddress,now=Date.now();let rate=limits.get(key);if(!rate||now-rate.start>60000){rate={start:now,count:0};limits.set(key,rate);}if(++rate.count>12)throw Object.assign(new Error('Please wait a moment before sending another question.'),{status:429});
          const body=await readJson(req);
          if(typeof body.question!=='string'||!body.question.trim()||body.question.length>3000)throw Object.assign(new Error('Enter a question of up to 3,000 characters.'),{status:400});
          if(!Array.isArray(body.tickers)||!body.tickers.length||body.tickers.length>4||body.tickers.some(t=>typeof t!=='string'||!/^[A-Z][A-Z0-9.-]{0,9}$/.test(t)))throw Object.assign(new Error('Select at least one company.'),{status:400});
          if(!['fixed','semantic'].includes(body.strategy||'fixed'))throw Object.assign(new Error('Invalid retrieval strategy.'),{status:400});
          const periods=body.periods||[];if(!Array.isArray(periods)||periods.length>32||periods.some(p=>!isISODate(p)))throw Object.assign(new Error('Invalid period selection.'),{status:400});
          const ready=state.jobs.filter(j=>j.status==='ready'&&jobMatchesAI(j));
          if(body.tickers.some(t=>!ready.some(j=>j.documents.some(d=>d.ticker===t))))throw Object.assign(new Error('The selected company is not ready yet. Finish loading its filings first.'),{status:409});
          const history=Array.isArray(body.history)?body.history.slice(-4).filter(m=>m&&['user','assistant'].includes(m.role)&&typeof m.content==='string').map(m=>({role:m.role,content:m.content.slice(0,1500)})):[];
          if(activeChats>=2)throw Object.assign(new Error('Two questions are already processing. Please retry shortly.'),{status:429});
          activeChats++;
          try{const result=await research({store,jobs:ready,question:body.question.trim(),tickers:body.tickers,periods,strategy:body.strategy||'fixed',rerank:body.rerank!==false,history});json(res,200,result);}finally{activeChats--;}
          return;
        }
        json(res,404,{error:'Endpoint not found.'});return;
      }
      const route=routes[url.pathname];if(!route||req.method!=='GET'){res.writeHead(404).end('Not found');return;}
      res.writeHead(200,{'Content-Type':`${route[1]}; charset=utf-8`,'Cache-Control':'no-store'});res.end(await readFile(new URL(route[0],root)));
    }catch(error){json(res,error.status||502,{error:error.message||'The request could not be completed.'});}
  };
  const server=createServer(async(req,res)=>{try{await withAIProvider(req.headers['x-ai-provider']||undefined,()=>handle(req,res));}catch(error){json(res,error.status||502,{error:error.message});}});
  server.requestTimeout=180000;
  server.on('close',()=>{closed=true;Promise.resolve(worker?.terminate()).then(()=>commits).finally(()=>store.close());});
  await save();setImmediate(()=>pump().catch(e=>console.error(e.message)));
  return server;
}
