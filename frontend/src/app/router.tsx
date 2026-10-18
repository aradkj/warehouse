import React from 'react';
import { createBrowserRouter, Navigate, useLocation } from 'react-router-dom';
import { AppLayout } from '../components/layout/AppLayout';

// Public / standalone views
import { LoginPage } from '../features/auth/LoginPage';
import { RegisterPage } from '../features/auth/RegisterPage';
import { SelectTenantPage } from '../features/tenants/SelectTenantPage';

// Authenticated layout views
import { DashboardPage } from '../features/dashboard/DashboardPage';
import { OrderListPage } from '../features/orders/OrderListPage';
import { CreateOrderPage } from '../features/orders/CreateOrderPage';
import { OrderDetailPage } from '../features/orders/OrderDetailPage';
import { ProductListPage } from '../features/catalog/ProductListPage';
import { ProductDetailPage } from '../features/catalog/ProductDetailPage';
import { InventoryPage } from '../features/inventory/InventoryPage';
import { WarehouseListPage } from '../features/inventory/WarehouseListPage';
import { ReservationsPage } from '../features/inventory/ReservationsPage';
import { FulfillmentLookupPage } from '../features/fulfillment/FulfillmentLookupPage';
import { FulfillmentDetailPage } from '../features/fulfillment/FulfillmentDetailPage';
import { OrderSearchPage } from '../features/search/OrderSearchPage';
import { ProductSearchPage } from '../features/search/ProductSearchPage';
import { AuditSearchPage } from '../features/search/AuditSearchPage';
import { SearchMaintenancePage } from '../features/search/SearchMaintenancePage';
import { AuditEventsPage } from '../features/audit/AuditEventsPage';
import { RolesPage } from '../features/access/RolesPage';
import { PoliciesPage } from '../features/access/PoliciesPage';
import { PolicySimulatorPage } from '../features/access/PolicySimulatorPage';
import { TenantMembershipsPage } from '../features/identity/TenantMembershipsPage';
import { ServiceAccountsPage } from '../features/identity/ServiceAccountsPage';
import { SecuritySettingsPage } from '../features/identity/SecuritySettingsPage';

// Query-preserving redirect components
const DashboardRedirect: React.FC = () => {
  const location = useLocation();
  return <Navigate to={{ pathname: '/dashboard', search: location.search }} replace />;
};

const LoginRedirect: React.FC = () => {
  const location = useLocation();
  return <Navigate to={{ pathname: '/login', search: location.search }} replace />;
};

export const router = createBrowserRouter([
  {
    path: '/login',
    element: <LoginPage />,
  },
  {
    path: '/login/',
    element: <LoginPage />,
  },
  {
    path: '/register',
    element: <RegisterPage />,
  },
  {
    path: '/register/',
    element: <RegisterPage />,
  },
  {
    path: '/select-tenant',
    element: <SelectTenantPage />,
  },
  {
    path: '/select-tenant/',
    element: <SelectTenantPage />,
  },
  {
    path: '/',
    element: <AppLayout />,
    children: [
      {
        index: true,
        element: <DashboardRedirect />,
      },
      {
        path: 'dashboard',
        element: <DashboardPage />,
      },
      // Catalog
      {
        path: 'catalog/products',
        element: <ProductListPage />,
      },
      {
        path: 'catalog/products/:id',
        element: <ProductDetailPage />,
      },
      // Inventory
      {
        path: 'inventory',
        element: <InventoryPage />,
      },
      {
        path: 'inventory/warehouses',
        element: <WarehouseListPage />,
      },
      {
        path: 'inventory/reservations',
        element: <ReservationsPage />,
      },
      // Orders
      {
        path: 'orders',
        element: <OrderListPage />,
      },
      {
        path: 'orders/new',
        element: <CreateOrderPage />,
      },
      {
        path: 'orders/:id',
        element: <OrderDetailPage />,
      },
      // Fulfillment
      {
        path: 'fulfillments',
        element: <FulfillmentLookupPage />,
      },
      {
        path: 'fulfillments/:id',
        element: <FulfillmentDetailPage />,
      },
      // Search
      {
        path: 'search/orders',
        element: <OrderSearchPage />,
      },
      {
        path: 'search/products',
        element: <ProductSearchPage />,
      },
      {
        path: 'search/audit',
        element: <AuditSearchPage />,
      },
      {
        path: 'settings/search-maintenance',
        element: <SearchMaintenancePage />,
      },
      // Audit
      {
        path: 'audit',
        element: <AuditEventsPage />,
      },
      // Access Control
      {
        path: 'access/roles',
        element: <RolesPage />,
      },
      {
        path: 'access/policies',
        element: <PoliciesPage />,
      },
      {
        path: 'access/evaluate',
        element: <PolicySimulatorPage />,
      },
      // Tenant & Identity Management
      {
        path: 'settings/members',
        element: <TenantMembershipsPage />,
      },
      {
        path: 'settings/service-accounts',
        element: <ServiceAccountsPage />,
      },
      {
        path: 'settings/security',
        element: <SecuritySettingsPage />,
      },
      // Catch-all route within app layout
      {
        path: '*',
        element: <DashboardRedirect />,
      },
    ],
  },
  {
    path: '*',
    element: <LoginRedirect />,
  },
]);
