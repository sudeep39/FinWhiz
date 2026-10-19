import { Storage } from '../lib/storage.mjs';
import { operationalStatus } from '../lib/operations.mjs';
const store=await new Storage().init();
const {jobs}=await store.json('state',{jobs:[]});
if(!process.argv.includes('--apply')){
  console.log(JSON.stringify(operationalStatus(jobs,await store.json('freshness-check')),null,2));
  console.log('Read-only local status. --apply checks SEC for each loaded CIK and records a weekly review.');
}else{
  if(!process.env.SEC_USER_AGENT||!/@/.test(process.env.SEC_USER_AGENT))throw new Error('Configure SEC_USER_AGENT with your app name and contact email.');
  const documents=jobs.filter(j=>j.status==='ready').flatMap(j=>j.documents||[]);
  const issuers=[...new Set(documents.map(d=>d.cik).filter(Boolean))];
  if(!issuers.length)throw new Error('No loaded CIK identifiers; verify filing metadata before checking SEC.');
  const updates=[];
  for(const cik of issuers){
    if(!/^\d{1,10}$/.test(cik))throw new Error('Invalid CIK');
    const response=await fetch(`https://data.sec.gov/submissions/CIK${cik.padStart(10,'0')}.json`,{headers:{'User-Agent':process.env.SEC_USER_AGENT,Accept:'application/json'},signal:AbortSignal.timeout(30000)});
    if(!response.ok)throw new Error(`SEC HTTP ${response.status}; review not marked complete.`);
    const data=await response.json(),recent=data.filings.recent;
    const known=documents.filter(d=>d.cik===cik);
    const newest=known.map(d=>d.reportDate).sort().at(-1);
    for(let i=0;i<recent.form.length;i++)if(['10-K','10-Q','10-K/A','10-Q/A'].includes(recent.form[i])&&recent.reportDate[i]>=newest&&!known.some(d=>d.accession===recent.accessionNumber[i]))updates.push({cik,ticker:known[0].ticker,form:recent.form[i],reportDate:recent.reportDate[i],accession:recent.accessionNumber[i]});
    await new Promise(resolve=>setTimeout(resolve,300));
  }
  const result={checkedAt:new Date().toISOString(),updates,checkedCiks:issuers};await store.putJson('freshness-check',result);console.log(JSON.stringify(result,null,2));
}
await store.close();
