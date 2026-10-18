import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { SelectTenantPage } from '../../src/features/tenants/SelectTenantPage';
import { AppLayout } from '../../src/components/layout/AppLayout';
import { TenantContext, TenantProvider } from '../../src/app/providers/TenantContext';
import { ToastContext, ToastProvider } from '../../src/app/providers/ToastContext';
import { AuthContext } from '../../src/app/providers/AuthContext';
import { authApi } from '../../src/api/endpoints/auth';
import { platformApi } from '../../src/api/endpoints/platform';
import { apiClient } from '../../src/api/client';
import { ApiError } from '../../src/api/errors';
import { TenantMembershipResponse, UserResponse } from '../../src/api/types';

describe('SelectTenantPage', () => {
  const mockSelectTenant = vi.fn();
  const mockRefreshMemberships = vi.fn();

  const mockMemberships: TenantMembershipResponse[] = [
    {
      id: 'mem-1',
      user_id: 'user-1',
      tenant_id: 'ten-alpha-uuid',
      tenant_name: 'Alpha Logistics',
      tenant_slug: 'alpha-logistics',
      role: 'ADMIN',
      status: 'ACTIVE',
      created_at: '2026-01-01T00:00:00Z',
      roles: ['ADMIN'],
    },
    {
      id: 'mem-2',
      user_id: 'user-1',
      tenant_id: 'ten-beta-uuid',
      tenant_name: 'Beta Fulfillment',
      tenant_slug: 'beta-fulfillment',
      role: 'OPERATOR',
      status: 'ACTIVE',
      created_at: '2026-01-02T00:00:00Z',
      roles: ['OPERATOR'],
    },
  ];

  const renderWithProviders = () => {
    return render(
      <MemoryRouter>
        <AuthContext.Provider
          value={{
            user: { id: 'user-1', email: 'operator@arad.com' } as UserResponse,
            principal: null,
            isAuthenticated: true,
            isLoading: false,
            isConnectionError: false,
            connectionErrorMessage: null,
            retryProfileLoad: vi.fn(),
            login: vi.fn(),
            register: vi.fn(),
            logout: vi.fn(),
            refreshProfile: vi.fn(),
          }}
        >
          <TenantContext.Provider
            value={{
              activeTenantId: 'ten-alpha-uuid',
              activeMembership: mockMemberships[0],
              memberships: mockMemberships,
              isLoadingTenants: false,
              hasLoadedTenants: true,
              membershipError: null,
              selectTenant: mockSelectTenant,
              refreshMemberships: mockRefreshMemberships,
            }}
          >
            <ToastContext.Provider
              value={{
                toasts: [],
                showToast: vi.fn(),
                removeToast: vi.fn(),
              }}
            >
              <SelectTenantPage />
            </ToastContext.Provider>
          </TenantContext.Provider>
        </AuthContext.Provider>
      </MemoryRouter>
    );
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders list of available tenant memberships', () => {
    renderWithProviders();

    expect(screen.getByText('Alpha Logistics')).toBeInTheDocument();
    expect(screen.getByText('Beta Fulfillment')).toBeInTheDocument();
    expect(screen.getByText('Active Context')).toBeInTheDocument();
  });

  it('calls selectTenant when switching to another tenant', async () => {
    renderWithProviders();

    const switchBtn = screen.getByRole('button', { name: /Switch Context/i });
    fireEvent.click(switchBtn);

    await waitFor(() => {
      expect(mockSelectTenant).toHaveBeenCalledWith('ten-beta-uuid');
    });
  });
});

