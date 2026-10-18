import React from 'react';
import { AlertCircle, CheckCircle, Info, TriangleAlert } from 'lucide-react';

export interface AlertProps {
  variant?: 'danger' | 'warning' | 'success' | 'info';
  title?: string;
  children: React.ReactNode;
  className?: string;
}

export const Alert: React.FC<AlertProps> = ({
  variant = 'info',
  title,
  children,
  className = '',
}) => {
  const Icon =
    variant === 'danger'
      ? AlertCircle
      : variant === 'warning'
        ? TriangleAlert
        : variant === 'success'
          ? CheckCircle
          : Info;

  return (
    <div className={`alert alert-${variant} ${className}`.trim()} role="alert">
      <Icon size={18} className="flex-shrink-0 mt-0.5" />
      <div className="flex-1">
        {title && <div className="font-semibold mb-1">{title}</div>}
        <div>{children}</div>
      </div>
    </div>
  );
};
