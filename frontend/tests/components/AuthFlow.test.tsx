import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { LoginPage } from '../../src/features/auth/LoginPage';
import { AuthContext, AuthProvider } from '../../src/app/providers/AuthContext';
import { TenantProvider } from '../../src/app/providers/TenantContext';
import { ToastProvider } from '../../src/app/providers/ToastContext';
import { AppLayout } from '../../src/components/layout/AppLayout';
import { authApi } from '../../src/api/endpoints/auth';
import { apiClient } from '../../src/api/client';
import { ApiError } from '../../src/api/errors';
import { UserResponse, PrincipalResponse, TenantMembershipResponse } from '../../src/api/types';

describe('LoginPage', () => {
  const mockLogin = vi.fn();
  const mockLogout = vi.fn();

  const renderWithAuth = (loginFn = mockLogin) => {
    return render(
      <MemoryRouter>
        <AuthContext.Provider
          value={{
            user: null,
            principal: null,
            isAuthenticated: false,
            isLoading: false,
            isConnectionError: false,
            connectionErrorMessage: null,
            retryProfileLoad: vi.fn(),
            login: loginFn,
            logout: mockLogout,
            register: vi.fn(),
            refreshProfile: vi.fn(),
          }}
        >
          <LoginPage />
        </AuthContext.Provider>
      </MemoryRouter>
    );
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders sign-in form with required inputs', () => {
    renderWithAuth();
    expect(screen.getByLabelText(/Email address/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign in/i })).toBeInTheDocument();
  });

  it('validates empty inputs before submitting', async () => {
    renderWithAuth();
    const submitBtn = screen.getByRole('button', { name: /Sign in/i });
    fireEvent.click(submitBtn);

    expect(await screen.findByText(/Email and password are required/i)).toBeInTheDocument();
    expect(mockLogin).not.toHaveBeenCalled();
  });

  it('submits form with entered credentials and optional tenant ID', async () => {
    mockLogin.mockResolvedValueOnce({
      tokens: { access_token: 'tok-1', refresh_token: 'tok-2', token_type: 'Bearer', expires_in: 3600 },
      user: { id: 'user-1', email: 'test@arad.com' } as UserResponse,
      principal: { id: 'user-1', tenant_id: 'ten-1', email: 'test@arad.com' } as unknown as PrincipalResponse,
    });

    renderWithAuth();

    fireEvent.change(screen.getByLabelText(/Email address/i), {
      target: { value: 'admin@warehouse.internal' },
    });
    fireEvent.change(screen.getByLabelText(/^Password/i), {
      target: { value: 'SecurePass123!' },
    });
    fireEvent.change(screen.getByLabelText(/Tenant ID/i), {
      target: { value: 'tenant-1234' },
    });

    fireEvent.click(screen.getByRole('button', { name: /Sign in/i }));

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith({
        email: 'admin@warehouse.internal',
        password: 'SecurePass123!',
        tenant_id: 'tenant-1234',
      });
    });
  });

  it('displays error alert on authentication rejection', async () => {
    mockLogin.mockRejectedValueOnce(new Error('Invalid username or password.'));

    renderWithAuth();

    fireEvent.change(screen.getByLabelText(/Email address/i), {
      target: { value: 'wrong@example.com' },
    });
    fireEvent.change(screen.getByLabelText(/^Password/i), {
      target: { value: 'badpass' },
    });

    fireEvent.click(screen.getByRole('button', { name: /Sign in/i }));

    expect(await screen.findByText(/Invalid username or password/i)).toBeInTheDocument();
  });
});

