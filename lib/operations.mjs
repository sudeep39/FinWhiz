import { readFile } from 'node:fs/promises';
export const qualityPolicy=JSON.parse(await readFile(new URL('../quality-policy.json',import.meta.url),'utf8'));
const age=(date,now)=>Number.isFinite(Date.parse(date))?Math.max(0,now-Date.parse(date)):Infinity;
export function operationalStatus(jobs,check=null,now=Date.now()) {
  const documents=jobs.filter(j=>j.status==='ready').flatMap(j=>j.documents||[]);
  const latest=new Map();for(const doc of documents)if(!latest.has(doc.ticker)||latest.get(doc.ticker)<doc.reportDate)latest.set(doc.ticker,doc.reportDate);
  return {policy:qualityPolicy,checkedAt:new Date(now).toISOString(),
    freshness:{lastReviewedAt:check?.checkedAt||null,reviewDue:!check||age(check.checkedAt,now)>qualityPolicy.freshness.reviewEveryDays*86400000,
      companies:[...latest].map(([ticker,reportDate])=>({ticker,reportDate,ageDays:Math.floor(age(reportDate,now)/86400000),stale:age(reportDate,now)>qualityPolicy.freshness.maxPeriodAgeDays*86400000})),updates:check?.updates||[]},
    sla:{breaches:jobs.filter(j=>j.status==='queued'&&age(j.createdAt,now)>qualityPolicy.service.maxQueueMinutes*60000||j.status==='processing'&&age(j.startedAt,now)>qualityPolicy.service.maxProcessingMinutes*60000).map(j=>({jobId:j.id,status:j.status})),
      completed:jobs.filter(j=>j.status==='ready'&&j.startedAt&&j.completedAt).map(j=>({jobId:j.id,processingMs:Date.parse(j.completedAt)-Date.parse(j.startedAt)}))}};
}
