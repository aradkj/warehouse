import React, { useState } from 'react';
import { Lock, Shield, User } from 'lucide-react';
import { authApi } from '../../api/endpoints/auth';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { useAuth } from '../../app/providers/AuthContext';
import { useToast } from '../../app/providers/ToastContext';
import { useTranslation } from '../../i18n';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';

export const SecuritySettingsPage: React.FC = () => {
  const { user, principal } = useAuth();
  const { showToast } = useToast();
  const { t } = useTranslation();

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!currentPassword) {
      setError('Current password is required.');
      return;
    }

    if (newPassword.length < 8) {
      setError('New password must be at least 8 characters long.');
      return;
    }

    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    setIsSubmitting(true);
    try {
      await authApi.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });

      showToast('Password changed successfully. All other active sessions have been revoked.', 'success');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to change password.';
      setError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div style={{ maxWidth: '800px' }}>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">{t('identity.securityTitle')}</h1>
          <p className="text-sm text-muted mt-1">
            {t('identity.securitySubtitle')}
          </p>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-6">{error}</Alert>}

      {/* User Session Metadata */}
      <Card title="Current Identity Session" className="mb-6">
        <div className="space-y-3 text-xs">
          <div className="flex items-center gap-2">
            <User size={16} className="text-muted" />
            <span className="text-muted">Authenticated Subject:</span>
            <strong className="text-main font-mono">{user?.id}</strong>
          </div>
          <div className="flex items-center gap-2">
            <Shield size={16} className="text-muted" />
            <span className="text-muted">Effective Principal Role:</span>
            <strong className="text-main">{principal?.role || 'MEMBER'}</strong>
          </div>
        </div>
      </Card>

      {/* Password Change Card */}
      <Card title="Change Account Password">
        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            label="Current Password"
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            placeholder="••••••••"
            required
            autoComplete="current-password"
          />

          <Input
            label="New Password"
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder="••••••••"
            helperText="Minimum 8 characters."
            required
            autoComplete="new-password"
          />

          <Input
            label="Confirm New Password"
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            placeholder="••••••••"
            required
            autoComplete="new-password"
          />

          <div className="flex justify-end pt-2">
            <Button variant="primary" type="submit" isLoading={isSubmitting}>
              <Lock size={14} className="mr-1.5" />
              <span>Update Password</span>
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
};
