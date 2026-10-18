import { apiClient } from '../client';
import {
  DecisionEvaluationResponse,
  PermissionListResponse,
  PolicyResponse,
  RoleResponse,
} from '../types';

export interface CreateRoleParams {
  name: string;
  description: string;
  permissions: string[];
}

export interface CreatePolicyParams {
  name: string;
  description: string;
  effect: 'ALLOW' | 'DENY';
  actions: string[];
  resources?: string[];
  resource_types?: string[];
  conditions?: Record<string, unknown>;
  condition?: Record<string, unknown>;
}

export interface DecisionEvaluationParams {
  action: string;
  resource_type: string;
  resource_id?: string | null;
  resource_tenant_id?: string | null;
  resource_attributes?: Record<string, unknown>;
  environment?: Record<string, unknown>;
}

export interface AccessMeResponse {
  principal_id: string;
  email?: string | null;
  active_tenant_id?: string | null;
  roles: string[];
  permissions: string[];
}

export const accessApi = {
  getEffectiveAccess: async (): Promise<AccessMeResponse> => {
    return apiClient.get<AccessMeResponse>('/access/me');
  },

  listPermissions: async (): Promise<PermissionListResponse> => {
    return apiClient.get<PermissionListResponse>('/access/permissions');
  },

  listRoles: async (): Promise<RoleResponse[]> => {
    return apiClient.get<RoleResponse[]>('/access/roles');
  },

  getRole: async (roleId: string): Promise<RoleResponse> => {
    return apiClient.get<RoleResponse>(`/access/roles/${roleId}`);
  },

  createRole: async (params: CreateRoleParams): Promise<RoleResponse> => {
    return apiClient.post<RoleResponse>('/access/roles', params);
  },

  assignRoleToMembership: async (membershipId: string, roleName: string): Promise<void> => {
    return apiClient.post<void>(`/access/memberships/${membershipId}/roles`, { role_name: roleName });
  },

  revokeRoleFromMembership: async (membershipId: string, roleName: string): Promise<void> => {
    return apiClient.delete<void>(`/access/memberships/${membershipId}/roles/${encodeURIComponent(roleName)}`);
  },

  listPolicies: async (): Promise<PolicyResponse[]> => {
    return apiClient.get<PolicyResponse[]>('/access/policies');
  },

  getPolicy: async (policyId: string): Promise<PolicyResponse> => {
    return apiClient.get<PolicyResponse>(`/access/policies/${policyId}`);
  },

  createPolicy: async (params: CreatePolicyParams): Promise<PolicyResponse> => {
    return apiClient.post<PolicyResponse>('/access/policies', params);
  },

  updatePolicyStatus: async (policyId: string, status: 'ACTIVE' | 'INACTIVE'): Promise<PolicyResponse> => {
    return apiClient.patch<PolicyResponse>(`/access/policies/${policyId}/status`, { status });
  },

  evaluateDecision: async (params: DecisionEvaluationParams): Promise<DecisionEvaluationResponse> => {
    return apiClient.post<DecisionEvaluationResponse>('/access/evaluate', params);
  },
};
