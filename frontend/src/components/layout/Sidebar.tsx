import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  Activity,
  Boxes,
  CheckSquare,
  ClipboardList,
  Database,
  FileText,
  Key,
  Layers,
  Lock,
  Search,
  Settings,
  Shield,
  Truck,
  Users,
  Warehouse,
} from 'lucide-react';
import { useTranslation } from '../../i18n';

export interface SidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ isOpen = false, onClose }) => {
  const { t } = useTranslation();

  const handleNavClick = () => {
    if (onClose) onClose();
  };

  return (
    <aside className={`sidebar ${isOpen ? 'open' : ''}`}>
      <div className="sidebar-header">
        <div className="flex items-center gap-2">
          <Warehouse size={22} className="text-primary" />
          <span className="font-bold text-base tracking-tight text-white">{t('nav.brand')}</span>
        </div>
      </div>

      <nav className="sidebar-nav" aria-label={t('nav.mainNav')}>
        <div className="nav-section-title">{t('nav.overview')}</div>
        <NavLink
          to="/dashboard"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Activity size={18} />
          <span>{t('nav.dashboard')}</span>
        </NavLink>

        <div className="nav-section-title">{t('nav.operations')}</div>
        <NavLink
          to="/orders"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <ClipboardList size={18} />
          <span>{t('nav.orders')}</span>
        </NavLink>
        <NavLink
          to="/catalog/products"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Boxes size={18} />
          <span>{t('nav.catalog')}</span>
        </NavLink>
        <NavLink
          to="/inventory"
          end
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Layers size={18} />
          <span>{t('nav.stock')}</span>
        </NavLink>
        <NavLink
          to="/inventory/warehouses"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Warehouse size={18} />
          <span>{t('nav.warehouses')}</span>
        </NavLink>
        <NavLink
          to="/inventory/reservations"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <CheckSquare size={18} />
          <span>{t('nav.reservations')}</span>
        </NavLink>

        <div className="nav-section-title">{t('nav.fulfillment')}</div>
        <NavLink
          to="/fulfillments"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Truck size={18} />
          <span>{t('nav.fulfillmentTasks')}</span>
        </NavLink>

        <div className="nav-section-title">{t('nav.searchProjections')}</div>
        <NavLink
          to="/search/orders"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Search size={18} />
          <span>{t('nav.searchOrders')}</span>
        </NavLink>
        <NavLink
          to="/search/products"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Search size={18} />
          <span>{t('nav.searchCatalog')}</span>
        </NavLink>
        <NavLink
          to="/search/audit"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Search size={18} />
          <span>{t('nav.searchAudit')}</span>
        </NavLink>
        <NavLink
          to="/settings/search-maintenance"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Database size={18} />
          <span>{t('nav.searchMaintenance')}</span>
        </NavLink>

        <div className="nav-section-title">{t('nav.accessAudit')}</div>
        <NavLink
          to="/audit"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <FileText size={18} />
          <span>{t('nav.auditTrail')}</span>
        </NavLink>
        <NavLink
          to="/access/roles"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Shield size={18} />
          <span>{t('nav.roles')}</span>
        </NavLink>
        <NavLink
          to="/access/policies"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Lock size={18} />
          <span>{t('nav.policies')}</span>
        </NavLink>
        <NavLink
          to="/access/evaluate"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Settings size={18} />
          <span>{t('nav.policySimulator')}</span>
        </NavLink>

        <div className="nav-section-title">{t('nav.tenantIdentity')}</div>
        <NavLink
          to="/settings/members"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Users size={18} />
          <span>{t('nav.memberships')}</span>
        </NavLink>
        <NavLink
          to="/settings/service-accounts"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Key size={18} />
          <span>{t('nav.serviceAccounts')}</span>
        </NavLink>
        <NavLink
          to="/settings/security"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Lock size={18} />
          <span>{t('nav.securitySettings')}</span>
        </NavLink>
      </nav>
    </aside>
  );
};
