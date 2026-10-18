import React from 'react';
import { RouterProvider } from 'react-router-dom';
import { I18nProvider } from '../i18n';
import { ToastProvider } from './providers/ToastContext';
import { AuthProvider } from './providers/AuthContext';
import { TenantProvider } from './providers/TenantContext';
import { ToastContainer } from '../components/feedback/ToastContainer';
import { router } from './router';

export const App: React.FC = () => {
  return (
    <I18nProvider>
      <ToastProvider>
        <AuthProvider>
          <TenantProvider>
            <RouterProvider router={router} />
            <ToastContainer />
          </TenantProvider>
        </AuthProvider>
      </ToastProvider>
    </I18nProvider>
  );
};

