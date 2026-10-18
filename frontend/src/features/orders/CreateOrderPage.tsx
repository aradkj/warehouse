import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Plus, Trash2, ShoppingBag } from 'lucide-react';
import { catalogApi } from '../../api/endpoints/catalog';
import { ordersApi } from '../../api/endpoints/orders';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { ProductResponse, SkuResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { useTranslation } from '../../i18n';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';

interface LineItem {
  id: string; // client key
  skuId: string;
  quantity: number;
}

export const CreateOrderPage: React.FC = () => {
  const navigate = useNavigate();
  const { showToast } = useToast();
  const { t } = useTranslation();

  const [customerReference, setCustomerReference] = useState('');
  const [lines, setLines] = useState<LineItem[]>([
    { id: crypto.randomUUID(), skuId: '', quantity: 1 },
  ]);
  const [products, setProducts] = useState<ProductResponse[]>([]);
  const [idempotencyKey, setIdempotencyKey] = useState<string>(crypto.randomUUID());
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Load catalog products to populate SKU options
    catalogApi.listProducts({ limit: 100, status: 'ACTIVE' }).then((res) => {
      setProducts(res.items);
    }).catch(() => {
      // Ignore
    });
  }, []);

  const handleAddLine = () => {
    setLines((prev) => [...prev, { id: crypto.randomUUID(), skuId: '', quantity: 1 }]);
  };

  const handleRemoveLine = (id: string) => {
    if (lines.length <= 1) return;
    setLines((prev) => prev.filter((l) => l.id !== id));
  };

  const handleLineChange = (id: string, field: 'skuId' | 'quantity', value: any) => {
    setLines((prev) =>
      prev.map((l) => (l.id === id ? { ...l, [field]: value } : l))
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // Validate lines
    for (const line of lines) {
      if (!line.skuId.trim()) {
        setError(t('orders.skuRequiredError'));
        return;
      }
      if (line.quantity <= 0) {
        setError(t('orders.quantityError'));
        return;
      }
    }

    setIsSubmitting(true);
    try {
      const order = await ordersApi.createOrder(
        {
          lines: lines.map((l) => ({ sku_id: l.skuId.trim(), quantity: Number(l.quantity) })),
          customer_reference: customerReference.trim() || null,
        },
        idempotencyKey
      );

      showToast(`Order ${order.id.slice(0, 8)} created successfully.`, 'success');
      navigate(`/orders/${order.id}`);
    } catch (err: unknown) {
      const msg = formatErrorMessage(err);
      setError(msg);
      // Notice: Do NOT reset idempotencyKey here. If user retries after a network blip,
      // the exact same idempotency key is submitted per requirements.
    } finally {
      setIsSubmitting(false);
    }
  };

  // Flatten active SKUs from catalog products
  const availableSkus: SkuResponse[] = products.flatMap((p) =>
    p.skus.filter((s) => s.status === 'ACTIVE')
  );

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto' }}>
      <div className="flex items-center gap-2 mb-4">
        <Button size="sm" variant="secondary" onClick={() => navigate('/orders')}>
          <ArrowLeft size={14} className="mr-1 rtl-flip" />
          <span>{t('orders.backToOrders')}</span>
        </Button>
      </div>

      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">{t('orders.createTitle')}</h1>
          <p className="text-sm text-muted mt-1">
            {t('orders.createSubtitle')}
          </p>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-6">{error}</Alert>}

      <form onSubmit={handleSubmit} noValidate>
        <Card title={t('orders.orderHeader')} className="mb-6">
          <Input
            label={t('orders.customerRefLabel')}
            value={customerReference}
            onChange={(e) => setCustomerReference(e.target.value)}
            placeholder={t('orders.customerRefPlaceholder')}
            maxLength={128}
          />

          <div className="text-xs text-muted font-mono mt-2 pt-2 border-t">
            {t('orders.idempotencyKey')} <span className="font-semibold text-main">{idempotencyKey}</span>
          </div>
        </Card>

        <Card
          title={t('orders.lineItemsTitle')}
          action={
            <Button size="sm" variant="secondary" type="button" onClick={handleAddLine}>
              <Plus size={14} className="mr-1" />
              <span>{t('orders.addItem')}</span>
            </Button>
          }
          className="mb-6"
        >
          <div className="table-container border-0">
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: '60%' }}>{t('orders.skuSelection')}</th>
                  <th style={{ width: '25%' }}>{t('orders.quantityLabel')}</th>
                  <th style={{ width: '15%' }}>{t('common.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {lines.map((line) => (
                  <tr key={line.id}>
                    <td>
                      {availableSkus.length > 0 ? (
                        <select
                          className="form-select text-xs"
                          value={line.skuId}
                          onChange={(e) => handleLineChange(line.id, 'skuId', e.target.value)}
                          required
                        >
                          <option value="">{t('orders.selectSkuPlaceholder')}</option>
                          {availableSkus.map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.code} — {s.name} {s.unit_price ? `(${((s.unit_price.amount_minor ?? 0) / 100).toFixed(2)} ${s.unit_price.currency})` : ''}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <Input
                          placeholder="SKU UUID"
                          value={line.skuId}
                          onChange={(e) => handleLineChange(line.id, 'skuId', e.target.value)}
                          required
                        />
                      )}
                    </td>
                    <td>
                      <input
                        type="number"
                        min="1"
                        max="1000000"
                        className="form-input text-xs"
                        value={line.quantity}
                        onChange={(e) => handleLineChange(line.id, 'quantity', Number(e.target.value))}
                        required
                      />
                    </td>
                    <td>
                      <Button
                        size="sm"
                        variant="danger"
                        type="button"
                        onClick={() => handleRemoveLine(line.id)}
                        disabled={lines.length <= 1}
                        title={t('orders.removeItem')}
                      >
                        <Trash2 size={12} />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <div className="flex justify-end gap-3">
          <Button variant="secondary" type="button" onClick={() => navigate('/orders')}>
            {t('common.cancel')}
          </Button>
          <Button variant="primary" type="submit" isLoading={isSubmitting}>
            <ShoppingBag size={14} className="mr-1" />
            <span>{t('orders.submitOrder')}</span>
          </Button>
        </div>
      </form>
    </div>
  );
};
