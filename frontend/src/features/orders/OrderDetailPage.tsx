import React, { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Ban,
  CheckCircle2,
  Clock,
  Layers,
  RefreshCw,
  ShoppingBag,
  Truck,
  Warehouse,
} from 'lucide-react';
import { ordersApi } from '../../api/endpoints/orders';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { OrderResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';
import { RequestFulfillmentModal } from '../fulfillment/RequestFulfillmentModal';

export const OrderDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const { showToast } = useToast();

  const [order, setOrder] = useState<OrderResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Cancellation Modal
  const [isCancelModalOpen, setIsCancelModalOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const [isCancelling, setIsCancelling] = useState(false);

  // Fulfillment Request Modal
  const [isFulfillModalOpen, setIsFulfillModalOpen] = useState(false);

  const fetchOrder = async () => {
    if (!id) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await ordersApi.getOrder(id);
      setOrder(data);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to retrieve order.';
      setError(msg);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchOrder();
  }, [id]);

  const handleConfirmOrder = async () => {
    if (!id) return;
    try {
      const updated = await ordersApi.confirmOrder(id);
      setOrder(updated);
      showToast('Order confirmed successfully.', 'success');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to confirm order.';
      showToast(msg, 'error');
    }
  };

  const handleCancelSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id) return;

    if (!cancelReason.trim()) {
      showToast('Cancellation reason is required.', 'error');
      return;
    }

    setIsCancelling(true);
    try {
      const updated = await ordersApi.cancelOrder(id, cancelReason.trim());
      setOrder(updated);
      setIsCancelModalOpen(false);
      setCancelReason('');
      showToast('Order cancelled. Reserved inventory released.', 'info');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to cancel order.';
      showToast(msg, 'error');
    } finally {
      setIsCancelling(false);
    }
  };

  if (isLoading) {
    return <div className="text-center py-12 text-muted">Loading order details...</div>;
  }

  if (error || !order) {
    return (
      <div>
        <Alert variant="danger" className="mb-4">
          {error || 'Order not found.'}
        </Alert>
        <Link to="/orders">
          <Button variant="secondary" size="sm">
            <ArrowLeft size={14} className="mr-1" />
            <span>Back to Orders</span>
          </Button>
        </Link>
      </div>
    );
  }

  const badgeVariant =
    order.status === 'CONFIRMED'
      ? 'success'
      : order.status === 'PENDING'
        ? 'warning'
        : 'neutral';

  const totalFormatted = order.total
    ? `${(order.total.amount_minor / 100).toFixed(2)} ${order.total.currency}`
    : 'Restricted (Financials Read Permission Required)';

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <Link to="/orders" className="text-xs text-muted hover:text-main flex items-center">
          <ArrowLeft size={14} className="mr-1" />
          <span>Back to Orders</span>
        </Link>
      </div>

      <div className="page-header">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">Order {order.id.slice(0, 8)}</h1>
            <Badge variant={badgeVariant}>{order.status}</Badge>
          </div>
          <div className="text-xs text-muted font-mono mt-1">
            Order UUID: {order.id} • Version: {order.version}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={fetchOrder}>
            <RefreshCw size={14} className="mr-1" />
            <span>Refresh</span>
          </Button>

          {order.status === 'PENDING' && (
            <>
              <Button size="sm" variant="primary" onClick={handleConfirmOrder}>
                <CheckCircle2 size={14} className="mr-1" />
                <span>Confirm Order</span>
              </Button>
              <Button size="sm" variant="danger" onClick={() => setIsCancelModalOpen(true)}>
                <Ban size={14} className="mr-1" />
                <span>Cancel Order</span>
              </Button>
            </>
          )}

          {order.status === 'CONFIRMED' && (
            <Button
              size="sm"
              variant="primary"
              onClick={() => setIsFulfillModalOpen(true)}
            >
              <Truck size={14} className="mr-1" />
              <span>Request Fulfillment</span>
            </Button>
          )}
        </div>
      </div>

      {/* Cancellation Notice */}
      {order.status === 'CANCELLED' && (
        <Alert variant="info" className="mb-6" title="Order Cancelled">
          This order was cancelled on {order.cancelled_at ? new Date(order.cancelled_at).toLocaleString() : 'N/A'}.<br />
          <strong>Reason:</strong> {order.cancellation_reason || 'No cancellation reason provided.'}
        </Alert>
      )}

      {/* Overview Cards */}
      <div className="grid grid-cols-3 gap-6 mb-6">
        <Card title="Summary & Financials" className="mb-0">
          <div className="space-y-3 text-xs">
            <div>
              <span className="text-muted block">Customer Reference:</span>
              <strong className="text-sm font-semibold">{order.customer_reference || 'None'}</strong>
            </div>
            <div>
              <span className="text-muted block">Total Quantity:</span>
              <span className="font-medium text-sm">{order.total_quantity} items across {order.lines.length} line(s)</span>
            </div>
            <div>
              <span className="text-muted block">Total Order Value:</span>
              <span className="font-mono text-sm font-bold text-main">{totalFormatted}</span>
            </div>
            <div>
              <span className="text-muted block">Placed At:</span>
              <span className="font-mono">{new Date(order.placed_at).toLocaleString()}</span>
            </div>
          </div>
        </Card>

        <Card title="Inventory Reservation" className="col-span-2 mb-0">
          {order.reservation ? (
            <div className="space-y-3 text-xs">
              <div className="flex items-center justify-between pb-2 border-b">
                <div>
                  <span className="text-muted block">Reservation State:</span>
                  <Badge variant={order.reservation.status === 'HELD' ? 'warning' : 'success'}>
                    {order.reservation.status}
                  </Badge>
                </div>
                <div>
                  <span className="text-muted block">Allocated Quantity:</span>
                  <strong className="text-sm">{order.reservation.allocated_quantity} units</strong>
                </div>
                <div>
                  <span className="text-muted block">Expires At:</span>
                  <span className="font-mono">{new Date(order.reservation.expires_at).toLocaleString()}</span>
                </div>
              </div>

              <div>
                <span className="text-muted block mb-1">Allocated Warehouses:</span>
                <div className="flex flex-wrap gap-2">
                  {order.reservation.warehouse_ids.map((wid) => (
                    <span key={wid} className="badge badge-neutral font-mono text-xs">
                      {wid}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-muted text-xs py-4">
              No active stock reservation held for this order.
            </div>
          )}
        </Card>
      </div>

      {/* Order Lines Table */}
      <Card title="Order Lines" subtitle="Snapshotted product information and unit list pricing">
        <div className="table-container border-0">
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Product Name</th>
                <th>SKU Code</th>
                <th>Quantity</th>
                <th>Unit Price</th>
                <th>Line Total</th>
              </tr>
            </thead>
            <tbody>
              {order.lines.map((line) => (
                <tr key={line.id}>
                  <td className="text-xs text-muted font-mono">{line.line_number}</td>
                  <td className="font-semibold text-xs text-main">{line.product_name}</td>
                  <td className="font-mono text-xs font-bold text-primary">{line.sku_code}</td>
                  <td className="text-xs font-medium">{line.quantity}</td>
                  <td className="text-xs font-mono">
                    {line.unit_price
                      ? `${(line.unit_price.amount_minor / 100).toFixed(2)} ${line.unit_price.currency}`
                      : '—'}
                  </td>
                  <td className="text-xs font-mono font-bold">
                    {line.line_total
                      ? `${(line.line_total.amount_minor / 100).toFixed(2)} ${line.line_total.currency}`
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Cancel Modal */}
      <Modal
        isOpen={isCancelModalOpen}
        onClose={() => setIsCancelModalOpen(false)}
        title="Cancel Order"
        description="Cancelling an order terminates downstream fulfillment and immediately releases all reserved stock."
      >
        <form onSubmit={handleCancelSubmit}>
          <div className="form-group">
            <label className="form-label">Cancellation Reason <span className="text-danger">*</span></label>
            <textarea
              className="form-textarea"
              rows={3}
              value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)}
              placeholder="e.g. Customer requested cancellation before dispatch"
              required
            />
          </div>

          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={() => setIsCancelModalOpen(false)} disabled={isCancelling}>
              Keep Order
            </Button>
            <Button variant="danger" type="submit" isLoading={isCancelling}>
              Confirm Cancellation
            </Button>
          </div>
        </form>
      </Modal>

      {/* Request Fulfillment Modal */}
      <RequestFulfillmentModal
        isOpen={isFulfillModalOpen}
        onClose={() => setIsFulfillModalOpen(false)}
        order={order}
      />
    </div>
  );
};
