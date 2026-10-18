import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  Archive,
  ArrowLeft,
  CheckCircle,
  Coins,
  Edit2,
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
import { useTranslation } from '../../i18n';
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
  const { t, formatDate, formatNumber } = useTranslation();

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
    return <div className="text-center py-12 text-muted">{t('common.loading')}</div>;
  }

  if (error || !product) {
    return (
      <div>
        <Alert variant="danger" className="mb-4">
          {error || t('errors.notFound')}
        </Alert>
        <Link to="/catalog/products">
          <Button variant="secondary" size="sm">
            <ArrowLeft size={14} className="mr-1 rtl-flip" />
            <span>{t('common.back')}</span>
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
          <ArrowLeft size={14} className="mr-1 rtl-flip" />
          <span>{t('catalog.title')}</span>
        </Link>
      </div>

      <div className="page-header">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{product.name}</h1>
            <Badge variant={badgeVariant}>{product.status}</Badge>
          </div>
          <div className="text-xs text-muted font-mono mt-1">
            Product UUID: {product.id} • Slug: {product.slug} • Version: {product.version}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={fetchProduct}>
            <RefreshCw size={14} className="mr-1" />
            <span>{t('common.refresh')}</span>
          </Button>

          <Button size="sm" variant="secondary" onClick={() => setIsReviseOpen(true)}>
            <Edit2 size={14} className="mr-1" />
            <span>{t('common.edit')}</span>
          </Button>

          {product.status === 'DRAFT' && (
            <Button size="sm" variant="primary" onClick={handleActivate}>
              <CheckCircle size={14} className="mr-1" />
              <span>Activate for Sale</span>
            </Button>
          )}

          {product.status === 'ACTIVE' && (
            <Button size="sm" variant="danger" onClick={handleArchive}>
              <Archive size={14} className="mr-1" />
              <span>Archive</span>
            </Button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-6 mb-6">
        <Card title={t('catalog.detailTitle')} className="mb-0">
          <div className="space-y-3 text-xs">
            <div>
              <span className="text-muted block">{t('catalog.name')}:</span>
              <strong className="text-sm font-semibold">{product.name}</strong>
            </div>
            <div>
              <span className="text-muted block">Slug:</span>
              <span className="font-mono">{product.slug}</span>
            </div>
            <div>
              <span className="text-muted block">{t('common.description')}:</span>
              <p className="mt-0.5 text-secondary">{product.description || 'No description provided.'}</p>
            </div>
            <div>
              <span className="text-muted block">{t('common.created_at')}:</span>
              <span className="font-mono">{formatDate(product.created_at)}</span>
            </div>
          </div>
        </Card>

        {/* SKUs Table */}
        <Card
          title="Stock Keeping Units (SKUs)"
          subtitle="Physical variants and list pricing for orders"
          action={
            <Button size="sm" variant="primary" onClick={() => setIsAddSkuOpen(true)}>
              <Plus size={14} className="mr-1" />
              <span>Add SKU</span>
            </Button>
          }
          className="col-span-2 mb-0"
        >
          {product.skus.length === 0 ? (
            <div className="text-center py-8 text-muted text-sm">
              No SKUs defined for this product yet. Add at least one SKU to enable sales.
            </div>
          ) : (
            <div className="table-container border-0">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t('catalog.sku')}</th>
                    <th>{t('catalog.name')}</th>
                    <th>{t('catalog.unitPrice')}</th>
                    <th>{t('catalog.status')}</th>
                    <th>{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {product.skus.map((sku) => {
                    const skuBadgeVariant = sku.status === 'ACTIVE' ? 'success' : 'neutral';
                    const priceFormatted = sku.unit_price
                      ? `${formatNumber(sku.unit_price.amount_minor / 100, { minimumFractionDigits: 2 })} ${sku.unit_price.currency}`
                      : 'Not priced';

                    return (
                      <tr key={sku.id}>
                        <td className="font-mono text-xs font-bold text-primary">
                          {sku.code}
                        </td>
                        <td className="text-xs">{sku.name}</td>
                        <td className="font-mono text-xs font-semibold">
                          {priceFormatted}
                        </td>
                        <td>
                          <Badge variant={skuBadgeVariant}>{sku.status}</Badge>
                        </td>
                        <td>
                          <div className="flex items-center gap-1">
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => {
                                setSelectedSku(sku);
                                setNewPriceMinor(sku.unit_price ? sku.unit_price.amount_minor : 0);
                                setNewCurrency(sku.unit_price ? sku.unit_price.currency : 'GBP');
                                setIsPriceModalOpen(true);
                              }}
                              title="Update list price"
                            >
                              <Coins size={12} className="mr-1" />
                              <span>Price</span>
                            </Button>

                            <Button
                              size="sm"
                              variant={sku.status === 'ACTIVE' ? 'secondary' : 'primary'}
                              onClick={() => handleToggleSkuStatus(sku)}
                              title={sku.status === 'ACTIVE' ? 'Deactivate SKU' : 'Activate SKU'}
                            >
                              {sku.status === 'ACTIVE' ? <ToggleRight size={14} /> : <ToggleLeft size={14} />}
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
      </div>

      {/* Revise Modal */}
      <Modal
        isOpen={isReviseOpen}
        onClose={() => setIsReviseOpen(false)}
        title="Revise Product"
        description="Update product metadata and specifications."
      >
        <form onSubmit={handleReviseSubmit}>
          <Input
            label={t('catalog.nameInput')}
            value={reviseName}
            onChange={(e) => setReviseName(e.target.value)}
            required
          />
          <div className="form-group">
            <label className="form-label">{t('catalog.descriptionInput')}</label>
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
              {t('common.cancel')}
            </Button>
            <Button variant="primary" type="submit" isLoading={isRevising}>
              {t('common.save')}
            </Button>
          </div>
        </form>
      </Modal>

      {/* Add SKU Modal */}
      <Modal
        isOpen={isAddSkuOpen}
        onClose={() => setIsAddSkuOpen(false)}
        title="Add Stock Keeping Unit (SKU)"
        description="Define an orderable SKU with distinct code and list price."
      >
        <form onSubmit={handleAddSkuSubmit}>
          <Input
            label={t('catalog.skuInput')}
            value={skuCode}
            onChange={(e) => setSkuCode(e.target.value.toUpperCase())}
            placeholder="e.g. KB-ERG-BLK"
            helperText="Uppercase alphanumeric stock code"
            required
          />
          <Input
            label="Variant / SKU Name"
            value={skuName}
            onChange={(e) => setSkuName(e.target.value)}
            placeholder="e.g. Midnight Black (ISO UK Layout)"
            required
          />
          <div className="grid grid-cols-2 gap-4">
            <Input
              label="Price (Minor units / pence / cents)"
              type="number"
              min="0"
              value={skuPriceMinor}
              onChange={(e) => setSkuPriceMinor(Number(e.target.value))}
              helperText={`Equivalent to ${(skuPriceMinor / 100).toFixed(2)} ${skuCurrency}`}
              required
            />
            <Input
              label={t('catalog.currencyInput')}
              value={skuCurrency}
              onChange={(e) => setSkuCurrency(e.target.value.toUpperCase())}
              placeholder="GBP"
              maxLength={3}
              required
            />
          </div>
          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={() => setIsAddSkuOpen(false)} disabled={isAddingSku}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" type="submit" isLoading={isAddingSku}>
              {t('common.create')}
            </Button>
          </div>
        </form>
      </Modal>

      {/* Update Price Modal */}
      <Modal
        isOpen={isPriceModalOpen}
        onClose={() => setIsPriceModalOpen(false)}
        title={`Change List Price: ${selectedSku?.code}`}
        description="Authoritatively adjust the catalog unit price for this SKU."
      >
        <form onSubmit={handlePriceSubmit}>
          <div className="grid grid-cols-2 gap-4">
            <Input
              label="Unit Price (Minor units)"
              type="number"
              min="0"
              value={newPriceMinor}
              onChange={(e) => setNewPriceMinor(Number(e.target.value))}
              helperText={`Equivalent to ${(newPriceMinor / 100).toFixed(2)} ${newCurrency}`}
              required
            />
            <Input
              label={t('catalog.currencyInput')}
              value={newCurrency}
              onChange={(e) => setNewCurrency(e.target.value.toUpperCase())}
              placeholder="GBP"
              maxLength={3}
              required
            />
          </div>
          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={() => setIsPriceModalOpen(false)} disabled={isUpdatingPrice}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" type="submit" isLoading={isUpdatingPrice}>
              {t('common.save')}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
