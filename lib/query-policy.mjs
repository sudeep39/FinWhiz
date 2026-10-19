export function planQuestion(question, history = []) {
  const normalized = question.trim();
  if (/^(?:what(?:'s| is)|show(?: me)?|tell me about)\s+(?:the\s+)?(?:profit|performance|growth)\s*[?.!]*$/i.test(normalized)) {
    return {status:'needs_clarification',answer:'Which metric and reporting period should I use? For example, net income, operating income, revenue growth, or gross margin for a particular quarter.'};
  }
  if (/\b(?:predict|forecast)\b.*\b(?:stock|share) price\b|\b(?:stock|share) price\b.*\b(?:next|tomorrow|will)\b|\b(?:buy|sell) (?:the )?(?:stock|shares)\b/i.test(normalized)) {
    return {status:'insufficient_evidence',answer:'Historical SEC filings cannot establish future stock prices or a trading recommendation. I can compare the disclosed financial performance and risks instead.'};
  }
  if (/\b(?:earnings call|transcript)\b/i.test(normalized) && !/\b(?:filing|10-[kq])\b/i.test(normalized)) {
    return {status:'insufficient_evidence',answer:'This corpus contains SEC filings, not earnings-call transcripts. Uploading transcripts is outside the current 10-K/10-Q ingestion scope; ask about disclosures in the selected filings instead.'};
  }
  const previous = [...history].reverse().find(m=>m.role==='user'&&typeof m.content==='string');
  const followup = /^(?:and\b|what about\b|how about\b|why\b|compare (?:it|that|those)\b)|\b(?:that metric|those figures|the same metric|that period)\b/i.test(normalized);
  return {retrievalQuestion:followup&&previous?`${previous.content.slice(0,1500)}\nFollow-up: ${normalized}`:normalized};
}

export function emptyAnswer(status, answer, metrics = {}) {
  return {status,answer,citation_ids:[],sources:[],chart:{type:'none',title:'',unit:'',points:[]},calculations:[],metrics:{latencyMs:0,evidence:0,candidates:0,...metrics}};
}
