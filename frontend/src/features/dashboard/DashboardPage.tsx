import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  Boxes,
  CheckCircle2,
  Clock,
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
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';

export const DashboardPage: React.FC = () => {
  const { principal } = useAuth();
  const { activeTenantId, activeMembership } = useTenant();

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
  const cancelledOrders = orders.filter((o) => o.status === 'CANCELLED').length;

  const lowStockItems = availability.filter((a) => a.available <= 5 && a.on_hand > 0);
  const outOfStockItems = availability.filter((a) => a.available <= 0);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">Platform Overview</h1>
          <p className="text-sm text-muted mt-1">
            Authoritative operational status for tenant{' '}
            <strong className="text-main">{activeMembership?.tenant_name || activeTenantId}</strong>
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

      {error && <Alert variant="warning" className="mb-6">{error}</Alert>}

      {/* Metrics Row */}
      <div className="grid grid-cols-4 mb-6">
        <Card className="mb-0">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs font-semibold text-muted uppercase">Recent Orders</div>
              <div className="text-2xl font-bold mt-1">{orders.length}</div>
              <div className="text-xs text-muted mt-1">
                {pendingOrders} pending • {confirmedOrders} confirmed
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
              <div className="text-xs font-semibold text-muted uppercase">Catalog Products</div>
              <div className="text-2xl font-bold mt-1">{products.length}</div>
              <div className="text-xs text-muted mt-1">
                {products.filter((p) => p.status === 'ACTIVE').length} active for sale
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
              <div className="text-xs font-semibold text-muted uppercase">Active Warehouses</div>
              <div className="text-2xl font-bold mt-1">{warehouses.length}</div>
              <div className="text-xs text-muted mt-1">Designated fulfillment sites</div>
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
              <div className="text-xs font-semibold text-muted uppercase">Stock Alerts</div>
              <div className="text-2xl font-bold mt-1 text-danger">
                {lowStockItems.length + outOfStockItems.length}
              </div>
              <div className="text-xs text-muted mt-1">
                {outOfStockItems.length} depleted • {lowStockItems.length} low stock
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
          title="Recent Orders"
          subtitle="Authoritative PostgreSQL orders"
          action={
            <Link to="/orders" className="text-xs text-primary font-medium hover:underline">
              View All Orders →
            </Link>
          }
        >
          {orders.length === 0 ? (
            <div className="text-center py-8 text-muted text-sm">
              No orders placed yet in this tenant.
            </div>
          ) : (
            <div className="table-container border-0">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Order ID</th>
                    <th>Status</th>
                    <th>Lines</th>
                    <th>Total</th>
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
                        <td className="text-xs">{order.lines.length} lines</td>
                        <td className="text-xs font-medium">
                          {order.total
                            ? `${(order.total.amount_minor / 100).toFixed(2)} ${order.total.currency}`
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
          title="Inventory Positions"
          subtitle="Real-time on-hand and reserved allocation"
          action={
            <Link to="/inventory" className="text-xs text-primary font-medium hover:underline">
              Full Inventory →
            </Link>
          }
        >
          {availability.length === 0 ? (
            <div className="text-center py-8 text-muted text-sm">
              No inventory positions tracked yet. Adjust stock to begin.
            </div>
          ) : (
            <div className="table-container border-0">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>SKU ID</th>
                    <th>Warehouse</th>
                    <th>Available</th>
                    <th>On Hand</th>
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
                          {item.available}
                        </span>
                      </td>
                      <td className="text-xs text-muted">{item.on_hand}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {/* Quick Action Workflows */}
      <Card title="Permitted Operational Workflows" className="mt-6">
        <div className="grid grid-cols-4 gap-4">
          <Link to="/orders/new" className="card p-4 hover:bg-surface-subtle text-decoration-none">
            <div className="flex items-center gap-3">
              <Package size={20} className="text-primary" />
              <div>
                <div className="font-semibold text-sm">Submit Order</div>
                <div className="text-xs text-muted">Idempotent submission</div>
              </div>
            </div>
          </Link>

          <Link to="/inventory" className="card p-4 hover:bg-surface-subtle text-decoration-none">
            <div className="flex items-center gap-3">
              <Layers size={20} className="text-primary" />
              <div>
                <div className="font-semibold text-sm">Adjust Stock</div>
                <div className="text-xs text-muted">Goods receipt & counts</div>
              </div>
            </div>
          </Link>

          <Link to="/catalog/products" className="card p-4 hover:bg-surface-subtle text-decoration-none">
            <div className="flex items-center gap-3">
              <Boxes size={20} className="text-primary" />
              <div>
                <div className="font-semibold text-sm">Catalog & SKUs</div>
                <div className="text-xs text-muted">Manage product prices</div>
              </div>
            </div>
          </Link>

          <Link to="/search/orders" className="card p-4 hover:bg-surface-subtle text-decoration-none">
            <div className="flex items-center gap-3">
              <Truck size={20} className="text-primary" />
              <div>
                <div className="font-semibold text-sm">Fulfillment Search</div>
                <div className="text-xs text-muted">Track carrier dispatches</div>
              </div>
            </div>
          </Link>
        </div>
      </Card>
    </div>
  );
};
