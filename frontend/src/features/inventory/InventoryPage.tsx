import React, { useEffect, useState } from 'react';
import { Layers, Plus, RefreshCw, Warehouse } from 'lucide-react';
import { inventoryApi } from '../../api/endpoints/inventory';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { AvailabilityResponse, WarehouseResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { EmptyState } from '../../components/feedback/EmptyState';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { CursorPagination } from '../../components/ui/Pagination';
import { StockAdjustmentModal } from './StockAdjustmentModal';

export const InventoryPage: React.FC = () => {
  const { showToast } = useToast();
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
          <h1 className="text-2xl font-bold">Stock Availability</h1>
          <p className="text-sm text-muted mt-1">
            Authoritative inventory positions under PostgreSQL row-level locks.
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
            <span>Refresh</span>
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
            <span>Adjust Stock</span>
          </Button>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      {/* Filter Bar */}
      <Card className="mb-6">
        <form onSubmit={handleSearchSubmit} className="flex items-center gap-4 flex-wrap">
          <div style={{ minWidth: '220px' }}>
            <label className="text-xs font-semibold text-muted uppercase block mb-1">
              Warehouse
            </label>
            <select
              className="form-select text-xs py-1.5"
              value={selectedWarehouse}
              onChange={(e) => setSelectedWarehouse(e.target.value)}
            >
              <option value="">All Warehouses</option>
              {warehouses.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} ({w.code})
                </option>
              ))}
            </select>
          </div>

          <div style={{ flex: 1, minWidth: '240px' }}>
            <label className="text-xs font-semibold text-muted uppercase block mb-1">
              SKU Identifier
            </label>
            <input
              type="text"
              className="form-input text-xs py-1.5 font-mono"
              placeholder="Filter by exact SKU UUID..."
              value={skuFilter}
              onChange={(e) => setSkuFilter(e.target.value)}
            />
          </div>

          <div className="pt-5">
            <Button size="sm" variant="secondary" type="submit">
              Apply Filter
            </Button>
          </div>
        </form>
      </Card>

      {/* Inventory Table */}
      {isLoading ? (
        <div className="text-center py-12 text-muted">Querying inventory availability...</div>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            title="No Inventory Positions Found"
            description="No inventory items match your criteria. Record a stock adjustment to add initial quantities."
            actionLabel="Adjust Stock"
            onAction={() => setIsAdjustOpen(true)}
            icon={<Layers size={48} strokeWidth={1.5} />}
          />
        </Card>
      ) : (
        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>SKU Identifier</th>
                <th>Warehouse</th>
                <th>On Hand</th>
                <th>Reserved</th>
                <th>Available</th>
                <th>Last Updated</th>
                <th>Actions</th>
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
                    <td className="text-xs font-medium">{it.on_hand}</td>
                    <td className="text-xs font-medium text-warning">
                      {it.reserved > 0 ? it.reserved : '0'}
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
                        {it.available}
                      </span>
                    </td>
                    <td className="text-xs text-muted">
                      {new Date(it.updated_at).toLocaleString()}
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
                        Adjust
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
