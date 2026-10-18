import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Truck, Search, ArrowRight, ClipboardList, Info } from 'lucide-react';
import { useTranslation } from '../../i18n';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { Button } from '../../components/ui/Button';
import { Alert } from '../../components/ui/Alert';

export const FulfillmentLookupPage: React.FC = () => {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [lookupId, setLookupId] = useState('');
  const [error, setError] = useState<string | null>(null);

  const handleLookup = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = lookupId.trim();
    if (!trimmed) {
      setError('Please enter a valid Fulfillment UUID.');
      return;
    }
    setError(null);
    navigate(`/fulfillments/${trimmed}`);
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold">{t('fulfillment.title')}</h1>
          <p className="text-sm text-muted mt-1">
            {t('fulfillment.subtitle')}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <Card title={t('fulfillment.lookupTitle')} subtitle={t('fulfillment.lookupSubtitle')}>
          {error && <Alert variant="danger" className="mb-4">{error}</Alert>}
          <form onSubmit={handleLookup} className="space-y-4">
            <Input
              label={t('fulfillment.fulfillmentId')}
              placeholder="e.g. 550e8400-e29b-41d4-a716-446655440000"
              value={lookupId}
              onChange={(e) => setLookupId(e.target.value)}
              required
            />
            <Button type="submit" variant="primary">
              <Search size={16} className="mr-1.5" />
              <span>{t('common.search')}</span>
            </Button>
          </form>
        </Card>

        <Card title="Fulfillment Lifecycle Workflow" subtitle="How fulfillments are created in Arad Platform">
          <div className="space-y-4 text-xs text-muted leading-relaxed">
            <div className="flex gap-3">
              <ClipboardList size={18} className="text-primary shrink-0 mt-0.5" />
              <div>
                <strong className="text-main block mb-0.5">1. Order Placement & Confirmation</strong>
                Orders are submitted and confirmed, verifying inventory reservations in designated warehouses.
              </div>
            </div>
            <div className="flex gap-3">
              <Truck size={18} className="text-primary shrink-0 mt-0.5" />
              <div>
                <strong className="text-main block mb-0.5">2. Request Warehouse Fulfillment</strong>
                From any confirmed order, authorized operators request fulfillment to allocate stock and initiate picking tasks.
              </div>
            </div>
            <div className="flex gap-3">
              <Info size={18} className="text-primary shrink-0 mt-0.5" />
              <div>
                <strong className="text-main block mb-0.5">3. Carrier Dispatch & Exceptions</strong>
                Dispatches are executed asynchronously. Any failed carrier calls can be retried directly from the fulfillment detail view.
              </div>
            </div>
            <div className="pt-2">
              <Link to="/orders">
                <Button variant="secondary" size="sm">
                  <span>Go to Orders to Request Fulfillment</span>
                  <ArrowRight size={14} className="ml-1.5 rtl-flip" />
                </Button>
              </Link>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
};
