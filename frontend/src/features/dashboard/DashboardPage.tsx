import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  Boxes,
  Layers,
  Package,
  Plus,
  RefreshCw,
  ShoppingBag,
  Truck,
  Warehouse,
} from 'lucide-react';
import { catalogApi } from '../../api/endpoints/catalog';
import { inventoryApi } from '../../api/endpoints/inventory';
import { ordersApi } from '../../api/endpoints/orders';
import { AvailabilityResponse, OrderResponse, ProductResponse, WarehouseResponse } from '../../api/types';
import { useAuth } from '../../app/providers/AuthContext';
import { useTenant } from '../../app/providers/TenantContext';
import { useTranslation } from '../../i18n';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';

export const DashboardPage: React.FC = () => {
  const { principal } = useAuth();
  const { activeTenantId, activeMembership } = useTenant();
  const { t, formatNumber } = useTranslation();

  const [orders, setOrders] = useState<OrderResponse[]>([]);
  const [products, setProducts] = useState<ProductResponse[]>([]);
  const [warehouses, setWarehouses] = useState<WarehouseResponse[]>([]);
  const [availability, setAvailability] = useState<AvailabilityResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadDashboardData = async () => {
    if (!activeTenantId) return;
    setIsLoading(true);
    setError(null);

    try {
      const [orderRes, prodRes, whRes, availRes] = await Promise.allSettled([
        ordersApi.listOrders({ limit: 10 }),
        catalogApi.listProducts({ limit: 10 }),
        inventoryApi.listWarehouses({ limit: 10 }),
        inventoryApi.getAvailability({ limit: 50 }),
      ]);

      if (orderRes.status === 'fulfilled') {
        setOrders(orderRes.value.items);
      }
      if (prodRes.status === 'fulfilled') {
        setProducts(prodRes.value.items);
      }
      if (whRes.status === 'fulfilled') {
        setWarehouses(whRes.value.items);
      }
      if (availRes.status === 'fulfilled') {
        setAvailability(availRes.value.items);
      }
    } catch {
      setError('Failed to load some dashboard metrics.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadDashboardData();
  }, [activeTenantId]);

  // Derive counts from actual authoritative records loaded
  const pendingOrders = orders.filter((o) => o.status === 'PENDING').length;
  const confirmedOrders = orders.filter((o) => o.status === 'CONFIRMED').length;

  const lowStockItems = availability.filter((a) => a.available <= 5 && a.on_hand > 0);
  const outOfStockItems = availability.filter((a) => a.available <= 0);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">{t('dashboard.title')}</h1>
          <p className="text-sm text-muted mt-1">
            {t('dashboard.subtitle', { tenant: activeMembership?.tenant_name || activeTenantId || '' })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={loadDashboardData}
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

      {error && <Alert variant="warning" className="mb-6">{error}</Alert>}

      {/* Metrics Row */}
      <div className="grid grid-cols-4 mb-6">
        <Card className="mb-0">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs font-semibold text-muted uppercase">{t('dashboard.recentOrdersTitle')}</div>
              <div className="text-2xl font-bold mt-1">{formatNumber(orders.length)}</div>
              <div className="text-xs text-muted mt-1">
                {formatNumber(pendingOrders)} pending • {formatNumber(confirmedOrders)} confirmed
              </div>
            </div>
            <div
              style={{
                padding: '0.75rem',
                borderRadius: '8px',
                backgroundColor: 'var(--color-primary-subtle)',
                color: 'var(--color-primary)',
              }}
            >
              <ShoppingBag size={24} />
            </div>
          </div>
        </Card>

        <Card className="mb-0">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs font-semibold text-muted uppercase">{t('dashboard.totalProducts')}</div>
              <div className="text-2xl font-bold mt-1">{formatNumber(products.length)}</div>
              <div className="text-xs text-muted mt-1">
                {formatNumber(products.filter((p) => p.status === 'ACTIVE').length)} {t('common.active')}
              </div>
            </div>
            <div
              style={{
                padding: '0.75rem',
                borderRadius: '8px',
                backgroundColor: 'var(--color-success-bg)',
                color: 'var(--color-success)',
              }}
            >
              <Boxes size={24} />
            </div>
          </div>
        </Card>

        <Card className="mb-0">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs font-semibold text-muted uppercase">{t('dashboard.warehouses')}</div>
              <div className="text-2xl font-bold mt-1">{formatNumber(warehouses.length)}</div>
              <div className="text-xs text-muted mt-1">{t('inventory.warehousesSubtitle')}</div>
            </div>
            <div
              style={{
                padding: '0.75rem',
                borderRadius: '8px',
                backgroundColor: 'var(--bg-surface-subtle)',
                color: 'var(--text-secondary)',
              }}
            >
              <Warehouse size={24} />
            </div>
          </div>
        </Card>

        <Card className="mb-0">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs font-semibold text-muted uppercase">{t('inventory.title')}</div>
              <div className="text-2xl font-bold mt-1 text-danger">
                {formatNumber(lowStockItems.length + outOfStockItems.length)}
              </div>
              <div className="text-xs text-muted mt-1">
                {formatNumber(outOfStockItems.length)} depleted • {formatNumber(lowStockItems.length)} low stock
              </div>
            </div>
            <div
              style={{
                padding: '0.75rem',
                borderRadius: '8px',
                backgroundColor: 'var(--color-danger-bg)',
                color: 'var(--color-danger)',
              }}
            >
              <AlertTriangle size={24} />
            </div>
          </div>
        </Card>
      </div>

      {/* Main Grid: Orders & Inventory */}
      <div className="grid grid-cols-2 gap-6">
        {/* Recent Orders */}
        <Card
          title={t('dashboard.recentOrdersTitle')}
          subtitle={t('dashboard.subtitle', { tenant: activeMembership?.tenant_name || '' })}
          action={
            <Link to="/orders" className="text-xs text-primary font-medium hover:underline">
              {t('dashboard.viewAllOrders')}
            </Link>
          }
        >
          {orders.length === 0 ? (
            <div className="text-center py-8 text-muted text-sm">
              {t('dashboard.noRecentOrders')}
            </div>
          ) : (
            <div className="table-container border-0">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('orders.orderId')}</th>
                    <th>{t('orders.status')}</th>
                    <th>{t('orders.itemsCount')}</th>
                    <th>{t('orders.totalAmount')}</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.slice(0, 5).map((order) => {
                    const badgeVariant =
                      order.status === 'CONFIRMED'
                        ? 'success'
                        : order.status === 'PENDING'
                          ? 'warning'
                          : 'neutral';

                    return (
                      <tr key={order.id}>
                        <td className="font-mono text-xs">
                          <Link to={`/orders/${order.id}`} className="font-semibold text-primary">
                            {order.id.slice(0, 8)}...
                          </Link>
                        </td>
                        <td>
                          <Badge variant={badgeVariant}>{order.status}</Badge>
                        </td>
                        <td className="text-xs">{formatNumber(order.lines.length)}</td>
                        <td className="text-xs font-medium">
                          {order.total
                            ? `${formatNumber(order.total.amount_minor / 100, { minimumFractionDigits: 2 })} ${order.total.currency}`
                            : '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {/* Low Stock Alerts */}
        <Card
          title={t('inventory.title')}
          subtitle={t('inventory.subtitle')}
          action={
            <Link to="/inventory" className="text-xs text-primary font-medium hover:underline">
              {t('common.view')} →
            </Link>
          }
        >
          {availability.length === 0 ? (
            <div className="text-center py-8 text-muted text-sm">
              {t('inventory.noStockRecords')}
            </div>
          ) : (
            <div className="table-container border-0">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('inventory.sku')}</th>
                    <th>{t('inventory.warehouse')}</th>
                    <th>{t('inventory.available')}</th>
                    <th>{t('inventory.onHand')}</th>
                  </tr>
                </thead>
                <tbody>
                  {availability.slice(0, 5).map((item) => (
                    <tr key={item.inventory_item_id}>
                      <td className="font-mono text-xs">{item.sku_id.slice(0, 8)}...</td>
                      <td className="font-mono text-xs">{item.warehouse_id.slice(0, 8)}...</td>
                      <td>
                        <span
                          className={`font-semibold text-xs ${
                            item.available <= 0 ? 'text-danger' : item.available <= 5 ? 'text-warning' : 'text-success'
                          }`}
                        >
                          {formatNumber(item.available)}
                        </span>
                      </td>
                      <td className="text-xs text-muted">{formatNumber(item.on_hand)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {/* Quick Action Workflows */}
      <Card title={t('dashboard.quickActions')} className="mt-6">
        <div className="grid grid-cols-4 gap-4">
          <Link to="/orders/new" className="card p-4 hover:bg-surface-subtle text-decoration-none">
            <div className="flex items-center gap-3">
              <Package size={20} className="text-primary" />
              <div>
                <div className="font-semibold text-sm">{t('orders.createTitle')}</div>
                <div className="text-xs text-muted">{t('orders.createSubtitle')}</div>
              </div>
            </div>
          </Link>

          <Link to="/inventory" className="card p-4 hover:bg-surface-subtle text-decoration-none">
            <div className="flex items-center gap-3">
              <Layers size={20} className="text-primary" />
              <div>
                <div className="font-semibold text-sm">{t('inventory.adjustStockBtn')}</div>
                <div className="text-xs text-muted">{t('inventory.subtitle')}</div>
              </div>
            </div>
          </Link>

          <Link to="/catalog/products" className="card p-4 hover:bg-surface-subtle text-decoration-none">
            <div className="flex items-center gap-3">
              <Boxes size={20} className="text-primary" />
              <div>
                <div className="font-semibold text-sm">{t('nav.catalog')}</div>
                <div className="text-xs text-muted">{t('catalog.subtitle', { tenant: '' })}</div>
              </div>
            </div>
          </Link>

          <Link to="/search/orders" className="card p-4 hover:bg-surface-subtle text-decoration-none">
            <div className="flex items-center gap-3">
              <Truck size={20} className="text-primary" />
              <div>
                <div className="font-semibold text-sm">{t('nav.searchOrders')}</div>
                <div className="text-xs text-muted">{t('search.ordersSubtitle')}</div>
              </div>
            </div>
          </Link>
        </div>
      </Card>
    </div>
  );
};
