// Handle an explicit FCF definition using the cash-flow table's signed values.
// Return null when the question or source layout cannot be resolved safely.
function readYearValue(row,header,year){
  const cells=header.flatMap((cell,index)=>cell===year?[row[index]]:[]).filter(cell=>cell&&cell!=='$');
  const numbers=cells.filter(cell=>/^\(?-?\d[\d,]*(?:\.\d+)?\)?$/.test(cell)).map(cell=>Number(cell.replace(/[(),]/g,''))*(cell.startsWith('(')?-1:1));
  return numbers.length&&numbers.every(n=>n===numbers[0])?numbers[0]:null;
}

export function cashFlowAdjustmentAnswer(question,sources){
  if(!/adjustment/i.test(question)||!/operating cash flow/i.test(question)||
    !/first six months|first half|\bH1\b|six months (?:ended|ending) June 30/i.test(question))return null;
  const years=[...new Set(question.match(/\b20\d{2}\b/g)||[])];
  if(years.length!==1||new Set(sources.map(s=>s.ticker)).size!==1)return null;
  const [year]=years,words=text=>(text.toLowerCase().match(/[a-z0-9]+/g)||[]),queryWords=new Set(words(question));
  const matches=[];
  for(const source of sources){
    if(!/\((?:dollars )?in millions\)/i.test(source.text))continue;
    const lines=source.text.split('\n');
    for(let i=0;i<lines.length;i++){
      const header=lines[i].split('|').map(s=>s.trim());
      if(!header.includes(year)||!/Six Months Ended June 30/i.test(lines.slice(Math.max(0,i-2),i).join(' ')))continue;
      let operating=false,reconciling=false;
      for(let j=i+1;j<lines.length&&lines[j].includes('|');j++){
        const row=lines[j].split('|').map(s=>s.trim()),label=row[0];
        if(/^Cash Flows from Operating Activities$/i.test(label))operating=true;
        if(/^Adjustments to reconcile net income/i.test(label))reconciling=true;
        if(/^Changes in operating assets|^Net cash provided|^Cash Flows from (?:Investing|Financing)/i.test(label))break;
        const tokens=words(label).filter(word=>!['and','the','of','net'].includes(word));
        if(!operating||!reconciling||tokens.length<3||!tokens.every(word=>queryWords.has(word)))continue;
        const value=readYearValue(row,header,year);
        if(value!==null)matches.push({label,value,source:source.id});
      }
    }
  }
  if(!matches.length||matches.some(m=>m.label!==matches[0].label||m.value!==matches[0].value))return null;
  const {label,value,source}=matches[0],amount=new Intl.NumberFormat('en-US',{maximumFractionDigits:2}).format(Math.abs(value));
  const signed=value<0?`−$${amount}`:`$${amount}`;
  const explanation=value<0?`It was deducted when reconciling net income to operating cash flow${/unrealized gain/i.test(label)?' to remove the noncash gain':''}`:value>0?'It was added when reconciling net income to operating cash flow':'It had no effect on the reconciliation';
  return {status:'answered',answer:`For the first six months of ${year}, the ${label} adjustment was ${signed} million${value<0?' (a negative adjustment)':''}. ${explanation} [${source}].`,citation_ids:[source],chart:{type:'none',title:'',unit:'',points:[]},calculations:[]};
}

export function cashFlowAnswer(question, sources) {
  if(!/free cash flow/i.test(question)||!/operating cash flow/i.test(question)||
    !/minus/i.test(question)||!/purchases of property and equipment excluding finance leases,?\s+net of sales/i.test(question)||
    !/first six months|first half|\bH1\b|six months (?:ended|ending) June 30/i.test(question))return null;
  const years=[...new Set(question.match(/\b20\d{2}\b/g)||[])];
  if(years.length!==2||new Set(sources.map(s=>s.ticker)).size!==1)return null;
  const periods=new Map();
  for(const source of sources){
    if(!/\((?:dollars )?in millions\)/i.test(source.text))continue;
    const lines=source.text.split('\n');
    for(let i=0;i<lines.length;i++){
      const header=lines[i].split('|').map(s=>s.trim());
      if(!years.every(year=>header.includes(year))||!/Six Months Ended June 30/i.test(lines.slice(Math.max(0,i-2),i).join(' ')))continue;
      const rows=[];
      for(let j=i+1;j<lines.length&&lines[j].includes('|');j++)rows.push(lines[j].split('|').map(s=>s.trim()));
      const operating=rows.find(row=>/^Net cash provided by(?: \(used in\))? operating activities$/i.test(row[0]));
      const capex=rows.find(row=>/^Purchases of property and equipment excluding finance leases, net of sales$/i.test(row[0]));
      if(!operating||!capex)continue;
      for(const year of years){
        const inflow=readYearValue(operating,header,year),outflow=readYearValue(capex,header,year);
        if(inflow===null||outflow===null||outflow>0)continue;
        const prior=periods.get(year);
        if(prior&&(prior.inflow!==inflow||prior.outflow!==outflow))return null;
        periods.set(year,{inflow,outflow,source:source.id});
      }
    }
  }
  if(!years.every(year=>periods.has(year)))return null;
  const calculations=years.map(year=>{
    const {inflow,outflow,source}=periods.get(year);
    return {label:`Free cash flow, first six months of ${year}`,operation:'sum',unit:'USD millions',values:[inflow,outflow],source_ids:[source],result:inflow+outflow};
  });
  const [earlier,later]=[...years].sort().map(year=>({year,...periods.get(year)}));
  const change=(later.inflow+later.outflow)-(earlier.inflow+earlier.outflow);
  const format=value=>new Intl.NumberFormat('en-US',{maximumFractionDigits:2}).format(value);
  const citation_ids=[...new Set(calculations.flatMap(c=>c.source_ids))];
  const answer=years.map(year=>{const p=periods.get(year);return `For the first six months of ${year}, free cash flow was $${format(p.inflow+p.outflow)} million: $${format(p.inflow)} million of operating cash flow less $${format(-p.outflow)} million of purchases of property and equipment excluding finance leases, net of sales [${p.source}].`;}).join(' ')+
    ` ${later.inflow>earlier.inflow?(change>0?'Yes. Stronger operating cash flow translated into higher free cash flow.':`No. Operating cash flow increased, but free cash flow ${change<0?'fell':'was unchanged'} because capital spending ${change<0?'increased by more':'rose by the same amount'}.`):'Operating cash flow did not increase over these periods.'} Free cash flow ${change<0?'decreased':change>0?'increased':'changed'} by $${format(Math.abs(change))} million from ${earlier.year} to ${later.year} ${citation_ids.map(id=>`[${id}]`).join(' ')}.`;
  return {status:'answered',answer,citation_ids,chart:{type:'none',title:'',unit:'',points:[]},calculations};
}
