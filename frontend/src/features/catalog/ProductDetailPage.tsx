import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  Archive,
  ArrowLeft,
  CheckCircle,
  Coins,
  Edit2,
  Package,
  Plus,
  RefreshCw,
  Tag,
  ToggleLeft,
  ToggleRight,
} from 'lucide-react';
import { catalogApi } from '../../api/endpoints/catalog';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { ProductResponse, SkuResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';

export const ProductDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { showToast } = useToast();

  const [product, setProduct] = useState<ProductResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modals state
  const [isReviseOpen, setIsReviseOpen] = useState(false);
  const [reviseName, setReviseName] = useState('');
  const [reviseDesc, setReviseDesc] = useState('');
  const [isRevising, setIsRevising] = useState(false);

  const [isAddSkuOpen, setIsAddSkuOpen] = useState(false);
  const [skuCode, setSkuCode] = useState('');
  const [skuName, setSkuName] = useState('');
  const [skuPriceMinor, setSkuPriceMinor] = useState<number>(0);
  const [skuCurrency, setSkuCurrency] = useState('GBP');
  const [isAddingSku, setIsAddingSku] = useState(false);

  const [isPriceModalOpen, setIsPriceModalOpen] = useState(false);
  const [selectedSku, setSelectedSku] = useState<SkuResponse | null>(null);
  const [newPriceMinor, setNewPriceMinor] = useState<number>(0);
  const [newCurrency, setNewCurrency] = useState('GBP');
  const [isUpdatingPrice, setIsUpdatingPrice] = useState(false);

  const fetchProduct = async () => {
    if (!id) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await catalogApi.getProduct(id);
      setProduct(data);
      setReviseName(data.name);
      setReviseDesc(data.description || '');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to fetch product details.';
      setError(msg);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchProduct();
  }, [id]);

  const handleActivate = async () => {
    if (!id) return;
    try {
      const updated = await catalogApi.activateProduct(id);
      setProduct(updated);
      showToast('Product activated for sale.', 'success');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to activate product.';
      showToast(msg, 'error');
    }
  };

  const handleArchive = async () => {
    if (!id) return;
    if (!window.confirm('Are you sure you want to archive this product? Archived products cannot be sold.')) {
      return;
    }
    try {
      const updated = await catalogApi.archiveProduct(id);
      setProduct(updated);
      showToast('Product archived.', 'info');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to archive product.';
      showToast(msg, 'error');
    }
  };

  const handleReviseSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id) return;
    setIsRevising(true);
    try {
      const updated = await catalogApi.reviseProduct(id, {
        name: reviseName.trim() || null,
        description: reviseDesc.trim() || null,
      });
      setProduct(updated);
      setIsReviseOpen(false);
      showToast('Product revised successfully.', 'success');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to revise product.';
      showToast(msg, 'error');
    } finally {
      setIsRevising(false);
    }
  };

  const handleAddSkuSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id) return;

    if (!skuCode.trim() || !skuName.trim()) {
      showToast('SKU Code and Name are required.', 'error');
      return;
    }

    setIsAddingSku(true);
    try {
      await catalogApi.addSkuToProduct(id, {
        code: skuCode.trim().toUpperCase(),
        name: skuName.trim(),
        unit_price_minor: Number(skuPriceMinor),
        currency: skuCurrency.toUpperCase(),
        attributes: {},
      });
      showToast(`SKU ${skuCode} created successfully.`, 'success');
      setIsAddSkuOpen(false);
      setSkuCode('');
      setSkuName('');
      setSkuPriceMinor(0);
      await fetchProduct();
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to add SKU.';
      showToast(msg, 'error');
    } finally {
      setIsAddingSku(false);
    }
  };

  const handleToggleSkuStatus = async (sku: SkuResponse) => {
    try {
      if (sku.status === 'ACTIVE') {
        await catalogApi.deactivateSku(sku.id);
        showToast(`SKU ${sku.code} deactivated.`, 'info');
      } else {
        await catalogApi.activateSku(sku.id);
        showToast(`SKU ${sku.code} returned to active sale.`, 'success');
      }
      await fetchProduct();
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to change SKU status.';
      showToast(msg, 'error');
    }
  };

  const handlePriceSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedSku) return;

    setIsUpdatingPrice(true);
    try {
      await catalogApi.changeSkuPrice(selectedSku.id, {
        unit_price_minor: Number(newPriceMinor),
        currency: newCurrency.toUpperCase(),
      });
      showToast(`Updated price for SKU ${selectedSku.code}.`, 'success');
      setIsPriceModalOpen(false);
      setSelectedSku(null);
      await fetchProduct();
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to update SKU price.';
      showToast(msg, 'error');
    } finally {
      setIsUpdatingPrice(false);
    }
  };

  if (isLoading) {
    return <div className="text-center py-12 text-muted">Loading product details...</div>;
  }

  if (error || !product) {
    return (
      <div>
        <Alert variant="danger" className="mb-4">
          {error || 'Product not found.'}
        </Alert>
        <Link to="/catalog/products">
          <Button variant="secondary" size="sm">
            <ArrowLeft size={14} className="mr-1" />
            <span>Back to Catalog</span>
          </Button>
        </Link>
      </div>
    );
  }

  const badgeVariant =
    product.status === 'ACTIVE'
      ? 'success'
      : product.status === 'DRAFT'
        ? 'warning'
        : 'neutral';

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <Link to="/catalog/products" className="text-xs text-muted hover:text-main flex items-center">
          <ArrowLeft size={14} className="mr-1" />
          <span>Back to Products</span>
        </Link>
      </div>

      <div className="page-header">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{product.name}</h1>
            <Badge variant={badgeVariant}>{product.status}</Badge>
          </div>
          <div className="text-xs text-muted font-mono mt-1">
            Slug: {product.slug} • ID: {product.id} • Version: {product.version}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={() => setIsReviseOpen(true)}>
            <Edit2 size={14} className="mr-1" />
            <span>Revise</span>
          </Button>

          {product.status === 'DRAFT' && (
            <Button size="sm" variant="primary" onClick={handleActivate}>
              <CheckCircle size={14} className="mr-1" />
              <span>Activate Product</span>
            </Button>
          )}

          {product.status !== 'ARCHIVED' && (
            <Button size="sm" variant="danger" onClick={handleArchive}>
              <Archive size={14} className="mr-1" />
              <span>Archive</span>
            </Button>
          )}
        </div>
      </div>

      {/* Product Details Overview */}
      <div className="grid grid-cols-3 gap-6 mb-6">
        <Card title="Product Attributes" className="col-span-2 mb-0">
          <div className="mb-4">
            <div className="text-xs font-semibold text-muted mb-1">Description</div>
            <p className="text-sm text-secondary">
              {product.description || 'No description provided.'}
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4 pt-4 border-t text-xs">
            <div>
              <span className="text-muted">Created: </span>
              <span className="font-medium">{new Date(product.created_at).toLocaleString()}</span>
            </div>
            <div>
              <span className="text-muted">Last Updated: </span>
              <span className="font-medium">{new Date(product.updated_at).toLocaleString()}</span>
            </div>
          </div>
        </Card>

        <Card title="SKU Summary" className="mb-0">
          <div className="text-center py-4">
            <div className="text-3xl font-bold text-primary">{product.skus.length}</div>
            <div className="text-xs text-muted mt-1">Stock Keeping Units</div>
          </div>
          <div className="pt-4 border-t text-xs text-muted">
            Each SKU represents a sellable variant with its own list price and inventory ledger.
          </div>
        </Card>
      </div>

      {/* SKUs Section */}
      <Card
        title="Product SKUs"
        subtitle="Manage product codes, active status, and unit prices"
        action={
          <Button size="sm" variant="primary" onClick={() => setIsAddSkuOpen(true)}>
            <Plus size={14} className="mr-1" />
            <span>Add SKU</span>
          </Button>
        }
      >
        {product.skus.length === 0 ? (
          <div className="text-center py-8 text-muted text-sm">
            No SKUs created for this product yet. Click "Add SKU" to define a stock keeping unit.
          </div>
        ) : (
          <div className="table-container border-0">
            <table className="data-table">
              <thead>
                <tr>
                  <th>SKU Code</th>
                  <th>SKU Name</th>
                  <th>List Price</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {product.skus.map((sku) => {
                  const skuBadgeVariant = sku.status === 'ACTIVE' ? 'success' : 'neutral';
                  const formattedPrice = `${(sku.unit_price.amount_minor / 100).toFixed(2)} ${sku.unit_price.currency}`;

                  return (
                    <tr key={sku.id}>
                      <td className="font-mono text-xs font-bold">{sku.code}</td>
                      <td className="text-xs">{sku.name}</td>
                      <td className="text-xs font-medium">
                        <span className="flex items-center gap-1">
                          <Coins size={14} className="text-muted" />
                          <span>{formattedPrice}</span>
                        </span>
                      </td>
                      <td>
                        <Badge variant={skuBadgeVariant}>{sku.status}</Badge>
                      </td>
                      <td>
                        <div className="flex items-center gap-2">
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => {
                              setSelectedSku(sku);
                              setNewPriceMinor(sku.unit_price.amount_minor);
                              setNewCurrency(sku.unit_price.currency);
                              setIsPriceModalOpen(true);
                            }}
                          >
                            Change Price
                          </Button>
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => handleToggleSkuStatus(sku)}
                            title={sku.status === 'ACTIVE' ? 'Deactivate SKU' : 'Activate SKU'}
                          >
                            {sku.status === 'ACTIVE' ? 'Deactivate' : 'Activate'}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Revise Product Modal */}
      <Modal
        isOpen={isReviseOpen}
        onClose={() => setIsReviseOpen(false)}
        title="Revise Product"
        description="Update display name and description."
      >
        <form onSubmit={handleReviseSubmit}>
          <Input
            label="Product Name"
            value={reviseName}
            onChange={(e) => setReviseName(e.target.value)}
            required
          />
          <div className="form-group">
            <label className="form-label">Description</label>
            <textarea
              className="form-textarea"
              rows={4}
              value={reviseDesc}
              onChange={(e) => setReviseDesc(e.target.value)}
              maxLength={4000}
            />
          </div>
          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={() => setIsReviseOpen(false)} disabled={isRevising}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" isLoading={isRevising}>
              Save Changes
            </Button>
          </div>
        </form>
      </Modal>

      {/* Add SKU Modal */}
      <Modal
        isOpen={isAddSkuOpen}
        onClose={() => setIsAddSkuOpen(false)}
        title="Add SKU to Product"
        description="Create a unique SKU code with price."
      >
        <form onSubmit={handleAddSkuSubmit}>
          <Input
            label="SKU Code"
            value={skuCode}
            onChange={(e) => setSkuCode(e.target.value.toUpperCase())}
            placeholder="KB-ERG-01"
            helperText="Alphanumeric code, underscores, dots, and hyphens."
            required
          />
          <Input
            label="SKU Name / Variant"
            value={skuName}
            onChange={(e) => setSkuName(e.target.value)}
            placeholder="Black - US Layout"
            required
          />
          <div className="grid grid-cols-2 gap-4">
            <Input
              label="Price (Minor units, e.g. 1999 = £19.99)"
              type="number"
              min="0"
              value={skuPriceMinor}
              onChange={(e) => setSkuPriceMinor(Number(e.target.value))}
              required
            />
            <Input
              label="Currency (ISO 3-letter)"
              value={skuCurrency}
              onChange={(e) => setSkuCurrency(e.target.value.toUpperCase())}
              maxLength={3}
              pattern="^[A-Za-z]{3}$"
              required
            />
          </div>
          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={() => setIsAddSkuOpen(false)} disabled={isAddingSku}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" isLoading={isAddingSku}>
              Create SKU
            </Button>
          </div>
        </form>
      </Modal>

      {/* Change Price Modal */}
      <Modal
        isOpen={isPriceModalOpen}
        onClose={() => {
          setIsPriceModalOpen(false);
          setSelectedSku(null);
        }}
        title={`Change Price for ${selectedSku?.code}`}
        description="Update SKU list price in minor units."
      >
        <form onSubmit={handlePriceSubmit}>
          <div className="grid grid-cols-2 gap-4">
            <Input
              label="Unit Price (Minor units)"
              type="number"
              min="0"
              value={newPriceMinor}
              onChange={(e) => setNewPriceMinor(Number(e.target.value))}
              required
            />
            <Input
              label="Currency"
              value={newCurrency}
              onChange={(e) => setNewCurrency(e.target.value.toUpperCase())}
              maxLength={3}
              required
            />
          </div>
          <div className="text-xs text-muted mt-2">
            Formatted display: {((newPriceMinor || 0) / 100).toFixed(2)} {newCurrency}
          </div>
          <div className="flex justify-end gap-2 mt-6">
            <Button
              variant="secondary"
              onClick={() => {
                setIsPriceModalOpen(false);
                setSelectedSku(null);
              }}
              disabled={isUpdatingPrice}
            >
              Cancel
            </Button>
            <Button variant="primary" type="submit" isLoading={isUpdatingPrice}>
              Update Price
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
