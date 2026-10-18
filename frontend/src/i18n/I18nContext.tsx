import React, { createContext, useContext, useEffect, useState, useMemo, useCallback } from 'react';
import { Direction, I18nContextType, Language, TranslationDictionary } from './types';
import enTranslations from './locales/en/translation.json';
import faTranslations from './locales/fa/translation.json';
import { formatDate as formatUtilDate, formatNumber as formatUtilNumber } from './formatters';

const STORAGE_KEY = 'arad_locale';

const translations: Record<Language, TranslationDictionary> = {
  en: enTranslations as TranslationDictionary,
  fa: faTranslations as TranslationDictionary,
};

const I18nContext = createContext<I18nContextType | null>(null);

function resolveKey(dict: TranslationDictionary, path: string): string | null {
  const parts = path.split('.');
  let current: any = dict;
  for (const part of parts) {
    if (current && typeof current === 'object' && part in current) {
      current = current[part];
    } else {
      return null;
    }
  }
  return typeof current === 'string' ? current : null;
}

function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  let result = template;
  for (const [key, val] of Object.entries(params)) {
    result = result.replace(new RegExp(`{{\\s*${key}\\s*}}`, 'g'), String(val));
    result = result.replace(new RegExp(`{\\s*${key}\\s*}`, 'g'), String(val));
  }
  return result;
}

export const I18nProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [language, setLanguageState] = useState<Language>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === 'fa' || stored === 'en') {
        return stored;
      }
    } catch {
      // Ignore localStorage access failures in restricted environments
    }
    return 'en';
  });

  const direction: Direction = language === 'fa' ? 'rtl' : 'ltr';

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, language);
    } catch {
      // Safe fallback
    }

    if (typeof document !== 'undefined') {
      document.documentElement.lang = language;
      document.documentElement.dir = direction;
      if (document.body) {
        document.body.dir = direction;
      }
    }
  }, [language, direction]);

  const setLanguage = useCallback((lang: Language) => {
    setLanguageState(lang);
  }, []);

  const toggleLanguage = useCallback(() => {
    setLanguageState((prev) => (prev === 'en' ? 'fa' : 'en'));
  }, []);

  const t = useCallback(
    (key: string, params?: Record<string, string | number>): string => {
      // Look up in active language
      let text = resolveKey(translations[language], key);
      // Fallback to English if not found in current language
      if (!text && language !== 'en') {
        text = resolveKey(translations.en, key);
      }
      // If still not found, return key
      if (!text) {
        return key;
      }
      return interpolate(text, params);
    },
    [language]
  );

  const formatNumber = useCallback(
    (value: number, options?: Intl.NumberFormatOptions): string => {
      return formatUtilNumber(value, language, options);
    },
    [language]
  );

  const formatDate = useCallback(
    (date: Date | string | number, options?: Intl.DateTimeFormatOptions): string => {
      return formatUtilDate(date, language, options);
    },
    [language]
  );

  const value = useMemo<I18nContextType>(
    () => ({
      language,
      direction,
      setLanguage,
      toggleLanguage,
      t,
      formatNumber,
      formatDate,
    }),
    [language, direction, setLanguage, toggleLanguage, t, formatNumber, formatDate]
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
};

export const useTranslation = (): I18nContextType => {
  const context = useContext(I18nContext);
  if (!context) {
    // Graceful fallback if rendered outside provider in isolated component tests
    const defaultLang: Language = 'en';
    return {
      language: defaultLang,
      direction: 'ltr',
      setLanguage: () => {},
      toggleLanguage: () => {},
      t: (key: string, params?: Record<string, string | number>) => {
        const text = resolveKey(translations[defaultLang], key) || key;
        return interpolate(text, params);
      },
      formatNumber: (val: number, opts?: Intl.NumberFormatOptions) =>
        formatUtilNumber(val, defaultLang, opts),
      formatDate: (d: Date | string | number, opts?: Intl.DateTimeFormatOptions) =>
        formatUtilDate(d, defaultLang, opts),
    };
  }
  return context;
};
