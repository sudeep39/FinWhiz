export const companies = [
  ['NVIDIA', ['NVDA'], '1045810'],
  ['Apple', ['AAPL'], '320193'],
  ['Microsoft', ['MSFT'], '789019'],
  ['Alphabet', ['GOOGL', 'GOOG'], '1652044'],
  ['Amazon', ['AMZN'], '1018724'],
  ['Broadcom', ['AVGO'], '1730168'],
  ['Meta Platforms', ['META'], '1326801'],
  ['Tesla', ['TSLA'], '1318605'],
  ['Berkshire Hathaway', ['BRK.B'], '1067983'],
  ['JPMorgan Chase', ['JPM'], '19617'],
].map(([name, tickers, cik], i) => ({ rank: i + 1, name, tickers, cik: cik.padStart(10, '0') }));
