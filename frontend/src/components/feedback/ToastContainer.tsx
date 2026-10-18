import React from 'react';
import { AlertCircle, CheckCircle, Info, TriangleAlert, X } from 'lucide-react';
import { useToast } from '../../app/providers/ToastContext';
import { useTranslation } from '../../i18n';

export const ToastContainer: React.FC = () => {
  const { toasts, removeToast } = useToast();
  const { t } = useTranslation();

  if (toasts.length === 0) return null;

  return (
    <div
      style={{
        position: 'fixed',
        bottom: '1.5rem',
        insetInlineEnd: '1.5rem',
        zIndex: 100,
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        maxWidth: '400px',
        width: '100%',
        pointerEvents: 'none',
      }}
      aria-live="polite"
    >
      {toasts.map((toast) => {
        const Icon =
          toast.type === 'error'
            ? AlertCircle
            : toast.type === 'warning'
              ? TriangleAlert
              : toast.type === 'success'
                ? CheckCircle
                : Info;

        const bg =
          toast.type === 'error'
            ? '#fef2f2'
            : toast.type === 'warning'
              ? '#fffbeb'
              : toast.type === 'success'
                ? '#ecfdf5'
                : '#f0f9ff';

        const border =
          toast.type === 'error'
            ? '#fecaca'
            : toast.type === 'warning'
              ? '#fde68a'
              : toast.type === 'success'
                ? '#a7f3d0'
                : '#bae6fd';

        const color =
          toast.type === 'error'
            ? '#991b1b'
            : toast.type === 'warning'
              ? '#92400e'
              : toast.type === 'success'
                ? '#065f46'
                : '#0369a1';

        return (
          <div
            key={toast.id}
            style={{
              pointerEvents: 'auto',
              backgroundColor: bg,
              border: `1px solid ${border}`,
              color: color,
              borderRadius: '6px',
              padding: '0.75rem 1rem',
              boxShadow: '0 4px 6px -1px rgba(0,0,0,0.1)',
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
              fontSize: '0.875rem',
            }}
          >
            <Icon size={18} style={{ flexShrink: 0 }} />
            <div style={{ flex: 1 }}>{toast.message}</div>
            <button
              onClick={() => removeToast(toast.id)}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: 'inherit',
                opacity: 0.7,
                padding: '2px',
              }}
              aria-label={t('common.dismiss')}
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
};
