import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Building, Check, ChevronDown, LogOut, Menu, Plus, Shield, User } from 'lucide-react';
import { useAuth } from '../../app/providers/AuthContext';
import { useTenant } from '../../app/providers/TenantContext';
import { useTranslation } from '../../i18n';
import { LanguageSwitcher } from '../common/LanguageSwitcher';
import { SystemStatusIndicator } from '../feedback/SystemStatusIndicator';
import { Button } from '../ui/Button';

export interface HeaderProps {
  onToggleMobileNav?: () => void;
}

export const Header: React.FC<HeaderProps> = ({ onToggleMobileNav }) => {
  const { user, principal, logout } = useAuth();
  const { activeTenantId, activeMembership, memberships, selectTenant } = useTenant();
  const { t } = useTranslation();
  const [isTenantMenuOpen, setIsTenantMenuOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const handleOutsideClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsTenantMenuOpen(false);
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsTenantMenuOpen(false);
      }
    };

    if (isTenantMenuOpen) {
      document.addEventListener('mousedown', handleOutsideClick);
      document.addEventListener('keydown', handleKeyDown);
    }
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isTenantMenuOpen]);

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const handleSwitchTenant = async (tenantId: string) => {
    try {
      await selectTenant(tenantId);
      setIsTenantMenuOpen(false);
    } catch {
      // Toast displays error from selectTenant
    }
  };

  const currentRole = activeMembership?.role || principal?.role || 'MEMBER';

  return (
    <header className="top-header">
      <div className="flex items-center gap-3">
        {onToggleMobileNav && (
          <button
            onClick={onToggleMobileNav}
            className="btn btn-secondary btn-sm md:hidden"
            aria-label={t('header.toggleNavigation')}
            style={{ display: 'none' }}
          >
            <Menu size={16} />
          </button>
        )}
        <div className="flex items-center gap-2">
          <span className="font-semibold text-base text-main">{t('header.title')}</span>
        </div>
      </div>

      <div className="flex items-center gap-4">
        {/* Language Switcher */}
        <LanguageSwitcher />

        {/* System Health Probe */}
        <SystemStatusIndicator />

        {/* Tenant Context Selector / Switcher */}
        {memberships.length > 0 && (
          <div ref={dropdownRef} style={{ position: 'relative' }}>
            <button
              onClick={() => setIsTenantMenuOpen(!isTenantMenuOpen)}
              className="btn btn-secondary btn-sm flex items-center gap-2"
              aria-expanded={isTenantMenuOpen}
              aria-label={t('header.activeTenantSelector')}
              data-testid="tenant-switcher-button"
            >
              <Building size={14} className="text-secondary flex-shrink-0" />
              <span className="font-semibold text-main truncate" style={{ maxWidth: '160px' }}>
                {activeMembership?.tenant_name || activeMembership?.tenant_slug || activeTenantId?.slice(0, 8) || t('header.selectTenant')}
              </span>
              <ChevronDown
                size={14}
                className="text-muted flex-shrink-0 transition-transform"
                style={{
                  transform: isTenantMenuOpen ? 'rotate(180deg)' : 'rotate(0deg)',
                  transition: 'transform 0.15s ease',
                }}
              />
            </button>

            {isTenantMenuOpen && (
              <div
                style={{
                  position: 'absolute',
                  top: '100%',
                  insetInlineEnd: 0,
                  marginTop: '0.35rem',
                  width: '280px',
                  backgroundColor: 'var(--bg-surface)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  boxShadow: 'var(--shadow-lg)',
                  zIndex: 50,
                  padding: '0.5rem 0',
                }}
              >
                <div className="px-3 py-1.5 text-xs font-semibold text-muted border-b mb-1 flex items-center justify-between">
                  <span>{t('header.tenantMemberships')}</span>
                  <span className="text-xs font-normal text-muted">({memberships.length})</span>
                </div>

                <div style={{ maxHeight: '240px', overflowY: 'auto' }}>
                  {memberships.map((m) => {
                    const isCurrent = m.tenant_id === activeTenantId;
                    return (
                      <button
                        key={m.id}
                        data-testid={`tenant-option-${m.tenant_id}`}
                        onClick={() => handleSwitchTenant(m.tenant_id)}
                        className={`w-full text-start px-3 py-2 text-sm flex items-center justify-between transition-colors ${
                          isCurrent ? 'font-semibold text-primary' : 'hover:bg-surface-subtle text-main'
                        }`}
                        style={{
                          background: isCurrent ? 'var(--color-primary-subtle)' : 'none',
                          border: 'none',
                          cursor: 'pointer',
                        }}
                      >
                        <div className="flex items-center gap-2 truncate" style={{ minWidth: 0 }}>
                          {isCurrent ? (
                            <Check
                              size={16}
                              className="text-primary flex-shrink-0"
                              data-testid="active-tenant-check"
                            />
                          ) : (
                            <span style={{ width: 16, display: 'inline-block' }} className="flex-shrink-0" />
                          )}
                          <div className="truncate">
                            <div className="truncate">{m.tenant_name || m.tenant_slug || m.tenant_id.slice(0, 8)}</div>
                            <div className="text-xs text-muted font-normal">{m.role}</div>
                          </div>
                        </div>
                        {isCurrent && (
                          <span className="badge badge-success text-xs flex-shrink-0">{t('header.activeBadge')}</span>
                        )}
                      </button>
                    );
                  })}
                </div>

                <div className="border-t mt-1 pt-1 flex flex-col gap-0.5">
                  <Link
                    to="/select-tenant?create=true"
                    data-testid="header-create-tenant-link"
                    onClick={() => setIsTenantMenuOpen(false)}
                    className="flex items-center gap-2 px-3 py-2 text-xs font-semibold text-primary hover:bg-surface-subtle transition-colors"
                  >
                    <Plus size={14} className="text-primary flex-shrink-0" />
                    <span>{t('tenants.createNewTenant')}</span>
                  </Link>
                  <Link
                    to="/select-tenant"
                    onClick={() => setIsTenantMenuOpen(false)}
                    className="block px-3 py-1.5 text-xs text-muted hover:text-primary hover:underline transition-colors"
                  >
                    {t('header.manageAllTenants')}
                  </Link>
                </div>
              </div>
            )}
          </div>
        )}

        {/* User Info & Role Badge */}
        <div className="flex items-center gap-3 border-inline-start padding-inline-start-2">
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
            title={t('header.signOut')}
            aria-label={t('header.signOut')}
          >
            <LogOut size={14} className="rtl-flip" />
          </Button>
        </div>
      </div>
    </header>
  );
};
