import React, { useState } from 'react';
import { FileText, Search, Shield, ChevronDown, ChevronRight } from 'lucide-react';
import { searchApi } from '../../api/endpoints/search';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { AuditSearchResultResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';

export const AuditSearchPage: React.FC = () => {
  const { showToast } = useToast();

  const [query, setQuery] = useState('');
  const [action, setAction] = useState('');
  const [resourceType, setResourceType] = useState('');
  const [results, setResults] = useState<AuditSearchResultResponse[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setIsLoading(true);
    setError(null);
    setHasSearched(true);

    try {
      const data = await searchApi.searchAudit({
        q: query.trim() || null,
        action: action.trim() || null,
        resource_type: resourceType.trim() || null,
        page_size: 30,
      });
      setResults(data.items);
      setTotal(data.total);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Audit search query failed.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsLoading(false);
    }
  };

  const toggleDetails = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">Search Audit Trail</h1>
          <p className="text-sm text-muted mt-1">
            Search immutable compliance events across actor actions, resource modifications, and authorization decisions.
          </p>
        </div>
      </div>

      <Card className="mb-6">
        <form onSubmit={handleSearch}>
          <div className="grid grid-cols-3 gap-4 mb-4">
            <div>
              <Input
                label="Free-text Search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Actor ID, reason, or keyword..."
              />
            </div>
            <div>
              <Input
                label="Action Filter"
                value={action}
                onChange={(e) => setAction(e.target.value)}
                placeholder="e.g. order.create or product.activate"
              />
            </div>
            <div>
              <Input
                label="Resource Type Filter"
                value={resourceType}
                onChange={(e) => setResourceType(e.target.value)}
                placeholder="e.g. order, product, inventory"
              />
            </div>
          </div>

          <div className="flex justify-end">
            <Button variant="primary" type="submit" isLoading={isLoading}>
              <Search size={14} className="mr-1" />
              <span>Search Audit Logs</span>
            </Button>
          </div>
        </form>
      </Card>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      {isLoading ? (
        <div className="text-center py-12 text-muted">Searching audit events...</div>
      ) : hasSearched && results.length === 0 ? (
        <Card className="text-center py-10">
          <FileText size={40} className="mx-auto text-muted mb-2" />
          <h2 className="text-base font-semibold mb-1">No Audit Events Found</h2>
          <p className="text-sm text-muted">No audit events match your search query.</p>
        </Card>
      ) : results.length > 0 ? (
        <div>
          <div className="text-xs text-muted mb-3">
            Found <strong className="text-main">{total}</strong> event(s)
          </div>

          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: '30px' }} />
                  <th>Timestamp</th>
                  <th>Action</th>
                  <th>Resource</th>
                  <th>Actor ID</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {results.map((item) => {
                  const isExpanded = expandedId === item.id;
                  const decisionVariant = item.policy_decision === 'ALLOW' ? 'success' : 'danger';

                  return (
                    <React.Fragment key={item.id}>
                      <tr
                        onClick={() => toggleDetails(item.id)}
                        style={{ cursor: 'pointer' }}
                      >
                        <td className="text-muted">
                          {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        </td>
                        <td className="text-xs text-muted font-mono">
                          {new Date(item.occurred_at).toLocaleString()}
                        </td>
                        <td className="text-xs font-semibold text-main font-mono">{item.action}</td>
                        <td className="text-xs font-mono">
                          {item.resource_type}:{item.resource_id.slice(0, 8)}...
                        </td>
                        <td className="font-mono text-xs text-muted">{item.actor_id.slice(0, 8)}...</td>
                        <td>
                          <Badge variant={decisionVariant}>{item.policy_decision}</Badge>
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr>
                          <td colSpan={6} style={{ backgroundColor: 'var(--bg-surface-subtle)', padding: '1rem' }}>
                            <div className="text-xs font-mono space-y-2">
                              <div><strong>Event ID:</strong> {item.id}</div>
                              <div><strong>Decision Reason:</strong> {item.decision_reason}</div>
                              {item.correlation_id && <div><strong>Correlation ID:</strong> {item.correlation_id}</div>}
                              <div><strong>Payload / Details:</strong></div>
                              <pre
                                className="p-2 bg-white rounded border text-xs overflow-x-auto"
                                style={{ maxHeight: '200px' }}
                              >
                                {JSON.stringify(item.details, null, 2)}
                              </pre>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
};
