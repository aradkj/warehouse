import React from 'react';
import { ChevronRight } from 'lucide-react';
import { useTranslation } from '../../i18n';
import { Button } from './Button';

export interface CursorPaginationProps {
  hasMore: boolean;
  onNext: () => void;
  onReset?: () => void;
  isLoading?: boolean;
}

export const CursorPagination: React.FC<CursorPaginationProps> = ({
  hasMore,
  onNext,
  onReset,
  isLoading = false,
}) => {
  const { t } = useTranslation();

  return (
    <div className="flex items-center justify-between pt-4 mt-4 border-t">
      <div>
        {onReset && (
          <Button size="sm" variant="secondary" onClick={onReset} disabled={isLoading}>
            {t('common.firstPage')}
          </Button>
        )}
      </div>
      <div>
        <Button
          size="sm"
          variant="secondary"
          onClick={onNext}
          disabled={!hasMore || isLoading}
          isLoading={isLoading}
        >
          <span>{t('common.nextPage')}</span>
          <ChevronRight size={14} className="ml-1 rtl-flip" />
        </Button>
      </div>
    </div>
  );
};
