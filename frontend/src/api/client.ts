/**
 * Centralized typed API Client for Arad Warehouse Platform.
 */

import { ApiError, normalizeError } from './errors';
import { ApiErrorDetail, ApiErrorResponse } from './types';

export interface RequestOptions extends RequestInit {
  idempotencyKey?: string;
  skipAuth?: boolean;
  timeoutMs?: number;
}

class ApiClient {
  private baseUrl: string;
  private accessToken: string | null = null;
  private refreshToken: string | null = null;
  private activeTenantId: string | null = null;
  private isRefreshing = false;
  private refreshSubscribers: Array<(token: string) => void> = [];
  private authErrorSubscribers: Array<() => void> = [];

  constructor() {
    // Read base URL from Vite env or fallback to '/v1'
    const configuredBase = import.meta.env?.VITE_API_BASE_URL;
    this.baseUrl = configuredBase !== undefined ? configuredBase : '/v1';
    // Trim trailing slash
    if (this.baseUrl.endsWith('/')) {
      this.baseUrl = this.baseUrl.slice(0, -1);
    }

    // Restore cached tokens from sessionStorage
    this.accessToken = sessionStorage.getItem('arad_access_token');
    this.refreshToken = sessionStorage.getItem('arad_refresh_token');
    this.activeTenantId = sessionStorage.getItem('arad_active_tenant_id');
  }

  public getBaseUrl(): string {
    return this.baseUrl;
  }

  public setBaseUrl(url: string): void {
    this.baseUrl = url.endsWith('/') ? url.slice(0, -1) : url;
  }

  public getAccessToken(): string | null {
    return this.accessToken;
  }

  public getActiveTenantId(): string | null {
    return this.activeTenantId;
  }

  public setTokens(accessToken: string, refreshToken: string, tenantId?: string | null): void {
    this.setSession({ accessToken, refreshToken, tenantId });
  }

  public setActiveTenantId(tenantId: string | null): void {
    this.setActiveTenant(tenantId);
  }

  public onAuthError(callback: () => void): () => void {
    this.authErrorSubscribers.push(callback);
    return () => {
      this.authErrorSubscribers = this.authErrorSubscribers.filter((cb) => cb !== callback);
    };
  }

  private notifyAuthError(): void {
    this.authErrorSubscribers.forEach((cb) => cb());
  }

  public setSession(tokens: {
    accessToken: string;
    refreshToken: string;
    tenantId?: string | null;
  }): void {
    this.accessToken = tokens.accessToken;
    this.refreshToken = tokens.refreshToken;
    sessionStorage.setItem('arad_access_token', tokens.accessToken);
    sessionStorage.setItem('arad_refresh_token', tokens.refreshToken);

    if (tokens.tenantId !== undefined) {
      this.activeTenantId = tokens.tenantId;
      if (tokens.tenantId) {
        sessionStorage.setItem('arad_active_tenant_id', tokens.tenantId);
      } else {
        sessionStorage.removeItem('arad_active_tenant_id');
      }
    }
  }

  public setActiveTenant(tenantId: string | null): void {
    this.activeTenantId = tenantId;
    if (tenantId) {
      sessionStorage.setItem('arad_active_tenant_id', tenantId);
    } else {
      sessionStorage.removeItem('arad_active_tenant_id');
    }
  }

  public clearSession(): void {
    this.accessToken = null;
    this.refreshToken = null;
    this.activeTenantId = null;
    sessionStorage.removeItem('arad_access_token');
    sessionStorage.removeItem('arad_refresh_token');
    sessionStorage.removeItem('arad_active_tenant_id');
  }

  private onTokenRefreshed(token: string) {
    this.refreshSubscribers.forEach((callback) => callback(token));
    this.refreshSubscribers = [];
  }

  private addRefreshSubscriber(callback: (token: string) => void) {
    this.refreshSubscribers.push(callback);
  }

  /**
   * Main request method with timeout, abort, headers and error normalization.
   */
  public async request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
    const {
      idempotencyKey,
      skipAuth = false,
      timeoutMs = 15000,
      headers: customHeaders = {},
      ...restOptions
    } = options;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    // If an external signal was passed, chain it
    if (options.signal) {
      options.signal.addEventListener('abort', () => controller.abort());
    }

    const headers = new Headers(customHeaders);

    // Ensure content-type is json if body is provided and not already specified
    if (restOptions.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }

