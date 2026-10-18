import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { apiClient } from '../../api/client';
import { authApi } from '../../api/endpoints/auth';
import { ApiError, formatErrorMessage, normalizeError } from '../../api/errors';
import { TenantMembershipResponse } from '../../api/types';
import { useAuth } from './AuthContext';
import { useToast } from './ToastContext';

export interface TenantContextValue {
  activeTenantId: string | null;
  activeMembership: TenantMembershipResponse | null;
  memberships: TenantMembershipResponse[];
  isLoadingTenants: boolean;
  hasLoadedTenants: boolean;
  membershipError: string | null;
  selectTenant: (tenantId: string) => Promise<void>;
  refreshMemberships: () => Promise<void>;
}

export const TenantContext = createContext<TenantContextValue | undefined>(undefined);

export const TenantProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, refreshProfile } = useAuth();
  const { showToast } = useToast();
  const [activeTenantId, setActiveTenantIdState] = useState<string | null>(apiClient.getActiveTenantId());
  const [memberships, setMemberships] = useState<TenantMembershipResponse[]>([]);
  const [isLoadingTenants, setIsLoadingTenants] = useState(false);
  const [hasLoadedTenants, setHasLoadedTenants] = useState(false);
  const [membershipError, setMembershipError] = useState<string | null>(null);

  const refreshMemberships = useCallback(async () => {
    if (!isAuthenticated) {
      setMemberships([]);
      setHasLoadedTenants(false);
      setMembershipError(null);
      return;
    }

    setIsLoadingTenants(true);
    try {
      const data = await authApi.listMemberships();
      setMemberships(data);
      setMembershipError(null);

      const currentActiveId = apiClient.getActiveTenantId();
      if (currentActiveId && data.some((m) => m.tenant_id === currentActiveId)) {
        setActiveTenantIdState(currentActiveId);
      } else if (data.length > 0) {
        // If current is invalid or lost membership, fall back to first active
        setActiveTenantIdState(data[0].tenant_id);
        apiClient.setActiveTenant(data[0].tenant_id);
      } else {
        // No memberships or stale tenant ID: clear active tenant
        setActiveTenantIdState(null);
        apiClient.setActiveTenant(null);
      }
    } catch (rawErr) {
      const err = normalizeError(rawErr);
      const msg = err.message || formatErrorMessage(err) || 'Failed to load tenant memberships.';
      setMembershipError(msg);
      if (!err.isAuthError()) {
        showToast(msg, 'error');
      }
    } finally {
      setIsLoadingTenants(false);
      setHasLoadedTenants(true);
    }
  }, [isAuthenticated, showToast]);

  useEffect(() => {
    if (isAuthenticated) {
      refreshMemberships();
    } else {
      setMemberships([]);
      setActiveTenantIdState(null);
      setHasLoadedTenants(false);
      setMembershipError(null);
    }
  }, [isAuthenticated, refreshMemberships]);

  const selectTenant = async (tenantId: string) => {
    try {
      const tokens = await authApi.selectTenant(tenantId);
      apiClient.setSession({
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
        tenantId: tokens.active_tenant_id || tenantId,
      });
      setActiveTenantIdState(tokens.active_tenant_id || tenantId);
      setMembershipError(null);
      await refreshProfile();
      await refreshMemberships();
      showToast('Switched tenant context.', 'success');
    } catch (err) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to switch tenant.';
      showToast(msg, 'error');
      throw err;
    }
  };

  const activeMembership =
    memberships.find((m) => m.tenant_id === activeTenantId) || null;

  return (
    <TenantContext.Provider
      value={{
        activeTenantId,
        activeMembership,
        memberships,
        isLoadingTenants,
        hasLoadedTenants,
        membershipError,
        selectTenant,
        refreshMemberships,
      }}
    >
      {children}
    </TenantContext.Provider>
  );
};

export function useTenant(): TenantContextValue {
  const context = useContext(TenantContext);
  if (!context) {
    throw new Error('useTenant must be used within a TenantProvider');
  }
  return context;
}
