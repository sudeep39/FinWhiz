import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { selectFilings } from './scripts/collect-filings.mjs';

test('catalog preserves archived issuers and eight periods for active companies', async () => {
  const data = JSON.parse(await readFile(new URL('./data/filings.json', import.meta.url)));
  assert.equal(data.companies.length, 10);
  assert.equal(new Set(data.companies.map(c => c.cik)).size, 10);
  assert.equal(data.companies.reduce((n,c) => n + c.filings.length, 0), 48);
  for (const company of data.companies) {
    const periods = ['NVDA', 'TSLA'].includes(company.tickers[0]) ? 8 : 4;
    const raw = JSON.parse(await readFile(new URL(`./data/raw/${company.cik}.json`, import.meta.url)));
    assert.deepEqual(company.filings, selectFilings(raw.filings.recent, company.cik, data.asOf, periods));
    assert.equal(company.filings.length, periods);
    assert.equal(company.filings.filter(f => f.form === '10-K').length, periods / 4);
    assert.equal(company.filings.filter(f => f.form === '10-Q').length, periods * 3 / 4);
    assert.equal(new Set(company.filings.map(f => f.reportDate)).size, periods);
    for (let i = 1; i < company.filings.length; i++) {
      const gap = (Date.parse(company.filings[i-1].reportDate) - Date.parse(company.filings[i].reportDate)) / 86400000;
      assert.ok(gap >= 60 && gap <= 120);
    }
    for (const filing of company.filings) {
      assert.ok(filing.filingDate <= data.asOf);
      assert.equal(new URL(filing.url).hostname, 'www.sec.gov');
      assert.ok(filing.url.includes(filing.accession.replaceAll('-', '')));
    }
  }
});

test('selection respects cutoff, excludes amendments, and deduplicates periods', () => {
  const rows = [
    ['10-Q', '2026-09-30', '2026-11-01'],
    ['10-Q/A', '2026-06-30', '2026-09-01'],
    ['10-Q', '2026-06-30', '2026-08-02'],
    ['10-Q', '2026-06-30', '2026-08-01'],
    ['8-K', '2026-06-30', '2026-07-20'],
    ['10-Q', '2026-03-31', '2026-05-01'],
    ['10-K', '2025-12-31', '2026-02-01'],
    ['10-Q', '2025-09-30', '2025-11-01'],
  ];
  const recent = { form: [], reportDate: [], filingDate: [], accessionNumber: [], primaryDocument: [] };
  rows.forEach(([form, reportDate, filingDate], i) => {
    recent.form.push(form); recent.reportDate.push(reportDate); recent.filingDate.push(filingDate);
    recent.accessionNumber.push(`0000000001-26-${String(i).padStart(6, '0')}`); recent.primaryDocument.push('report.htm');
  });
  const result = selectFilings(recent, '0000000001', '2026-10-18');
  assert.deepEqual(result.map(r => r.reportDate), ['2026-06-30', '2026-03-31', '2025-12-31', '2025-09-30']);
  assert.equal(result[0].filingDate, '2026-08-02');
});
