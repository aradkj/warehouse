import React, { useState } from 'react';
import { Database, RefreshCw, Wrench } from 'lucide-react';
import { searchApi } from '../../api/endpoints/search';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { ReconcileResponse, ReindexResponse } from '../../api/types';
import { useTenant } from '../../app/providers/TenantContext';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';

export const SearchMaintenancePage: React.FC = () => {
  const { activeTenantId } = useTenant();
  const { showToast } = useToast();

  const [isReindexing, setIsReindexing] = useState(false);
  const [reindexResult, setReindexResult] = useState<ReindexResponse | null>(null);

  const [isReconciling, setIsReconciling] = useState(false);
  const [autoRepair, setAutoRepair] = useState(false);
  const [reconcileResult, setReconcileResult] = useState<ReconcileResponse | null>(null);

  const [error, setError] = useState<string | null>(null);

  const handleReindex = async () => {
    if (!window.confirm('Rebuild search index from PostgreSQL for this tenant?')) {
      return;
    }

    setIsReindexing(true);
    setError(null);
    try {
      const res = await searchApi.reindex({
        tenant_id: activeTenantId,
        retain_old_indices: false,
        async_mode: false,
      });
      setReindexResult(res);
      showToast(`Reindexed ${res.total_indexed} products successfully.`, 'success');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Reindex operation failed.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsReindexing(false);
    }
  };

  const handleReconcile = async () => {
    setIsReconciling(true);
    setError(null);
    try {
      const res = await searchApi.reconcile({
        tenant_id: activeTenantId,
        auto_repair: autoRepair,
        async_mode: false,
      });
      setReconcileResult(res);
      if (res.discrepancies.length === 0) {
        showToast('Reconciliation complete. PostgreSQL and Elasticsearch are fully synchronized.', 'success');
      } else {
        showToast(`Discovered ${res.discrepancies.length} discrepancy(ies).`, 'warning');
      }
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Reconciliation check failed.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsReconciling(false);
    }
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">Search Read-Model Maintenance</h1>
          <p className="text-sm text-muted mt-1">
            Administrative utilities for zero-downtime reindexing and consistency verification between PostgreSQL and Elasticsearch.
          </p>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-6">{error}</Alert>}

      <div className="grid grid-cols-2 gap-6 mb-6">
        {/* Reindexing Section */}
        <Card
          title="Search Index Rebuild"
          subtitle="Re-indexes all catalog products from PostgreSQL into a fresh versioned index and swaps alias atomically."
        >
          <p className="text-xs text-muted mb-4">
            Zero-downtime operation. Existing queries continue serving while the target index builds in chunks.
          </p>

          {reindexResult && (
            <div className="card p-3 bg-surface-subtle mb-4 text-xs font-mono">
              <div><strong>Status:</strong> {reindexResult.status}</div>
              <div><strong>Total Documents Indexed:</strong> {reindexResult.total_indexed}</div>
              {reindexResult.index_name && <div><strong>Index:</strong> {reindexResult.index_name}</div>}
              {reindexResult.alias && <div><strong>Alias:</strong> {reindexResult.alias}</div>}
            </div>
          )}

          <Button variant="primary" onClick={handleReindex} isLoading={isReindexing}>
            <RefreshCw size={14} className="mr-1" />
            <span>Rebuild Tenant Index</span>
          </Button>
        </Card>

        {/* Reconciliation Section */}
        <Card
          title="Read Model Reconciliation"
          subtitle="Compares PostgreSQL (source of truth) with Elasticsearch read models to detect drift or lost messages."
        >
          <div className="flex items-center gap-2 mb-4">
            <input
              type="checkbox"
              id="auto-repair"
              checked={autoRepair}
              onChange={(e) => setAutoRepair(e.target.checked)}
            />
            <label htmlFor="auto-repair" className="text-xs font-medium cursor-pointer">
              Auto-repair detected discrepancies in Elasticsearch
            </label>
          </div>

          {reconcileResult && (
            <div className="card p-3 bg-surface-subtle mb-4 text-xs font-mono space-y-1">
              <div><strong>Status:</strong> {reconcileResult.status}</div>
              <div><strong>PostgreSQL Authoritative Count:</strong> {reconcileResult.total_authoritative}</div>
              <div><strong>Elasticsearch Count:</strong> {reconcileResult.total_search}</div>
              <div><strong>Matched Documents:</strong> {reconcileResult.matched}</div>
              <div>
                <strong>Discrepancies:</strong>{' '}
                <span className={reconcileResult.discrepancies.length > 0 ? 'text-danger font-bold' : 'text-success'}>
                  {reconcileResult.discrepancies.length}
                </span>
              </div>
              {reconcileResult.repaired_count > 0 && (
                <div className="text-success font-bold">
                  Repaired Discrepancies: {reconcileResult.repaired_count}
                </div>
              )}
            </div>
          )}

          <Button variant="secondary" onClick={handleReconcile} isLoading={isReconciling}>
            <Wrench size={14} className="mr-1" />
            <span>Run Consistency Audit</span>
          </Button>
        </Card>
      </div>

      {/* Discrepancies Table */}
      {reconcileResult && reconcileResult.discrepancies.length > 0 && (
        <Card title="Discovered Discrepancies" subtitle="Mismatched documents requiring re-indexing">
          <div className="table-container border-0">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Document ID</th>
                  <th>Type</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {reconcileResult.discrepancies.map((d, i) => (
                  <tr key={i}>
                    <td className="font-mono text-xs">{d.document_id}</td>
                    <td className="text-xs font-bold text-danger">{d.discrepancy_type}</td>
                    <td className="text-xs font-mono">{JSON.stringify(d.details)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
};
