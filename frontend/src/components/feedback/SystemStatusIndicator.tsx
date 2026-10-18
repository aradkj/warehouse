import React, { useEffect, useState } from 'react';
import { healthApi } from '../../api/endpoints/health';

export const SystemStatusIndicator: React.FC = () => {
  const [status, setStatus] = useState<'checking' | 'healthy' | 'unhealthy'>('checking');
  const [dbStatus, setDbStatus] = useState<string>('unknown');

  const checkHealth = async () => {
    try {
      const data = await healthApi.getLiveness();
      if (data && data.status === 'ok') {
        setStatus('healthy');
        setDbStatus(data.checks?.database || 'ok');
      } else {
        setStatus('unhealthy');
      }
    } catch {
      setStatus('unhealthy');
    }
  };

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 45000); // Probe every 45s
    return () => clearInterval(interval);
  }, []);

  const color =
    status === 'healthy'
      ? 'var(--color-success)'
      : status === 'checking'
        ? 'var(--color-warning)'
        : 'var(--color-danger)';

  const label =
    status === 'healthy'
      ? `System Online (DB: ${dbStatus})`
      : status === 'checking'
        ? 'Checking API...'
        : 'API Unreachable';

  return (
    <div
      className="flex items-center gap-1.5 text-xs text-muted"
      title={label}
      aria-label={label}
    >
      <span
        style={{
          width: '8px',
          height: '8px',
          borderRadius: '50%',
          backgroundColor: color,
          display: 'inline-block',
        }}
      />
      <span className="hidden lg:inline">{status === 'healthy' ? 'API Online' : 'Degraded'}</span>
    </div>
  );
};
