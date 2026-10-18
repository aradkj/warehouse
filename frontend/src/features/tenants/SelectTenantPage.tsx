import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, ArrowRight, Building, Plus, RefreshCw, Shield } from 'lucide-react';
import { platformApi } from '../../api/endpoints/platform';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { useAuth } from '../../app/providers/AuthContext';
import { useTenant } from '../../app/providers/TenantContext';
import { useToast } from '../../app/providers/ToastContext';
import { useTranslation } from '../../i18n';
import { LanguageSwitcher } from '../../components/common/LanguageSwitcher';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';

export const SelectTenantPage: React.FC = () => {
  const { user } = useAuth();
  const {
    memberships,
    activeTenantId,
    selectTenant,
    refreshMemberships,
    isLoadingTenants,
    hasLoadedTenants,
    membershipError,
  } = useTenant();
  const { showToast } = useToast();
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [isRetrying, setIsRetrying] = useState(false);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [tenantName, setTenantName] = useState('');
  const [tenantSlug, setTenantSlug] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createdInvitation, setCreatedInvitation] = useState<string | null>(null);

  const handleRetryMemberships = async () => {
    setIsRetrying(true);
    try {
      await refreshMemberships();
    } finally {
      setIsRetrying(false);
    }
  };

  const handleSelectTenant = async (tenantId: string) => {
    try {
      await selectTenant(tenantId);
      navigate('/dashboard');
    } catch {
      // Toast handles error display
    }
  };

  const handleCreateTenant = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError(null);

    if (!tenantName.trim() || !tenantSlug.trim()) {
      setCreateError('Name and slug are required.');
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await platformApi.registerTenant({
        name: tenantName.trim(),
        slug: tenantSlug.trim().toLowerCase(),
      });

      if (response.invitation_token) {
        setCreatedInvitation(response.invitation_token);
      }

      showToast(`Tenant "${response.name}" provisioned successfully.`, 'success');
      await refreshMemberships();
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to create tenant.';
      setCreateError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const resetModal = () => {
    setIsCreateModalOpen(false);
    setTenantName('');
    setTenantSlug('');
    setCreateError(null);
    setCreatedInvitation(null);
  };

  return (
    <div style={{ maxWidth: '800px', margin: '2rem auto', padding: '0 1rem' }}>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">{t('tenants.selectTitle')}</h1>
          <p className="text-sm text-muted mt-1">
            {t('tenants.selectSubtitle')}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <LanguageSwitcher />
          <Button
            variant="primary"
            onClick={() => setIsCreateModalOpen(true)}
          >
            <Plus size={16} />
            <span>{t('tenants.createNewTenant')}</span>
          </Button>
        </div>
      </div>

      {isLoadingTenants && !hasLoadedTenants ? (
        <div className="text-center py-12 text-muted">{t('common.loading')}</div>
      ) : membershipError ? (
        <Card className="text-center py-10">
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
            <AlertCircle size={24} />
          </div>
          <h2 className="text-lg font-semibold text-main mb-1">{t('tenants.loadError')}</h2>
          <p className="text-sm text-muted max-w-md mx-auto mb-6">
            {membershipError || 'Could not retrieve your organization memberships from the server.'}
          </p>
          <div className="flex items-center justify-center gap-3">
            <Button
              variant="primary"
              onClick={handleRetryMemberships}
              isLoading={isRetrying || isLoadingTenants}
              disabled={isRetrying || isLoadingTenants}
            >
              <RefreshCw size={14} className="mr-1.5" />
              <span>{t('tenants.retryLoading')}</span>
            </Button>
            <Button variant="secondary" onClick={() => setIsCreateModalOpen(true)}>
              <Plus size={14} className="mr-1.5" />
              <span>{t('tenants.provisionNewTenant')}</span>
            </Button>
          </div>
        </Card>
      ) : memberships.length === 0 ? (
        <Card className="text-center py-10">
          <Building size={48} className="mx-auto text-muted mb-3" />
          <h2 className="text-lg font-semibold mb-1">{t('tenants.noMembershipsTitle')}</h2>
          <p className="text-sm text-muted max-w-md mx-auto mb-6">
            {t('tenants.noMembershipsDesc')}
          </p>
          <Button variant="primary" onClick={() => setIsCreateModalOpen(true)}>
            {t('tenants.provisionFirstTenant')}
          </Button>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4">
          {memberships.map((m) => {
            const isCurrent = m.tenant_id === activeTenantId;
            return (
              <div
                key={m.id}
                className={`card p-4 flex items-center justify-between transition-all ${
                  isCurrent ? 'border-primary' : ''
                }`}
                style={{
                  borderWidth: isCurrent ? '2px' : '1px',
                  backgroundColor: isCurrent ? 'var(--color-primary-subtle)' : 'var(--bg-surface)',
                }}
              >
                <div className="flex items-center gap-4">
                  <div
                    style={{
                      width: '44px',
                      height: '44px',
                      borderRadius: '8px',
                      backgroundColor: isCurrent ? 'var(--color-primary)' : 'var(--bg-surface-subtle)',
                      color: isCurrent ? '#ffffff' : 'var(--text-secondary)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <Building size={22} />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-base">
                        {m.tenant_name || m.tenant_slug || `Tenant ${m.tenant_id.slice(0, 8)}`}
                      </span>
                      {isCurrent && <Badge variant="success">{t('tenants.activeContext')}</Badge>}
                    </div>
                    <div className="text-xs text-muted flex items-center gap-2 mt-1">
                      <span>ID: <code className="font-mono">{m.tenant_id}</code></span>
                      <span>•</span>
                      <span className="flex items-center gap-1 font-medium">
                        <Shield size={12} />
                        {m.role}
                      </span>
                    </div>
                  </div>
                </div>

                <Button
                  variant={isCurrent ? 'secondary' : 'primary'}
                  size="sm"
                  onClick={() => handleSelectTenant(m.tenant_id)}
                >
                  <span>{isCurrent ? t('common.continue') || 'Continue' : t('tenants.switchContext')}</span>
                  <ArrowRight size={14} className="ml-1 rtl-flip" />
                </Button>
              </div>
            );
          })}
        </div>
      )}

      {/* Provision Tenant Modal */}
      <Modal
        isOpen={isCreateModalOpen}
        onClose={resetModal}
        title={t('tenants.createModalTitle')}
        description={t('tenants.createModalDesc')}
      >
        {createdInvitation ? (
          <div>
            <Alert variant="success" className="mb-4">
              Tenant provisioned successfully! Save your bootstrap administrator invitation token.
            </Alert>
            <div className="form-group">
              <label className="form-label">Administrator Invitation Token</label>
              <textarea
                readOnly
                className="form-textarea font-mono text-xs"
                rows={3}
                value={createdInvitation}
              />
              <p className="text-xs text-muted mt-1">
                Use this token when registering an admin account or inviting colleagues.
              </p>
            </div>
            <div className="flex justify-end mt-4">
              <Button variant="primary" onClick={resetModal}>
                {t('common.close')}
              </Button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleCreateTenant}>
            {createError && (
              <Alert variant="danger" className="mb-4">
                {createError}
              </Alert>
            )}

            <Input
              label={t('tenants.tenantNameLabel')}
              value={tenantName}
              onChange={(e) => {
                setTenantName(e.target.value);
                if (!tenantSlug) {
                  setTenantSlug(
                    e.target.value
                      .toLowerCase()
                      .replace(/[^a-z0-9]+/g, '-')
                      .replace(/^-|-$/g, '')
                  );
                }
              }}
              placeholder={t('tenants.tenantNamePlaceholder')}
              required
            />

            <Input
              label={t('tenants.tenantSlugLabel')}
              value={tenantSlug}
              onChange={(e) => setTenantSlug(e.target.value.toLowerCase())}
              placeholder={t('tenants.tenantSlugPlaceholder')}
              pattern="^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$"
              helperText={t('tenants.tenantSlugHelp')}
              required
            />

            <div className="flex justify-end gap-2 mt-6">
              <Button variant="secondary" onClick={resetModal} disabled={isSubmitting}>
                {t('common.cancel')}
              </Button>
              <Button variant="primary" type="submit" isLoading={isSubmitting}>
                {t('tenants.createTenantBtn')}
              </Button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
};
