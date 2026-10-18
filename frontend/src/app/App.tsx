import React from 'react';
import { RouterProvider } from 'react-router-dom';
import { ToastProvider } from './providers/ToastContext';
import { AuthProvider } from './providers/AuthContext';
import { TenantProvider } from './providers/TenantContext';
import { ToastContainer } from '../components/feedback/ToastContainer';
import { router } from './router';

export const App: React.FC = () => {
  return (
    <ToastProvider>
      <AuthProvider>
        <TenantProvider>
          <RouterProvider router={router} />
          <ToastContainer />
        </TenantProvider>
      </AuthProvider>
    </ToastProvider>
  );
};
