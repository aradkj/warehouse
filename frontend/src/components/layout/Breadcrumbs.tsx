import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ChevronRight, Home } from 'lucide-react';

export const Breadcrumbs: React.FC = () => {
  const location = useLocation();
  const pathnames = location.pathname.split('/').filter((x) => x);

  if (pathnames.length === 0 || location.pathname === '/dashboard') {
    return null;
  }

  return (
    <nav aria-label="Breadcrumb" className="flex items-center text-xs text-muted mb-4">
      <Link to="/dashboard" className="flex items-center text-muted hover:text-main">
        <Home size={14} className="mr-1" />
        <span>Dashboard</span>
      </Link>
      {pathnames.map((segment, index) => {
        const to = `/${pathnames.slice(0, index + 1).join('/')}`;
        const isLast = index === pathnames.length - 1;
        const formatted = segment
          .replace(/-/g, ' ')
          .replace(/\b\w/g, (c) => c.toUpperCase());

        return (
          <React.Fragment key={to}>
            <ChevronRight size={12} className="mx-2 text-muted" />
            {isLast ? (
              <span className="font-semibold text-main" aria-current="page">
                {formatted}
              </span>
            ) : (
              <Link to={to} className="text-muted hover:text-main">
                {formatted}
              </Link>
            )}
          </React.Fragment>
        );
      })}
    </nav>
  );
};
