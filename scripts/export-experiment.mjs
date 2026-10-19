import { readFile, writeFile } from 'node:fs/promises';
export async function exportExperiment() {
  const root = new URL('../data/', import.meta.url);
  const experiment = JSON.parse(await readFile(new URL('../experiment.json', import.meta.url)));
  const catalog = JSON.parse(await readFile(new URL('filings.json', root)));
  const companies = catalog.companies.filter(c => experiment.tickers.includes(c.tickers[0]));
  if (companies.length !== experiment.tickers.length || companies.some(c => c.filings.length !== experiment.periods_per_company) || catalog.asOf !== experiment.as_of) throw new Error('Experiment catalog does not match experiment.json');
  const dataset = { ...catalog, experiment, companies };
  await writeFile(new URL('experiment-filings.json', root), JSON.stringify(dataset, null, 2) + '\n');
  const csv = ['company,ticker,cik,form,report_date,filing_date,accession,url', ...companies.flatMap(c => c.filings.map(f => [c.name,c.tickers[0],c.cik,f.form,f.reportDate,f.filingDate,f.accession,f.url].map(v => `"${v.replaceAll('"','""')}"`).join(',')))].join('\n');
  await writeFile(new URL('experiment-filings.csv', root), csv + '\n');
}
