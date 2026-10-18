import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Lock, Mail, Warehouse } from 'lucide-react';
import { useAuth } from '../../app/providers/AuthContext';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Input } from '../../components/ui/Input';

export const LoginPage: React.FC = () => {
  const { login } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [tenantId, setTenantId] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!email.trim() || !password) {
      setError('Email and password are required.');
      return;
    }

    setIsLoading(true);
    try {
      await login({
        email: email.trim(),
        password,
        tenant_id: tenantId.trim() || null,
      });
      navigate('/dashboard');
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Invalid credentials.';
      setError(message);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen px-4 bg-app">
      <div className="card w-full" style={{ maxWidth: '420px', padding: '2rem' }}>
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
          <h1 className="text-xl font-bold text-main">Arad Platform</h1>
          <p className="text-sm text-muted mt-1">Sign in to your warehouse account</p>
        </div>

        {error && (
          <Alert variant="danger" className="mb-4">
            {error}
          </Alert>
        )}

        <form onSubmit={handleSubmit} noValidate>
          <Input
            label="Email address"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="operator@example.com"
            required
            autoComplete="email"
          />

          <Input
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
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
            Sign in
          </Button>
        </form>

        <div className="mt-6 pt-4 border-t text-center text-xs text-muted">
          Don't have an account?{' '}
          <Link to="/register" className="font-semibold text-primary hover:underline">
            Register or accept invitation
          </Link>
        </div>
      </div>
    </div>
  );
};
