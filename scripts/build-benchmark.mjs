// Gold candidates come from original source tables/passages, never retrieval output.
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { extractHtml } from '../lib/chunking.mjs';
const config=JSON.parse(await readFile(new URL('../experiment.json',import.meta.url)));
const manifest=JSON.parse(await readFile(new URL('../data/filings/manifest.json',import.meta.url)));
const entries=manifest.entries.filter(e=>config.tickers.includes(e.ticker)).sort((a,b)=>a.ticker.localeCompare(b.ticker)||b.reportDate.localeCompare(a.reportDate));
const documents=[];
for(const entry of entries){
  const bytes=await readFile(new URL(`../${entry.localPath}`,import.meta.url));
  if(createHash('sha256').update(bytes).digest('hex')!==entry.sha256)throw new Error(`Changed source: ${entry.localPath}`);
  documents.push({...entry,blocks:extractHtml(bytes.toString('utf8'))});
}
const output=[];
const label=(doc,contains,quote)=>({ticker:doc.ticker,reportDate:doc.reportDate,accession:doc.accession,sourceSha256:doc.sha256,contains,sourceUrl:doc.url,sourceQuote:quote});
function tableLabel(doc,expression){
  for(const block of doc.blocks.filter(b=>b.kind==='table'))for(const line of block.text.split('\n'))if(expression.test(line)){
    const metric=line.split('|')[0].trim(),value=line.split('|').slice(1).map(s=>s.trim()).find(s=>/^\(?-?\d[\d,]*(?:\.\d+)?\)?$/.test(s));
    if(value)return label(doc,[metric,value],block.text.split('\n').slice(0,3).join('\n')+'\n'+line);
  }
  throw new Error(`No source metric found: ${doc.ticker} ${doc.reportDate} ${expression}`);
}
const revenue=doc=>tableLabel(doc,/^Total revenues?\s*\|/i);
function add(pattern,question,docs,labels,expected='answered'){
  output.push({id:`B${String(output.length+1).padStart(2,'0')}`,pattern,question,tickers:[...new Set(docs.map(d=>d.ticker))],periods:[...new Set(docs.map(d=>d.reportDate))].sort(),expected,reviewed:false,reviewer:'',labels});
}
for(const doc of documents)add(doc.form==='10-K'?'annual-table':'quarterly-table',`What total revenue does ${doc.ticker} report for the ${doc.form==='10-K'?'full fiscal year':'three-month quarter'} ended ${doc.reportDate}? State currency and scale; distinguish quarterly from YTD values.`,[doc],[revenue(doc)]);
const issuer=t=>documents.filter(d=>d.ticker===t),nv=issuer('NVDA'),ts=issuer('TSLA');
add('segment','What Data Center revenue does NVIDIA report for its latest three-month quarter?',[nv[0]],[tableLabel(nv[0],/^Data Center\s*\|/i)]);
add('exact-term','What automotive regulatory credits does Tesla report in its latest three-month quarter?',[ts[0]],[tableLabel(ts[0],/^Automotive regulatory credits\s*\|/i)]);
for(const [docs,term,question] of [[nv,'export control','What export-control risks does NVIDIA disclose in its latest annual filing?'],[ts,'competition','What competition risks does Tesla disclose in its latest annual filing?']]){
  const doc=docs.find(d=>d.form==='10-K'),block=doc.blocks.find(b=>b.kind==='narrative'&&/risk/i.test(b.section)&&b.text.length>250&&b.text.toLowerCase().includes(term));
  if(!block)throw new Error(`Missing narrative label: ${term}`);
  const start=Math.max(0,block.text.toLowerCase().indexOf(term)-80),quote=block.text.slice(start,start+450);
  add('narrative-risk',question,[doc],[label(doc,[term],quote)]);
}
for(const docs of [nv,ts]){
  const annual=docs.find(d=>d.form==='10-K'),nine=docs.find(d=>d.form==='10-Q'&&d.reportDate<annual.reportDate);
  const ytd=revenue(nine),values=[...new Set(ytd.sourceQuote.split('\n').at(-1).split('|').map(s=>s.trim()).filter(s=>/^\d[\d,]*$/.test(s)))];
  if(values.length<3)throw new Error('Cannot identify nine-month revenue input.');
  ytd.contains=[ytd.contains[0],values[2]];
  add('derived-q4',`Derive ${annual.ticker} fourth-quarter revenue for the fiscal year ended ${annual.reportDate} from annual and matching nine-month amounts. Cite both inputs; show the subtraction.`,[annual,nine],[revenue(annual),ytd]);
}
for(const docs of [nv,ts])add('eight-period-trend',`Show ${docs[0].ticker} revenue over all eight selected fiscal period ends. Annual reports are annual totals; derive standalone Q4 only when nine-month inputs support it. Do not mix annual and quarterly values in a single trend.`,docs,docs.map(revenue));
add('cross-company','Compare the latest reported quarterly revenue for NVIDIA and Tesla. State both fiscal period ends and units.',[nv[0],ts[0]],[revenue(nv[0]),revenue(ts[0])]);
const annuals=[nv.find(d=>d.form==='10-K'),ts.find(d=>d.form==='10-K')];
add('cross-company-annual','Compare total revenue in the latest annual filings of NVIDIA and Tesla; explain the different fiscal year ends.',annuals,annuals.map(revenue));
for(const docs of [nv,ts]){const annual=docs.filter(d=>d.form==='10-K');add('growth-calculation',`Calculate ${docs[0].ticker} annual revenue growth between the two selected annual reports. Cite consistently scaled inputs.`,annual,annual.map(revenue));}
add('ambiguous','What is profit?',[nv[0]],[],'needs_clarification');
add('unanswerable','What will the NVIDIA stock price be next Friday?',[nv[0]],[],'insufficient_evidence');
add('missing-corpus','What did Tesla executives say on the latest earnings call? Use the transcript.',[ts[0]],[],'insufficient_evidence');
add('missing-period','What was NVIDIA revenue for fiscal 2010? Use only the selected filings.',nv,[],'insufficient_evidence');
if(output.length!==32)throw new Error('Expected 32 questions.');
const path=new URL('../evaluation/benchmark.json',import.meta.url);
if(!process.argv.includes('--write'))console.log('Use --write to create source-labelled candidates; review source quotes before enabling benchmark execution.');
else{try{await readFile(path);if(!process.argv.includes('--replace'))throw new Error('Benchmark already exists. Use --replace only after backing up reviewed labels.');}catch(e){if(e.code!=='ENOENT')throw e;}await writeFile(path,JSON.stringify(output,null,2)+'\n');}
for(const q of output)console.log(JSON.stringify({id:q.id,pattern:q.pattern,labels:q.labels.map(l=>({ticker:l.ticker,period:l.reportDate,contains:l.contains,quote:l.sourceQuote.slice(-460)}))}));
