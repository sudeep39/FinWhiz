import { load } from 'cheerio';
import { isISODate } from './validation.mjs';
const normalizeDate=value=>{
  if(!value)return '';
  if(/^\d{4}-\d{2}-\d{2}$/.test(value))return isISODate(value)?value:'';
  const date=new Date(value);return Number.isFinite(date.getTime())?date.toISOString().slice(0,10):'';
};
export function htmlMetadata(html){
  const $=load(html);
  const fact=name=>$(`[name="dei:${name}"]`).first().text().trim();
  return {ticker:fact('TradingSymbol').toUpperCase(),reportDate:normalizeDate(fact('DocumentPeriodEndDate')),
    form:fact('DocumentType').toUpperCase(),company:fact('EntityRegistrantName'),cik:fact('EntityCentralIndexKey')};
}
export function textMetadata(text){
  return {ticker:text.match(/(?:trading\s+symbol(?:\(s\))?|ticker(?:\s+symbol)?)\s*[:=]\s*([A-Z][A-Z0-9.-]{0,9})\b/i)?.[1]?.toUpperCase()||'',
    form:text.match(/\b10-[KQ]\b/i)?.[0]?.toUpperCase()||'',
    reportDate:text.match(/(?:fiscal\s+period\s+end|period\s+ended|report\s+date)\s*[:=]?\s*(\d{4}-\d{2}-\d{2})/i)?.[1]||''};
}
export function mergeMetadata(hints,detected){
  // Content takes precedence; manual details only fill absent/ambiguous fields.
  return {...hints,...Object.fromEntries(Object.entries(detected).filter(([,value])=>value))};
}
