import React, { useEffect, useState } from 'react';
import { Layers, Plus, RefreshCw } from 'lucide-react';
import { inventoryApi } from '../../api/endpoints/inventory';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { AvailabilityResponse, WarehouseResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { useTranslation } from '../../i18n';
import { EmptyState } from '../../components/feedback/EmptyState';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { CursorPagination } from '../../components/ui/Pagination';
import { StockAdjustmentModal } from './StockAdjustmentModal';

export const InventoryPage: React.FC = () => {
  const { showToast } = useToast();
  const { t, formatDate, formatNumber } = useTranslation();
  const [items, setItems] = useState<AvailabilityResponse[]>([]);
  const [warehouses, setWarehouses] = useState<WarehouseResponse[]>([]);
  const [selectedWarehouse, setSelectedWarehouse] = useState('');
  const [skuFilter, setSkuFilter] = useState('');
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Adjustment modal
  const [isAdjustOpen, setIsAdjustOpen] = useState(false);
  const [adjustTarget, setAdjustTarget] = useState<{ warehouseId?: string; skuId?: string }>({});

  const fetchWarehouses = async () => {
    try {
      const res = await inventoryApi.listWarehouses({ limit: 100 });
      setWarehouses(res.items);
    } catch {
      // Ignored
    }
  };

  const fetchInventory = async (cursor: string | null = null) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await inventoryApi.getAvailability({
        warehouse_id: selectedWarehouse || null,
        sku_id: skuFilter.trim() || null,
        limit: 20,
        cursor,
      });
      setItems(data.items);
      setNextCursor(data.next_cursor);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to fetch inventory availability.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchWarehouses();
  }, []);

  useEffect(() => {
    fetchInventory(null);
  }, [selectedWarehouse]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    fetchInventory(null);
  };

  const handleAdjusted = () => {
    fetchInventory(null);
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">{t('inventory.title')}</h1>
          <p className="text-sm text-muted mt-1">
            {t('inventory.subtitle')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => fetchInventory(null)}
            isLoading={isLoading}
          >
            <RefreshCw size={14} className="mr-1" />
            <span>{t('common.refresh')}</span>
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={() => {
              setAdjustTarget({ warehouseId: selectedWarehouse || undefined });
              setIsAdjustOpen(true);
            }}
          >
            <Plus size={14} className="mr-1" />
            <span>{t('inventory.adjustStockBtn')}</span>
          </Button>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      {/* Filter Bar */}
      <Card className="mb-6">
        <form onSubmit={handleSearchSubmit} className="flex items-center gap-4 flex-wrap">
          <div style={{ minWidth: '220px' }}>
            <label className="text-xs font-semibold text-muted uppercase block mb-1">
              {t('inventory.warehouse')}
            </label>
            <select
              className="form-select text-xs py-1.5"
              value={selectedWarehouse}
              onChange={(e) => setSelectedWarehouse(e.target.value)}
            >
              <option value="">{t('common.all')}</option>
              {warehouses.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} ({w.code})
                </option>
              ))}
            </select>
          </div>

          <div style={{ flex: 1, minWidth: '240px' }}>
            <label className="text-xs font-semibold text-muted uppercase block mb-1">
              {t('inventory.sku')}
            </label>
            <input
              type="text"
              className="form-input text-xs py-1.5 font-mono"
              placeholder={t('inventory.filterPlaceholder')}
              value={skuFilter}
              onChange={(e) => setSkuFilter(e.target.value)}
            />
          </div>

          <div className="pt-5">
            <Button size="sm" variant="secondary" type="submit">
              {t('common.filter')}
            </Button>
          </div>
        </form>
      </Card>

      {/* Inventory Table */}
      {isLoading ? (
        <div className="text-center py-12 text-muted">{t('common.loading')}</div>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            title={t('inventory.noStockRecords')}
            description={t('inventory.subtitle')}
            actionLabel={t('inventory.adjustStockBtn')}
            onAction={() => setIsAdjustOpen(true)}
            icon={<Layers size={48} strokeWidth={1.5} />}
          />
        </Card>
      ) : (
        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('inventory.sku')}</th>
                <th>{t('inventory.warehouse')}</th>
                <th>{t('inventory.onHand')}</th>
                <th>{t('inventory.reserved')}</th>
                <th>{t('inventory.available')}</th>
                <th>{t('common.updated_at')}</th>
                <th>{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const wh = warehouses.find((w) => w.id === it.warehouse_id);
                return (
                  <tr key={it.inventory_item_id}>
                    <td className="font-mono text-xs font-bold text-main">
                      {it.sku_id}
                    </td>
                    <td className="text-xs">
                      <div>{wh?.name || it.warehouse_id.slice(0, 8)}</div>
                      {wh && <div className="text-muted font-mono">{wh.code}</div>}
                    </td>
                    <td className="text-xs font-medium">{formatNumber(it.on_hand)}</td>
                    <td className="text-xs font-medium text-warning">
                      {it.reserved > 0 ? formatNumber(it.reserved) : '0'}
                    </td>
                    <td>
                      <span
                        className={`font-bold text-sm ${
                          it.available <= 0
                            ? 'text-danger'
                            : it.available <= 5
                              ? 'text-warning'
                              : 'text-success'
                        }`}
                      >
                        {formatNumber(it.available)}
                      </span>
                    </td>
                    <td className="text-xs text-muted">
                      {formatDate(it.updated_at)}
                    </td>
                    <td>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          setAdjustTarget({ warehouseId: it.warehouse_id, skuId: it.sku_id });
                          setIsAdjustOpen(true);
                        }}
                      >
                        {t('common.edit')}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <CursorPagination
            hasMore={!!nextCursor}
            onNext={() => fetchInventory(nextCursor)}
            isLoading={isLoading}
          />
        </div>
      )}

      <StockAdjustmentModal
        isOpen={isAdjustOpen}
        onClose={() => setIsAdjustOpen(false)}
        onAdjusted={handleAdjusted}
        warehouses={warehouses}
        defaultWarehouseId={adjustTarget.warehouseId}
        defaultSkuId={adjustTarget.skuId}
      />
    </div>
  );
};
