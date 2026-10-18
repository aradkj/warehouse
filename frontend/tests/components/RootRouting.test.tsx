import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation, Navigate } from 'react-router-dom';
import { AuthContext } from '../../src/app/providers/AuthContext';
import { TenantContext } from '../../src/app/providers/TenantContext';
import { AppLayout } from '../../src/components/layout/AppLayout';
import { LoginPage } from '../../src/features/auth/LoginPage';
import { PrincipalResponse, TenantMembershipResponse } from '../../src/api/types';

// Helper component that displays the current location pathname and search
const LocationEcho: React.FC<{ testId: string }> = ({ testId }) => {
  const location = useLocation();
  return (
    <div data-testid={testId}>
      <span data-testid="current-pathname">{location.pathname}</span>
      <span data-testid="current-search">{location.search}</span>
    </div>
  );
};

// Mirroring the router's index redirect
const DashboardRedirect: React.FC = () => {
  const location = useLocation();
  return <Navigate to={{ pathname: '/dashboard', search: location.search }} replace />;
};

describe('Root URL Routing and Query Preservation', () => {
  const mockLogin = vi.fn();
  const mockLogout = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  const renderRoute = ({
    initialEntry,
    isAuthenticated = false,
    activeTenantId = null as string | null,
    memberships = [] as TenantMembershipResponse[],
  }: {
    initialEntry: string;
    isAuthenticated?: boolean;
    activeTenantId?: string | null;
    memberships?: TenantMembershipResponse[];
  }) => {
    return render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <AuthContext.Provider
          value={{
            user: null,
            principal: isAuthenticated
              ? ({ id: 'usr-1', email: 'test@arad.com' } as unknown as PrincipalResponse)
              : null,
            isAuthenticated,
            isLoading: false,
            isConnectionError: false,
            connectionErrorMessage: null,
            retryProfileLoad: vi.fn(),
            login: mockLogin,
            logout: mockLogout,
            register: vi.fn(),
            refreshProfile: vi.fn(),
          }}
        >
          <TenantContext.Provider
            value={{
              activeTenantId,
              activeMembership: null,
              memberships,
              isLoadingTenants: false,
              hasLoadedTenants: true,
              membershipError: null,
              selectTenant: vi.fn(),
              refreshMemberships: vi.fn(),
            }}
          >
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/login/" element={<LoginPage />} />
              <Route path="/select-tenant" element={<LocationEcho testId="select-tenant-view" />} />
              <Route path="/" element={<AppLayout />}>
                <Route index element={<DashboardRedirect />} />
                <Route path="dashboard" element={<LocationEcho testId="dashboard-view" />} />
              </Route>
            </Routes>
          </TenantContext.Provider>
        </AuthContext.Provider>
      </MemoryRouter>
    );
  };

  it('redirects unauthenticated user visiting "/" to "/login" (Arad Platform Login)', () => {
    renderRoute({ initialEntry: '/' });

    // The login form must be visible (Arad Platform, NOT To-Do List)
    expect(screen.getByRole('button', { name: /Sign in/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Email address/i)).toBeInTheDocument();
  });

  it('redirects unauthenticated user visiting "/?utm_source=chatgpt.com" to "/login" while preserving query parameter', () => {
    renderRoute({ initialEntry: '/?utm_source=chatgpt.com' });

    expect(screen.getByRole('button', { name: /Sign in/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Email address/i)).toBeInTheDocument();
  });

  it('renders login page directly when visiting "/login/" with trailing slash', () => {
    renderRoute({ initialEntry: '/login/' });

    expect(screen.getByRole('button', { name: /Sign in/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Email address/i)).toBeInTheDocument();
  });

  it('redirects authenticated user with tenant to "/dashboard" preserving arbitrary query params', () => {
    const mockMemberships: TenantMembershipResponse[] = [
      {
        id: 'mem-1',
        user_id: 'usr-1',
        tenant_id: 'tenant-100',
        tenant_name: 'Test Tenant',
        tenant_slug: 'test-tenant',
        role: 'ADMIN',
        status: 'ACTIVE',
        created_at: '2026-01-01T00:00:00Z',
        roles: ['ADMIN'],
      },
    ];

    renderRoute({
      initialEntry: '/?utm_source=chatgpt.com&campaign=summer',
      isAuthenticated: true,
      activeTenantId: 'tenant-100',
      memberships: mockMemberships,
    });

    expect(screen.getByTestId('dashboard-view')).toBeInTheDocument();
    expect(screen.getByTestId('current-pathname').textContent).toBe('/dashboard');
    expect(screen.getByTestId('current-search').textContent).toBe('?utm_source=chatgpt.com&campaign=summer');
  });

  it('redirects authenticated user without active tenant to "/select-tenant" preserving query params', () => {
    renderRoute({
      initialEntry: '/?utm_source=chatgpt.com',
      isAuthenticated: true,
      activeTenantId: null,
      memberships: [],
    });

    expect(screen.getByTestId('select-tenant-view')).toBeInTheDocument();
    expect(screen.getByTestId('current-pathname').textContent).toBe('/select-tenant');
    expect(screen.getByTestId('current-search').textContent).toBe('?utm_source=chatgpt.com');
  });
});
