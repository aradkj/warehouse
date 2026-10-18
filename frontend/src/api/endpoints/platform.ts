import { apiClient } from '../client';
import { TenantResponse } from '../types';

export interface RegisterTenantParams {
  slug: string;
  name: string;
}

export const platformApi = {
  registerTenant: async (params: RegisterTenantParams): Promise<TenantResponse> => {
    return apiClient.post<TenantResponse>('/tenants', params);
  },

  getTenant: async (tenantId: string): Promise<TenantResponse> => {
    return apiClient.get<TenantResponse>(`/tenants/${tenantId}`);
  },
};
