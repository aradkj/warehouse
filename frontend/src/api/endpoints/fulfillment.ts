import { apiClient } from '../client';
import { FulfillmentResponse } from '../types';

export interface TaskRequirementParam {
  sku_id: string;
  quantity: number;
}

export interface RequestFulfillmentParams {
  order_id: string;
  warehouse_id: string;
  requirements: TaskRequirementParam[];
  max_retries?: number;
}

export const fulfillmentApi = {
  requestFulfillment: async (params: RequestFulfillmentParams, idempotencyKey?: string): Promise<FulfillmentResponse> => {
    return apiClient.post<FulfillmentResponse>('/fulfillments', params, { idempotencyKey });
  },

  getFulfillment: async (fulfillmentId: string): Promise<FulfillmentResponse> => {
    return apiClient.get<FulfillmentResponse>(`/fulfillments/${fulfillmentId}`);
  },

  retryFulfillment: async (fulfillmentId: string, idempotencyKey?: string): Promise<FulfillmentResponse> => {
    return apiClient.post<FulfillmentResponse>(`/fulfillments/${fulfillmentId}/retry`, undefined, { idempotencyKey });
  },
};
