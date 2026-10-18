import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { ClipboardList, Search, Sparkles } from 'lucide-react';
import { searchApi } from '../../api/endpoints/search';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { OrderSearchResultResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { useTranslation } from '../../i18n';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';

export const OrderSearchPage: React.FC = () => {
  const { showToast } = useToast();
  const { t, formatNumber } = useTranslation();

  const [query, setQuery] = useState('');
  const [customer, setCustomer] = useState('');
  const [sku, setSku] = useState('');
  const [status, setStatus] = useState('');
  const [results, setResults] = useState<OrderSearchResultResponse[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setIsLoading(true);
    setError(null);
    setHasSearched(true);

    try {
      const data = await searchApi.searchOrders({
        q: query.trim() || null,
        customer: customer.trim() || null,
        sku: sku.trim() || null,
        status: status || null,
        page_size: 25,
      });
      setResults(data.items);
      setTotal(data.total);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Search query failed.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">{t('search.ordersTitle')}</h1>
          <p className="text-sm text-muted mt-1">
            {t('search.ordersSubtitle')}
          </p>
        </div>
      </div>

      <Card className="mb-6">
        <form onSubmit={handleSearch}>
          <div className="grid grid-cols-3 gap-4 mb-4">
            <div>
              <Input
                label={t('search.queryPlaceholder')}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t('search.queryPlaceholder')}
              />
            </div>
            <div>
              <Input
                label={t('orders.customerRef')}
                value={customer}
                onChange={(e) => setCustomer(e.target.value)}
                placeholder="PO-2026-..."
              />
            </div>
            <div>
              <Input
                label={t('orders.skuLabel')}
                value={sku}
                onChange={(e) => setSku(e.target.value)}
                placeholder="e.g. SKU-123"
              />
            </div>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-xs font-semibold text-muted uppercase">{t('common.status')}:</span>
              <select
                className="form-select text-xs py-1"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                style={{ width: '150px' }}
              >
                <option value="">{t('common.all')}</option>
                <option value="PENDING">PENDING</option>
                <option value="CONFIRMED">CONFIRMED</option>
                <option value="CANCELLED">CANCELLED</option>
              </select>
            </div>

            <Button variant="primary" type="submit" isLoading={isLoading}>
              <Search size={14} className="mr-1" />
              <span>{t('search.searchBtn')}</span>
            </Button>
          </div>
        </form>
      </Card>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      {isLoading ? (
        <div className="text-center py-12 text-muted">{t('common.loading')}</div>
      ) : hasSearched && results.length === 0 ? (
        <Card className="text-center py-10">
          <ClipboardList size={40} className="mx-auto text-muted mb-2" />
          <h2 className="text-base font-semibold mb-1">{t('orders.noOrdersFound')}</h2>
          <p className="text-sm text-muted">
            {t('orders.noOrdersFound')}
          </p>
        </Card>
      ) : results.length > 0 ? (
        <div>
          <div className="text-xs text-muted mb-3">
            {total !== null ? `${formatNumber(total)} results` : ''}
          </div>

          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('orders.orderId')}</th>
                  <th>{t('orders.customerRef')}</th>
                  <th>{t('orders.status')}</th>
                  <th>{t('common.details')}</th>
                  <th>{t('catalog.sku')}</th>
                  <th>Score</th>
                  <th>{t('common.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {results.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Link to={`/orders/${item.order_id}`} className="font-mono text-xs font-bold text-primary hover:underline">
                        {item.order_id.slice(0, 8)}...
                      </Link>
                    </td>
                    <td className="text-xs font-medium">
                      {item.customer_reference || <span className="text-muted">—</span>}
                    </td>
                    <td>
                      <Badge variant={item.status === 'CONFIRMED' ? 'success' : item.status === 'PENDING' ? 'warning' : 'neutral'}>
                        {item.status}
                      </Badge>
                    </td>
                    <td className="text-xs font-medium">
                      {(item.total_minor / 100).toFixed(2)} {item.currency}
                    </td>
                    <td className="text-xs font-mono">
                      {item.skus.map((s) => s.sku_code).join(', ')}
                    </td>
                    <td className="text-xs text-muted font-mono">
                      {item.score ? (
                        <span className="flex items-center gap-1">
                          <Sparkles size={12} className="text-warning" />
                          <span>{item.score.toFixed(2)}</span>
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>
                      <Link to={`/orders/${item.order_id}`}>
                        <Button size="sm" variant="secondary">
                          {t('common.view')}
                        </Button>
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
};
