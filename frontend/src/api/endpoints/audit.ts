import { apiClient } from '../client';
import { AuditPageResponse } from '../types';

export interface ListAuditEventsParams {
  limit?: number;
}

export const auditApi = {
  listEvents: async (params: ListAuditEventsParams = {}): Promise<AuditPageResponse> => {
    const query = new URLSearchParams();
    if (params.limit) query.set('limit', String(params.limit));
    const qs = query.toString();
    return apiClient.get<AuditPageResponse>(`/audit/events${qs ? `?${qs}` : ''}`);
  },
};
