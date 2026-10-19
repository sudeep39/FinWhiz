export function isISODate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function validateDetails(details) {
  const fields = { ticker: 10, company: 200, reportDate: 10, form: 4 };
  if (!Array.isArray(details) || details.length > 32) return false;
  return details.every(item => item && typeof item === 'object' && !Array.isArray(item)
    && Object.entries(item).every(([key, value]) => Object.hasOwn(fields, key)
      && typeof value === 'string' && value.length <= fields[key])
    && (!item.ticker || /^[A-Z][A-Z0-9.-]{0,9}$/.test(item.ticker))
    && (!item.reportDate || isISODate(item.reportDate))
    && (!item.form || ['10-K', '10-Q'].includes(item.form)));
}
