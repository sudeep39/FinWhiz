const $=id=>document.getElementById(id);
const make=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;};
let state=null,view='research',selectedFiles=[],fileDetails=[],busy=false,uploading=false,periodSelection=new Set(),conversation=[],password=sessionStorage.getItem('finwhiz-password')||'';
let toastTimer,pollTimer;
let initialLibraryProviderResolved=false;
let selectedProvider=sessionStorage.getItem('finwhiz-provider')||'';
$('ai-provider').value=selectedProvider||'openai';
$('ai-provider').onchange=()=>{if(busy||uploading){$('ai-provider').value=selectedProvider||'openai';return;}selectedProvider=$('ai-provider').value;sessionStorage.setItem('finwhiz-provider',selectedProvider);periodSelection.clear();clearChat();refresh().catch(error=>toast(error.message,true));};
const freshnessNote=make('p','upload-explanation');freshnessNote.hidden=true;document.querySelector('.workflow-card').append(freshnessNote);
const date=value=>new Date(`${value}T12:00:00Z`).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'});
const number=value=>new Intl.NumberFormat('en-US',{maximumFractionDigits:2}).format(value);
function toast(message,error=false){clearTimeout(toastTimer);$('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,error?9000:6500);}
async function api(path,options={}){
  const headers={...(selectedProvider?{'X-AI-Provider':selectedProvider}:{}),...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...(password?{Authorization:`Bearer ${password}`}:{})};
  const response=await fetch(path,{...options,headers:{...headers,...options.headers}});
  if(response.status===401){$('password-dialog').showModal();throw new Error('Enter your workspace password to continue.');}
  const result=await response.json();if(!response.ok)throw new Error(result.error||'The request failed.');return result;
}
function switchView(next){view=next;location.hash=next;$('research-view').hidden=next!=='research';$('documents-view').hidden=next!=='documents';$('page-title').textContent=next==='research'?'Research':'Documents';document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===next));}
document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>switchView(button.dataset.view));
$('open-documents').onclick=$('manage-corpus').onclick=()=>switchView('documents');
$('empty-upload').onclick=()=>switchView('documents');
window.addEventListener('hashchange',()=>switchView(location.hash==='#documents'?'documents':'research'));
$('connection-toggle').onclick=()=>$('connection-panel').hidden=!$('connection-panel').hidden;
$('change-password').onclick=()=>$('password-dialog').showModal();
$('password-form').onsubmit=async event=>{event.preventDefault();password=$('workspace-password').value;try{await refresh();sessionStorage.setItem('finwhiz-password',password);$('password-dialog').close();$('password-error').textContent='';}catch(error){$('password-error').textContent=error.message;}};
const selectedTickers=()=>$('company-select').value==='compare'?state.companies.slice(0,4).map(c=>c.ticker):[$('company-select').value].filter(Boolean);
const scopeDocuments=()=>state?.documents.filter(d=>selectedTickers().includes(d.ticker))||[];
function selectedPeriods(){return periodSelection.size?[...periodSelection]:[];}
function readyToChat(){return state?.config.providersConfigured&&selectedTickers().length>0&&selectedTickers().every(t=>state.documents.some(d=>d.ticker===t&&d.strategies.includes($('strategy-select').value)));}
function clearChat(){conversation=[];$('messages').replaceChildren();$('welcome').hidden=false;sessionStorage.removeItem('finwhiz-conversation');}
$('company-select').onchange=()=>{periodSelection.clear();clearChat();renderScope();};
$('new-chat').onclick=clearChat;
$('strategy-select').onchange=()=>{clearChat();renderScope();};
$('all-periods').onclick=()=>{periodSelection.clear();renderScope();};
function renderScope(){
  if(!state)return;
  const tickers=selectedTickers(),companies=state.companies.filter(c=>tickers.includes(c.ticker)),compare=tickers.length>1;
  const company=companies[0];
  const hasCorpus=Boolean(company);
  $('research-view').classList.toggle('empty-corpus',!hasCorpus);
  $('research-empty').hidden=hasCorpus;
  for(const id of ['research-scope','chat-scroll','research-composer','knowledge-context'])$(id).hidden=!hasCorpus;
  if(!company){
    for(const id of ['selected-monogram','context-monogram']){$(id).textContent='—';$(id).className='company-monogram';}
    $('scope-periods').textContent='No filing periods';$('context-company-name').textContent='No corpus loaded';$('context-ticker').textContent='Upload documents to begin';
    $('dataset-title').textContent='Your research starts with your documents';$('dataset-description').textContent='Upload financial filings in Documents';$('dataset-status').textContent='No corpus';$('dataset-status').className='pill amber';
    $('composer-scope').textContent='No company selected';$('ready-count').textContent='0';$('period-list').replaceChildren();$('all-periods').disabled=true;$('send-button').disabled=true;
    $('research-notice').hidden=false;$('research-notice').textContent='Upload your filings in Documents. Companies and reporting periods will appear here after processing completes.';return;
  }
  $('all-periods').disabled=false;
  const display=companies.map(c=>c.name).join(' + ');
  for(const id of ['selected-monogram','context-monogram']){$(id).textContent=compare?'↔':company.ticker[0];$(id).className=`company-monogram ${compare?'compare':''}`;}
  $('context-company-name').textContent=display;$('context-ticker').textContent=`${tickers.join(' + ')} · SEC filings`;
  const periods=companies.flatMap(c=>c.periods.map(p=>({...p,ticker:c.ticker}))).sort((a,b)=>b.reportDate.localeCompare(a.reportDate));
  $('scope-periods').textContent=`${periods.length} fiscal periods`;
  $('dataset-title').textContent=`${display} · Company filings`;$('dataset-description').textContent=`${periods.length} fiscal periods · 10-K & 10-Q`;
  const ready=periods.filter(p=>p.ready).length;
  $('dataset-status').textContent=ready===periods.length&&ready?'Ready for research':ready?`${ready} of ${periods.length} ready`:'Not indexed';
  $('dataset-status').className=`pill ${ready===periods.length&&ready?'':'amber'}`;
  $('composer-scope').textContent=`${display} filings`;$('ready-count').textContent=ready;
  $('period-list').replaceChildren(...periods.map(period=>{
    const row=make('label',`period-row ${period.form==='10-K'?'annual':''}`),check=make('input');check.type='checkbox';check.disabled=!period.ready;
    check.checked=!periodSelection.size||periodSelection.has(period.reportDate);row.classList.toggle('selected',check.checked&&period.ready);
    check.onchange=()=>{
      if(!periodSelection.size)periods.filter(p=>p.ready).forEach(p=>periodSelection.add(p.reportDate));
      if(check.checked)periodSelection.add(period.reportDate);else periodSelection.delete(period.reportDate);
      // Empty selection means all periods; display that convention explicitly.
      renderScope();
    };
    row.append(check,make('span','period-date',`${compare?period.ticker+' · ':''}${date(period.reportDate)}`),make('span','form-badge',period.form),make('span',`status-dot ${period.ready?'':'amber'}`));
    row.title=period.ready?'Indexed and available for research':'Downloaded locally; not yet indexed';return row;
  }));
  $('send-button').disabled=busy||!readyToChat();
  $('research-notice').hidden=readyToChat();
  $('research-notice').textContent=!state.config.providersConfigured?'Configure the selected AI provider and Pinecone to start researching.':`No ready documents are available for ${display} with this strategy.`;
}
function renderJobs(){
  $('jobs-list').replaceChildren();
  if(!state.jobs.length){$('jobs-list').append(make('div','empty-activity','No processing batches yet. Upload your financial documents to begin.'));return;}
  for(const job of state.jobs){
    const card=make('article','job-card'),heading=make('div','job-heading');
    heading.append(make('strong','',`${job.files.length} documents · ${new Date(job.createdAt).toLocaleString()}`),make('span',`pill ${job.status==='ready'?'':job.status==='failed'?'failed':'amber'}`,job.status==='ready'?'Ready for research':job.status==='failed'?'Needs attention':job.status==='queued'?'Queued':'Processing'));
    card.append(heading,make('p','',`${job.aiConfig?.provider==='nebius'?'Nebius Token Factory':'OpenAI'} · ${job.detail||job.phase}`));
    if(job.status==='failed'){card.append(make('p','job-error',job.error));const retry=make('button','text-button','Retry processing →');retry.onclick=async()=>{try{await api(`/api/jobs/${job.id}/retry`,{method:'POST',body:'{}'});await refresh();}catch(error){toast(error.message,true);}};card.append(retry);}
    const track=make('div','progress-track'),bar=make('span');bar.style.width=`${job.percent||0}%`;track.append(bar);card.append(track);
    const note=job.status==='ready'?job.summaries.map(s=>`${s.strategy}: ${number(s.chunks)} chunks`).join(' · '):'You can close this page. Processing continues on the server.';
    card.append(make('small','',note));$('jobs-list').append(card);
  }
}
async function refresh(){
  const requestedProvider=selectedProvider;
  const updated=await api('/api/state');if(selectedProvider!==requestedProvider)return;
  if(!initialLibraryProviderResolved){
    initialLibraryProviderResolved=true;
    if(!updated.documents?.length){
      const available=(updated.jobs||[]).find(job=>job.status==='ready'&&job.documents?.length&&['openai','nebius'].includes(job.aiConfig?.provider));
      if(available&&available.aiConfig.provider!==(updated.config.aiProvider||selectedProvider)){
        selectedProvider=available.aiConfig.provider;sessionStorage.setItem('finwhiz-provider',selectedProvider);await refresh();return;
      }
    }
  }
  state=updated;
  selectedProvider=state.config.aiProvider||selectedProvider||'openai';$('ai-provider').value=selectedProvider;
  // Do not display legacy catalog-only company entries from an older server.
  // A company and period must have a corresponding completed-upload document.
  state.companies=(state.companies||[]).filter(c=>(state.documents||[]).some(d=>d.ticker===c.ticker)).map(c=>({...c,periods:c.periods.filter(p=>(state.documents||[]).some(d=>d.ticker===c.ticker&&d.reportDate===p.reportDate&&d.form===p.form))}));
  const previous=$('company-select').value;
  const options=state.companies.map(c=>({value:c.ticker,label:`${c.name} · ${c.ticker}`}));
  if(state.companies.length>=2)options.push({value:'compare',label:state.companies.length>4?'Compare loaded companies (first 4)':'Compare loaded companies'});
  if(!options.length)options.push({value:'',label:'No documents loaded'});
  $('company-select').disabled=!state.companies.length;
  if(JSON.stringify(options)!==$('company-select').dataset.options){$('company-select').replaceChildren(...options.map(o=>{const opt=make('option','',o.label);opt.value=o.value;return opt;}));$('company-select').value=options.some(o=>o.value===previous)?previous:options[0]?.value;$('company-select').dataset.options=JSON.stringify(options);}
  $('nav-doc-count').textContent=state.documents.length;
  $('connection-text').textContent=state.config.providersConfigured?'Providers configured':'Setup required';$('connection-dot').classList.toggle('amber',!state.config.providersConfigured);
  $('connection-details').textContent=`${state.config.providersConfigured?'Provider credentials are configured.':'The selected AI provider and/or Pinecone credentials are missing.'} Pinecone stores vectors. Uploads and processing state use server files; hosted persistence must be configured separately.`;
  renderScope();renderJobs();
  if(state.documents.length){
    const operations=await api('/api/operations').catch(()=>null);
    if(operations){freshnessNote.hidden=false;freshnessNote.textContent=operations.freshness.updates.length?'New or amended SEC filings are available. Review your corpus.':operations.freshness.reviewDue?'Weekly source review is due. Existing answers use the uploaded filing dates.':'Source review is current. Answers use the filing periods you select.';if(operations.sla.breaches.length)freshnessNote.textContent+=' A processing batch has exceeded its service target.';}
  }else freshnessNote.hidden=true;
  clearTimeout(pollTimer);pollTimer=setTimeout(()=>refresh().catch(error=>{toast(error.message,true);}),state.jobs.some(j=>['queued','processing'].includes(j.status))?4000:15000);
}
$('refresh-jobs').onclick=()=>refresh().catch(error=>toast(error.message,true));
function inferDetails(file,text=''){
  const value=name=>{const match=text.match(new RegExp(`name=["']dei:${name}["'][^>]*>([\\s\\S]*?)<\\/ix:nonnumeric>`,'i'));return match?match[1].replace(/<[^>]*>/g,'').trim():'';};
  let rawDate=value('DocumentPeriodEndDate'),reportDate=file.name.match(/\d{4}-\d{2}-\d{2}/)?.[0]||'';
  if(!reportDate&&rawDate){const d=new Date(rawDate);if(Number.isFinite(d.getTime()))reportDate=d.toISOString().slice(0,10);}
  const ticker=value('TradingSymbol');
  return {ticker,reportDate,form:value('DocumentType')||file.name.match(/10-[KQ]/i)?.[0]?.toUpperCase()||'10-Q'};
}
async function addFiles(files){
  const picked=[...files];
  for(const file of picked){
    if(!/\.(html?|pdf|txt)$/i.test(file.name)){toast(`${file.name}: choose HTML, PDF, or TXT.`,true);continue;}
    if(file.size>20*1024*1024){toast(`${file.name}: file exceeds 20 MB.`,true);continue;}
    if(selectedFiles.length>=32){toast('A batch can contain up to 32 files.',true);break;}
    if(selectedFiles.some(f=>f.name===file.name&&f.size===file.size)){continue;}
    const text=/\.html?$/i.test(file.name)?await file.slice(0,250000).text():'';
    selectedFiles.push(file);fileDetails.push(inferDetails(file,text));
  }
  renderFiles();
}
function renderFiles(){
  $('selected-files').replaceChildren(...selectedFiles.map((file,i)=>{
    const row=make('div','selected-file'),head=make('div','file-heading'),remove=make('button','remove-file','×');remove.title=`Remove ${file.name}`;remove.onclick=()=>{selectedFiles.splice(i,1);fileDetails.splice(i,1);renderFiles();};
    head.append(make('span','','▧'),make('strong','',file.name),make('small','',`${(file.size/1024/1024).toFixed(1)} MB`),remove);
    const details=make('div','file-details');
    for(const [key,label] of [['ticker','Ticker'],['form','Report'],['reportDate','Fiscal period end']]){
      const field=make('label','',label),input=make(key==='form'?'select':'input');
      if(key==='form')input.append(...['10-Q','10-K'].map(v=>{const o=make('option','',v);o.value=v;return o;}));else input.type=key==='reportDate'?'date':'text';
      input.value=fileDetails[i][key];input.setAttribute('aria-label',`${file.name}: ${label}`);if(key==='ticker'){input.maxLength=10;input.placeholder='Auto-detect';}
      input.onchange=()=>{fileDetails[i][key]=key==='ticker'?input.value.trim().toUpperCase():input.value;input.value=fileDetails[i][key];};field.append(input);details.append(field);
    }
    row.append(head,details);return row;
  }));
  const total=selectedFiles.reduce((n,f)=>n+f.size,0);
  $('upload-selection-label').textContent=selectedFiles.length?`${selectedFiles.length} files · ${(total/1024/1024).toFixed(1)} MB`:'No files selected';
  $('upload-submit').disabled=uploading||!selectedFiles.length;
}
$('file-picker').onchange=event=>{addFiles(event.target.files).catch(error=>toast(error.message,true));event.target.value='';};
for(const event of ['dragover','dragenter'])$('drop-zone').addEventListener(event,e=>{e.preventDefault();$('drop-zone').classList.add('dragging');});
for(const event of ['dragleave','drop'])$('drop-zone').addEventListener(event,e=>{e.preventDefault();$('drop-zone').classList.remove('dragging');});
$('drop-zone').addEventListener('drop',e=>addFiles(e.dataTransfer.files).catch(error=>toast(error.message,true)));
$('upload-submit').onclick=async()=>{
  if(!selectedFiles.length||uploading)return;
  const total=selectedFiles.reduce((n,f)=>n+f.size,0);if(total>100*1024*1024){toast('A batch must be smaller than 100 MB.',true);return;}
  if(fileDetails.some((d,i)=>!/\.html?$/i.test(selectedFiles[i].name)&&(!d.ticker||!d.reportDate))){toast('Confirm the ticker and fiscal period end for PDF and text files.',true);return;}
  const data=new FormData();data.append('details',JSON.stringify(fileDetails));selectedFiles.forEach((file,i)=>data.append(`file_${i}`,file));
  uploading=true;$('ai-provider').disabled=true;$('upload-submit').textContent='Uploading files…';renderFiles();
  try{const result=await api('/api/uploads',{method:'POST',body:data});selectedFiles=[];fileDetails=[];const banner=make('div','success-banner');banner.append(make('strong','',result.created===false?'Existing files found.':'Upload accepted.'),make('span','',result.message));$('documents-view').prepend(banner);setTimeout(()=>banner.remove(),30000);toast(result.created===false?'Existing files reused; no duplicate indexing.':'Upload accepted. New files will process in the background.');await refresh();}
  catch(error){toast(error.message,true);}finally{uploading=false;$('ai-provider').disabled=busy;$('upload-submit').textContent='Upload & process ↗';renderFiles();}
};

