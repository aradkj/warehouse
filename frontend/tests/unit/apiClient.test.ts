import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { apiClient } from '../../src/api/client';
import { ApiError } from '../../src/api/errors';

describe('ApiClient', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    apiClient.clearSession();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('constructs correct base URL and relative paths', () => {
    expect(apiClient.getBaseUrl()).toBe('/v1');
  });

  it('injects Authorization Bearer and X-Tenant-Id headers when set', async () => {
    apiClient.setTokens('mock-access-token', 'mock-refresh-token');
    apiClient.setActiveTenantId('tenant-uuid-123');

    let capturedHeaders: Record<string, string> = {};

    global.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      capturedHeaders = {};
      if (init?.headers instanceof Headers) {
        init.headers.forEach((val, key) => {
          capturedHeaders[key.toLowerCase()] = val;
        });
      } else if (init?.headers) {
        Object.entries(init.headers).forEach(([k, v]) => {
          capturedHeaders[k.toLowerCase()] = String(v);
        });
      }
      return new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    const result = await apiClient.get<{ status: string }>('/test');

    expect(result).toEqual({ status: 'ok' });
    expect(capturedHeaders['authorization']).toBe('Bearer mock-access-token');
    expect(capturedHeaders['x-tenant-id']).toBe('tenant-uuid-123');
    expect(capturedHeaders['x-correlation-id']).toBeDefined();
  });

  it('injects Idempotency-Key header when provided in request options', async () => {
    let capturedHeaders: Record<string, string> = {};

    global.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      capturedHeaders = {};
      if (init?.headers instanceof Headers) {
        init.headers.forEach((val, key) => {
          capturedHeaders[key.toLowerCase()] = val;
        });
      } else if (init?.headers) {
        Object.entries(init.headers).forEach(([k, v]) => {
          capturedHeaders[k.toLowerCase()] = String(v);
        });
      }
      return new Response(JSON.stringify({ id: 'res-1' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    const testKey = 'unique-idempotency-key-abc';
    await apiClient.post('/orders', { test: true }, { idempotencyKey: testKey });

    expect(capturedHeaders['idempotency-key']).toBe(testKey);
  });

  it('maps 400 Bad Request into structured ApiError', async () => {
    global.fetch = vi.fn().mockImplementation(async () => {
      return new Response(
        JSON.stringify({
          error: {
            code: 'INVALID_TRANSITION',
            message: 'Order cannot be confirmed from CANCELLED state.',
            details: { state: 'CANCELLED' },
            correlation_id: 'corr-999',
            type: 'business_rule',
          },
        }),
        {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        }
      );
    });

    await expect(apiClient.post('/orders/1/confirm')).rejects.toSatisfy((err: unknown) => {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(400);
      expect(apiErr.code).toBe('INVALID_TRANSITION');
      expect(apiErr.message).toBe('Order cannot be confirmed from CANCELLED state.');
      expect(apiErr.correlationId).toBe('corr-999');
      return true;
    });
  });

  it('maps 401 Unauthorized and notifies subscribers', async () => {
    global.fetch = vi.fn().mockImplementation(async () => {
      return new Response(
        JSON.stringify({
          error: {
            code: 'UNAUTHENTICATED',
            message: 'Access token expired.',
            details: {},
            type: 'authentication',
          },
        }),
        {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }
      );
    });

    let auth401Fired = false;
    const unsubscribe = apiClient.onAuthError(() => {
      auth401Fired = true;
    });

    await expect(apiClient.get('/orders')).rejects.toBeInstanceOf(ApiError);
    expect(auth401Fired).toBe(true);
    unsubscribe();
  });

  it('maps 403 Forbidden with proper denial explanation', async () => {
    global.fetch = vi.fn().mockImplementation(async () => {
      return new Response(
        JSON.stringify({
          error: {
            code: 'PERMISSION_DENIED',
            message: 'ABAC policy denied fulfillments.modify on warehouse SEA.',
            details: { warehouse: 'SEA' },
            type: 'authorization',
          },
        }),
        {
          status: 403,
          headers: { 'Content-Type': 'application/json' },
        }
      );
    });

    await expect(apiClient.post('/fulfillments/1/retry')).rejects.toSatisfy((err: unknown) => {
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(403);
      expect(apiErr.code).toBe('PERMISSION_DENIED');
      return true;
    });
  });

  it('maps 409 Conflict for duplicate SKU or idempotency conflict', async () => {
    global.fetch = vi.fn().mockImplementation(async () => {
      return new Response(
        JSON.stringify({
          error: {
            code: 'SKU_ALREADY_EXISTS',
            message: 'SKU code WIDGET-RED already exists in this tenant.',
            details: {},
            type: 'conflict',
          },
        }),
        {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        }
      );
    });

    await expect(apiClient.post('/catalog/skus', { code: 'WIDGET-RED' })).rejects.toSatisfy((err: unknown) => {
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(409);
      expect(apiErr.code).toBe('SKU_ALREADY_EXISTS');
      return true;
    });
  });

  it('clears credentials and tenant context on clearSession()', () => {
    apiClient.setTokens('tok-1', 'tok-2');
    apiClient.setActiveTenantId('ten-1');
    expect(apiClient.getAccessToken()).toBe('tok-1');
    expect(apiClient.getActiveTenantId()).toBe('ten-1');

    apiClient.clearSession();
    expect(apiClient.getAccessToken()).toBeNull();
    expect(apiClient.getActiveTenantId()).toBeNull();
    expect(sessionStorage.getItem('arad_access_token')).toBeNull();
    expect(sessionStorage.getItem('arad_active_tenant_id')).toBeNull();
  });
});
