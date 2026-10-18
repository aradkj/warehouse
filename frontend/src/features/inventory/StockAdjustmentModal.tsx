import React, { useState } from 'react';
import { inventoryApi } from '../../api/endpoints/inventory';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { AdjustmentResponse, WarehouseResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';
import { Select } from '../../components/ui/Select';

export interface StockAdjustmentModalProps {
  isOpen: boolean;
  onClose: () => void;
  onAdjusted: (res: AdjustmentResponse) => void;
  warehouses: WarehouseResponse[];
  defaultWarehouseId?: string;
  defaultSkuId?: string;
}

export const StockAdjustmentModal: React.FC<StockAdjustmentModalProps> = ({
  isOpen,
  onClose,
  onAdjusted,
  warehouses,
  defaultWarehouseId = '',
  defaultSkuId = '',
}) => {
  const { showToast } = useToast();

  const [warehouseId, setWarehouseId] = useState(defaultWarehouseId);
  const [skuId, setSkuId] = useState(defaultSkuId);
  const [quantityDelta, setQuantityDelta] = useState<number>(0);
  const [reason, setReason] = useState<'GOODS_RECEIPT' | 'CUSTOMER_RETURN' | 'CYCLE_COUNT' | 'DAMAGE' | 'MANUAL_CORRECTION'>('GOODS_RECEIPT');
  const [note, setNote] = useState('');
  const [step, setStep] = useState<'form' | 'confirm'>('form');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleReview = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!warehouseId || !skuId) {
      setError('Warehouse and SKU identifier are required.');
      return;
    }

    if (quantityDelta === 0) {
      setError('Quantity delta cannot be zero. Positive receives stock, negative writes it off.');
      return;
    }

    setStep('confirm');
  };

  const handleConfirmSubmit = async () => {
    setIsSubmitting(true);
    setError(null);

    try {
      const result = await inventoryApi.adjustInventory({
        warehouse_id: warehouseId,
        sku_id: skuId,
        quantity_delta: Number(quantityDelta),
        reason,
        note: note.trim() || null,
      });

      showToast(`Stock adjusted (${quantityDelta > 0 ? `+${quantityDelta}` : quantityDelta} units).`, 'success');
      onAdjusted(result);
      handleClose();
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to adjust inventory.';
      setError(msg);
      setStep('form');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleClose = () => {
    setWarehouseId(defaultWarehouseId);
    setSkuId(defaultSkuId);
    setQuantityDelta(0);
    setReason('GOODS_RECEIPT');
    setNote('');
    setStep('form');
    setError(null);
    onClose();
  };

  const selectedWarehouse = warehouses.find((w) => w.id === warehouseId);

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title={step === 'confirm' ? 'Confirm Stock Adjustment' : 'Adjust Stock'}
      description="Record physical stock receipts, damage write-offs, or cycle counts into the immutable inventory ledger."
    >
      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      {step === 'form' ? (
        <form onSubmit={handleReview}>
          <div className="form-group">
            <label className="form-label">Warehouse <span className="text-danger">*</span></label>
            <select
              className="form-select"
              value={warehouseId}
              onChange={(e) => setWarehouseId(e.target.value)}
              required
            >
              <option value="">Select a warehouse...</option>
              {warehouses.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} ({w.code})
                </option>
              ))}
            </select>
          </div>

          <Input
            label="SKU Identifier"
            value={skuId}
            onChange={(e) => setSkuId(e.target.value)}
            placeholder="e.g. 550e8400-e29b-41d4-a716-446655440000"
            helperText="The unique SKU UUID from the catalog"
            required
          />

          <div className="grid grid-cols-2 gap-4">
            <Input
              label="Quantity Delta (Signed integer)"
              type="number"
              value={quantityDelta}
              onChange={(e) => setQuantityDelta(Number(e.target.value))}
              helperText="Positive to add stock, negative to deduct."
              required
            />

            <div className="form-group">
              <label className="form-label">Adjustment Reason <span className="text-danger">*</span></label>
              <select
                className="form-select"
                value={reason}
                onChange={(e) => setReason(e.target.value as any)}
                required
              >
                <option value="GOODS_RECEIPT">GOODS_RECEIPT (Receipt)</option>
                <option value="CUSTOMER_RETURN">CUSTOMER_RETURN (Return)</option>
                <option value="CYCLE_COUNT">CYCLE_COUNT (Count correction)</option>
                <option value="DAMAGE">DAMAGE (Damage write-off)</option>
                <option value="MANUAL_CORRECTION">MANUAL_CORRECTION (Correction)</option>
              </select>
            </div>
          </div>

          <Input
            label="Audit Note (Optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="PO #10294 or physical inspection note"
            maxLength={256}
          />

          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={handleClose}>
              Cancel
            </Button>
            <Button variant="primary" type="submit">
              Review Adjustment →
            </Button>
          </div>
        </form>
      ) : (
        <div>
          <Alert variant="warning" className="mb-4">
            Please verify the adjustment details. This transaction will write an immutable row to the stock movement ledger.
          </Alert>

          <div className="card p-4 bg-surface-subtle mb-4 text-xs font-mono">
            <div className="mb-2"><strong>Warehouse:</strong> {selectedWarehouse?.name} ({warehouseId})</div>
            <div className="mb-2"><strong>SKU:</strong> {skuId}</div>
            <div className="mb-2">
              <strong>Change:</strong>{' '}
              <span className={`font-bold ${quantityDelta > 0 ? 'text-success' : 'text-danger'}`}>
                {quantityDelta > 0 ? `+${quantityDelta}` : quantityDelta} units
              </span>
            </div>
            <div className="mb-2"><strong>Reason:</strong> {reason}</div>
            {note && <div><strong>Note:</strong> {note}</div>}
          </div>

          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={() => setStep('form')} disabled={isSubmitting}>
              ← Back to Edit
            </Button>
            <Button variant="danger" onClick={handleConfirmSubmit} isLoading={isSubmitting}>
              Confirm Mutation
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
};
