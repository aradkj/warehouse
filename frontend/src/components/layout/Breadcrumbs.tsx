import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ChevronRight, Home } from 'lucide-react';
import { useTranslation } from '../../i18n';

const SEGMENT_TRANSLATION_MAP: Record<string, string> = {
  dashboard: 'nav.dashboard',
  orders: 'nav.orders',
  catalog: 'nav.catalog',
  products: 'nav.catalog',
  inventory: 'nav.stock',
  warehouses: 'nav.warehouses',
  reservations: 'nav.reservations',
  fulfillments: 'nav.fulfillmentTasks',
  search: 'nav.searchProjections',
  audit: 'nav.auditTrail',
  access: 'nav.accessAudit',
  roles: 'nav.roles',
  policies: 'nav.policies',
  evaluate: 'nav.policySimulator',
  settings: 'nav.tenantIdentity',
  members: 'nav.memberships',
  'service-accounts': 'nav.serviceAccounts',
  security: 'nav.securitySettings',
  'search-maintenance': 'nav.searchMaintenance',
  new: 'common.create',
};

export const Breadcrumbs: React.FC = () => {
  const location = useLocation();
  const { t } = useTranslation();
  const pathnames = location.pathname.split('/').filter((x) => x);

  if (pathnames.length === 0 || location.pathname === '/dashboard') {
    return null;
  }

  return (
    <nav aria-label="Breadcrumb" className="flex items-center text-xs text-muted mb-4">
      <Link to="/dashboard" className="flex items-center text-muted hover:text-main">
        <Home size={14} className="mr-1 rtl-flip" />
        <span>{t('nav.dashboard')}</span>
      </Link>
      {pathnames.map((segment, index) => {
        const to = `/${pathnames.slice(0, index + 1).join('/')}`;
        const isLast = index === pathnames.length - 1;

        const translationKey = SEGMENT_TRANSLATION_MAP[segment.toLowerCase()];
        const label = translationKey
          ? t(translationKey)
          : segment.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

        return (
          <React.Fragment key={to}>
            <ChevronRight size={12} className="mx-2 text-muted rtl-flip" />
            {isLast ? (
              <span className="font-semibold text-main" aria-current="page">
                {label}
              </span>
            ) : (
              <Link to={to} className="text-muted hover:text-main">
                {label}
              </Link>
            )}
          </React.Fragment>
        );
      })}
    </nav>
  );
};
