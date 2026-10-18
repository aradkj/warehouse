import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ClipboardList, Filter, Plus, RefreshCw } from 'lucide-react';
import { ordersApi } from '../../api/endpoints/orders';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { OrderResponse, OrderStatus } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { useTranslation } from '../../i18n';
import { EmptyState } from '../../components/feedback/EmptyState';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { CursorPagination } from '../../components/ui/Pagination';

export const OrderListPage: React.FC = () => {
  const { showToast } = useToast();
  const { t, formatDate, formatNumber } = useTranslation();
  const [orders, setOrders] = useState<OrderResponse[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [currentCursor, setCurrentCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<(string | null)[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchOrders = async (cursor: string | null = null) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await ordersApi.listOrders({
        limit: 20,
        cursor,
        status: (statusFilter as OrderStatus) || null,
      });
      setOrders(data.items);
      setNextCursor(data.next_cursor);
      setCurrentCursor(cursor);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to fetch orders.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    setCursorHistory([]);
    fetchOrders(null);
  }, [statusFilter]);

  const handleNextPage = () => {
    if (!nextCursor) return;
    setCursorHistory((prev) => [...prev, currentCursor]);
    fetchOrders(nextCursor);
  };

  const handleResetPagination = () => {
    setCursorHistory([]);
    fetchOrders(null);
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">{t('orders.title')}</h1>
          <p className="text-sm text-muted mt-1">
            {t('orders.subtitle', { tenant: '' })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => fetchOrders(currentCursor)}
            isLoading={isLoading}
          >
            <RefreshCw size={14} className="mr-1" />
            <span>{t('common.refresh')}</span>
          </Button>
          <Link to="/orders/new">
            <Button size="sm" variant="primary">
              <Plus size={14} className="mr-1" />
              <span>{t('orders.newOrderBtn')}</span>
            </Button>
          </Link>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      <Card className="mb-6">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-3">
            <Filter size={16} className="text-muted" />
            <span className="text-xs font-semibold text-muted uppercase">{t('common.status')}</span>
            <div style={{ width: '160px' }}>
              <select
                className="form-select text-xs py-1"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="">{t('common.all')}</option>
                <option value="PENDING">PENDING</option>
                <option value="CONFIRMED">CONFIRMED</option>
                <option value="CANCELLED">CANCELLED</option>
              </select>
            </div>
          </div>
          <div className="text-xs text-muted">
            {formatNumber(orders.length)} {t('orders.title')}
          </div>
        </div>
      </Card>

      {isLoading ? (
        <div className="text-center py-12 text-muted">{t('common.loading')}</div>
      ) : orders.length === 0 ? (
        <Card>
          <EmptyState
            title={t('orders.noOrdersFound')}
            description={
              statusFilter
                ? `${t('orders.noOrdersFound')} (${statusFilter})`
                : t('orders.noOrdersYet')
            }
            actionLabel={t('orders.newOrderBtn')}
            onAction={() => window.location.assign('/orders/new')}
            icon={<ClipboardList size={48} strokeWidth={1.5} />}
          />
        </Card>
      ) : (
        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('orders.orderId')}</th>
                <th>{t('orders.status')}</th>
                <th>{t('orders.customerRef')}</th>
                <th>{t('orders.itemsCount')}</th>
                <th>{t('orders.totalAmount')}</th>
                <th>{t('orders.createdAt')}</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => {
                const badgeVariant =
                  order.status === 'CONFIRMED'
                    ? 'success'
                    : order.status === 'PENDING'
                      ? 'warning'
                      : 'neutral';

                const totalFormatted = order.total
                  ? `${formatNumber(order.total.amount_minor / 100, { minimumFractionDigits: 2 })} ${order.total.currency}`
                  : '—';

                return (
                  <tr key={order.id}>
                    <td>
                      <Link to={`/orders/${order.id}`} className="font-mono text-xs font-bold text-primary hover:underline">
                        {order.id.slice(0, 8)}...
                      </Link>
                    </td>
                    <td>
                      <Badge variant={badgeVariant}>{order.status}</Badge>
                    </td>
                    <td className="text-xs">
                      {order.customer_reference || <span className="text-muted">—</span>}
                    </td>
                    <td className="text-xs">
                      {formatNumber(order.total_quantity)} ({formatNumber(order.lines.length)})
                    </td>
                    <td className="text-xs font-medium">{totalFormatted}</td>
                    <td className="text-xs text-muted">
                      {formatDate(order.placed_at)}
                    </td>
                    <td>
                      <Link to={`/orders/${order.id}`}>
                        <Button size="sm" variant="secondary">
                          {t('common.details')}
                        </Button>
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <CursorPagination
            hasMore={!!nextCursor}
            onNext={handleNextPage}
            onReset={cursorHistory.length > 0 ? handleResetPagination : undefined}
            isLoading={isLoading}
          />
        </div>
      )}
    </div>
  );
};
