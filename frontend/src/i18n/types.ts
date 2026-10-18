export type Language = 'en' | 'fa';
export type Direction = 'ltr' | 'rtl';

export interface I18nContextType {
  language: Language;
  direction: Direction;
  setLanguage: (lang: Language) => void;
  toggleLanguage: () => void;
  t: (key: string, params?: Record<string, string | number>) => string;
  formatNumber: (value: number, options?: Intl.NumberFormatOptions) => string;
  formatDate: (date: Date | string | number, options?: Intl.DateTimeFormatOptions) => string;
}

export type TranslationDictionary = {
  [key: string]: string | TranslationDictionary;
};
