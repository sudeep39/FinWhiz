import { qualityPolicy } from './operations.mjs';
const key=r=>`${r.queryId}/${r.strategy}/${r.rerank}/${r.repeat}`;
export function scoreManualReviews(run,reviews){
  if(!run.provenance.answers)throw new Error('Manual answer scoring requires an end-to-end run.');
  if(!Array.isArray(reviews)||new Set(reviews.map(key)).size!==reviews.length)throw new Error('Invalid or duplicate manual reviews.');
  const keys=new Set(run.results.map(key));
  if(reviews.some(r=>!keys.has(key(r))))throw new Error('Review does not belong to this run.');
  const groups={};
  for(const [name,summary] of Object.entries(run.summary.groups)){
    const [strategy,mode]=name.split('/'),rows=run.results.filter(r=>r.strategy===strategy&&r.rerank===(mode==='reranked'));
    const selected=rows.map(r=>reviews.find(v=>key(v)===key(r)));
    const complete=rows.length>0&&selected.every((r,i)=>r?.reviewed===true&&typeof r.reviewer==='string'&&r.reviewer.trim()&&Array.isArray(r.claims)&&(!rows[i].answerable||r.claims.length)&&r.claims.every(c=>typeof c.text==='string'&&c.text.trim()&&typeof c.supported==='boolean'&&typeof c.citationCorrect==='boolean')&&['numericCorrect','unitsCorrect','periodsCorrect','relevantAnswer'].every(k=>typeof r[k]==='boolean'));
    const claims=selected.flatMap(r=>r?.reviewed?r.claims||[]:[]);
    const faithfulness=complete&&claims.length?claims.filter(c=>c.supported).length/claims.length:null;
    const citationAccuracy=complete&&claims.length?claims.filter(c=>c.citationCorrect).length/claims.length:null;
    const correct=complete?selected.filter(r=>r.numericCorrect&&r.unitsCorrect&&r.periodsCorrect).length/rows.length:null;
    const relevance=complete?selected.filter(r=>r.relevantAnswer).length/rows.length:null;
    const passed=complete&&summary.failures===0&&faithfulness!==null&&faithfulness>=qualityPolicy.targets.faithfulness&&citationAccuracy>=qualityPolicy.targets.citationAccuracy&&relevance>=qualityPolicy.targets.answerRelevance&&correct>=qualityPolicy.targets.numericAccuracy&&summary.recallAt5>=qualityPolicy.targets.recallAt5&&summary.statusAccuracy>=qualityPolicy.targets.statusAccuracy&&summary.p95LatencyMs<=qualityPolicy.service.answerP95Ms;
    groups[name]={reviewComplete:complete,faithfulness,citationAccuracy,answerRelevance:relevance,numericUnitPeriodAccuracy:correct,acceptance:complete?(passed?'pass':'fail'):'pending'};
  }
  return {scoredAt:new Date().toISOString(),groups,targets:qualityPolicy,source:'independent manual review; self-reported reviewer identities'};
}
