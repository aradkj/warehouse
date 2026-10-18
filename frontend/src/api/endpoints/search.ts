import { apiClient } from '../client';
import {
  AuditSearchPageResponse,
  OrderSearchPageResponse,
  ProductSearchPageResponse,
  ReconcileResponse,
  ReindexResponse,
} from '../types';

export interface ProductSearchParams {
  q?: string | null;
  status?: string | null;
  sku?: string | null;
  page?: number;
  page_size?: number;
  cursor?: string | null;
  limit?: number | null;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
}

export interface OrderSearchParams {
  q?: string | null;
  status?: string | null;
  sku?: string | null;
  customer?: string | null;
  warehouse?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  page?: number;
  page_size?: number;
  cursor?: string | null;
  limit?: number | null;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
}

export interface AuditSearchParams {
  q?: string | null;
  action?: string | null;
  actor_id?: string | null;
  resource_type?: string | null;
  resource_id?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  page?: number;
  page_size?: number;
  cursor?: string | null;
  limit?: number | null;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
}

export interface ReindexParams {
  tenant_id?: string | null;
  retain_old_indices?: boolean;
  async_mode?: boolean;
}

export interface ReconcileParams {
  tenant_id?: string | null;
  auto_repair?: boolean;
  async_mode?: boolean;
}

export const searchApi = {
  searchProducts: async (params: ProductSearchParams = {}): Promise<ProductSearchPageResponse> => {
    const query = new URLSearchParams();
    if (params.q) query.set('q', params.q);
    if (params.status) query.set('status', params.status);
    if (params.sku) query.set('sku', params.sku);
    if (params.page) query.set('page', String(params.page));
    if (params.page_size) query.set('page_size', String(params.page_size));
    if (params.cursor) query.set('cursor', params.cursor);
    if (params.limit) query.set('limit', String(params.limit));
    if (params.sort_by) query.set('sort_by', params.sort_by);
    if (params.sort_order) query.set('sort_order', params.sort_order);
    const qs = query.toString();
    return apiClient.get<ProductSearchPageResponse>(`/search/products${qs ? `?${qs}` : ''}`);
  },

  searchOrders: async (params: OrderSearchParams = {}): Promise<OrderSearchPageResponse> => {
    const query = new URLSearchParams();
    if (params.q) query.set('q', params.q);
    if (params.status) query.set('status', params.status);
    if (params.sku) query.set('sku', params.sku);
    if (params.customer) query.set('customer', params.customer);
    if (params.warehouse) query.set('warehouse', params.warehouse);
    if (params.date_from) query.set('date_from', params.date_from);
    if (params.date_to) query.set('date_to', params.date_to);
    if (params.page) query.set('page', String(params.page));
    if (params.page_size) query.set('page_size', String(params.page_size));
    if (params.cursor) query.set('cursor', params.cursor);
    if (params.limit) query.set('limit', String(params.limit));
    if (params.sort_by) query.set('sort_by', params.sort_by);
    if (params.sort_order) query.set('sort_order', params.sort_order);
    const qs = query.toString();
    return apiClient.get<OrderSearchPageResponse>(`/search/orders${qs ? `?${qs}` : ''}`);
  },

  searchAudit: async (params: AuditSearchParams = {}): Promise<AuditSearchPageResponse> => {
    const query = new URLSearchParams();
    if (params.q) query.set('q', params.q);
    if (params.action) query.set('action', params.action);
    if (params.actor_id) query.set('actor_id', params.actor_id);
    if (params.resource_type) query.set('resource_type', params.resource_type);
    if (params.resource_id) query.set('resource_id', params.resource_id);
    if (params.date_from) query.set('date_from', params.date_from);
    if (params.date_to) query.set('date_to', params.date_to);
    if (params.page) query.set('page', String(params.page));
    if (params.page_size) query.set('page_size', String(params.page_size));
    if (params.cursor) query.set('cursor', params.cursor);
    if (params.limit) query.set('limit', String(params.limit));
    if (params.sort_by) query.set('sort_by', params.sort_by);
    if (params.sort_order) query.set('sort_order', params.sort_order);
    const qs = query.toString();
    return apiClient.get<AuditSearchPageResponse>(`/search/audit${qs ? `?${qs}` : ''}`);
  },

  reindex: async (params: ReindexParams = {}): Promise<ReindexResponse> => {
    return apiClient.post<ReindexResponse>('/search/reindex', params);
  },

  reconcile: async (params: ReconcileParams = {}): Promise<ReconcileResponse> => {
    return apiClient.post<ReconcileResponse>('/search/reconcile', params);
  },
};
