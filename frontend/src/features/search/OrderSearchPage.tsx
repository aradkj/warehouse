import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { ClipboardList, Search, Sparkles } from 'lucide-react';
import { searchApi } from '../../api/endpoints/search';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { OrderSearchResultResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';

export const OrderSearchPage: React.FC = () => {
  const { showToast } = useToast();

  const [query, setQuery] = useState('');
  const [customer, setCustomer] = useState('');
  const [sku, setSku] = useState('');
  const [status, setStatus] = useState('');
  const [results, setResults] = useState<OrderSearchResultResponse[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setIsLoading(true);
    setError(null);
    setHasSearched(true);

    try {
      const data = await searchApi.searchOrders({
        q: query.trim() || null,
        customer: customer.trim() || null,
        sku: sku.trim() || null,
        status: status || null,
        page_size: 25,
      });
      setResults(data.items);
      setTotal(data.total);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Search query failed.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">Search Orders</h1>
          <p className="text-sm text-muted mt-1">
            Elasticsearch query across customer reference, order ID, SKUs, and dates.
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
                placeholder="Query across reference, notes..."
              />
            </div>
            <div>
              <Input
                label="Customer Reference"
                value={customer}
                onChange={(e) => setCustomer(e.target.value)}
                placeholder="PO-2026-..."
              />
            </div>
            <div>
              <Input
                label="SKU Code Filter"
                value={sku}
                onChange={(e) => setSku(e.target.value)}
                placeholder="e.g. SKU-123"
              />
            </div>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-xs font-semibold text-muted uppercase">Status:</span>
              <select
                className="form-select text-xs py-1"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                style={{ width: '150px' }}
              >
                <option value="">All Statuses</option>
                <option value="PENDING">PENDING</option>
                <option value="CONFIRMED">CONFIRMED</option>
                <option value="CANCELLED">CANCELLED</option>
              </select>
            </div>

            <Button variant="primary" type="submit" isLoading={isLoading}>
              <Search size={14} className="mr-1" />
              <span>Search Orders</span>
            </Button>
          </div>
        </form>
      </Card>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      {isLoading ? (
        <div className="text-center py-12 text-muted">Searching orders...</div>
      ) : hasSearched && results.length === 0 ? (
        <Card className="text-center py-10">
          <ClipboardList size={40} className="mx-auto text-muted mb-2" />
          <h2 className="text-base font-semibold mb-1">No Orders Found</h2>
          <p className="text-sm text-muted">
            No orders match the current search criteria.
          </p>
        </Card>
      ) : results.length > 0 ? (
        <div>
          <div className="text-xs text-muted mb-3">
            Found <strong className="text-main">{total}</strong> order(s)
          </div>

          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Order ID</th>
                  <th>Customer Reference</th>
                  <th>Status</th>
                  <th>Total</th>
                  <th>SKUs</th>
                  <th>Score</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {results.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Link to={`/orders/${item.order_id}`} className="font-mono text-xs font-bold text-primary hover:underline">
                        {item.order_id.slice(0, 8)}...
                      </Link>
                    </td>
                    <td className="text-xs font-medium">
                      {item.customer_reference || <span className="text-muted">—</span>}
                    </td>
                    <td>
                      <Badge variant={item.status === 'CONFIRMED' ? 'success' : item.status === 'PENDING' ? 'warning' : 'neutral'}>
                        {item.status}
                      </Badge>
                    </td>
                    <td className="text-xs font-medium">
                      {(item.total_minor / 100).toFixed(2)} {item.currency}
                    </td>
                    <td className="text-xs font-mono">
                      {item.skus.map((s) => s.sku_code).join(', ')}
                    </td>
                    <td className="text-xs text-muted font-mono">
                      {item.score ? (
                        <span className="flex items-center gap-1">
                          <Sparkles size={12} className="text-warning" />
                          <span>{item.score.toFixed(2)}</span>
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>
                      <Link to={`/orders/${item.order_id}`}>
                        <Button size="sm" variant="secondary">
                          View
                        </Button>
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
};
