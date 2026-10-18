import { apiClient } from '../client';
import {
  AuthTokensResponse,
  InvitationResponse,
  PrincipalResponse,
  ServiceAccountCreatedResponse,
  ServiceAccountResponse,
  TenantMembershipResponse,
  UserResponse,
} from '../types';

export interface LoginParams {
  email: string;
  password: string;
  tenant_id?: string | null;
}

export interface RegisterParams {
  email: string;
  password: string;
  tenant_id?: string | null;
  invitation_token?: string | null;
}

export interface ChangePasswordParams {
  current_password: string;
  new_password: string;
}

export interface CreateInvitationParams {
  email?: string | null;
  role: 'ADMIN' | 'MEMBER';
}

export interface ProvisionMembershipParams {
  user_id?: string | null;
  email?: string | null;
  role: 'ADMIN' | 'MEMBER';
}

export interface CreateServiceAccountParams {
  name: string;
  client_id: string;
  scopes: string[];
}

export const authApi = {
  login: async (params: LoginParams): Promise<AuthTokensResponse> => {
    return apiClient.post<AuthTokensResponse>('/auth/login', params, { skipAuth: true });
  },

  register: async (params: RegisterParams): Promise<UserResponse> => {
    return apiClient.post<UserResponse>('/auth/register', params, { skipAuth: true });
  },

  logout: async (refreshToken: string): Promise<void> => {
    return apiClient.post<void>('/auth/logout', { refresh_token: refreshToken });
  },

  refresh: async (refreshToken: string): Promise<AuthTokensResponse> => {
    return apiClient.post<AuthTokensResponse>('/auth/refresh', { refresh_token: refreshToken }, { skipAuth: true });
  },

  getMe: async (): Promise<PrincipalResponse> => {
    return apiClient.get<PrincipalResponse>('/auth/me');
  },

  changePassword: async (params: ChangePasswordParams): Promise<void> => {
    return apiClient.post<void>('/auth/change-password', params);
  },

  listMemberships: async (): Promise<TenantMembershipResponse[]> => {
    return apiClient.get<TenantMembershipResponse[]>('/auth/memberships');
  },

  selectTenant: async (tenantId: string): Promise<AuthTokensResponse> => {
    return apiClient.post<AuthTokensResponse>('/auth/select-tenant', { tenant_id: tenantId });
  },

  createInvitation: async (params: CreateInvitationParams): Promise<InvitationResponse> => {
    return apiClient.post<InvitationResponse>('/auth/invitations', params);
  },

  provisionMembership: async (params: ProvisionMembershipParams): Promise<TenantMembershipResponse> => {
    return apiClient.post<TenantMembershipResponse>('/auth/memberships', params);
  },

  createServiceAccount: async (params: CreateServiceAccountParams): Promise<ServiceAccountCreatedResponse> => {
    return apiClient.post<ServiceAccountCreatedResponse>('/auth/service-accounts', params);
  },

  revokeServiceAccount: async (serviceAccountId: string): Promise<void> => {
    return apiClient.delete<void>(`/auth/service-accounts/${serviceAccountId}`);
  },

  rotateServiceAccountSecret: async (serviceAccountId: string): Promise<ServiceAccountCreatedResponse> => {
    return apiClient.post<ServiceAccountCreatedResponse>(`/auth/service-accounts/${serviceAccountId}/rotate`);
  },
};
