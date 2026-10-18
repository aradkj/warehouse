import React, { useState } from 'react';
import { CheckCircle2, Play, Settings, XCircle } from 'lucide-react';
import { accessApi } from '../../api/endpoints/access';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { DecisionEvaluationResponse } from '../../api/types';
import { useTranslation } from '../../i18n';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';

export const PolicySimulatorPage: React.FC = () => {
  const { t } = useTranslation();
  const [action, setAction] = useState('orders.confirm');
  const [resourceType, setResourceType] = useState('order');
  const [resourceId, setResourceId] = useState('');
  const [attributesJson, setAttributesJson] = useState('{\n  "warehouse_id": "wh-london-01"\n}');
  const [environmentJson, setEnvironmentJson] = useState('{\n  "ip_address": "127.0.0.1"\n}');
  const [result, setResult] = useState<DecisionEvaluationResponse | null>(null);
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleEvaluate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setResult(null);

    let parsedAttributes = {};
    let parsedEnvironment = {};

    try {
      if (attributesJson.trim()) parsedAttributes = JSON.parse(attributesJson);
    } catch {
      setError('Resource attributes must be valid JSON.');
      return;
    }

    try {
      if (environmentJson.trim()) parsedEnvironment = JSON.parse(environmentJson);
    } catch {
      setError('Environment attributes must be valid JSON.');
      return;
    }

    setIsEvaluating(true);
    try {
      const decision = await accessApi.evaluateDecision({
        action: action.trim(),
        resource_type: resourceType.trim(),
        resource_id: resourceId.trim() || null,
        resource_attributes: parsedAttributes,
        environment: parsedEnvironment,
      });
      setResult(decision);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Policy evaluation failed.';
      setError(msg);
    } finally {
      setIsEvaluating(false);
    }
  };

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto' }}>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">{t('access.simulatorTitle')}</h1>
          <p className="text-sm text-muted mt-1">
            {t('access.simulatorSubtitle')}
          </p>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-6">{error}</Alert>}

      <div className="grid grid-cols-2 gap-6">
        {/* Input Form */}
        <Card title="Simulation Parameters">
          <form onSubmit={handleEvaluate}>
            <Input
              label="Action"
              value={action}
              onChange={(e) => setAction(e.target.value)}
              placeholder="e.g. orders.confirm or inventory.adjust"
              required
            />

            <Input
              label="Resource Type"
              value={resourceType}
              onChange={(e) => setResourceType(e.target.value)}
              placeholder="e.g. order, product, warehouse"
              required
            />

            <Input
              label="Resource ID (Optional)"
              value={resourceId}
              onChange={(e) => setResourceId(e.target.value)}
              placeholder="Target entity UUID"
            />

            <div className="form-group">
              <label className="form-label" htmlFor="resource-attrs-input">Resource Attributes (JSON)</label>
              <textarea
                id="resource-attrs-input"
                className="form-textarea font-mono text-xs"
                rows={3}
                value={attributesJson}
                onChange={(e) => setAttributesJson(e.target.value)}
              />
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="environment-attrs-input">Environment Attributes (JSON)</label>
              <textarea
                id="environment-attrs-input"
                className="form-textarea font-mono text-xs"
                rows={3}
                value={environmentJson}
                onChange={(e) => setEnvironmentJson(e.target.value)}
              />
            </div>

            <Button variant="primary" type="submit" isLoading={isEvaluating} className="w-full mt-2">
              <Play size={14} className="mr-1 rtl-flip" />
              <span>Evaluate Decision</span>
            </Button>
          </form>
        </Card>

        {/* Decision Output Card */}
        <Card title={t('access.outcomeTitle')}>
          {!result && !isEvaluating ? (
            <div className="text-center py-16 text-muted text-sm">
              <Settings size={36} className="mx-auto mb-2 opacity-50" />
              Configure parameters and click "Evaluate Decision" to test policies.
            </div>
          ) : result ? (
            <div className="space-y-4">
              <div
                className={`p-4 rounded border flex items-center gap-3 ${
                  result.allowed
                    ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
                    : 'bg-rose-50 border-rose-200 text-rose-800'
                }`}
                style={{
                  backgroundColor: result.allowed ? 'var(--color-success-bg)' : 'var(--color-danger-bg)',
                  borderColor: result.allowed ? 'var(--color-success-border)' : 'var(--color-danger-border)',
                  color: result.allowed ? 'var(--color-success)' : 'var(--color-danger)',
                }}
              >
                {result.allowed ? <CheckCircle2 size={24} /> : <XCircle size={24} />}
                <div>
                  <div className="text-base font-bold">
                    {result.allowed ? `DECISION: ${t('access.allow')}` : `DECISION: ${t('access.deny')}`}
                  </div>
                  <div className="text-xs">{result.reason}</div>
                </div>
              </div>

              <div className="space-y-2 text-xs font-mono">
                <div><strong>Reason Code:</strong> {result.reason_code}</div>
                <div><strong>Action Evaluated:</strong> {result.action}</div>
                <div><strong>Resource Type:</strong> {result.resource_type}</div>
                {result.policy_id && <div><strong>{t('access.matchedRule')}:</strong> {result.policy_id}</div>}
              </div>

              {result.missing_permissions && result.missing_permissions.length > 0 && (
                <div className="pt-3 border-t">
                  <div className="text-xs font-semibold text-danger uppercase mb-1">
                    Missing Permissions:
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {result.missing_permissions.map((p) => (
                      <span key={p} className="badge badge-danger text-xs font-mono">{p}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : null}
        </Card>
      </div>
    </div>
  );
};
