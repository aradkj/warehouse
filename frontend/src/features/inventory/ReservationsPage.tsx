import React, { useState } from 'react';
import { CheckSquare, Clock, Plus, Search, Trash2, XCircle } from 'lucide-react';
import { inventoryApi } from '../../api/endpoints/inventory';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { ReservationResponse, ReservationResultResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';

export const ReservationsPage: React.FC = () => {
  const { showToast } = useToast();

  const [lookupId, setLookupId] = useState('');
  const [currentReservation, setCurrentReservation] = useState<ReservationResponse | null>(null);
  const [isLoadingLookup, setIsLoadingLookup] = useState(false);
  const [lookupError, setLookupError] = useState<string | null>(null);

  // Create Modal
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [skuId, setSkuId] = useState('');
  const [quantity, setQuantity] = useState<number>(1);
  const [strategy, setStrategy] = useState<'ALL_OR_NOTHING' | 'PARTIAL'>('ALL_OR_NOTHING');
  const [ttlSeconds, setTtlSeconds] = useState<number>(1800);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [createResult, setCreateResult] = useState<ReservationResultResponse | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);

  const handleLookup = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!lookupId.trim()) return;

    setIsLoadingLookup(true);
    setLookupError(null);
    setCurrentReservation(null);

    try {
      const data = await inventoryApi.getReservation(lookupId.trim());
      setCurrentReservation(data);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Reservation not found.';
      setLookupError(msg);
    } finally {
      setIsLoadingLookup(false);
    }
  };

  const handleRelease = async (resId: string) => {
    if (!window.confirm('Are you sure you want to release this reservation? Reserved quantities will immediately return to available stock.')) {
      return;
    }

    try {
      const released = await inventoryApi.releaseReservation(resId);
      setCurrentReservation(released);
      showToast('Reservation successfully released.', 'info');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to release reservation.';
      showToast(msg, 'error');
    }
  };

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError(null);
    setCreateResult(null);

    if (!skuId.trim() || quantity <= 0) {
      setCreateError('Valid SKU ID and positive quantity are required.');
      return;
    }

    setIsSubmitting(true);
    try {
      const result = await inventoryApi.createReservation(
        {
          requirements: [{ sku_id: skuId.trim(), quantity: Number(quantity) }],
          strategy,
          ttl_seconds: ttlSeconds > 0 ? ttlSeconds : null,
        },
        crypto.randomUUID()
      );

      setCreateResult(result);
      if (result.reservation) {
        showToast('Stock reserved successfully.', 'success');
        setCurrentReservation(result.reservation);
      } else {
        showToast('Stock allocation incomplete (see shortfalls).', 'warning');
      }
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to create reservation.';
      setCreateError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const resetCreateModal = () => {
    setIsCreateOpen(false);
    setSkuId('');
    setQuantity(1);
    setStrategy('ALL_OR_NOTHING');
    setTtlSeconds(1800);
    setCreateResult(null);
    setCreateError(null);
  };

  const statusVariant =
    currentReservation?.status === 'CONFIRMED'
      ? 'success'
      : currentReservation?.status === 'HELD'
        ? 'warning'
        : currentReservation?.status === 'EXPIRED'
          ? 'danger'
          : 'neutral';

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">Stock Reservations</h1>
          <p className="text-sm text-muted mt-1">
            Manage row-level transactional holds and verify allocation shortfalls.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="primary"
            onClick={() => setIsCreateOpen(true)}
          >
            <Plus size={14} className="mr-1" />
            <span>Create Reservation</span>
          </Button>
        </div>
      </div>

      {/* Lookup Card */}
      <Card title="Inspect Reservation by ID" className="mb-6">
        <form onSubmit={handleLookup} className="flex items-center gap-3">
          <input
            type="text"
            className="form-input font-mono text-xs"
            placeholder="Enter Reservation UUID..."
            value={lookupId}
            onChange={(e) => setLookupId(e.target.value)}
            style={{ maxWidth: '420px' }}
            required
          />
          <Button size="sm" variant="secondary" type="submit" isLoading={isLoadingLookup}>
            <Search size={14} className="mr-1" />
            <span>Retrieve</span>
          </Button>
        </form>

        {lookupError && <Alert variant="danger" className="mt-4 mb-0">{lookupError}</Alert>}
      </Card>

      {/* Current Reservation Detail View */}
      {currentReservation && (
        <Card
          title={`Reservation ${currentReservation.id}`}
          action={
            currentReservation.status === 'HELD' && (
              <Button
                size="sm"
                variant="danger"
                onClick={() => handleRelease(currentReservation.id)}
              >
                <Trash2 size={14} className="mr-1" />
                <span>Release Reservation</span>
              </Button>
            )
          }
        >
          <div className="grid grid-cols-4 gap-4 pb-4 border-b text-xs mb-4">
            <div>
              <span className="text-muted block">Status:</span>
              <Badge variant={statusVariant}>{currentReservation.status}</Badge>
            </div>
            <div>
              <span className="text-muted block">Total Quantity Held:</span>
              <strong className="text-sm font-semibold">{currentReservation.total_quantity} units</strong>
            </div>
            <div>
              <span className="text-muted block">Expires At:</span>
              <span className="font-mono">{new Date(currentReservation.expires_at).toLocaleString()}</span>
            </div>
            <div>
              <span className="text-muted block">Order Attached:</span>
              <span className="font-mono">{currentReservation.order_id || 'Standalone (None)'}</span>
            </div>
          </div>

          <h2 className="text-sm font-semibold mb-2">Allocations Breakdown</h2>
          {currentReservation.allocations.length === 0 ? (
            <div className="text-xs text-muted py-3">No physical allocations currently held.</div>
          ) : (
            <div className="table-container border-0">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Allocation ID</th>
                    <th>Warehouse ID</th>
                    <th>SKU ID</th>
                    <th>Quantity Held</th>
                  </tr>
                </thead>
                <tbody>
                  {currentReservation.allocations.map((alloc) => (
                    <tr key={alloc.id}>
                      <td className="font-mono text-xs">{alloc.id.slice(0, 8)}...</td>
                      <td className="font-mono text-xs">{alloc.warehouse_id}</td>
                      <td className="font-mono text-xs font-bold">{alloc.sku_id}</td>
                      <td className="text-xs font-semibold text-primary">{alloc.quantity}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {/* Create Reservation Modal */}
      <Modal
        isOpen={isCreateOpen}
        onClose={resetCreateModal}
        title="Create Standalone Stock Hold"
        description="Reserve physical warehouse inventory without placing a customer order."
      >
        {createError && <Alert variant="danger" className="mb-4">{createError}</Alert>}

        {createResult ? (
          <div>
            {createResult.is_complete ? (
              <Alert variant="success" className="mb-4">
                Reservation created successfully! Full stock requirement satisfied.
              </Alert>
            ) : (
              <Alert variant="warning" className="mb-4">
                Stock reservation was partial. Review shortfalls below.
              </Alert>
            )}

            {createResult.shortfalls.length > 0 && (
              <div className="mb-4">
                <div className="text-xs font-semibold uppercase text-muted mb-2">Stock Shortfalls</div>
                <div className="table-container border-0">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>SKU</th>
                        <th>Required</th>
                        <th>Allocated</th>
                        <th>Missing</th>
                      </tr>
                    </thead>
                    <tbody>
                      {createResult.shortfalls.map((s) => (
                        <tr key={s.sku_id}>
                          <td className="font-mono text-xs">{s.sku_id.slice(0, 8)}...</td>
                          <td className="text-xs">{s.required}</td>
                          <td className="text-xs font-semibold text-success">{s.allocated}</td>
                          <td className="text-xs font-semibold text-danger">{s.missing}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="flex justify-end mt-4">
              <Button variant="primary" onClick={resetCreateModal}>
                Done
              </Button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleCreateSubmit}>
            <Input
              label="SKU Identifier"
              value={skuId}
              onChange={(e) => setSkuId(e.target.value)}
              placeholder="e.g. 550e8400-e29b-41d4-a716-446655440000"
              required
            />

            <div className="grid grid-cols-2 gap-4">
              <Input
                label="Quantity"
                type="number"
                min="1"
                value={quantity}
                onChange={(e) => setQuantity(Number(e.target.value))}
                required
              />

              <div className="form-group">
                <label className="form-label">Allocation Strategy</label>
                <select
                  className="form-select"
                  value={strategy}
                  onChange={(e) => setStrategy(e.target.value as any)}
                >
                  <option value="ALL_OR_NOTHING">ALL_OR_NOTHING (Strict)</option>
                  <option value="PARTIAL">PARTIAL (Hold available)</option>
                </select>
              </div>
            </div>

            <Input
              label="Time to Live (Seconds)"
              type="number"
              min="1"
              max="86400"
              value={ttlSeconds}
              onChange={(e) => setTtlSeconds(Number(e.target.value))}
              helperText="Default 1800s (30m). Hold releases automatically on expiry."
              required
            />

            <div className="flex justify-end gap-2 mt-6">
              <Button variant="secondary" onClick={resetCreateModal} disabled={isSubmitting}>
                Cancel
              </Button>
              <Button variant="primary" type="submit" isLoading={isSubmitting}>
                Reserve Stock
              </Button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
};
