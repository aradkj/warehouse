import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Boxes, Filter, Plus, RefreshCw } from 'lucide-react';
import { catalogApi } from '../../api/endpoints/catalog';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { ProductResponse, ProductStatus } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { EmptyState } from '../../components/feedback/EmptyState';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { CursorPagination } from '../../components/ui/Pagination';
import { Select } from '../../components/ui/Select';
import { CreateProductModal } from './CreateProductModal';

export const ProductListPage: React.FC = () => {
  const { showToast } = useToast();
  const [products, setProducts] = useState<ProductResponse[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [currentCursor, setCurrentCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<(string | null)[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  const fetchProducts = async (cursor: string | null = null) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await catalogApi.listProducts({
        limit: 20,
        cursor,
        status: (statusFilter as ProductStatus) || null,
      });
      setProducts(data.items);
      setNextCursor(data.next_cursor);
      setCurrentCursor(cursor);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to fetch catalog products.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    setCursorHistory([]);
    fetchProducts(null);
  }, [statusFilter]);

  const handleNextPage = () => {
    if (!nextCursor) return;
    setCursorHistory((prev) => [...prev, currentCursor]);
    fetchProducts(nextCursor);
  };

  const handleResetPagination = () => {
    setCursorHistory([]);
    fetchProducts(null);
  };

  const handleProductCreated = (newProduct: ProductResponse) => {
    setProducts((prev) => [newProduct, ...prev]);
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">Catalog & Products</h1>
          <p className="text-sm text-muted mt-1">
            Authoritative catalog items and SKU definitions with pricing.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => fetchProducts(currentCursor)}
            isLoading={isLoading}
          >
            <RefreshCw size={14} className="mr-1" />
            <span>Refresh</span>
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={() => setIsCreateOpen(true)}
          >
            <Plus size={14} className="mr-1" />
            <span>New Product</span>
          </Button>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      <Card className="mb-6">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-3">
            <Filter size={16} className="text-muted" />
            <span className="text-xs font-semibold text-muted uppercase">Filters</span>
            <div style={{ width: '160px' }}>
              <select
                className="form-select text-xs py-1"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="">All Statuses</option>
                <option value="DRAFT">DRAFT</option>
                <option value="ACTIVE">ACTIVE</option>
                <option value="ARCHIVED">ARCHIVED</option>
              </select>
            </div>
          </div>
          <div className="text-xs text-muted">
            Showing <strong className="text-main">{products.length}</strong> items
          </div>
        </div>
      </Card>

      {isLoading ? (
        <div className="text-center py-12 text-muted">Loading catalog products...</div>
      ) : products.length === 0 ? (
        <Card>
          <EmptyState
            title="No Products Found"
            description={
              statusFilter
                ? `No products currently match status "${statusFilter}". Try clearing your filters.`
                : 'Your catalog has no products registered yet. Create your first product.'
            }
            actionLabel="Create Product"
            onAction={() => setIsCreateOpen(true)}
            icon={<Boxes size={48} strokeWidth={1.5} />}
          />
        </Card>
      ) : (
        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>Product Name</th>
                <th>Slug</th>
                <th>Status</th>
                <th>SKUs</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p) => {
                const badgeVariant =
                  p.status === 'ACTIVE'
                    ? 'success'
                    : p.status === 'DRAFT'
                      ? 'warning'
                      : 'neutral';

                return (
                  <tr key={p.id}>
                    <td>
                      <Link to={`/catalog/products/${p.id}`} className="font-semibold text-primary hover:underline">
                        {p.name}
                      </Link>
                      {p.description && (
                        <div className="text-xs text-muted truncate max-w-xs">{p.description}</div>
                      )}
                    </td>
                    <td className="font-mono text-xs">{p.slug}</td>
                    <td>
                      <Badge variant={badgeVariant}>{p.status}</Badge>
                    </td>
                    <td className="text-xs">
                      <span className="badge badge-neutral">{p.skus.length} SKU(s)</span>
                    </td>
                    <td className="text-xs text-muted">
                      {new Date(p.created_at).toLocaleDateString()}
                    </td>
                    <td>
                      <Link to={`/catalog/products/${p.id}`}>
                        <Button size="sm" variant="secondary">
                          Manage
                        </Button>
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <CursorPagination
            hasMore={!!nextCursor}
            onNext={handleNextPage}
            onReset={cursorHistory.length > 0 ? handleResetPagination : undefined}
            isLoading={isLoading}
          />
        </div>
      )}

      <CreateProductModal
        isOpen={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        onCreated={handleProductCreated}
      />
    </div>
  );
};
