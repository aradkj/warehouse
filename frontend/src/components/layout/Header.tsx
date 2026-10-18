import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Building, LogOut, Menu, Shield, User } from 'lucide-react';
import { useAuth } from '../../app/providers/AuthContext';
import { useTenant } from '../../app/providers/TenantContext';
import { SystemStatusIndicator } from '../feedback/SystemStatusIndicator';
import { Button } from '../ui/Button';

export interface HeaderProps {
  onToggleMobileNav?: () => void;
}

export const Header: React.FC<HeaderProps> = ({ onToggleMobileNav }) => {
  const { user, principal, logout } = useAuth();
  const { activeTenantId, activeMembership, memberships, selectTenant } = useTenant();
  const [isTenantMenuOpen, setIsTenantMenuOpen] = useState(false);
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const currentRole = activeMembership?.role || principal?.role || 'MEMBER';

  return (
    <header className="top-header">
      <div className="flex items-center gap-3">
        {onToggleMobileNav && (
          <button
            onClick={onToggleMobileNav}
            className="btn btn-secondary btn-sm md:hidden"
            aria-label="Toggle navigation"
            style={{ display: 'none' }}
          >
            <Menu size={16} />
          </button>
        )}
        <div className="flex items-center gap-2">
          <span className="font-semibold text-base text-main">Arad Platform</span>
        </div>
      </div>

      <div className="flex items-center gap-4">
        {/* System Health Probe */}
        <SystemStatusIndicator />

        {/* Tenant Context Selector */}
        {memberships.length > 0 && (
          <div style={{ position: 'relative' }}>
            <button
              onClick={() => setIsTenantMenuOpen(!isTenantMenuOpen)}
              className="btn btn-secondary btn-sm flex items-center gap-2"
              aria-expanded={isTenantMenuOpen}
              aria-label="Active tenant selector"
            >
              <Building size={14} className="text-secondary" />
              <span className="font-medium">
                {activeMembership?.tenant_name || activeMembership?.tenant_slug || activeTenantId?.slice(0, 8) || 'Select Tenant'}
              </span>
            </button>

            {isTenantMenuOpen && (
              <div
                style={{
                  position: 'absolute',
                  top: '100%',
                  right: 0,
                  marginTop: '0.25rem',
                  width: '260px',
                  backgroundColor: 'var(--bg-surface)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  boxShadow: 'var(--shadow-lg)',
                  zIndex: 50,
                  padding: '0.5rem 0',
                }}
              >
                <div className="px-3 py-1 text-xs font-semibold text-muted border-b mb-1">
                  Tenant Memberships
                </div>
                {memberships.map((m) => (
                  <button
                    key={m.id}
                    onClick={() => {
                      selectTenant(m.tenant_id);
                      setIsTenantMenuOpen(false);
                    }}
                    className={`w-full text-left px-3 py-2 text-sm flex items-center justify-between hover:bg-surface-subtle ${
                      m.tenant_id === activeTenantId ? 'font-semibold text-primary' : ''
                    }`}
                    style={{ background: 'none', border: 'none', cursor: 'pointer' }}
                  >
                    <div className="truncate">
                      <div>{m.tenant_name || m.tenant_slug || m.tenant_id.slice(0, 8)}</div>
                      <div className="text-xs text-muted">{m.role}</div>
                    </div>
                    {m.tenant_id === activeTenantId && (
                      <span className="badge badge-success text-xs">Active</span>
                    )}
                  </button>
                ))}
                <div className="border-t mt-1 pt-1">
                  <Link
                    to="/select-tenant"
                    onClick={() => setIsTenantMenuOpen(false)}
                    className="block px-3 py-1.5 text-xs text-primary hover:underline"
                  >
                    Manage all tenants...
                  </Link>
                </div>
              </div>
            )}
          </div>
        )}

        {/* User Info & Role Badge */}
        <div className="flex items-center gap-3 pl-2 border-l">
          <div className="flex items-center gap-2">
            <div
              style={{
                width: '32px',
                height: '32px',
                borderRadius: '50%',
                backgroundColor: 'var(--bg-surface-subtle)',
                border: '1px solid var(--border-subtle)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontWeight: 600,
                fontSize: '0.75rem',
                color: 'var(--color-primary)',
              }}
            >
              {user?.email ? user.email.slice(0, 2).toUpperCase() : <User size={16} />}
            </div>
            <div className="hidden sm:block">
              <div className="text-xs font-semibold text-main truncate" style={{ maxWidth: '160px' }}>
                {user?.email}
              </div>
              <div className="flex items-center gap-1 text-xs text-muted">
                <Shield size={10} />
                <span>{currentRole}</span>
              </div>
            </div>
          </div>

          {/* Logout Action */}
          <Button
            size="sm"
            variant="secondary"
            onClick={handleLogout}
            title="Sign out"
            aria-label="Sign out"
          >
            <LogOut size={14} />
          </Button>
        </div>
      </div>
    </header>
  );
};
