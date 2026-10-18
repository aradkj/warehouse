import React, { useEffect, useState } from 'react';
import { Building, Plus, RefreshCw, Warehouse } from 'lucide-react';
import { inventoryApi } from '../../api/endpoints/inventory';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { WarehouseResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { useTranslation } from '../../i18n';
import { EmptyState } from '../../components/feedback/EmptyState';
import { Alert } from '../../components/ui/Alert';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';

export const WarehouseListPage: React.FC = () => {
  const { showToast } = useToast();
  const { t, formatDate, formatNumber } = useTranslation();
  const [warehouses, setWarehouses] = useState<WarehouseResponse[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Register modal
  const [isRegisterOpen, setIsRegisterOpen] = useState(false);
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const fetchWarehouses = async (cursor: string | null = null) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await inventoryApi.listWarehouses({ limit: 20, cursor });
      setWarehouses(data.items);
      setNextCursor(data.next_cursor);
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to fetch warehouses.';
      setError(msg);
      showToast(msg, 'error');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchWarehouses();
  }, []);

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (!code.trim() || !name.trim()) {
      setFormError(t('common.required'));
      return;
    }

    setIsSubmitting(true);
    try {
      const created = await inventoryApi.registerWarehouse({
        code: code.trim().toUpperCase(),
        name: name.trim(),
      });
      showToast(`Warehouse "${created.name}" registered.`, 'success');
      setWarehouses((prev) => [created, ...prev]);
      setIsRegisterOpen(false);
      setCode('');
      setName('');
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to register warehouse.';
      setFormError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">{t('inventory.warehousesTitle')}</h1>
          <p className="text-sm text-muted mt-1">
            {t('inventory.warehousesSubtitle')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => fetchWarehouses(null)}
            isLoading={isLoading}
          >
            <RefreshCw size={14} className="mr-1" />
            <span>{t('common.refresh')}</span>
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={() => setIsRegisterOpen(true)}
          >
            <Plus size={14} className="mr-1" />
            <span>{t('common.create')}</span>
          </Button>
        </div>
      </div>

      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      {isLoading ? (
        <div className="text-center py-12 text-muted">{t('common.loading')}</div>
      ) : warehouses.length === 0 ? (
        <Card>
          <EmptyState
            title={t('inventory.warehousesTitle')}
            description={t('inventory.warehousesSubtitle')}
            actionLabel={t('common.create')}
            onAction={() => setIsRegisterOpen(true)}
            icon={<Warehouse size={48} strokeWidth={1.5} />}
          />
        </Card>
      ) : (
        <div className="grid grid-cols-3 gap-6">
          {warehouses.map((wh) => (
            <Card key={wh.id} className="mb-0">
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div
                    style={{
                      width: '40px',
                      height: '40px',
                      borderRadius: '8px',
                      backgroundColor: 'var(--color-primary-subtle)',
                      color: 'var(--color-primary)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <Building size={20} />
                  </div>
                  <div>
                    <h2 className="font-semibold text-base">{wh.name}</h2>
                    <div className="text-xs font-mono text-muted">{wh.code}</div>
                  </div>
                </div>
                <Badge variant={wh.is_active ? 'success' : 'neutral'}>
                  {wh.is_active ? t('common.active') : t('common.inactive')}
                </Badge>
              </div>

              <div className="pt-3 border-t text-xs text-muted font-mono space-y-1">
                <div>ID: {wh.id}</div>
                <div>{t('common.created_at')}: {formatDate(wh.created_at)}</div>
                <div>Version: {formatNumber(wh.version)}</div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Register Warehouse Modal */}
      <Modal
        isOpen={isRegisterOpen}
        onClose={() => setIsRegisterOpen(false)}
        title={t('inventory.warehousesTitle')}
        description={t('inventory.warehousesSubtitle')}
      >
        {formError && <Alert variant="danger" className="mb-4">{formError}</Alert>}

        <form onSubmit={handleRegister}>
          <Input
            label={t('inventory.code')}
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder="e.g. LON-01 or LAX"
            helperText="2-32 characters, uppercase alphanumeric"
            required
          />

          <Input
            label={t('inventory.warehouseName')}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="London Gateway Fulfillment Hub"
            required
          />

          <div className="flex justify-end gap-2 mt-6">
            <Button variant="secondary" onClick={() => setIsRegisterOpen(false)} disabled={isSubmitting}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" type="submit" isLoading={isSubmitting}>
              {t('common.save')}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
