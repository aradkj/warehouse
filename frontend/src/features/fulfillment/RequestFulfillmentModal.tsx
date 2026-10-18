import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fulfillmentApi } from '../../api/endpoints/fulfillment';
import { inventoryApi } from '../../api/endpoints/inventory';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { OrderResponse, WarehouseResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { useTranslation } from '../../i18n';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';

export interface RequestFulfillmentModalProps {
  isOpen: boolean;
  onClose: () => void;
  order: OrderResponse;
}

export const RequestFulfillmentModal: React.FC<RequestFulfillmentModalProps> = ({
  isOpen,
  onClose,
  order,
}) => {
  const navigate = useNavigate();
  const { showToast } = useToast();
  const { t, formatNumber } = useTranslation();

  const [warehouses, setWarehouses] = useState<WarehouseResponse[]>([]);
  const [selectedWarehouseId, setSelectedWarehouseId] = useState('');
  const [maxRetries, setMaxRetries] = useState<number>(3);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      inventoryApi.listWarehouses({ limit: 50 }).then((res) => {
        setWarehouses(res.items);
        if (res.items.length > 0 && !selectedWarehouseId) {
          // Default to order's reserved warehouse if available
          const reservedWh = order.reservation?.warehouse_ids[0];
          setSelectedWarehouseId(reservedWh || res.items[0].id);
        }
      }).catch(() => {
        // Ignore
      });
    }
  }, [isOpen, order]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!selectedWarehouseId) {
      setError('Please select a designated fulfillment warehouse.');
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await fulfillmentApi.requestFulfillment(
        {
          order_id: order.id,
          warehouse_id: selectedWarehouseId,
          requirements: order.lines.map((l) => ({ sku_id: l.sku_id, quantity: l.quantity })),
          max_retries: Number(maxRetries),
        },
        crypto.randomUUID()
      );

      showToast(`Fulfillment requested for order ${order.id.slice(0, 8)}.`, 'success');
      onClose();
      navigate(`/fulfillments/${response.id}`);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to request fulfillment.';
      setError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('orders.fulfillBtn')}
      description="Designate a fulfillment warehouse and initiate carrier dispatch tasks."
    >
      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label className="form-label">{t('inventory.warehouseSelect')} <span className="text-danger">*</span></label>
          <select
            className="form-select"
            value={selectedWarehouseId}
            onChange={(e) => setSelectedWarehouseId(e.target.value)}
            required
          >
            <option value="">{t('common.selectOption')}</option>
            {warehouses.map((wh) => (
              <option key={wh.id} value={wh.id}>
                {wh.name} ({wh.code})
              </option>
            ))}
          </select>
          <p className="text-xs text-muted mt-1">
            Warehouse-scoped ABAC applies. Caller must have dispatch permissions for this site.
          </p>
        </div>

        <Input
          label="Maximum Carrier Retry Attempts"
          type="number"
          min="0"
          max="10"
          value={maxRetries}
          onChange={(e) => setMaxRetries(Number(e.target.value))}
          helperText="Automatic background worker retry threshold on transient carrier failure."
          required
        />

        <div className="card p-3 bg-surface-subtle mb-4 text-xs font-mono">
          <div className="mb-1 font-bold">Fulfillment Task Scope:</div>
          <div>{t('orders.orderId')}: {order.id}</div>
          <div>Lines to Pick: {formatNumber(order.lines.length)} lines ({formatNumber(order.total_quantity)} items)</div>
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <Button variant="secondary" onClick={onClose} disabled={isSubmitting}>
            {t('common.cancel')}
          </Button>
          <Button variant="primary" type="submit" isLoading={isSubmitting}>
            {t('orders.fulfillBtn')}
          </Button>
        </div>
      </form>
    </Modal>
  );
};
