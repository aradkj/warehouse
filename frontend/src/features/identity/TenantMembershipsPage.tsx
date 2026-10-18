import React, { useEffect, useState } from 'react';
import { Mail, Plus, Shield, UserPlus, Users, X } from 'lucide-react';
import { accessApi } from '../../api/endpoints/access';
import { authApi } from '../../api/endpoints/auth';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { RoleResponse, TenantMembershipResponse } from '../../api/types';
import { useTenant } from '../../app/providers/TenantContext';
import { useToast } from '../../app/providers/ToastContext';
import { useTranslation } from '../../i18n';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';

export const TenantMembershipsPage: React.FC = () => {
  const { activeTenantId } = useTenant();
  const { showToast } = useToast();
  const { t, formatDate } = useTranslation();

  const [memberships, setMemberships] = useState<TenantMembershipResponse[]>([]);
  const [roles, setRoles] = useState<RoleResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Invite Modal
  const [isInviteOpen, setIsInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<'ADMIN' | 'MEMBER'>('MEMBER');
  const [isInviting, setIsInviting] = useState(false);
  const [invitationToken, setInvitationToken] = useState<string | null>(null);

  // Provision Modal
  const [isProvisionOpen, setIsProvisionOpen] = useState(false);
  const [provisionEmail, setProvisionEmail] = useState('');
  const [provisionRole, setProvisionRole] = useState<'ADMIN' | 'MEMBER'>('MEMBER');
  const [isProvisioning, setIsProvisioning] = useState(false);

  // Role Assignment Modal
  const [selectedMembership, setSelectedMembership] = useState<TenantMembershipResponse | null>(null);
  const [selectedRoleToAssign, setSelectedRoleToAssign] = useState('');
  const [isAssigning, setIsAssigning] = useState(false);

  const fetchMemberships = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [mems, r] = await Promise.all([
        authApi.listMemberships(),
        accessApi.listRoles(),
      ]);
      // Filter memberships for active tenant
      setMemberships(mems.filter((m) => m.tenant_id === activeTenantId));
      setRoles(r);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to fetch memberships.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchMemberships();
  }, [activeTenantId]);

  const handleInviteSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsInviting(true);
    try {
      const res = await authApi.createInvitation({
        email: inviteEmail.trim() || null,
        role: inviteRole,
      });
      setInvitationToken(res.invitation_token);
      showToast('Invitation token generated.', 'success');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to create invitation.';
      showToast(msg, 'error');
    } finally {
      setIsInviting(false);
    }
  };

  const handleProvisionSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsProvisioning(true);
    try {
      await authApi.provisionMembership({
        email: provisionEmail.trim() || null,
        role: provisionRole,
      });
      showToast('Membership provisioned successfully.', 'success');
      setIsProvisionOpen(false);
      setProvisionEmail('');
      await fetchMemberships();
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to provision membership.';
      showToast(msg, 'error');
    } finally {
      setIsProvisioning(false);
    }
  };

  const handleAssignRole = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedMembership || !selectedRoleToAssign) return;

    setIsAssigning(true);
    try {
      await accessApi.assignRoleToMembership(selectedMembership.id, selectedRoleToAssign);
      showToast(`Assigned role ${selectedRoleToAssign}.`, 'success');
      setSelectedMembership(null);
      setSelectedRoleToAssign('');
      await fetchMemberships();
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to assign role.';
      showToast(msg, 'error');
    } finally {
      setIsAssigning(false);
    }
  };

  const handleRevokeRole = async (membershipId: string, roleName: string) => {
    if (!window.confirm(`Revoke role "${roleName}" from this membership?`)) return;

    try {
      await accessApi.revokeRoleFromMembership(membershipId, roleName);
      showToast(`Revoked role ${roleName}.`, 'info');
      await fetchMemberships();
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to revoke role.';
      showToast(msg, 'error');
    }
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">{t('identity.membersTitle')}</h1>
          <p className="text-sm text-muted mt-1">
            {t('identity.membersSubtitle', { tenant: '' })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={() => setIsProvisionOpen(true)}>
            <UserPlus size={14} className="mr-1" />
            <span>Provision Member</span>
          </Button>
          <Button size="sm" variant="primary" onClick={() => setIsInviteOpen(true)}>
            <Mail size={14} className="mr-1" />
            <span>{t('identity.inviteBtn')}</span>
          </Button>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      {isLoading ? (
        <div className="text-center py-12 text-muted">{t('common.loading')}</div>
      ) : (
        <Card title={t('identity.membersTitle')}>
          <div className="table-container border-0">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('identity.userEmail')}</th>
                  <th>{t('common.status')}</th>
                  <th>{t('identity.role')}</th>
                  <th>{t('access.permissions')}</th>
                  <th>{t('identity.joinedAt')}</th>
                  <th>{t('common.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {memberships.map((m) => (
                  <tr key={m.id}>
                    <td className="font-mono text-xs font-bold text-main">{m.user_id}</td>
                    <td>
                      <Badge variant={m.status === 'ACTIVE' ? 'success' : 'neutral'}>
                        {m.status}
                      </Badge>
                    </td>
                    <td className="text-xs font-semibold">{m.role}</td>
                    <td>
                      <div className="flex flex-wrap gap-1 items-center">
                        {m.roles.map((r) => (
                          <span key={r} className="badge badge-info text-xs flex items-center gap-1 font-mono">
                            <span>{r}</span>
                            <button
                              onClick={() => handleRevokeRole(m.id, r)}
                              className="text-muted hover:text-danger ml-1"
                              style={{ background: 'none', border: 'none', cursor: 'pointer' }}
                              title="Revoke role"
                            >
                              <X size={10} />
                            </button>
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="text-xs text-muted">
                      {new Date(m.created_at).toLocaleDateString()}
                    </td>
                    <td>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          setSelectedMembership(m);
                          setSelectedRoleToAssign(roles[0]?.name || '');
                        }}
                      >
                        Assign Role
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* Invite Modal */}
      <Modal
        isOpen={isInviteOpen}
        onClose={() => {
          setIsInviteOpen(false);
          setInvitationToken(null);
          setInviteEmail('');
        }}
        title="Generate Tenant Invitation"
        description="Create an invitation token that allows an operator to register and join this tenant."
      >
        {invitationToken ? (
          <div>
            <Alert variant="success" className="mb-4">
              Invitation token generated successfully!
            </Alert>
            <div className="form-group">
              <label className="form-label">Invitation Token</label>
              <textarea
                readOnly
                className="form-textarea font-mono text-xs"
                rows={3}
                value={invitationToken}
              />
              <p className="text-xs text-muted mt-1">
                Share this token with the user. Valid for 7 days.
              </p>
            </div>
            <div className="flex justify-end mt-4">
              <Button
                variant="primary"
                onClick={() => {
                  setIsInviteOpen(false);
                  setInvitationToken(null);
                  setInviteEmail('');
                }}
              >
                Done
              </Button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleInviteSubmit}>
            <Input
              label="Recipient Email (Optional)"
              type="email"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              placeholder="colleague@example.com"
              helperText="If specified, token can only be claimed by this email address."
            />

            <div className="form-group">
              <label className="form-label">Initial Role</label>
              <select
                className="form-select"
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as any)}
              >
                <option value="MEMBER">MEMBER (Standard operations)</option>
                <option value="ADMIN">ADMIN (Full administrative access)</option>
              </select>
            </div>

            <div className="flex justify-end gap-2 mt-6">
              <Button variant="secondary" onClick={() => setIsInviteOpen(false)} disabled={isInviting}>
                Cancel
              </Button>
              <Button variant="primary" type="submit" isLoading={isInviting}>
                Generate Token
              </Button>
            </div>
          </form>
        )}
      </Modal>

      {/* Provision Member Modal */}
      <Modal
        isOpen={isProvisionOpen}
        onClose={() => setIsProvisionOpen(false)}
        title="Provision Existing User"
        description="Attach an existing user account to this tenant immediately."
      >
        <form onSubmit={handleProvisionSubmit}>
          <Input
            label="User Email"
            type="email"
            value={provisionEmail}
            onChange={(e) => setProvisionEmail(e.target.value)}
            placeholder="existing.user@example.com"
            required
          />

          <div className="form-group">
            <label className="form-label">Role</label>
            <select
              className="form-select"
              value={provisionRole}
              onChange={(e) => setProvisionRole(e.target.value as any)}
            >
              <option value="MEMBER">MEMBER</option>
              <option value="ADMIN">ADMIN</option>
            </select>
          </div>

          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={() => setIsProvisionOpen(false)} disabled={isProvisioning}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" isLoading={isProvisioning}>
              Add Member
            </Button>
          </div>
        </form>
      </Modal>

      {/* Assign Role Modal */}
      <Modal
        isOpen={!!selectedMembership}
        onClose={() => setSelectedMembership(null)}
        title="Assign Role to Member"
        description={`Assign an RBAC role to user ${selectedMembership?.user_id.slice(0, 8)}.`}
      >
        <form onSubmit={handleAssignRole}>
          <div className="form-group">
            <label className="form-label">Select Role</label>
            <select
              className="form-select"
              value={selectedRoleToAssign}
              onChange={(e) => setSelectedRoleToAssign(e.target.value)}
              required
            >
              <option value="">Select a role...</option>
              {roles.map((r) => (
                <option key={r.id} value={r.name}>
                  {r.name} ({r.is_system ? 'System' : 'Custom'})
                </option>
              ))}
            </select>
          </div>

          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={() => setSelectedMembership(null)} disabled={isAssigning}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" isLoading={isAssigning}>
              Assign Role
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
