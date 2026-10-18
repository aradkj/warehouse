import React, { useState } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { RefreshCw, WifiOff } from 'lucide-react';
import { apiClient } from '../../api/client';
import { useAuth } from '../../app/providers/AuthContext';
import { useTenant } from '../../app/providers/TenantContext';
import { useTranslation } from '../../i18n';
import { Button } from '../ui/Button';
import { Breadcrumbs } from './Breadcrumbs';
import { Header } from './Header';
import { Sidebar } from './Sidebar';

export const AppLayout: React.FC = () => {
  const {
    isAuthenticated,
    isLoading,
    isConnectionError,
    connectionErrorMessage,
    retryProfileLoad,
    logout,
  } = useAuth();
  const { activeTenantId, memberships, isLoadingTenants, hasLoadedTenants } = useTenant();
  const { t } = useTranslation();
  const location = useLocation();
  const [isRetrying, setIsRetrying] = useState(false);
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);

  // If a stored token exists but the backend is temporarily unreachable,
  // display a recoverable connection error instead of prematurely redirecting to login.
  if (isConnectionError && apiClient.getAccessToken()) {
    const handleRetry = async () => {
      setIsRetrying(true);
      try {
        await retryProfileLoad();
      } finally {
        setIsRetrying(false);
      }
    };

    return (
      <div className="flex items-center justify-center min-h-screen px-4 bg-app">
        <div className="card w-full text-center" style={{ maxWidth: '440px', padding: '2rem' }}>
          <div
            style={{
              width: '48px',
              height: '48px',
              borderRadius: '10px',
              backgroundColor: 'var(--color-danger-bg)',
              color: 'var(--color-danger)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              margin: '0 auto 1rem',
            }}
          >
            <WifiOff size={24} />
          </div>
          <h2 className="text-lg font-bold text-main mb-1">{t('auth.serviceUnavailable')}</h2>
          <p className="text-sm text-muted mb-4">
            {connectionErrorMessage || t('auth.serviceUnavailableDesc')}
          </p>
          <div className="flex flex-col gap-2">
            <Button variant="primary" onClick={handleRetry} isLoading={isRetrying}>
              <RefreshCw size={14} className="mr-1.5" />
              <span>{t('auth.retryConnection')}</span>
            </Button>
            <Button variant="secondary" size="sm" onClick={() => logout()}>
              <span>{t('header.signOut')}</span>
            </Button>
          </div>
        </div>
      </div>
    );
  }

  if (isLoading || isLoadingTenants || (isAuthenticated && !hasLoadedTenants)) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="text-sm font-medium text-muted">{t('auth.loadingApp')}</div>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to={{ pathname: '/login', search: location.search }} replace />;
  }

  // Ensure an authenticated user cannot enter tenant-scoped application pages
  // until a valid active tenant corresponding to a current membership has been selected.
  const hasValidActiveTenant =
    Boolean(activeTenantId) && memberships.some((m) => m.tenant_id === activeTenantId);

  if (!hasValidActiveTenant) {
    return <Navigate to={{ pathname: '/select-tenant', search: location.search }} replace />;
  }

  return (
    <div className="app-container">
      <Sidebar isOpen={isMobileNavOpen} onClose={() => setIsMobileNavOpen(false)} />
      <div className="main-content-wrapper">
        <Header onToggleMobileNav={() => setIsMobileNavOpen(!isMobileNavOpen)} />
        <main className="page-content">
          <Breadcrumbs />
          <Outlet />
        </main>
      </div>
    </div>
  );
};
