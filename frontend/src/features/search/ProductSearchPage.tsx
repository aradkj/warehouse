import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { Boxes, Search, Sparkles } from 'lucide-react';
import { searchApi } from '../../api/endpoints/search';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { ProductSearchResultResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';

export const ProductSearchPage: React.FC = () => {
  const { showToast } = useToast();

  const [query, setQuery] = useState('');
  const [skuCode, setSkuCode] = useState('');
  const [status, setStatus] = useState('');
  const [results, setResults] = useState<ProductSearchResultResponse[]>([]);
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
      const data = await searchApi.searchProducts({
        q: query.trim() || null,
        sku: skuCode.trim() || null,
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
          <h1 className="text-2xl font-bold">Search Catalog</h1>
          <p className="text-sm text-muted mt-1">
            Elasticsearch-backed full-text and filtered search projection for products and SKUs.
          </p>
        </div>
      </div>

      <Card className="mb-6">
        <form onSubmit={handleSearch}>
          <div className="grid grid-cols-3 gap-4 mb-4">
            <div className="col-span-2">
              <Input
                label="Full-text Query"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search across product name, description, and keywords..."
              />
            </div>
            <div>
              <Input
                label="SKU Code Filter"
                value={skuCode}
                onChange={(e) => setSkuCode(e.target.value)}
                placeholder="e.g. KB-ERG-01"
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
                style={{ width: '140px' }}
              >
                <option value="">All Statuses</option>
                <option value="DRAFT">DRAFT</option>
                <option value="ACTIVE">ACTIVE</option>
                <option value="ARCHIVED">ARCHIVED</option>
              </select>
            </div>

            <Button variant="primary" type="submit" isLoading={isLoading}>
              <Search size={14} className="mr-1" />
              <span>Search Products</span>
            </Button>
          </div>
        </form>
      </Card>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      {isLoading ? (
        <div className="text-center py-12 text-muted">Executing search query...</div>
      ) : hasSearched && results.length === 0 ? (
        <Card className="text-center py-10">
          <Boxes size={40} className="mx-auto text-muted mb-2" />
          <h2 className="text-base font-semibold mb-1">No Matching Products</h2>
          <p className="text-sm text-muted">
            No products matched your search criteria. Note: newly created products may take a moment to project to the search index.
          </p>
        </Card>
      ) : results.length > 0 ? (
        <div>
          <div className="text-xs text-muted mb-3">
            Found <strong className="text-main">{total}</strong> result(s)
          </div>

          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Product</th>
                  <th>Slug</th>
                  <th>Status</th>
                  <th>SKUs</th>
                  <th>Score</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {results.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Link to={`/catalog/products/${item.id}`} className="font-semibold text-primary hover:underline">
                        {item.name}
                      </Link>
                      {item.description && (
                        <div className="text-xs text-muted truncate max-w-sm">{item.description}</div>
                      )}
                    </td>
                    <td className="font-mono text-xs">{item.slug}</td>
                    <td>
                      <Badge variant={item.status === 'ACTIVE' ? 'success' : 'neutral'}>
                        {item.status}
                      </Badge>
                    </td>
                    <td className="text-xs font-mono">
                      {item.skus.map((s) => s.sku_code).join(', ') || 'No SKUs'}
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
                      <Link to={`/catalog/products/${item.id}`}>
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
