import { createHash } from 'node:crypto';
import { research } from './retrieval.mjs';
import { retrievalMetrics } from './evaluation.mjs';
import { activeAI, aiIndexConfig, jobMatchesAI } from './ai-config.mjs';
import { qualityPolicy } from './operations.mjs';
const hash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
export const conditions=[{strategy:'fixed',rerank:false},{strategy:'fixed',rerank:true},{strategy:'semantic',rerank:false},{strategy:'semantic',rerank:true}];
export function validateSuite(suite){
  if(!Array.isArray(suite)||!suite.length||new Set(suite.map(q=>q.id)).size!==suite.length)throw new Error('Supply queries with unique IDs.');
  for(const q of suite){
    if(typeof q.id!=='string'||typeof q.question!=='string'||!q.question.trim()||!q.tickers?.length||!Array.isArray(q.periods)||!Array.isArray(q.labels)||!['answered','insufficient_evidence','needs_clarification'].includes(q.expected))throw new Error(`Invalid query: ${q.id}`);
    for(const l of q.labels)if(!l.ticker||!l.reportDate||!l.contains?.length||l.contains.some(s=>typeof s!=='string'||!s.trim()))throw new Error(`Invalid evidence label: ${q.id}`);
    if(q.expected==='answered'&&!q.labels.length)throw new Error(`Missing gold evidence labels: ${q.id}`);
  }
}
export function verifyCorpus(suite,jobs){
  const ready=jobs.filter(j=>j.status==='ready'&&jobMatchesAI(j));
  for(const q of suite)for(const ticker of q.tickers){
    const docs=ready.flatMap(j=>j.documents||[]).filter(d=>d.ticker===ticker&&(!q.periods.length||q.periods.includes(d.reportDate)));
    if(!docs.length)throw new Error(`Missing corpus: ${ticker} for ${q.id}`);
  }
  const corpus={};
  for(const strategy of ['fixed','semantic'])corpus[strategy]=[...new Set(ready.filter(j=>j.namespaces?.[strategy]).flatMap(j=>j.documents||[]).map(d=>`${d.ticker}:${d.reportDate}:${d.checksum||d.accession}`))].sort();
  if(JSON.stringify(corpus.fixed)!==JSON.stringify(corpus.semantic)||!corpus.fixed.length)throw new Error('Both chunkers must use the identical document corpus.');
  for(const q of suite)for(const l of q.labels)if(!ready.some(j=>j.documents?.some(d=>d.ticker===l.ticker&&d.reportDate===l.reportDate&&(!l.accession||d.accession===l.accession)&&(!l.sourceSha256||d.checksum===l.sourceSha256))))throw new Error(`Gold source missing or changed: ${q.id}`);
  return corpus.fixed;
}
export async function runBenchmark({suite,jobs,store,answers=false,judge=null,repetitions=1,researchFn=research,onResult=async()=>{}}){
  validateSuite(suite);
  if(!Number.isInteger(repetitions)||repetitions<1||repetitions>5)throw new Error('Repetitions must be 1–5.');
  const corpus=verifyCorpus(suite,jobs),results=[];
  const provenance={createdAt:new Date().toISOString(),suiteSha256:hash(suite),corpusSha256:hash(corpus),corpus,aiProvider:activeAI().provider,index:aiIndexConfig(),chatModel:activeAI().chatModel,judgeModel:judge?activeAI().judgeModel:null,answers,repetitions,order:'rotated by query and repetition',targets:qualityPolicy.targets,policy:qualityPolicy};
  for(let repeat=0;repeat<repetitions;repeat++)for(const [i,q] of suite.entries())for(let offset=0;offset<conditions.length;offset++){
    const condition=conditions[(i+repeat+offset)%conditions.length],started=Date.now();
    let row={queryId:q.id,repeat,...condition,expected:q.expected,answerable:q.expected==='answered',success:false,recallAt5:q.expected==='answered'?0:null,mrrAt5:q.expected==='answered'?0:null};
    try{
      const answer=await researchFn({store,jobs,question:q.question,tickers:q.tickers,periods:q.periods,strategy:condition.strategy,rerank:condition.rerank,history:q.history||[],retrievalOnly:!answers,trace:answers});
      row={...row,success:true,latencyMs:Date.now()-started,status:answer.status||null,statusCorrect:answers?answer.status===q.expected:null,
        ...(q.expected==='answered'?retrievalMetrics(answer.ranking||[],q.labels):{}),contextEvidenceRecall:answers&&q.labels.length?retrievalMetrics((answer.sources||[]).map(s=>({...s,text:s.excerpt||s.text||''})),q.labels,16).recallAt5:null,ranking:answer.ranking||[],answer:answers?{...answer,ranking:undefined}:null};
      if(answers&&judge&&answer.status==='answered'){
        try{row.automatedReview=await judge(q,answer);}catch(error){row.judgeError=error.message;}
      }
    }catch(error){row={...row,latencyMs:Date.now()-started,error:error.message,statusCorrect:answers?false:null};}
    results.push(row);await onResult(row,provenance);
  }
  return {provenance,queries:suite,results,summary:summarizeBenchmark(results,answers)};
}
const mean=values=>values.length?values.reduce((a,b)=>a+b,0)/values.length:null;
const percentile=(values,p)=>values.length?[...values].sort((a,b)=>a-b)[Math.max(0,Math.ceil(values.length*p)-1)]:null;
export function summarizeBenchmark(results,answers){
  const groups={};
  for(const {strategy,rerank} of conditions){
    const rows=results.filter(r=>r.strategy===strategy&&r.rerank===rerank),scored=rows.filter(r=>r.answerable);
    const claims=rows.flatMap(r=>r.automatedReview?.claims||[]);
    groups[`${strategy}/${rerank?'reranked':'baseline'}`]={queries:rows.length,failures:rows.filter(r=>!r.success).length,
      recallAt5:mean(scored.map(r=>r.recallAt5)),mrrAt5:mean(scored.map(r=>r.mrrAt5)),meanLatencyMs:mean(rows.map(r=>r.latencyMs)),p95LatencyMs:percentile(rows.map(r=>r.latencyMs),.95),
      statusAccuracy:answers?mean(rows.map(r=>Number(r.statusCorrect))):null,automatedFaithfulness:claims.length?claims.filter(c=>c.supported).length/claims.length:null,reviewedClaims:claims.length};
  }
  const deltas={};for(const strategy of ['fixed','semantic']){
    const a=groups[`${strategy}/baseline`],b=groups[`${strategy}/reranked`];
    deltas[strategy]=Object.fromEntries(['recallAt5','mrrAt5','meanLatencyMs'].map(key=>[key,a[key]===null||b[key]===null?null:b[key]-a[key]]));
  }
  return {groups,rerankingDeltas:deltas,latencyScope:answers?'end-to-end answer, excluding optional judge':'retrieval only',humanFaithfulness:'pending independent claim review'};
}
const format=(value,digits=3)=>value===null||value===undefined?'pending':value.toFixed(digits);
export function comparisonReport(run){
  const lines=['# Chunking comparison and reranking impact','',`Run: ${run.provenance.createdAt}`,`Corpus SHA-256: ${run.provenance.corpusSha256}`,`Query suite SHA-256: ${run.provenance.suiteSha256}`,'',`Latency scope: ${run.summary.latencyScope}. Failed requests remain in accuracy/recall denominators.`, '', '| Condition | Trials | Failures | Recall@5 | MRR@5 | p95 ms | Status accuracy |','|---|---:|---:|---:|---:|---:|---:|'];
  for(const [key,r] of Object.entries(run.summary.groups))lines.push(`| ${key} | ${r.queries} | ${r.failures} | ${format(r.recallAt5)} | ${format(r.mrrAt5)} | ${format(r.p95LatencyMs,0)} | ${format(r.statusAccuracy)} |`);
  lines.push('','## Paired reranking impact','','Positive retrieval deltas indicate better scores; positive latency deltas indicate added time.');
  for(const [key,r] of Object.entries(run.summary.rerankingDeltas))lines.push(`* ${key}: recall@5 delta ${format(r.recallAt5)}, MRR@5 delta ${format(r.mrrAt5)}, mean latency delta ${format(r.meanLatencyMs,0)} ms.`);
  lines.push('','## Interpretation and limitations','','Compare both chunkers on the same source and query hashes. Tables use identical table-aware splitting in both strategies; differences are primarily narrative segmentation. These evidence labels are source-specific and incomplete relevance judgments, so they measure required-evidence coverage rather than universal relevance. Small samples have substantial uncertainty. Reranking can regress; do not claim improvement when deltas are zero or negative.','','Automated claim reviews, when enabled, are model judgments and must not be presented as human faithfulness scores. Independent manual claim/citation/period/unit review remains required. End-to-end p95 must be compared with the 15-second target only when answer generation was enabled. Provider failures and judge failures are retained in JSON/JSONL.','','## Per-query evidence and review','','See the sibling JSON/JSONL and manual-review template. Document each regression, missing input, unsupported answer, period mismatch and unit mismatch before selecting a default.');
  return lines.join('\n')+'\n';
}
