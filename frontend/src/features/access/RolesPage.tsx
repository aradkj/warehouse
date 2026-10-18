import React, { useEffect, useState } from 'react';
import { Plus, RefreshCw, Shield } from 'lucide-react';
import { accessApi } from '../../api/endpoints/access';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { PermissionResponse, RoleResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';

export const RolesPage: React.FC = () => {
  const { showToast } = useToast();
  const [roles, setRoles] = useState<RoleResponse[]>([]);
  const [permissions, setPermissions] = useState<PermissionResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create Role Modal
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [roleName, setRoleName] = useState('');
  const [roleDesc, setRoleDesc] = useState('');
  const [selectedPerms, setSelectedPerms] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const fetchData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [rolesData, permsData] = await Promise.all([
        accessApi.listRoles(),
        accessApi.listPermissions(),
      ]);
      setRoles(rolesData);
      setPermissions(permsData.permissions);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to load roles.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (!roleName.trim()) {
      setFormError('Role name is required.');
      return;
    }

    setIsSubmitting(true);
    try {
      const created = await accessApi.createRole({
        name: roleName.trim().toUpperCase(),
        description: roleDesc.trim(),
        permissions: selectedPerms,
      });

      showToast(`Custom role ${created.name} created.`, 'success');
      setRoles((prev) => [...prev, created]);
      setIsCreateOpen(false);
      setRoleName('');
      setRoleDesc('');
      setSelectedPerms([]);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to create role.';
      setFormError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const togglePermission = (perm: string) => {
    setSelectedPerms((prev) =>
      prev.includes(perm) ? prev.filter((p) => p !== perm) : [...prev, perm]
    );
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">Roles (RBAC)</h1>
          <p className="text-sm text-muted mt-1">
            System and custom tenant roles mapping to fine-grained permission catalogs.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={fetchData} isLoading={isLoading}>
            <RefreshCw size={14} className="mr-1" />
            <span>Refresh</span>
          </Button>
          <Button size="sm" variant="primary" onClick={() => setIsCreateOpen(true)}>
            <Plus size={14} className="mr-1" />
            <span>Create Custom Role</span>
          </Button>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      {isLoading ? (
        <div className="text-center py-12 text-muted">Loading roles...</div>
      ) : (
        <div className="grid grid-cols-2 gap-6">
          {roles.map((r) => (
            <Card key={r.id} className="mb-0">
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div
                    style={{
                      width: '36px',
                      height: '36px',
                      borderRadius: '8px',
                      backgroundColor: r.is_system ? 'var(--bg-surface-subtle)' : 'var(--color-primary-subtle)',
                      color: r.is_system ? 'var(--text-secondary)' : 'var(--color-primary)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <Shield size={18} />
                  </div>
                  <div>
                    <h2 className="font-semibold text-base flex items-center gap-2">
                      <span>{r.name}</span>
                      <Badge variant={r.is_system ? 'neutral' : 'info'}>
                        {r.is_system ? 'System' : 'Custom'}
                      </Badge>
                    </h2>
                    <p className="text-xs text-muted">{r.description || 'No description provided.'}</p>
                  </div>
                </div>
              </div>

              <div className="pt-3 border-t">
                <div className="text-xs font-semibold uppercase text-muted mb-2">
                  Permissions ({r.permissions.length})
                </div>
                <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto">
                  {r.permissions.map((perm) => (
                    <span key={perm} className="badge badge-neutral text-xs font-mono">
                      {perm}
                    </span>
                  ))}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Create Custom Role Modal */}
      <Modal
        isOpen={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        title="Create Custom Role"
        description="Define a new role scoped to your tenant with a custom permission set."
      >
        {formError && <Alert variant="danger" className="mb-4">{formError}</Alert>}

        <form onSubmit={handleCreateSubmit}>
          <Input
            label="Role Name"
            value={roleName}
            onChange={(e) => setRoleName(e.target.value.toUpperCase())}
            placeholder="WAREHOUSE_SUPERVISOR"
            helperText="Uppercase alphanumeric and underscores (2-64 chars)."
            required
          />

          <Input
            label="Description"
            value={roleDesc}
            onChange={(e) => setRoleDesc(e.target.value)}
            placeholder="Supervisory access over inventory adjustments and fulfillments"
            maxLength={255}
          />

          <div className="form-group">
            <label className="form-label">Assign Permissions ({selectedPerms.length} selected)</label>
            <div className="border rounded p-3 max-h-48 overflow-y-auto space-y-2">
              {permissions.map((p) => (
                <div key={p.name} className="flex items-start gap-2 text-xs">
                  <input
                    type="checkbox"
                    id={`perm-${p.name}`}
                    checked={selectedPerms.includes(p.name)}
                    onChange={() => togglePermission(p.name)}
                  />
                  <label htmlFor={`perm-${p.name}`} className="cursor-pointer">
                    <span className="font-mono font-semibold">{p.name}</span>
                    <span className="text-muted block">{p.description}</span>
                  </label>
                </div>
              ))}
            </div>
          </div>

          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={() => setIsCreateOpen(false)} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" isLoading={isSubmitting}>
              Create Role
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
