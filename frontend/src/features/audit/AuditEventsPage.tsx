import React, { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, FileText, Lock, RefreshCw, Shield } from 'lucide-react';
import { auditApi } from '../../api/endpoints/audit';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { AuditEntryResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { useTranslation } from '../../i18n';
import { EmptyState } from '../../components/feedback/EmptyState';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';

export const AuditEventsPage: React.FC = () => {
  const { showToast } = useToast();
  const { t, formatDate, formatNumber } = useTranslation();
  const [events, setEvents] = useState<AuditEntryResponse[]>([]);
  const [limit, setLimit] = useState<number>(50);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const fetchEvents = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await auditApi.listEvents({ limit });
      setEvents(data.items);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to fetch audit events.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchEvents();
  }, [limit]);

  const toggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold">{t('audit.title')}</h1>
            <Badge variant="neutral" className="flex items-center gap-1">
              <Lock size={10} />
              <span>Read-Only Compliance</span>
            </Badge>
          </div>
          <p className="text-sm text-muted mt-1">
            {t('audit.subtitle', { tenant: '' })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={fetchEvents} isLoading={isLoading}>
            <RefreshCw size={14} className="mr-1" />
            <span>{t('common.refresh')}</span>
          </Button>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      <Card className="mb-6">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold text-muted uppercase">Fetch Limit:</span>
            <select
              className="form-select text-xs py-1"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              style={{ width: '120px' }}
            >
              <option value="25">25 entries</option>
              <option value="50">50 entries</option>
              <option value="100">100 entries</option>
              <option value="200">200 entries</option>
            </select>
          </div>
          <div className="text-xs text-muted">
            Displaying latest <strong className="text-main">{events.length}</strong> events
          </div>
        </div>
      </Card>

      {isLoading ? (
        <div className="text-center py-12 text-muted">{t('common.loading')}</div>
      ) : events.length === 0 ? (
        <Card>
          <EmptyState
            title={t('audit.noAuditEvents')}
            description={t('audit.subtitle', { tenant: '' })}
            icon={<FileText size={48} strokeWidth={1.5} />}
          />
        </Card>
      ) : (
        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: '30px' }} />
                <th>{t('audit.timestamp')}</th>
                <th>{t('audit.action')}</th>
                <th>{t('audit.resourceType')}</th>
                <th>{t('audit.resourceId')}</th>
                <th>{t('audit.actor')}</th>
              </tr>
            </thead>
            <tbody>
              {events.map((ev) => {
                const isExpanded = expandedId === ev.id;
                return (
                  <React.Fragment key={ev.id}>
                    <tr
                      onClick={() => toggleExpand(ev.id)}
                      style={{ cursor: 'pointer' }}
                    >
                      <td className="text-muted">
                        {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} className="rtl-flip" />}
                      </td>
                      <td className="text-xs text-muted font-mono">
                        {formatDate(ev.occurred_at)}
                      </td>
                      <td className="text-xs font-semibold font-mono text-main">
                        {ev.action}
                      </td>
                      <td className="text-xs font-medium">
                        <Badge variant="neutral">{ev.resource_type}</Badge>
                      </td>
                      <td className="font-mono text-xs text-muted">{ev.resource_id.slice(0, 8)}...</td>
                      <td className="font-mono text-xs text-muted">{ev.actor_id.slice(0, 8)}...</td>
                    </tr>
                    {isExpanded && (
                      <tr>
                        <td colSpan={6} style={{ backgroundColor: 'var(--bg-surface-subtle)', padding: '1rem' }}>
                          <div className="text-xs font-mono space-y-2">
                            <div><strong>Audit Event ID:</strong> {ev.id}</div>
                            <div><strong>Actor UUID:</strong> {ev.actor_id}</div>
                            <div><strong>Full Resource ID:</strong> {ev.resource_id}</div>
                            {ev.correlation_id && <div><strong>{t('audit.correlationId')}:</strong> {ev.correlation_id}</div>}
                            {ev.causation_id && <div><strong>Causation ID:</strong> {ev.causation_id}</div>}
                            <div><strong>Event Details JSON:</strong></div>
                            <pre className="p-2 bg-white rounded border text-xs overflow-x-auto max-h-48">
                              {JSON.stringify(ev.details || {}, null, 2)}
                            </pre>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
