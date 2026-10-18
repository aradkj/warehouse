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
import { useTenant } from '../../app/providers/TenantContext';

export interface SidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ isOpen = false, onClose }) => {
  const { activeTenantId } = useTenant();

  const handleNavClick = () => {
    if (onClose) onClose();
  };

  return (
    <aside className={`sidebar ${isOpen ? 'open' : ''}`}>
      <div className="sidebar-header">
        <div className="flex items-center gap-2">
          <Warehouse size={22} className="text-primary" />
          <span className="font-bold text-base tracking-tight text-white">ARAD WAREHOUSE</span>
        </div>
      </div>

      <nav className="sidebar-nav" aria-label="Main Navigation">
        <div className="nav-section-title">Overview</div>
        <NavLink
          to="/dashboard"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Activity size={18} />
          <span>Dashboard</span>
        </NavLink>

        <div className="nav-section-title">Operations</div>
        <NavLink
          to="/orders"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <ClipboardList size={18} />
          <span>Orders</span>
        </NavLink>
        <NavLink
          to="/catalog/products"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Boxes size={18} />
          <span>Catalog & SKUs</span>
        </NavLink>
        <NavLink
          to="/inventory"
          end
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Layers size={18} />
          <span>Stock Availability</span>
        </NavLink>
        <NavLink
          to="/inventory/warehouses"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Warehouse size={18} />
          <span>Warehouses</span>
        </NavLink>
        <NavLink
          to="/inventory/reservations"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <CheckSquare size={18} />
          <span>Reservations</span>
        </NavLink>

        <div className="nav-section-title">Fulfillment</div>
        <NavLink
          to="/fulfillments"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Truck size={18} />
          <span>Fulfillment Tasks</span>
        </NavLink>

        <div className="nav-section-title">Search & Projections</div>
        <NavLink
          to="/search/orders"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Search size={18} />
          <span>Search Orders</span>
        </NavLink>
        <NavLink
          to="/search/products"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Search size={18} />
          <span>Search Catalog</span>
        </NavLink>
        <NavLink
          to="/search/audit"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Search size={18} />
          <span>Search Audit</span>
        </NavLink>
        <NavLink
          to="/settings/search-maintenance"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Database size={18} />
          <span>Search Maintenance</span>
        </NavLink>

        <div className="nav-section-title">Access & Audit</div>
        <NavLink
          to="/audit"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <FileText size={18} />
          <span>Audit Trail</span>
        </NavLink>
        <NavLink
          to="/access/roles"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Shield size={18} />
          <span>Roles (RBAC)</span>
        </NavLink>
        <NavLink
          to="/access/policies"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Lock size={18} />
          <span>Policies (ABAC)</span>
        </NavLink>
        <NavLink
          to="/access/evaluate"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Settings size={18} />
          <span>Policy Simulator</span>
        </NavLink>

        <div className="nav-section-title">Tenant & Identity</div>
        <NavLink
          to="/settings/members"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Users size={18} />
          <span>Memberships</span>
        </NavLink>
        <NavLink
          to="/settings/service-accounts"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Key size={18} />
          <span>Service Accounts</span>
        </NavLink>
        <NavLink
          to="/settings/security"
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          onClick={handleNavClick}
        >
          <Lock size={18} />
          <span>Security Settings</span>
        </NavLink>
      </nav>
    </aside>
  );
};
