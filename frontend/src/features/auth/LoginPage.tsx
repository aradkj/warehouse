import React, { useState } from 'react';
import { Link, useNavigate, useLocation, Navigate } from 'react-router-dom';
import { Warehouse } from 'lucide-react';
import { useAuth } from '../../app/providers/AuthContext';
import { useTranslation } from '../../i18n';
import { LanguageSwitcher } from '../../components/common/LanguageSwitcher';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Input } from '../../components/ui/Input';

export const LoginPage: React.FC = () => {
  const { login, isAuthenticated } = useAuth();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [tenantId, setTenantId] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (isAuthenticated) {
    return <Navigate to={{ pathname: '/', search: location.search }} replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!email.trim() || !password) {
      setError(t('auth.emailPasswordRequired') || 'Email and password are required.');
      return;
    }

    setIsLoading(true);
    try {
      await login({
        email: email.trim(),
        password,
        tenant_id: tenantId.trim() || null,
      });
      navigate({ pathname: '/dashboard', search: location.search });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : t('errors.authFailed');
      setError(message);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen px-4 bg-app">
      <div className="card w-full" style={{ maxWidth: '420px', padding: '2rem' }}>
        <div className="flex justify-end mb-2">
          <LanguageSwitcher />
        </div>

        <div className="flex flex-col items-center mb-6 text-center">
          <div
            style={{
              width: '44px',
              height: '44px',
              borderRadius: '8px',
              backgroundColor: 'var(--color-primary-subtle)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--color-primary)',
              marginBottom: '0.75rem',
            }}
          >
            <Warehouse size={26} />
          </div>
          <h1 className="text-xl font-bold text-main">{t('header.title')}</h1>
          <p className="text-sm text-muted mt-1">{t('auth.signInSubtitle')}</p>
        </div>

        {error && (
          <Alert variant="danger" className="mb-4">
            {error}
          </Alert>
        )}

        <form onSubmit={handleSubmit} noValidate>
          <Input
            label={t('auth.emailLabel')}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder={t('auth.emailPlaceholder')}
            required
            autoComplete="email"
          />

          <Input
            label={t('auth.passwordLabel')}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t('auth.passwordPlaceholder')}
            required
            autoComplete="current-password"
          />

          <Input
            label="Tenant ID (Optional)"
            type="text"
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            placeholder="Optional tenant UUID"
            helperText="Leave empty to select from your memberships after login"
          />

          <Button
            type="submit"
            variant="primary"
            className="w-full mt-2"
            isLoading={isLoading}
          >
            {t('auth.signInButton')}
          </Button>
        </form>

        <div className="mt-6 pt-4 border-t text-center text-xs text-muted">
          {t('auth.noAccountPrompt')}{' '}
          <Link to="/register" className="font-semibold text-primary hover:underline">
            {t('auth.registerLink')}
          </Link>
        </div>
      </div>
    </div>
  );
};