    // Attach Bearer token
    if (!skipAuth && this.accessToken && !headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${this.accessToken}`);
    }

    // Attach active tenant context header
    if (!skipAuth && this.activeTenantId && !headers.has('X-Tenant-Id')) {
      headers.set('X-Tenant-Id', this.activeTenantId);
    }

    // Attach Idempotency-Key if provided
    if (idempotencyKey && !headers.has('Idempotency-Key')) {
      headers.set('Idempotency-Key', idempotencyKey);
    }

    // Attach Correlation ID for end-to-end tracing
    if (!headers.has('X-Correlation-Id')) {
      headers.set('X-Correlation-Id', crypto.randomUUID());
    }

    // Normalize path: if endpoint already starts with http, use as is; otherwise prepend baseUrl
    let url: string;
    if (endpoint.startsWith('http://') || endpoint.startsWith('https://')) {
      url = endpoint;
    } else if (endpoint.startsWith('/healthz') || endpoint.startsWith('/readyz') || endpoint.startsWith('/metrics')) {
      // Non-v1 root probes
      url = endpoint;
    } else {
      const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
      url = `${this.baseUrl}${cleanEndpoint}`;
    }

    try {
      const response = await fetch(url, {
        ...restOptions,
        headers,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      // Handle 401 Unauthorized with token refresh if possible
      if (response.status === 401 && !skipAuth && this.refreshToken && !endpoint.includes('/auth/refresh') && !endpoint.includes('/auth/login')) {
        return this.handleUnauthorizedAndRetry<T>(endpoint, options);
      }

      if (!response.ok) {
        let errorDetail: ApiErrorDetail = {
          code: `http_${response.status}`,
          message: response.statusText,
          details: {},
          correlation_id: null,
          type: 'http',
        };

        try {
          const contentType = response.headers.get('content-type');
          if (contentType && contentType.includes('application/json')) {
            const data = (await response.json()) as ApiErrorResponse;
            if (data.error) {
              errorDetail = {
                code: data.error.code,
                message: data.error.message,
                details: data.error.details || {},
                correlation_id: data.error.correlation_id || response.headers.get('x-correlation-id') || null,
                type: data.error.type || 'http',
              };
            }
          } else {
            const text = await response.text();
            if (text) errorDetail.message = text;
          }
        } catch {
          // Keep fallback
        }

        if (response.status === 401) {
          this.notifyAuthError();
        }

        throw new ApiError(response.status, errorDetail);
      }

      // 204 No Content
      if (response.status === 204) {
        return undefined as unknown as T;
      }

      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('application/json')) {
        return (await response.json()) as T;
      }

      return (await response.text()) as unknown as T;
    } catch (err) {
      clearTimeout(timeoutId);
      throw normalizeError(err);
    }
  }

  private async handleUnauthorizedAndRetry<T>(
    endpoint: string,
    options: RequestOptions
  ): Promise<T> {
    if (!this.refreshToken) {
      this.clearSession();
      throw new ApiError(401, {
        code: 'authentication_failed',
        message: 'Session expired. Please log in again.',
        details: {},
        type: 'application',
      });
    }

    if (this.isRefreshing) {
      return new Promise<T>((resolve, reject) => {
        this.addRefreshSubscriber(async (newToken: string) => {
          try {
            const retryHeaders = new Headers(options.headers);
            retryHeaders.set('Authorization', `Bearer ${newToken}`);
            const result = await this.request<T>(endpoint, {
              ...options,
              headers: retryHeaders,
            });
            resolve(result);
          } catch (retryErr) {
            reject(retryErr);
          }
        });
      });
    }

    this.isRefreshing = true;

    try {
      const refreshResponse = await fetch(`${this.baseUrl}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: this.refreshToken }),
      });

      if (!refreshResponse.ok) {
        this.clearSession();
        throw new ApiError(401, {
          code: 'session_expired',
          message: 'Refresh token expired. Please log in again.',
          details: {},
          type: 'application',
        });
      }

      const authData = await refreshResponse.json();
      this.setSession({
        accessToken: authData.access_token,
        refreshToken: authData.refresh_token,
        tenantId: authData.active_tenant_id,
      });

      this.isRefreshing = false;
      this.onTokenRefreshed(authData.access_token);

      return this.request<T>(endpoint, options);
    } catch (err) {
      this.isRefreshing = false;
      this.clearSession();
      throw normalizeError(err);
    }
  }

  // Convenience methods
  public get<T>(endpoint: string, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, { ...options, method: 'GET' });
  }

  public post<T>(endpoint: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, {
      ...options,
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  }

  public put<T>(endpoint: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, {
      ...options,
      method: 'PUT',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  }

  public patch<T>(endpoint: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, {
      ...options,
      method: 'PATCH',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  }

  public delete<T>(endpoint: string, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, { ...options, method: 'DELETE' });
  }
}

export const apiClient = new ApiClient();