describe('Initial Profile Loading & Network Recovery (Fix 3)', () => {
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
    apiClient.clearSession();
  });

  const renderApp = () => {
    return render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <ToastProvider>
          <AuthProvider>
            <TenantProvider>
              <Routes>
                <Route element={<AppLayout />}>
                  <Route path="/dashboard" element={<div data-testid="dashboard-content">Dashboard Content</div>} />
                </Route>
                <Route path="/select-tenant" element={<div data-testid="select-tenant-page">Select Tenant Page</div>} />
                <Route path="/login" element={<div data-testid="login-page">Login Page</div>} />
              </Routes>
            </TenantProvider>
          </AuthProvider>
        </ToastProvider>
      </MemoryRouter>
    );
  };

  it('displays recoverable connection error and does not redirect to login when token exists but profile loading fails due to network error', async () => {
    apiClient.setSession({
      accessToken: 'valid-access-token-123',
      refreshToken: 'valid-refresh-token-456',
    });

    vi.spyOn(authApi, 'getMe').mockRejectedValue(
      new ApiError(0, { code: 'NETWORK_ERROR', message: 'Network connection refused', details: {}, type: 'http' })
    );

    renderApp();

    expect(await screen.findByText('Service Temporarily Unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry Connection/i })).toBeInTheDocument();
    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
    // Tokens must be preserved
    expect(apiClient.getAccessToken()).toBe('valid-access-token-123');
  });

  it('restores normal authenticated navigation when retry succeeds', async () => {
    apiClient.setSession({
      accessToken: 'valid-access-token-123',
      refreshToken: 'valid-refresh-token-456',
      tenantId: 'ten-alpha-uuid',
    });

    let attempt = 0;
    vi.spyOn(authApi, 'getMe').mockImplementation(async () => {
      attempt++;
      if (attempt === 1) {
        throw new ApiError(0, { code: 'NETWORK_ERROR', message: 'Failed to fetch', details: {}, type: 'http' });
      }
      return {
        id: 'user-1',
        email: 'admin@arad.com',
        role: 'ADMIN',
        account_status: 'ACTIVE',
        tenant_id: 'ten-alpha-uuid',
        is_active: true,
      } as unknown as PrincipalResponse;
    });

    vi.spyOn(authApi, 'listMemberships').mockResolvedValue(mockMemberships);

    renderApp();

    const retryBtn = await screen.findByRole('button', { name: /Retry Connection/i });
    expect(retryBtn).toBeInTheDocument();

    fireEvent.click(retryBtn);

    expect(await screen.findByTestId('dashboard-content')).toBeInTheDocument();
    expect(screen.queryByText('Service Temporarily Unavailable')).not.toBeInTheDocument();
  });

  it('leaves a usable error/retry state if retry fails again', async () => {
    apiClient.setSession({
      accessToken: 'valid-access-token-123',
      refreshToken: 'valid-refresh-token-456',
    });

    const getMeSpy = vi.spyOn(authApi, 'getMe').mockRejectedValue(
      new ApiError(0, { code: 'NETWORK_ERROR', message: 'Failed to fetch', details: {}, type: 'http' })
    );

    renderApp();

    const retryBtn = await screen.findByRole('button', { name: /Retry Connection/i });
    expect(retryBtn).toBeInTheDocument();

    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(getMeSpy).toHaveBeenCalledTimes(2);
    });

    expect(screen.getByText('Service Temporarily Unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry Connection/i })).toBeInTheDocument();
    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
  });

  it('clears invalid session and redirects to login on genuine HTTP 401', async () => {
    apiClient.setSession({
      accessToken: 'expired-access-token',
      refreshToken: 'invalid-refresh-token',
    });

    vi.spyOn(authApi, 'getMe').mockRejectedValue(
      new ApiError(401, { code: 'UNAUTHORIZED', message: 'Token expired or invalid', details: {}, type: 'http' })
    );

    renderApp();

    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
    expect(screen.queryByText('Service Temporarily Unavailable')).not.toBeInTheDocument();
    expect(apiClient.getAccessToken()).toBeNull();
  });

  it('reaches normal login flow immediately on no-token startup', async () => {
    const getMeSpy = vi.spyOn(authApi, 'getMe');

    renderApp();

    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
    expect(getMeSpy).not.toHaveBeenCalled();
    expect(screen.queryByText('Service Temporarily Unavailable')).not.toBeInTheDocument();
  });
});
