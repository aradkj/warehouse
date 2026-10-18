import { apiClient } from '../client';
import { Page, ProductResponse, ProductStatus, SkuResponse } from '../types';

export interface ListProductsParams {
  limit?: number;
  cursor?: string | null;
  status?: ProductStatus | null;
}

export interface CreateProductParams {
  slug: string;
  name: string;
  description?: string | null;
  attributes?: Record<string, unknown>;
}

export interface ReviseProductParams {
  name?: string | null;
  description?: string | null;
  attributes?: Record<string, unknown> | null;
}

export interface CreateSkuParams {
  code: string;
  name: string;
  unit_price_minor: number;
  currency: string;
  attributes?: Record<string, unknown>;
}

export interface ChangeSkuPriceParams {
  unit_price_minor: number;
  currency: string;
}

export const catalogApi = {
  listProducts: async (params: ListProductsParams = {}): Promise<Page<ProductResponse>> => {
    const query = new URLSearchParams();
    if (params.limit) query.set('limit', String(params.limit));
    if (params.cursor) query.set('cursor', params.cursor);
    if (params.status) query.set('status', params.status);
    const qs = query.toString();
    return apiClient.get<Page<ProductResponse>>(`/products${qs ? `?${qs}` : ''}`);
  },

  getProduct: async (productId: string): Promise<ProductResponse> => {
    return apiClient.get<ProductResponse>(`/products/${productId}`);
  },

  createProduct: async (params: CreateProductParams): Promise<ProductResponse> => {
    return apiClient.post<ProductResponse>('/products', params);
  },

  reviseProduct: async (productId: string, params: ReviseProductParams): Promise<ProductResponse> => {
    return apiClient.put<ProductResponse>(`/products/${productId}`, params);
  },

  activateProduct: async (productId: string): Promise<ProductResponse> => {
    return apiClient.post<ProductResponse>(`/products/${productId}/activate`);
  },

  archiveProduct: async (productId: string): Promise<ProductResponse> => {
    return apiClient.post<ProductResponse>(`/products/${productId}/archive`);
  },

  addSkuToProduct: async (productId: string, params: CreateSkuParams): Promise<SkuResponse> => {
    return apiClient.post<SkuResponse>(`/products/${productId}/skus`, params);
  },

  getSku: async (skuId: string): Promise<SkuResponse> => {
    return apiClient.get<SkuResponse>(`/skus/${skuId}`);
  },

  changeSkuPrice: async (skuId: string, params: ChangeSkuPriceParams): Promise<SkuResponse> => {
    return apiClient.put<SkuResponse>(`/skus/${skuId}/price`, params);
  },

  activateSku: async (skuId: string): Promise<SkuResponse> => {
    return apiClient.post<SkuResponse>(`/skus/${skuId}/activate`);
  },

  deactivateSku: async (skuId: string): Promise<SkuResponse> => {
    return apiClient.post<SkuResponse>(`/skus/${skuId}/deactivate`);
  },
};
