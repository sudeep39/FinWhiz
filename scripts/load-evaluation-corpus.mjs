// Explicit operator action: never automatically prepopulate the Research screen.
import { readFile } from 'node:fs/promises';
import { activeAI, enterAIProvider } from '../lib/ai-config.mjs';
enterAIProvider(process.argv.find(a=>a.startsWith('--provider='))?.slice(11));
import { createHash } from 'node:crypto';
const experiment=JSON.parse(await readFile(new URL('../experiment.json',import.meta.url)));
const manifest=JSON.parse(await readFile(new URL('../data/filings/manifest.json',import.meta.url)));
const files=manifest.entries.filter(e=>experiment.tickers.includes(e.ticker));
if(files.length!==experiment.tickers.length*experiment.periods_per_company)throw new Error('Unexpected evaluation corpus count.');
const form=new FormData();const details=[];
for(const [i,file] of files.entries()){
  const bytes=await readFile(new URL(`../${file.localPath}`,import.meta.url));
  if(createHash('sha256').update(bytes).digest('hex')!==file.sha256)throw new Error(`Checksum mismatch: ${file.localPath}`);
  form.append(`file_${i}`,new Blob([bytes]),file.localPath.split('/').at(-1));
  details.push({ticker:file.ticker,form:file.form,reportDate:file.reportDate});
}
form.append('details',JSON.stringify(details));
console.log(`Verified ${files.length} source files for the same two chunking strategies.`);
if(process.argv.includes('--apply')){
  const base=process.env.FINWHIZ_URL||'http://127.0.0.1:3100';
  if(!/^https:\/\//.test(base)&&!/^http:\/\/(?:127\.0\.0\.1|localhost):\d+$/.test(base))throw new Error('Use HTTPS or a local server address.');
  const response=await fetch(`${base}/api/uploads`,{method:'POST',headers:{'X-AI-Provider':activeAI().provider,...(process.env.APP_PASSWORD?{Authorization:`Bearer ${process.env.APP_PASSWORD}`}:{})},body:form});
  const result=await response.json();if(!response.ok)throw new Error(result.error);console.log(JSON.stringify(result,null,2));
}else console.log('Validation only. Start the app and use --apply to submit this corpus; ingestion uses paid providers.');
