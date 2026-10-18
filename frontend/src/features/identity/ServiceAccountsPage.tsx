import React, { useState } from 'react';
import { Copy, Key, Plus, RefreshCw, Shield, Trash2 } from 'lucide-react';
import { authApi } from '../../api/endpoints/auth';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { ServiceAccountCreatedResponse, ServiceAccountResponse } from '../../api/types';
import { useTenant } from '../../app/providers/TenantContext';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';

export const ServiceAccountsPage: React.FC = () => {
  const { activeTenantId } = useTenant();
  const { showToast } = useToast();

  const [serviceAccounts, setServiceAccounts] = useState<ServiceAccountResponse[]>([]);
  const [createdCredentials, setCreatedCredentials] = useState<ServiceAccountCreatedResponse | null>(null);

  // Create Modal
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [clientId, setClientId] = useState('');
  const [scopesStr, setScopesStr] = useState('orders.read, inventory.read');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (!name.trim() || !clientId.trim()) {
      setFormError('Account name and client ID are required.');
      return;
    }

    const scopes = scopesStr.split(',').map((s) => s.trim()).filter(Boolean);

    setIsSubmitting(true);
    try {
      const created = await authApi.createServiceAccount({
        name: name.trim(),
        client_id: clientId.trim().toLowerCase(),
        scopes,
      });

      setCreatedCredentials(created);
      setServiceAccounts((prev) => [created, ...prev]);
      showToast('Service account created. Save the client secret.', 'success');
      setIsCreateOpen(false);
      setName('');
      setClientId('');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to create service account.';
      setFormError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRevoke = async (saId: string) => {
    if (!window.confirm('Are you sure you want to revoke this service account? Existing tokens will immediately be rejected.')) {
      return;
    }

    try {
      await authApi.revokeServiceAccount(saId);
      setServiceAccounts((prev) => prev.filter((s) => s.id !== saId));
      showToast('Service account revoked.', 'info');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to revoke service account.';
      showToast(msg, 'error');
    }
  };

  const handleRotate = async (saId: string) => {
    if (!window.confirm('Rotate credentials for this service account? Existing secret and tokens will be invalidated.')) {
      return;
    }

    try {
      const rotated = await authApi.rotateServiceAccountSecret(saId);
      setCreatedCredentials(rotated);
      showToast('Credentials rotated successfully. Save your new secret.', 'success');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to rotate secret.';
      showToast(msg, 'error');
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    showToast('Copied to clipboard.', 'info');
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">Service Accounts</h1>
          <p className="text-sm text-muted mt-1">
            Machine-to-machine integration credentials scoped to this tenant.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="primary" onClick={() => setIsCreateOpen(true)}>
            <Plus size={14} className="mr-1" />
            <span>New Service Account</span>
          </Button>
        </div>
      </div>

      {serviceAccounts.length === 0 ? (
        <Card className="text-center py-12">
          <Key size={48} className="mx-auto text-muted mb-3" />
          <h2 className="text-base font-semibold mb-1">No Service Accounts</h2>
          <p className="text-sm text-muted max-w-md mx-auto mb-6">
            Create programmatic service accounts with scoped permissions for automated background systems, carriers, or ERP integrations.
          </p>
          <Button variant="primary" onClick={() => setIsCreateOpen(true)}>
            Create Service Account
          </Button>
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-6">
          {serviceAccounts.map((sa) => (
            <Card key={sa.id} className="mb-0">
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div
                    style={{
                      width: '36px',
                      height: '36px',
                      borderRadius: '8px',
                      backgroundColor: 'var(--color-primary-subtle)',
                      color: 'var(--color-primary)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <Key size={18} />
                  </div>
                  <div>
                    <h2 className="font-semibold text-base">{sa.name}</h2>
                    <div className="font-mono text-xs text-muted">{sa.client_id}</div>
                  </div>
                </div>
                <Badge variant={sa.status === 'ACTIVE' ? 'success' : 'neutral'}>
                  {sa.status}
                </Badge>
              </div>

              <div className="pt-3 border-t text-xs space-y-2">
                <div>
                  <span className="text-muted block mb-1">Scopes:</span>
                  <div className="flex flex-wrap gap-1">
                    {sa.scopes.map((scope) => (
                      <span key={scope} className="badge badge-neutral font-mono">{scope}</span>
                    ))}
                  </div>
                </div>

                <div className="flex justify-end gap-2 pt-2 border-t">
                  <Button size="sm" variant="secondary" onClick={() => handleRotate(sa.id)}>
                    <RefreshCw size={12} className="mr-1" />
                    <span>Rotate Secret</span>
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => handleRevoke(sa.id)}>
                    <Trash2 size={12} className="mr-1" />
                    <span>Revoke</span>
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Secret Revelation Modal */}
      <Modal
        isOpen={!!createdCredentials}
        onClose={() => setCreatedCredentials(null)}
        title="Service Account Credentials"
        description="Save your client secret now. It will never be shown again."
      >
        <Alert variant="warning" className="mb-4">
          Make sure to copy your client secret now. The platform hashes client secrets using Argon2id and cannot recover it later.
        </Alert>

        <div className="space-y-4">
          <div className="form-group">
            <label className="form-label">Client ID</label>
            <div className="flex items-center gap-2">
              <input
                readOnly
                className="form-input font-mono text-xs bg-surface-subtle"
                value={createdCredentials?.client_id || ''}
              />
              <Button
                size="sm"
                variant="secondary"
                onClick={() => copyToClipboard(createdCredentials?.client_id || '')}
              >
                <Copy size={14} />
              </Button>
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Client Secret</label>
            <div className="flex items-center gap-2">
              <input
                readOnly
                className="form-input font-mono text-xs bg-surface-subtle"
                value={createdCredentials?.client_secret || ''}
              />
              <Button
                size="sm"
                variant="secondary"
                onClick={() => copyToClipboard(createdCredentials?.client_secret || '')}
              >
                <Copy size={14} />
              </Button>
            </div>
          </div>
        </div>

        <div className="flex justify-end mt-6">
          <Button variant="primary" onClick={() => setCreatedCredentials(null)}>
            I Have Saved My Secret
          </Button>
        </div>
      </Modal>

      {/* Create Modal */}
      <Modal
        isOpen={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        title="Create Service Account"
        description="Provision automated API credentials with scoped permission tokens."
      >
        {formError && <Alert variant="danger" className="mb-4">{formError}</Alert>}

        <form onSubmit={handleCreateSubmit}>
          <Input
            label="Service Account Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Carrier Dispatch Worker"
            required
          />

          <Input
            label="Client ID (Slug)"
            value={clientId}
            onChange={(e) => setClientId(e.target.value.toLowerCase())}
            placeholder="sa-carrier-dispatch"
            pattern="^[a-z0-9_-]{3,64}$"
            helperText="3-64 characters, lowercase alphanumeric, hyphens, and underscores."
            required
          />

          <Input
            label="Scopes / Permissions (Comma-separated)"
            value={scopesStr}
            onChange={(e) => setScopesStr(e.target.value)}
            placeholder="orders.read, fulfillments.modify"
            helperText="Permissions granted to tokens issued for this client."
          />

          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={() => setIsCreateOpen(false)} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" isLoading={isSubmitting}>
              Create Account & Secret
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
