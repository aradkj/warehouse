import React from 'react';
import { Globe } from 'lucide-react';
import { useTranslation } from '../../i18n';

export interface LanguageSwitcherProps {
  className?: string;
  variant?: 'button' | 'toggle' | 'pill';
}

export const LanguageSwitcher: React.FC<LanguageSwitcherProps> = ({
  className = '',
  variant = 'button',
}) => {
  const { language, setLanguage, toggleLanguage, t } = useTranslation();

  if (variant === 'pill') {
    return (
      <div
        className={`inline-flex items-center rounded-full p-0.5 border border-subtle bg-surface ${className}`}
        role="group"
        aria-label={t('header.switchLanguage')}
      >
        <button
          type="button"
          onClick={() => setLanguage('en')}
          className={`px-2.5 py-1 text-xs font-semibold rounded-full transition-colors ${
            language === 'en'
              ? 'bg-primary text-white shadow-sm'
              : 'text-muted hover:text-main'
          }`}
          aria-pressed={language === 'en'}
          aria-label="Switch to English"
        >
          EN
        </button>
        <button
          type="button"
          onClick={() => setLanguage('fa')}
          className={`px-2.5 py-1 text-xs font-semibold rounded-full transition-colors ${
            language === 'fa'
              ? 'bg-primary text-white shadow-sm'
              : 'text-muted hover:text-main'
          }`}
          aria-pressed={language === 'fa'}
          aria-label="تغییر به فارسی"
        >
          فا
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={toggleLanguage}
      className={`btn btn-secondary btn-sm flex items-center gap-1.5 ${className}`}
      title={t('header.switchLanguage')}
      aria-label={`${t('header.language')}: ${language === 'en' ? 'English' : 'فارسی'}. ${t('header.switchLanguage')}`}
      data-testid="language-switcher"
    >
      <Globe size={14} className="text-muted" />
      <span className="font-semibold text-xs">
        {language === 'en' ? 'فارسی' : 'English'}
      </span>
    </button>
  );
};
