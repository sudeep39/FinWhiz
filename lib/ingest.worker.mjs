import { parentPort, workerData } from 'node:worker_threads';
import { readFile } from 'node:fs/promises';
import { htmlMetadata, textMetadata, mergeMetadata } from './filing-metadata.mjs';
import { Storage } from './storage.mjs';
import { isISODate } from './validation.mjs';
import { embed, pinecone, pineconeHost, upsertVectors } from './providers.mjs';
import { extractHtml, extractPlain, makeParents, splitParent, hash, tokenCount } from './chunking.mjs';

import { enterAIProvider, activeAI, aiIndexConfig, embeddingFingerprint } from './ai-config.mjs';
enterAIProvider(workerData.job.aiConfig||workerData.job.provider||'openai');
const ai=activeAI(),indexConfig=aiIndexConfig(),fingerprint=embeddingFingerprint();
const store = await new Storage(workerData.storage).init();
const job = workerData.job;
let strategyIndex=-1;
const progress = (phase, percent, detail) => parentPort.postMessage({ type:'progress', phase,
  percent:strategyIndex<0?percent:Math.round(20+(strategyIndex+percent/100)*75/job.strategies.length), detail });
async function cachedEmbeddings(texts) {
  const keys = texts.map(text=>`embedding/${hash([fingerprint,indexConfig.tags['embedding-model'],indexConfig.dimension,text])}`);
  const found = await Promise.all(keys.map(key=>store.json(key)));
  const missing=[...new Set(texts.filter((_,i)=>!found[i]))];
  const fresh=new Map();
  let cursor=0;
  while(cursor<missing.length) {
    const batch=[];let count=0;
    while(cursor<missing.length && batch.length<32) {const text=missing[cursor],n=tokenCount(text);if(n>8191)throw new Error('An embedding input exceeds the token limit.');if(batch.length && count+n>12000)break;batch.push(text);count+=n;cursor++;}
    const vectors=await embed(batch);
    await Promise.all(batch.map((text,i)=>{fresh.set(text,vectors[i]);return store.putJson(`embedding/${hash([fingerprint,indexConfig.tags['embedding-model'],indexConfig.dimension,text])}`,vectors[i]);}));
  }
  return texts.map((text,i)=>found[i]||fresh.get(text));
}
async function parseFile(file) {
  const bytes=file.samplePath ? await readFile(new URL(`../${file.samplePath}`,import.meta.url)) : await store.get(file.key);
  if(!bytes?.length)throw new Error(`Missing uploaded file: ${file.name}`);
  const checksum=hash(bytes);
  let known=file.known;
  if(!known) {try{const manifest=JSON.parse(await readFile(new URL('../data/filings/manifest.json',import.meta.url)));known=manifest.entries.find(e=>e.sha256===checksum);}catch(error){if(error.code!=='ENOENT')throw error;}}
  if(known?.sha256&&known.sha256!==checksum)throw new Error('A sample filing failed its source checksum. Download it again before processing.');
  const ext=file.name.split('.').at(-1).toLowerCase();
  let blocks,details=file.details||{};
  if(['html','htm'].includes(ext)) {
    const html=bytes.toString('utf8');
    blocks=extractHtml(html);
    details=mergeMetadata(details,htmlMetadata(html));
  } else if(ext==='pdf') {
    const { PDFParse }=await import('pdf-parse');const parser=new PDFParse({data:bytes});
    try{const result=await parser.getText();blocks=extractPlain(result.text);details=mergeMetadata(details,textMetadata(result.text));}finally{await parser.destroy();}
  } else if(ext==='txt'){const text=bytes.toString('utf8');blocks=extractPlain(text);details=mergeMetadata(details,textMetadata(text));}
  else throw new Error('Supported formats: HTML, HTM, PDF, and TXT.');
  if(known) details={ticker:known.ticker,company:known.company,cik:known.cik,form:known.form,reportDate:known.reportDate,filingDate:known.filingDate,accession:known.accession,sourceUrl:known.url};
  if(!/^[A-Z][A-Z0-9.-]{0,9}$/.test(details.ticker||''))throw new Error(`${file.name}: specify a ticker in the file details before uploading.`);
  if(!isISODate(details.reportDate))throw new Error(`${file.name}: specify its fiscal period end in the file details.`);
  if(!['10-K','10-Q'].includes(details.form))throw new Error(`${file.name}: select 10-K or 10-Q in the file details.`);
  if(blocks.reduce((n,b)=>n+b.text.length,0)<100)throw new Error(`${file.name}: no readable text; scanned files need OCR first.`);
  const sourceUrl=known?.url||`/api/sources/${file.id}`;
  return {id:file.id,name:file.name,checksum,...details,sourceUrl,company:details.company||details.ticker,blocks};
}
try {
  await pineconeHost();
  const documents=[];
  progress('validating',4,'Checking document metadata and readable content');
  for(let i=0;i<job.files.length;i++){
    const doc=await parseFile(job.files[i]);documents.push(doc);
    progress('cleaning',5+Math.round((i+1)/job.files.length*15),`Validated ${i+1} of ${job.files.length} documents`);
  }
  const namespaces={};const summaries=[];
  for(const strategy of job.strategies){
    strategyIndex++;
    const namespace=`web-${job.id}-${strategy}`;namespaces[strategy]=namespace;
    const records=[];
    for(let di=0;di<documents.length;di++){
      const doc=documents[di],parents=makeParents(doc.blocks);
      await store.putJson(`parents/${job.id}/${doc.id}`,parents);
      progress('chunking',20+Math.round(di/documents.length*20),`${strategy}: segmenting ${doc.name}`);
      for(const parent of parents){
        const prefix=`${doc.company} | ${doc.ticker} | ${doc.form} | Period ended ${doc.reportDate}\n${parent.section}\n`;
        const pieces=await splitParent(parent,strategy,cachedEmbeddings,prefix);
        for(let i=0;i<pieces.length;i++){
          const text=prefix+pieces[i];
          const id=hash([fingerprint,doc.checksum,strategy,parent.index,i,'js-v1']);
          const metadata={text,ticker:doc.ticker,company:doc.company,form:doc.form,report_date:doc.reportDate,filing_date:doc.filingDate||'',accession:doc.accession||doc.checksum.slice(0,24),source_url:doc.sourceUrl,section_path:parent.section,source_anchor:parent.anchor||'',doc_id:doc.id,corpus_id:job.id,parent_id:`${doc.id}:${parent.index}`,chunk_type:parent.kind,strategy,parser_version:'js-v1',embedding_model:ai.embeddingModel,embedding_provider:ai.provider,embedding_dimensions:ai.dimension,embedding_fingerprint:fingerprint};
          if(Buffer.byteLength(JSON.stringify(metadata))>39000||tokenCount(text)>1600)throw new Error('A chunk exceeds its size limit; review the document formatting.');
          records.push({id,metadata});
        }
      }
    }
    await store.putJson(`chunks/${job.id}/${strategy}`,records);
    progress('embedding',45,`${strategy}: embedding ${records.length} chunks`);
    for(let i=0;i<records.length;i+=24){
      const batch=records.slice(i,i+24),vectors=await cachedEmbeddings(batch.map(r=>r.metadata.text));
      const result=await upsertVectors(namespace,batch.map((r,j)=>({...r,values:vectors[j]})));
      if(result.upsertedCount!==batch.length)throw new Error('Pinecone did not acknowledge the full batch. Retry this job.');
      progress('indexing',50+Math.round((i+batch.length)/records.length*40),`${strategy}: indexed ${i+batch.length} of ${records.length} chunks`);
    }
    progress('verifying',94,`${strategy}: waiting for search visibility`);
    let visible=false;
    for(let attempt=0;attempt<40;attempt++){
      const stats=await pinecone('describe_index_stats',{});
      if((stats.namespaces?.[namespace]?.vectorCount||0)>=records.length){visible=true;break;}
      await new Promise(r=>setTimeout(r,3000));
    }
    if(!visible)throw new Error('Pinecone is still making records visible. Retry this job to verify and complete it.');
    summaries.push({strategy,chunks:records.length});
  }
  parentPort.postMessage({type:'complete',namespaces,summaries,documents:documents.map(({blocks,...doc})=>doc)});
} catch(error){parentPort.postMessage({type:'failed',error:error.message});}
finally{await store.close();}
