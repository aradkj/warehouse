import { apiClient } from '../client';
import { OrderPageResponse, OrderResponse, OrderStatus } from '../types';

export interface OrderLineParam {
  sku_id: string;
  quantity: number;
}

export interface CreateOrderParams {
  lines: OrderLineParam[];
  customer_reference?: string | null;
}

export interface ListOrdersParams {
  limit?: number;
  cursor?: string | null;
  status?: OrderStatus | null;
}

export const ordersApi = {
  listOrders: async (params: ListOrdersParams = {}): Promise<OrderPageResponse> => {
    const query = new URLSearchParams();
    if (params.limit) query.set('limit', String(params.limit));
    if (params.cursor) query.set('cursor', params.cursor);
    if (params.status) query.set('status', params.status);
    const qs = query.toString();
    return apiClient.get<OrderPageResponse>(`/orders${qs ? `?${qs}` : ''}`);
  },

  getOrder: async (orderId: string): Promise<OrderResponse> => {
    return apiClient.get<OrderResponse>(`/orders/${orderId}`);
  },

  createOrder: async (params: CreateOrderParams, idempotencyKey: string): Promise<OrderResponse> => {
    return apiClient.post<OrderResponse>('/orders', params, { idempotencyKey });
  },

  confirmOrder: async (orderId: string): Promise<OrderResponse> => {
    return apiClient.post<OrderResponse>(`/orders/${orderId}/confirm`);
  },

  cancelOrder: async (orderId: string, reason: string): Promise<OrderResponse> => {
    return apiClient.post<OrderResponse>(`/orders/${orderId}/cancel`, { reason });
  },
};
