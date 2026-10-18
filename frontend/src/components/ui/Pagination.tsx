import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
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
  return (
    <div className="flex items-center justify-between pt-4 mt-4 border-t">
      <div>
        {onReset && (
          <Button size="sm" variant="secondary" onClick={onReset} disabled={isLoading}>
            First Page
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
          <span>Next Page</span>
          <ChevronRight size={14} className="ml-1" />
        </Button>
      </div>
    </div>
  );
};
