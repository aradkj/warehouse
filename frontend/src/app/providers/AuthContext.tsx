import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { apiClient } from '../../api/client';
import { authApi, LoginParams, RegisterParams } from '../../api/endpoints/auth';
import { ApiError, formatErrorMessage, normalizeError } from '../../api/errors';
import { PrincipalResponse, UserResponse } from '../../api/types';
import { useToast } from './ToastContext';

export interface AuthContextValue {
  user: UserResponse | null;
  principal: PrincipalResponse | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  isConnectionError: boolean;
  connectionErrorMessage: string | null;
  retryProfileLoad: () => Promise<void>;
  login: (params: LoginParams) => Promise<void>;
  register: (params: RegisterParams) => Promise<void>;
  logout: () => Promise<void>;
  refreshProfile: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [principal, setPrincipal] = useState<PrincipalResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isConnectionError, setIsConnectionError] = useState(false);
  const [connectionErrorMessage, setConnectionErrorMessage] = useState<string | null>(null);
  const { showToast } = useToast();

  const refreshProfile = useCallback(async () => {
    try {
      const p = await authApi.getMe();
      setPrincipal(p);
      if (p) {
        setUser({
          id: p.id,
          email: p.email || '',
          status: p.account_status,
          created_at: '',
          updated_at: '',
        });
      }
      setIsConnectionError(false);
      setConnectionErrorMessage(null);
    } catch (rawErr) {
      const err = normalizeError(rawErr);
      if (err.isAuthError()) {
        apiClient.clearSession();
        setUser(null);
        setPrincipal(null);
        setIsConnectionError(false);
        setConnectionErrorMessage(null);
      } else {
        setIsConnectionError(true);
        setConnectionErrorMessage(formatErrorMessage(err));
      }
      throw rawErr;
    }
  }, []);

  const retryProfileLoad = useCallback(async () => {
    setIsLoading(true);
    try {
      await refreshProfile();
    } catch {
      // isConnectionError is set inside refreshProfile catch block
    } finally {
      setIsLoading(false);
    }
  }, [refreshProfile]);

  useEffect(() => {
    const token = apiClient.getAccessToken();
    if (token) {
      refreshProfile().catch(() => {}).finally(() => setIsLoading(false));
    } else {
      setIsLoading(false);
    }
  }, [refreshProfile]);

  const login = async (params: LoginParams) => {
    try {
      const response = await authApi.login(params);
      apiClient.setSession({
        accessToken: response.access_token,
        refreshToken: response.refresh_token,
        tenantId: response.active_tenant_id,
      });
      setUser(response.user);
      setIsConnectionError(false);
      setConnectionErrorMessage(null);
      await refreshProfile();
      showToast('Signed in successfully.', 'success');
    } catch (err) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to sign in.';
      showToast(msg, 'error');
      throw err;
    }
  };

  const register = async (params: RegisterParams) => {
    try {
      await authApi.register(params);
      showToast('Account created successfully. You can now sign in.', 'success');
    } catch (err) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to register account.';
      showToast(msg, 'error');
      throw err;
    }
  };

  const logout = async () => {
    const refreshToken = sessionStorage.getItem('arad_refresh_token');
    try {
      if (refreshToken) {
        await authApi.logout(refreshToken);
      }
    } catch {
      // Ignore network errors on logout
    } finally {
      apiClient.clearSession();
      setUser(null);
      setPrincipal(null);
      setIsConnectionError(false);
      setConnectionErrorMessage(null);
      showToast('Signed out.', 'info');
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        principal,
        isAuthenticated: !!user && !!apiClient.getAccessToken(),
        isLoading,
        isConnectionError,
        connectionErrorMessage,
        retryProfileLoad,
        login,
        register,
        logout,
        refreshProfile,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
