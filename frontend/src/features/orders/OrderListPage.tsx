import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ClipboardList, Filter, Plus, RefreshCw } from 'lucide-react';
import { ordersApi } from '../../api/endpoints/orders';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { OrderResponse, OrderStatus } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { EmptyState } from '../../components/feedback/EmptyState';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { CursorPagination } from '../../components/ui/Pagination';

export const OrderListPage: React.FC = () => {
  const { showToast } = useToast();
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
          <h1 className="text-2xl font-bold">Orders</h1>
          <p className="text-sm text-muted mt-1">
            Authoritative order lifecycle management and fulfillment status.
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
            <span>Refresh</span>
          </Button>
          <Link to="/orders/new">
            <Button size="sm" variant="primary">
              <Plus size={14} className="mr-1" />
              <span>Create Order</span>
            </Button>
          </Link>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      <Card className="mb-6">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-3">
            <Filter size={16} className="text-muted" />
            <span className="text-xs font-semibold text-muted uppercase">Lifecycle Status</span>
            <div style={{ width: '160px' }}>
              <select
                className="form-select text-xs py-1"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="">All Orders</option>
                <option value="PENDING">PENDING</option>
                <option value="CONFIRMED">CONFIRMED</option>
                <option value="CANCELLED">CANCELLED</option>
              </select>
            </div>
          </div>
          <div className="text-xs text-muted">
            Showing <strong className="text-main">{orders.length}</strong> orders
          </div>
        </div>
      </Card>

      {isLoading ? (
        <div className="text-center py-12 text-muted">Loading orders...</div>
      ) : orders.length === 0 ? (
        <Card>
          <EmptyState
            title="No Orders Found"
            description={
              statusFilter
                ? `No orders currently match status "${statusFilter}". Try selecting a different filter.`
                : 'No orders have been submitted for this tenant yet.'
            }
            actionLabel="Submit First Order"
            onAction={() => window.location.assign('/orders/new')}
            icon={<ClipboardList size={48} strokeWidth={1.5} />}
          />
        </Card>
      ) : (
        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>Order ID</th>
                <th>Status</th>
                <th>Customer Ref</th>
                <th>Lines</th>
                <th>Total Value</th>
                <th>Placed At</th>
                <th>Actions</th>
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
                  ? `${(order.total.amount_minor / 100).toFixed(2)} ${order.total.currency}`
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
                      {order.total_quantity} unit(s) ({order.lines.length} lines)
                    </td>
                    <td className="text-xs font-medium">{totalFormatted}</td>
                    <td className="text-xs text-muted">
                      {new Date(order.placed_at).toLocaleString()}
                    </td>
                    <td>
                      <Link to={`/orders/${order.id}`}>
                        <Button size="sm" variant="secondary">
                          View Details
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