describe('Tenant-Scoped Navigation Guard (AppLayout)', () => {
  const mockMemberships: TenantMembershipResponse[] = [
    {
      id: 'mem-1',
      user_id: 'user-1',
      tenant_id: 'ten-alpha-uuid',
      tenant_name: 'Alpha Logistics',
      tenant_slug: 'alpha-logistics',
      role: 'ADMIN',
      status: 'ACTIVE',
      created_at: '2026-01-01T00:00:00Z',
      roles: ['ADMIN'],
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  const renderWithTenantState = (options: {
    isAuthenticated?: boolean;
    isLoading?: boolean;
    isLoadingTenants?: boolean;
    hasLoadedTenants?: boolean;
    activeTenantId?: string | null;
    memberships?: TenantMembershipResponse[];
  }) => {
    const {
      isAuthenticated = true,
      isLoading = false,
      isLoadingTenants = false,
      hasLoadedTenants = true,
      activeTenantId = null,
      memberships = [],
    } = options;

    return render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <AuthContext.Provider
          value={{
            user: isAuthenticated ? ({ id: 'u1', email: 'test@arad.com' } as UserResponse) : null,
            principal: null,
            isAuthenticated,
            isLoading,
            isConnectionError: false,
            connectionErrorMessage: null,
            retryProfileLoad: vi.fn(),
            login: vi.fn(),
            register: vi.fn(),
            logout: vi.fn(),
            refreshProfile: vi.fn(),
          }}
        >
          <TenantContext.Provider
            value={{
              activeTenantId,
              activeMembership: memberships.find((m) => m.tenant_id === activeTenantId) || null,
              memberships,
              isLoadingTenants,
              hasLoadedTenants,
              membershipError: null,
              selectTenant: vi.fn(),
              refreshMemberships: vi.fn(),
            }}
          >
            <Routes>
              <Route element={<AppLayout />}>
                <Route path="/dashboard" element={<div data-testid="dashboard-content">Dashboard Content</div>} />
              </Route>
              <Route path="/select-tenant" element={<div data-testid="select-tenant-page">Select Tenant Page</div>} />
              <Route path="/login" element={<div data-testid="login-page">Login Page</div>} />
            </Routes>
          </TenantContext.Provider>
        </AuthContext.Provider>
      </MemoryRouter>
    );
  };

  it('redirects an authenticated user with zero memberships and no active tenant to /select-tenant', () => {
    renderWithTenantState({
      isAuthenticated: true,
      memberships: [],
      activeTenantId: null,
      hasLoadedTenants: true,
      isLoadingTenants: false,
    });

    expect(screen.getByTestId('select-tenant-page')).toBeInTheDocument();
    expect(screen.queryByTestId('dashboard-content')).not.toBeInTheDocument();
  });

  it('redirects when active tenant ID does not match any current membership', () => {
    renderWithTenantState({
      isAuthenticated: true,
      memberships: [mockMemberships[0]],
      activeTenantId: 'stale-or-unauthorized-tenant-id',
      hasLoadedTenants: true,
      isLoadingTenants: false,
    });

    expect(screen.getByTestId('select-tenant-page')).toBeInTheDocument();
    expect(screen.queryByTestId('dashboard-content')).not.toBeInTheDocument();
  });

  it('retains normal navigation when the active tenant ID is valid and matches a current membership', () => {
    renderWithTenantState({
      isAuthenticated: true,
      memberships: [mockMemberships[0]],
      activeTenantId: mockMemberships[0].tenant_id,
      hasLoadedTenants: true,
      isLoadingTenants: false,
    });

    expect(screen.getByTestId('dashboard-content')).toBeInTheDocument();
    expect(screen.queryByTestId('select-tenant-page')).not.toBeInTheDocument();
  });

  it('displays loading state and does not trigger a premature redirect while memberships are still loading', () => {
    renderWithTenantState({
      isAuthenticated: true,
      memberships: [],
      activeTenantId: null,
      isLoadingTenants: true,
      hasLoadedTenants: false,
    });

    expect(screen.getByText('Loading Arad Platform...')).toBeInTheDocument();
    expect(screen.queryByTestId('select-tenant-page')).not.toBeInTheDocument();
    expect(screen.queryByTestId('dashboard-content')).not.toBeInTheDocument();
  });
});

describe('SelectTenantPage Recovery & Workflows', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiClient.clearSession();
  });

  const renderSelectTenantFlow = () => {
    return render(
      <MemoryRouter initialEntries={['/select-tenant']}>
        <ToastProvider>
          <AuthContext.Provider
            value={{
              user: { id: 'user-1', email: 'operator@arad.com' } as UserResponse,
              principal: null,
              isAuthenticated: true,
              isLoading: false,
              isConnectionError: false,
              connectionErrorMessage: null,
              retryProfileLoad: vi.fn(),
              login: vi.fn(),
              register: vi.fn(),
              logout: vi.fn(),
              refreshProfile: vi.fn(),
            }}
          >
            <TenantProvider>
              <SelectTenantPage />
            </TenantProvider>
          </AuthContext.Provider>
        </ToastProvider>
      </MemoryRouter>
    );
  };

  it('renders confirmed empty membership state with prompt to provision first tenant', async () => {
    vi.spyOn(authApi, 'listMemberships').mockResolvedValue([]);

    renderSelectTenantFlow();

    expect(await screen.findByText('No Active Memberships')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Provision First Tenant/i })).toBeInTheDocument();
    expect(screen.queryByText('Failed to Load Memberships')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Retry Loading Memberships/i })).not.toBeInTheDocument();
  });

  it('handles membership request failure and restores memberships upon successful retry', async () => {
    let attempt = 0;
    vi.spyOn(authApi, 'listMemberships').mockImplementation(async () => {
      attempt++;
      if (attempt === 1) {
        throw new ApiError(0, {
          code: 'NETWORK_ERROR',
          message: 'Unable to connect to tenant registry',
          details: {},
          type: 'http',
        });
      }
      return [
        {
          id: 'mem-1',
          user_id: 'user-1',
          tenant_id: 'ten-alpha-uuid',
          tenant_name: 'Alpha Logistics',
          tenant_slug: 'alpha-logistics',
          role: 'ADMIN',
          status: 'ACTIVE',
          created_at: '2026-01-01T00:00:00Z',
          roles: ['ADMIN'],
        },
      ];
    });

    renderSelectTenantFlow();

    expect(await screen.findByText('Failed to Load Memberships')).toBeInTheDocument();
    expect(screen.getByText('Unable to connect to tenant registry')).toBeInTheDocument();
    // Must NOT show "No Active Memberships" as if it were a confirmed empty result
    expect(screen.queryByText('No Active Memberships')).not.toBeInTheDocument();

    const retryBtn = screen.getByRole('button', { name: /Retry Loading Memberships/i });
    expect(retryBtn).toBeInTheDocument();

    fireEvent.click(retryBtn);

    expect(await screen.findByText('Alpha Logistics')).toBeInTheDocument();
    expect(screen.queryByText('Failed to Load Memberships')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Retry Loading Memberships/i })).not.toBeInTheDocument();
  });

  it('keeps error and retry action available on repeated membership failure without loop', async () => {
    const listSpy = vi.spyOn(authApi, 'listMemberships').mockRejectedValue(
      new ApiError(0, {
        code: 'NETWORK_ERROR',
        message: 'Persistent connection failure',
        details: {},
        type: 'http',
      })
    );

    renderSelectTenantFlow();

    expect(await screen.findByText('Failed to Load Memberships')).toBeInTheDocument();

    const retryBtn = screen.getByRole('button', { name: /Retry Loading Memberships/i });
    expect(retryBtn).toBeInTheDocument();

    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledTimes(2);
    });

    expect(screen.getByText('Failed to Load Memberships')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry Loading Memberships/i })).toBeInTheDocument();
    expect(screen.queryByText('No Active Memberships')).not.toBeInTheDocument();
  });

  it('preserves tenant creation and provisioning workflow', async () => {
    vi.spyOn(authApi, 'listMemberships').mockResolvedValue([]);
    const registerSpy = vi.spyOn(platformApi, 'registerTenant').mockResolvedValue({
      id: 'ten-new-uuid',
      name: 'Omni Fulfillment',
      slug: 'omni-fulfillment',
      status: 'ACTIVE',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      version: 1,
      invitation_token: 'inv-tok-123',
    });

    renderSelectTenantFlow();

    expect(await screen.findByText('No Active Memberships')).toBeInTheDocument();

    const newTenantBtn = screen.getByRole('button', { name: /Provision First Tenant/i });
    fireEvent.click(newTenantBtn);

    expect(await screen.findByRole('heading', { name: /Provision New Tenant/i })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Organization \/ Tenant Name/i), {
      target: { value: 'Omni Fulfillment' },
    });
    fireEvent.change(screen.getByLabelText(/Tenant Slug/i), {
      target: { value: 'omni-fulfillment' },
    });

    const submitBtn = screen.getByRole('button', { name: /Provision Tenant/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(registerSpy).toHaveBeenCalledWith({
        name: 'Omni Fulfillment',
        slug: 'omni-fulfillment',
      });
    });
  });
});
