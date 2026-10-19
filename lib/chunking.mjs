import { load } from 'cheerio';
import { getEncoding } from 'js-tiktoken';
import { createHash } from 'node:crypto';
export const encoding = getEncoding('cl100k_base');
export const tokenCount = text => encoding.encode(text, [], []).length;
export const hash = value => createHash('sha256').update(typeof value === 'string' || Buffer.isBuffer(value) ? value : JSON.stringify(value)).digest('hex');
const clean = s => s.replace(/\s+/g, ' ').trim();

export function extractHtml(html) {
  const $ = load(html);
  $('script,style,head,noscript,ix\\:header,ix\\:hidden,[hidden]').remove();
  $('[style]').each((_, el) => { if (/display\s*:\s*none|visibility\s*:\s*hidden/i.test($(el).attr('style'))) $(el).remove(); });
  const blocks = [];
  let section = 'Cover', anchor = '';
  const emit = (text, kind = 'narrative') => {
    text = kind === 'table' ? text.trim() : clean(text);
    if (!text || /^\d{1,4}$/.test(text) || /^table of contents$/i.test(text)) return;
    if (kind === 'narrative' && text.length < 220 && /^(?:part\s+[ivx]+\b|item\s+\d+[a-z]?[. :]|note\s+\d+\b)/i.test(text)) section = text;
    blocks.push({ kind, text, section, anchor, id: `b${blocks.length}` });
  };
  function walk(el) {
    if (el.type === 'text') return;
    const node = $(el);
    anchor = node.attr('id') || node.attr('name') || anchor;
    if (el.name === 'table' && !node.find('table').length) {
      const links = node.find('a[href^="#"]');
      if (links.length >= 5 && links.text().length > node.text().length * .4) return;
      const spans = new Map(), rows = [];
      node.find('tr').each((ri, row) => {
        const cells = new Map();
        for (const [key, value] of spans) { const [r,c] = key.split(':').map(Number); if (r === ri) cells.set(c, value); }
        let col = 0;
        $(row).children('td,th').each((_, cell) => {
          while (cells.has(col)) col++;
          const value = clean($(cell).text());
          const width = Math.min(80, Math.max(1, Number($(cell).attr('colspan')) || 1));
          const height = Math.min(80, Math.max(1, Number($(cell).attr('rowspan')) || 1));
          for (let c=0;c<width;c++) { cells.set(col+c,value); for(let r=1;r<height;r++) spans.set(`${ri+r}:${col+c}`,value); }
          col += width;
        });
        if (cells.size) rows.push(Array.from({length:Math.max(...cells.keys())+1},(_,i)=>cells.get(i)||''));
      });
      if (rows.length >= 2 && Math.max(...rows.map(r=>r.length)) >= 2) {
        const width = Math.max(...rows.map(r=>r.length));
        const keep = [];
        for (let col=0;col<width;col++) if (rows.some(r=>r[col]) && (!keep.length || rows.some(r=>(r[col]||'') !== (r[keep.at(-1)]||'')))) keep.push(col);
        const caption = clean(node.children('caption').text());
        emit([caption,...rows.filter(r=>r.some(Boolean)).map(r=>keep.map(c=>r[c]||'').join(' | '))].filter(Boolean).join('\n'),'table'); return;
      }
    }
    let pending = '';
    for (const child of el.children || []) {
      if (child.type === 'text') pending += child.data;
      else if (/^(div|p|table|section|article|ul|ol|li|h[1-6]|tr|td|th|blockquote)$/.test(child.name || '') || $(child).find('div,p,table,section,li').length) {
        emit(pending); pending = ''; walk(child);
      } else { anchor = $(child).attr('id') || anchor; pending += child.name === 'br' ? '\n' : $(child).text(); }
    }
    emit(pending);
  }
  walk($('body')[0] || $.root()[0]);
  if (blocks.reduce((n,b)=>n+b.text.length,0) < 100) throw new Error('No readable text found. Scanned PDFs require OCR before upload.');
  return blocks;
}
export function extractPlain(text) {
  return text.split(/\n\s*\n/).map((text,i)=>({ kind:'narrative',text:clean(text),section:'Document',anchor:'',id:`b${i}` })).filter(b=>b.text);
}
export function windows(text, size=600, overlap=90) {
  const ids = encoding.encode(text, [], []), result=[];
  for(let start=0;start<ids.length;) {
    let end=Math.min(start+size,ids.length);
    // A character can span token IDs. Shrink until the window decodes intact.
    while(end>start && encoding.decode(ids.slice(start,end)).includes('\uFFFD')) end--;
    if(end<=start) throw new Error('Cannot split this document safely.');
    result.push(encoding.decode(ids.slice(start,end)));
    if(end===ids.length) break;
    start=Math.max(start+1,end-overlap);
    while(start<end && encoding.decode(ids.slice(start,end)).includes('\uFFFD')) start++;
  }
  return result;
}
export function makeParents(blocks) {
  const result=[];
  for(const block of blocks) {
    const last=result.at(-1);
    if(block.kind==='narrative' && last?.kind==='narrative' && last.section===block.section) last.text += '\n\n'+block.text;
    else result.push({...block,index:result.length,context:block.kind==='table' ? (last?.text||'').slice(-500) : ''});
  }
  return result;
}
function tablePieces(parent,budget) {
  const rows=parent.text.split('\n'), header=[parent.context,...rows.slice(0,3)].filter(Boolean).join('\n');
  if(tokenCount(header)>budget/2) return windows(parent.text,budget,90);
  const out=[]; let current=header;
  for(const row of rows.slice(3)) for(const piece of windows(row,Math.max(32,budget-tokenCount(header)-8),0)) {
    if(tokenCount(current+'\n'+piece)>budget && current!==header) {out.push(current);current=header;}
    current+='\n'+piece;
  }
  if(current) out.push(current);
  return out;
}
export function cosine(a,b) { let dot=0,aa=0,bb=0;for(let i=0;i<a.length;i++){dot+=a[i]*b[i];aa+=a[i]**2;bb+=b[i]**2;}return dot/Math.sqrt(aa*bb); }
export async function splitParent(parent, strategy, embedUnits, prefix='') {
  const budget=Math.max(200,700-tokenCount(prefix)-10);
  if(parent.kind==='table') return tablePieces(parent,budget);
  if(strategy==='fixed' || tokenCount(parent.text)<=budget) return windows(parent.text,budget,100);
  const units=parent.text.split(/(?<=[.!?;])\s+(?=[A-Z(\d])/).flatMap(s=>windows(s,120,0)).filter(Boolean);
  const vectors=await embedUnits(units.map(t=>prefix+t));
  const distances=units.slice(1).map((_,i)=>1-cosine(vectors[i],vectors[i+1]));
  const sorted=[...distances].sort((a,b)=>a-b), threshold=sorted[Math.floor(sorted.length*.8)] ?? 1;
  const pieces=[]; let start=0,end=0;
  while(end<units.length) {
    const count=tokenCount(units.slice(start,end+1).join(' '));
    if(count>1000 && end>start || end===units.length-1 || count>=350 && (distances[end]>threshold || count>=budget)) {
      const cut=count>1000 && end>start ? end : end+1;
      pieces.push(units.slice(start,cut).join(' '));
      let next=cut;
      while(next>start+1 && tokenCount(units.slice(next-1,cut).join(' '))<=100) next--;
      if(cut===units.length) break;
      start=next;end=cut;
    } else end++;
  }
  return pieces;
}
