import React, { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle,
  Clock,
  Package,
  RefreshCw,
  RotateCw,
  Truck,
  Warehouse,
} from 'lucide-react';
import { fulfillmentApi } from '../../api/endpoints/fulfillment';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { FulfillmentResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';

export const FulfillmentDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const { showToast } = useToast();

  const [fulfillment, setFulfillment] = useState<FulfillmentResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRetrying, setIsRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchFulfillment = async () => {
    if (!id) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await fulfillmentApi.getFulfillment(id);
      setFulfillment(data);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to retrieve fulfillment.';
      setError(msg);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchFulfillment();
  }, [id]);

  const handleRetry = async () => {
    if (!id) return;
    setIsRetrying(true);
    try {
      const updated = await fulfillmentApi.retryFulfillment(id, crypto.randomUUID());
      setFulfillment(updated);
      showToast('Fulfillment retry dispatched to background worker.', 'success');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to retry fulfillment.';
      showToast(msg, 'error');
    } finally {
      setIsRetrying(false);
    }
  };

  if (isLoading) {
    return <div className="text-center py-12 text-muted">Loading fulfillment record...</div>;
  }

  if (error || !fulfillment) {
    return (
      <div>
        <Alert variant="danger" className="mb-4">
          {error || 'Fulfillment not found.'}
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

  const isFailed = fulfillment.status === 'FAILED';
  const isDispatched = fulfillment.status === 'DISPATCHED';

  const badgeVariant = isDispatched ? 'success' : isFailed ? 'danger' : 'warning';

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <Link to={`/orders/${fulfillment.order_id}`} className="text-xs text-muted hover:text-main flex items-center">
          <ArrowLeft size={14} className="mr-1" />
          <span>Back to Order {fulfillment.order_id.slice(0, 8)}</span>
        </Link>
      </div>

      <div className="page-header">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">Fulfillment {fulfillment.id.slice(0, 8)}</h1>
            <Badge variant={badgeVariant}>{fulfillment.status}</Badge>
          </div>
          <div className="text-xs text-muted font-mono mt-1">
            Fulfillment UUID: {fulfillment.id} • Version: {fulfillment.version}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={fetchFulfillment}>
            <RefreshCw size={14} className="mr-1" />
            <span>Refresh</span>
          </Button>

          {isFailed && (
            <Button size="sm" variant="danger" onClick={handleRetry} isLoading={isRetrying}>
              <RotateCw size={14} className="mr-1" />
              <span>Retry Carrier Dispatch</span>
            </Button>
          )}
        </div>
      </div>

      {/* Failure explanation alert */}
      {isFailed && (
        <Alert variant="danger" className="mb-6" title="Carrier Dispatch Failed">
          {fulfillment.failure_reason || 'Carrier dispatch encountered a permanent or timeout error.'}
          <div className="mt-2 text-xs font-mono">
            Attempts: {fulfillment.retry_count} / {fulfillment.max_retries} max retries
          </div>
        </Alert>
      )}

      {/* Details Grid */}
      <div className="grid grid-cols-3 gap-6 mb-6">
        <Card title="Dispatch Overview" className="mb-0">
          <div className="space-y-3 text-xs">
            <div>
              <span className="text-muted block">Associated Order:</span>
              <Link to={`/orders/${fulfillment.order_id}`} className="font-mono text-primary font-bold hover:underline">
                {fulfillment.order_id}
              </Link>
            </div>
            <div>
              <span className="text-muted block">Warehouse ID:</span>
              <span className="font-mono">{fulfillment.warehouse_id}</span>
            </div>
            <div>
              <span className="text-muted block">Created At:</span>
              <span className="font-mono">{new Date(fulfillment.created_at).toLocaleString()}</span>
            </div>
            <div>
              <span className="text-muted block">Last Updated:</span>
              <span className="font-mono">{new Date(fulfillment.updated_at).toLocaleString()}</span>
            </div>
          </div>
        </Card>

        <Card title="Carrier & Tracking" className="mb-0">
          <div className="space-y-3 text-xs">
            <div>
              <span className="text-muted block">Assigned Carrier:</span>
              <strong className="text-sm font-semibold">{fulfillment.carrier || 'Pending Carrier Assignment'}</strong>
            </div>
            <div>
              <span className="text-muted block">Tracking Number:</span>
              {fulfillment.tracking_number ? (
                <span className="font-mono text-sm font-bold text-main">{fulfillment.tracking_number}</span>
              ) : (
                <span className="text-muted">—</span>
              )}
            </div>
            <div>
              <span className="text-muted block">Retry Counter:</span>
              <span className="font-medium">{fulfillment.retry_count} of {fulfillment.max_retries} attempts</span>
            </div>
          </div>
        </Card>

        <Card title="Lifecycle Status" className="mb-0">
          <div className="text-center py-3">
            <div className="text-lg font-bold mb-1">{fulfillment.status}</div>
            <p className="text-xs text-muted">
              {isDispatched
                ? 'Package has been dispatched and handed off to carrier.'
                : isFailed
                  ? 'Dispatch failed. Review failure reason above and retry.'
                  : 'Fulfillment task is actively being processed by warehouse worker.'}
            </p>
          </div>
        </Card>
      </div>

      {/* Picking Tasks Table */}
      <Card title="Warehouse Picking Tasks" subtitle="Individual SKU items designated for this dispatch">
        <div className="table-container border-0">
          <table className="data-table">
            <thead>
              <tr>
                <th>Task ID</th>
                <th>SKU Identifier</th>
                <th>Quantity</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {fulfillment.tasks.map((task) => (
                <tr key={task.task_id}>
                  <td className="font-mono text-xs text-muted">{task.task_id.slice(0, 8)}...</td>
                  <td className="font-mono text-xs font-bold text-main">{task.sku_id}</td>
                  <td className="text-xs font-semibold">{task.quantity}</td>
                  <td>
                    <Badge variant={task.is_completed ? 'success' : 'warning'}>
                      {task.is_completed ? 'Completed' : 'Pending'}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
};