const scrollBottom=()=>{$('chat-scroll').scrollTop=$('chat-scroll').scrollHeight;};
function addMessage(role,text){
  const message=make('article',`message ${role==='user'?'user-message':'assistant-message'}`),head=make('div','message-header');
  head.append(make('span','message-avatar',role==='user'?'A':'f'),make('span','',role==='user'?'You':'Finwhiz'));message.append(head);
  const content=make('div','message-content',text);message.append(content);$('messages').append(message);$('welcome').hidden=true;return {message,content};
}
function sourceButton(id,sources,label=id){const button=make('button','citation-button',label);button.onclick=()=>showSource(sources.find(s=>s.id===id));return button;}
function citationText(node,text,sources){const re=/\[(S\d+)\]/g;let cursor=0;for(const match of text.matchAll(re)){node.append(document.createTextNode(text.slice(cursor,match.index)),sourceButton(match[1],sources));cursor=match.index+match[0].length;}node.append(document.createTextNode(text.slice(cursor)));}
async function showSource(source){
  if(!source)return;
  const content=$('source-content');content.replaceChildren(make('div','eyebrow',`${source.id} · SOURCE EVIDENCE`),make('h2','source-title',`${source.company} · ${source.form}`),make('p','source-details',`Period ended ${date(source.report_date)}\n${source.section_path}`),make('div','source-excerpt',source.excerpt));
  const link=make('a','source-link','Open original source ↗');
  if(source.source_url.startsWith('/api/sources/')){
    link.href='#';link.onclick=async event=>{event.preventDefault();try{const response=await fetch(source.source_url,{headers:password?{Authorization:`Bearer ${password}`}:{}});if(!response.ok)throw new Error('Cannot download this source.');const url=URL.createObjectURL(await response.blob());const a=make('a');a.href=url;a.download=response.headers.get('content-disposition')?.match(/filename="([^"]+)"/)?.[1]||`${source.ticker}-${source.report_date}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){toast(error.message,true);}};
  }else if(/^https:\/\/www\.sec\.gov\//.test(source.source_url)){link.href=source.source_url+(source.source_anchor?`#${encodeURIComponent(source.source_anchor)}`:'');link.target='_blank';link.rel='noopener noreferrer';}else return;
  content.append(link);$('source-dialog').showModal();
}
$('close-source').onclick=()=>$('source-dialog').close();
const svgNode=(name,attrs={})=>{const node=document.createElementNS('http://www.w3.org/2000/svg',name);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,v);return node;};
function chartCard(chart,sources){
  const card=make('section','chart-card');card.append(make('h3','',chart.title),make('p','',chart.unit));
  const svg=svgNode('svg',{viewBox:'0 0 600 240',role:'img','aria-label':chart.title});const title=svgNode('title');title.textContent=chart.title;svg.append(title);
  const points=chart.points.slice(0,20),values=points.map(p=>p.value),min=Math.min(0,...values),max=Math.max(0,...values),range=max-min||1;
  const y=v=>190-(v-min)/range*160,x=i=>65+i*490/Math.max(1,points.length-1);
  for(let i=0;i<4;i++){const value=min+range*i/3,line=svgNode('line',{x1:50,x2:575,y1:y(value),y2:y(value),class:'chart-grid'}),label=svgNode('text',{x:43,y:y(value)+4,'text-anchor':'end',class:'chart-label'});label.textContent=number(value);svg.append(line,label);}
  if(chart.type==='line')svg.append(svgNode('path',{d:points.map((p,i)=>`${i?'L':'M'}${x(i)},${y(p.value)}`).join(' '),class:'chart-line'}));
  points.forEach((point,i)=>{
    const px=chart.type==='bar'?60+i*510/points.length:x(i),width=Math.min(45,410/points.length);
    const shape=chart.type==='bar'?svgNode('rect',{x:px,y:Math.min(y(point.value),y(0)),width,height:Math.max(1,Math.abs(y(point.value)-y(0))),rx:3,class:'chart-bar',tabindex:0}):svgNode('circle',{cx:px,cy:y(point.value),r:4,class:'chart-point',tabindex:0});
    const tooltip=svgNode('title');tooltip.textContent=`${point.label}: ${number(point.value)} ${chart.unit}`;shape.append(tooltip);svg.append(shape);
    const label=svgNode('text',{x:chart.type==='bar'?px+width/2:px,y:215,'text-anchor':'middle',class:'chart-label'});label.textContent=point.label.length>13?point.label.slice(0,12)+'…':point.label;svg.append(label);
  });card.append(svg);
  const details=make('details'),summary=make('summary','','View values and sources'),table=make('table','chart-values'),head=make('tr');for(const label of ['Period / metric','Value','Evidence'])head.append(make('th','',label));const thead=make('thead');thead.append(head);table.append(thead);const tbody=make('tbody');
  for(const p of points){const row=make('tr'),refs=make('td');p.source_ids.forEach(id=>refs.append(sourceButton(id,sources)));row.append(make('td','',p.label),make('td','',`${number(p.value)} ${chart.unit}`),refs);tbody.append(row);}table.append(tbody);details.append(summary,table);card.append(details);return card;
}
function renderAnswer(result){
  const {message,content}=addMessage('assistant','');citationText(content,result.answer,result.sources);
  if(result.status==='needs_clarification')message.prepend(make('div','eyebrow','A LITTLE MORE CONTEXT'));
  if(result.chart?.type!=='none'&&result.chart?.points?.length)message.append(chartCard(result.chart,result.sources));
  if(result.calculations?.length){const grid=make('div','calculations');for(const c of result.calculations){const card=make('div','calculation');card.append(make('h4','',c.label),make('strong','',`${number(c.result)} ${c.unit}`),make('small','',`${c.operation.replaceAll('_',' ')} · Inputs: ${c.values.map(number).join(', ')}`));c.source_ids.forEach(id=>card.append(sourceButton(id,result.sources)));grid.append(card);}message.append(grid);}
  const links=make('div','evidence-links');for(const source of result.sources){const chip=make('button','evidence-chip',`${source.id} · ${source.ticker} · ${source.form} · ${source.report_date}`);chip.onclick=()=>showSource(source);links.append(chip);}message.append(links);
  if(result.scope)message.append(make('div','message-metrics',result.scope.label+(result.scope.requestedPeriods?.length?' | Requested: '+result.scope.requestedPeriods.join(', '):'')));
  if(result.metrics)message.append(make('div','message-metrics',`${result.metrics.strategy||$('strategy-select').value} · Hybrid${result.metrics.rerank?(result.metrics.rerankFallback?' ? Reranking limited':' + rerank'):''} · ${result.metrics.evidence||0} sources · ${(result.metrics.latencyMs/1000).toFixed(1)}s`));
  const toolbar=make('div','message-toolbar'),copy=make('button','','Copy answer');copy.onclick=()=>navigator.clipboard.writeText(result.answer).then(()=>toast('Answer copied.'),()=>toast('Copy is unavailable in this browser.',true));toolbar.append(copy);message.append(toolbar);scrollBottom();
}
document.querySelectorAll('[data-question]').forEach(button=>button.onclick=()=>{$('question').value=button.dataset.question;$('question').focus();});
$('question').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('chat-form').requestSubmit();}};
$('chat-form').onsubmit=async event=>{
  event.preventDefault();if(busy)return;if(!readyToChat()){toast('Process your selected company filings in Documents first.',true);return;}
  const question=$('question').value.trim();if(!question)return;
  const scope=selectedTickers(),strategy=$('strategy-select').value,periods=selectedPeriods();
  addMessage('user',question);$('question').value='';busy=true;$('ai-provider').disabled=true;renderScope();
  const thinking=make('div','thinking');thinking.append(make('span','thinking-dot'),make('span','','Searching filings and checking the evidence…'));$('messages').append(thinking);scrollBottom();
  try{
    const payload={question,tickers:scope,periods,strategy,rerank:$('rerank-toggle').checked,history:conversation.slice(-4)};
    const resolved=await api('/api/scope',{method:'POST',body:JSON.stringify(payload)});
    let result;
    if(resolved.status!=='resolved'){
      result={status:resolved.status,answer:resolved.message,scope:resolved,sources:[],calculations:[],chart:{type:'none',points:[]}};
    }else{
      thinking.lastChild.textContent=resolved.label+' | Checking the evidence\u2026';
      result=await api('/api/chat',{method:'POST',body:JSON.stringify(payload)});
    }
    thinking.remove();renderAnswer(result);
    conversation.push({role:'user',content:question,scope:result.scope||resolved},{role:'assistant',content:result.answer});
  }
  catch(error){thinking.remove();addMessage('assistant',error.message);toast(error.message,true);}
  finally{busy=false;$('ai-provider').disabled=uploading;renderScope();scrollBottom();}
};
switchView(location.hash==='#documents'?'documents':'research');
refresh().catch(error=>{if(!$('password-dialog').open)toast(error.message,true);$('connection-text').textContent='Connection needed';});
