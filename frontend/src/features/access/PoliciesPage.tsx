import React, { useEffect, useState } from 'react';
import { Lock, Plus, RefreshCw, ToggleLeft, ToggleRight } from 'lucide-react';
import { accessApi } from '../../api/endpoints/access';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { PolicyResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { useTranslation } from '../../i18n';
import { EmptyState } from '../../components/feedback/EmptyState';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';

export const PoliciesPage: React.FC = () => {
  const { showToast } = useToast();
  const { t } = useTranslation();
  const [policies, setPolicies] = useState<PolicyResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create Policy Modal
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [effect, setEffect] = useState<'ALLOW' | 'DENY'>('ALLOW');
  const [actions, setActions] = useState('orders.confirm, orders.cancel');
  const [resources, setResources] = useState('*');
  const [conditionsJson, setConditionsJson] = useState('{}');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const fetchPolicies = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await accessApi.listPolicies();
      setPolicies(data);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to fetch access policies.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchPolicies();
  }, []);

  const handleToggleStatus = async (policy: PolicyResponse) => {
    const newStatus = policy.status === 'ACTIVE' ? 'INACTIVE' : 'ACTIVE';
    try {
      const updated = await accessApi.updatePolicyStatus(policy.id, newStatus);
      setPolicies((prev) => prev.map((p) => (p.id === policy.id ? updated : p)));
      showToast(`Policy "${policy.name}" status updated to ${newStatus}.`, 'info');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to update policy status.';
      showToast(msg, 'error');
    }
  };

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    let parsedConditions = {};
    try {
      parsedConditions = JSON.parse(conditionsJson || '{}');
    } catch {
      setFormError('Conditions must be a valid JSON object.');
      return;
    }

    const actionList = actions.split(',').map((a) => a.trim()).filter(Boolean);
    if (actionList.length === 0) {
      setFormError('At least one action is required.');
      return;
    }

    setIsSubmitting(true);
    try {
      const created = await accessApi.createPolicy({
        name: name.trim(),
        description: description.trim(),
        effect,
        actions: actionList,
        resources: resources.split(',').map((r) => r.trim()).filter(Boolean),
        conditions: parsedConditions,
        condition: parsedConditions,
      });

      showToast(`Policy "${created.name}" created.`, 'success');
      setPolicies((prev) => [...prev, created]);
      setIsCreateOpen(false);
      setName('');
      setDescription('');
      setConditionsJson('{}');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to create access policy.';
      setFormError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">{t('access.policiesTitle')}</h1>
          <p className="text-sm text-muted mt-1">
            {t('access.policiesSubtitle')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={fetchPolicies} isLoading={isLoading}>
            <RefreshCw size={14} className="mr-1" />
            <span>{t('common.refresh')}</span>
          </Button>
          <Button size="sm" variant="primary" onClick={() => setIsCreateOpen(true)}>
            <Plus size={14} className="mr-1" />
            <span>New Policy</span>
          </Button>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      {isLoading ? (
        <div className="text-center py-12 text-muted">Loading access policies...</div>
      ) : policies.length === 0 ? (
        <Card>
          <EmptyState
            title="No Access Policies Configured"
            description="You have not defined any custom ABAC policies for this tenant yet."
            actionLabel="Create Access Policy"
            onAction={() => setIsCreateOpen(true)}
            icon={<Lock size={48} strokeWidth={1.5} />}
          />
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-6">
          {policies.map((p) => {
            const effectVariant = p.effect === 'ALLOW' ? 'success' : 'danger';
            const statusVariant = p.status === 'ACTIVE' ? 'success' : 'neutral';

            return (
              <Card key={p.id} className="mb-0">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <h2 className="font-semibold text-base flex items-center gap-2">
                      <span>{p.name}</span>
                      <Badge variant={effectVariant}>{p.effect}</Badge>
                      <Badge variant={statusVariant}>{p.status}</Badge>
                    </h2>
                    <p className="text-xs text-muted mt-1">{p.description || 'No description provided.'}</p>
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => handleToggleStatus(p)}
                  >
                    {p.status === 'ACTIVE' ? 'Deactivate' : 'Activate'}
                  </Button>
                </div>

                <div className="pt-3 border-t space-y-2 text-xs">
                  <div>
                    <span className="font-semibold text-muted block mb-1">Actions:</span>
                    <div className="flex flex-wrap gap-1">
                      {p.actions.map((act) => (
                        <span key={act} className="badge badge-neutral font-mono">{act}</span>
                      ))}
                    </div>
                  </div>

                  <div>
                    <span className="font-semibold text-muted block mb-1">Conditions:</span>
                    <pre className="p-2 bg-surface-subtle rounded border font-mono text-xs overflow-x-auto">
                      {JSON.stringify(p.condition, null, 2)}
                    </pre>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* Create Policy Modal */}
      <Modal
        isOpen={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        title="Create Access Policy (ABAC)"
        description="Structured policy rule granting ALLOW or DENY outcomes based on actions and conditions."
      >
        {formError && <Alert variant="danger" className="mb-4">{formError}</Alert>}

        <form onSubmit={handleCreateSubmit}>
          <Input
            label="Policy Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Restrict-Order-Confirmation-To-Regional-Admins"
            required
          />

          <Input
            label="Description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Enforces warehouse location matching before order confirmation"
          />

          <div className="form-group">
            <label className="form-label">Effect <span className="text-danger">*</span></label>
            <select
              className="form-select"
              value={effect}
              onChange={(e) => setEffect(e.target.value as any)}
            >
              <option value="ALLOW">ALLOW</option>
              <option value="DENY">DENY (Explicit Denial takes precedence)</option>
            </select>
          </div>

          <Input
            label="Permitted / Denied Actions (Comma-separated)"
            value={actions}
            onChange={(e) => setActions(e.target.value)}
            placeholder="orders.confirm, orders.cancel"
            helperText="Canonical action patterns (e.g. orders.* or inventory.adjust)"
            required
          />

          <Input
            label="Resource Types (Comma-separated)"
            value={resources}
            onChange={(e) => setResources(e.target.value)}
            placeholder="order, fulfillment"
          />

          <div className="form-group">
            <label className="form-label">Conditions JSON (ABAC Predicates)</label>
            <textarea
              className="form-textarea font-mono text-xs"
              rows={4}
              value={conditionsJson}
              onChange={(e) => setConditionsJson(e.target.value)}
              placeholder='{ "subject.department": "logistics" }'
            />
            <p className="text-xs text-muted mt-1">
              JSON schema evaluated against subject, resource attributes, and environment.
            </p>
          </div>

          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={() => setIsCreateOpen(false)} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" isLoading={isSubmitting}>
              Create Policy
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
