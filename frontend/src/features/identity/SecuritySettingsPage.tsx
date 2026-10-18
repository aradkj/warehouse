import React, { useState } from 'react';
import { Lock, Shield, User } from 'lucide-react';
import { authApi } from '../../api/endpoints/auth';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { useAuth } from '../../app/providers/AuthContext';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';

export const SecuritySettingsPage: React.FC = () => {
  const { user, principal } = useAuth();
  const { showToast } = useToast();

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
    <div style={{ maxWidth: '800px', margin: '0 auto' }}>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">Security & Profile Settings</h1>
          <p className="text-sm text-muted mt-1">
            Manage your credentials, active sessions, and password security.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-6 mb-6">
        {/* Profile Card */}
        <Card title="Current Principal Details">
          <div className="space-y-3 text-xs">
            <div>
              <span className="text-muted block">User ID:</span>
              <span className="font-mono">{user?.id || principal?.id}</span>
            </div>
            <div>
              <span className="text-muted block">Email:</span>
              <span className="font-semibold text-main">{user?.email || principal?.email}</span>
            </div>
            <div>
              <span className="text-muted block">Account Status:</span>
              <span className="font-medium">{principal?.account_status || 'ACTIVE'}</span>
            </div>
            <div>
              <span className="text-muted block">Default Role:</span>
              <span className="font-medium">{principal?.role || 'MEMBER'}</span>
            </div>
          </div>
        </Card>

        {/* Change Password Card */}
        <Card title="Change Password" subtitle="Updating your password revokes all other concurrent sessions.">
          {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

          <form onSubmit={handleSubmit}>
            <Input
              label="Current Password"
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
            />

            <Input
              label="New Password"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              helperText="Minimum 8 characters."
              required
            />

            <Input
              label="Confirm New Password"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
            />

            <div className="flex justify-end mt-4">
              <Button variant="primary" type="submit" isLoading={isSubmitting}>
                <Lock size={14} className="mr-1" />
                <span>Update Password</span>
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </div>
  );
};
