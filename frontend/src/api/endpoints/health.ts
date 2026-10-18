import { apiClient } from '../client';
import { HealthResponse } from '../types';

export const healthApi = {
  getLiveness: async (): Promise<HealthResponse> => {
    return apiClient.get<HealthResponse>('/healthz', { skipAuth: true });
  },

  getReadiness: async (): Promise<HealthResponse> => {
    return apiClient.get<HealthResponse>('/readyz', { skipAuth: true });
  },
};
