// Labels identify evidence content, not chunk IDs, so both chunkers use the same gold set.
export function matchesLabel(row, label) {
  return row.ticker === label.ticker && row.report_date === label.reportDate
    && label.contains.every(text => row.text.toLowerCase().includes(text.toLowerCase()));
}
export function retrievalMetrics(ranking, labels, k = 5) {
  if (!labels.length) throw new Error('Answerable queries require evidence labels.');
  const top = ranking.slice(0, k);
  const found = new Set(); let dcg = 0, first = 0;
  top.forEach((row, i) => {
    const hits = labels.map((label,j)=>matchesLabel(row,label)?j:-1).filter(j=>j>=0&&!found.has(j));
    if(hits.length){ if(!first)first=i+1;dcg+=1/Math.log2(i+2);hits.forEach(j=>found.add(j)); }
  });
  // Passage labels can overlap in a single chunk. Recall is label coverage;
  // MRR measures first relevant chunk. Do not infer nDCG from overlapping labels.
  return {recallAt5:found.size/labels.length,mrrAt5:first?1/first:0,uniqueEvidenceDcgAt5:dcg};
}
