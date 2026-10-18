import { Language } from './types';

export function formatNumber(
  value: number,
  lang: Language = 'en',
  options?: Intl.NumberFormatOptions
): string {
  const locale = lang === 'fa' ? 'fa-IR' : 'en-US';
  try {
    return new Intl.NumberFormat(locale, options).format(value);
  } catch {
    return String(value);
  }
}

export function formatDate(
  date: Date | string | number,
  lang: Language = 'en',
  options?: Intl.DateTimeFormatOptions
): string {
  const parsedDate = typeof date === 'string' || typeof date === 'number' ? new Date(date) : date;
  if (isNaN(parsedDate.getTime())) {
    return String(date);
  }

  const locale = lang === 'fa' ? 'fa-IR' : 'en-US';
  const defaultOptions: Intl.DateTimeFormatOptions = options || {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  };

  try {
    return new Intl.DateTimeFormat(locale, defaultOptions).format(parsedDate);
  } catch {
    return parsedDate.toLocaleDateString();
  }
}
