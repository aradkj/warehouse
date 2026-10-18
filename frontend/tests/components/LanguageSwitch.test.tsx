import React from 'react';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider, useTranslation } from '../../src/i18n';
import { LanguageSwitcher } from '../../src/components/common/LanguageSwitcher';
import enTranslations from '../../src/i18n/locales/en/translation.json';
import faTranslations from '../../src/i18n/locales/fa/translation.json';

const TestConsumer: React.FC = () => {
  const { language, direction, t, formatNumber } = useTranslation();
  return (
    <div>
      <LanguageSwitcher />
      <div data-testid="current-lang">{language}</div>
      <div data-testid="current-dir">{direction}</div>
      <div data-testid="dashboard-text">{t('nav.dashboard')}</div>
      <div data-testid="orders-text">{t('nav.orders')}</div>
      <div data-testid="save-btn">{t('common.save')}</div>
      <div data-testid="formatted-number">{formatNumber(1234.5)}</div>
    </div>
  );
};

describe('Bilingual Internationalization (FA/EN)', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.lang = 'en';
    document.documentElement.dir = 'ltr';
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('selects English (en / ltr) on a clean first visit by default', () => {
    render(
      <I18nProvider>
        <TestConsumer />
      </I18nProvider>
    );

    expect(screen.getByTestId('current-lang').textContent).toBe('en');
    expect(screen.getByTestId('current-dir').textContent).toBe('ltr');
    expect(screen.getByTestId('dashboard-text').textContent).toBe('Dashboard');
    expect(screen.getByTestId('orders-text').textContent).toBe('Orders');
    expect(screen.getByTestId('save-btn').textContent).toBe('Save');
    expect(document.documentElement.lang).toBe('en');
    expect(document.documentElement.dir).toBe('ltr');
  });

  it('switches dynamically to Persian (fa / rtl) when language switcher is clicked', async () => {
    render(
      <I18nProvider>
        <TestConsumer />
      </I18nProvider>
    );

    const switcher = screen.getByTestId('language-switcher');
    fireEvent.click(switcher);

    await waitFor(() => {
      expect(screen.getByTestId('current-lang').textContent).toBe('fa');
      expect(screen.getByTestId('current-dir').textContent).toBe('rtl');
    });

    expect(screen.getByTestId('dashboard-text').textContent).toBe('داشبورد');
    expect(screen.getByTestId('orders-text').textContent).toBe('سفارش‌ها');
    expect(screen.getByTestId('save-btn').textContent).toBe('ذخیره');
    expect(document.documentElement.lang).toBe('fa');
    expect(document.documentElement.dir).toBe('rtl');
    expect(localStorage.getItem('arad_locale')).toBe('fa');
  });

  it('switches back to English cleanly upon second toggle', async () => {
    render(
      <I18nProvider>
        <TestConsumer />
      </I18nProvider>
    );

    const switcher = screen.getByTestId('language-switcher');

    // Switch to FA
    fireEvent.click(switcher);
    await waitFor(() => {
      expect(screen.getByTestId('current-lang').textContent).toBe('fa');
    });

    // Switch back to EN
    fireEvent.click(switcher);
    await waitFor(() => {
      expect(screen.getByTestId('current-lang').textContent).toBe('en');
      expect(screen.getByTestId('current-dir').textContent).toBe('ltr');
    });

    expect(screen.getByTestId('dashboard-text').textContent).toBe('Dashboard');
    expect(document.documentElement.lang).toBe('en');
    expect(document.documentElement.dir).toBe('ltr');
    expect(localStorage.getItem('arad_locale')).toBe('en');
  });

  it('restores persisted language preference from localStorage on mount', () => {
    localStorage.setItem('arad_locale', 'fa');

    render(
      <I18nProvider>
        <TestConsumer />
      </I18nProvider>
    );

    expect(screen.getByTestId('current-lang').textContent).toBe('fa');
    expect(screen.getByTestId('current-dir').textContent).toBe('rtl');
    expect(screen.getByTestId('dashboard-text').textContent).toBe('داشبورد');
    expect(document.documentElement.lang).toBe('fa');
    expect(document.documentElement.dir).toBe('rtl');
  });

  it('formats numbers according to the active locale', async () => {
    const { unmount } = render(
      <I18nProvider>
        <TestConsumer />
      </I18nProvider>
    );

    // English format
    expect(screen.getByTestId('formatted-number').textContent).toBe('1,234.5');
    unmount();

    // Persian format
    localStorage.setItem('arad_locale', 'fa');
    render(
      <I18nProvider>
        <TestConsumer />
      </I18nProvider>
    );

    // In fa-IR locale, Persian digits are used
    const faNum = screen.getByTestId('formatted-number').textContent;
    expect(faNum).toBeTruthy();
    expect(faNum).not.toBe('1,234.5');
  });

  it('verifies 100% key parity between English and Persian translation dictionaries', () => {
    function getAllKeys(obj: Record<string, any>, prefix = ''): string[] {
      let keys: string[] = [];
      for (const [k, v] of Object.entries(obj)) {
        const fullKey = prefix ? `${prefix}.${k}` : k;
        if (typeof v === 'object' && v !== null && !Array.isArray(v)) {
          keys = keys.concat(getAllKeys(v, fullKey));
        } else {
          keys.push(fullKey);
        }
      }
      return keys;
    }

    const enKeys = getAllKeys(enTranslations).sort();
    const faKeys = getAllKeys(faTranslations).sort();

    const missingInFa = enKeys.filter((k) => !faKeys.includes(k));
    const extraInFa = faKeys.filter((k) => !enKeys.includes(k));

    expect(missingInFa, `Missing Persian translation keys: ${missingInFa.join(', ')}`).toEqual([]);
    expect(extraInFa, `Extra Persian translation keys not in English: ${extraInFa.join(', ')}`).toEqual([]);
    expect(faKeys.length).toBe(enKeys.length);
  });
});
