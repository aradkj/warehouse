import { apiClient } from '../client';
import {
  AdjustmentResponse,
  AvailabilityResponse,
  Page,
  ReservationResponse,
  ReservationResultResponse,
  WarehouseResponse,
} from '../types';

export interface ListWarehousesParams {
  limit?: number;
  cursor?: string | null;
}

export interface RegisterWarehouseParams {
  code: string;
  name: string;
}

export interface GetAvailabilityParams {
  warehouse_id?: string | null;
  sku_id?: string | null;
  limit?: number;
  cursor?: string | null;
}

export interface AdjustInventoryParams {
  warehouse_id: string;
  sku_id: string;
  quantity_delta: number;
  reason: 'GOODS_RECEIPT' | 'CUSTOMER_RETURN' | 'CYCLE_COUNT' | 'DAMAGE' | 'MANUAL_CORRECTION';
  note?: string | null;
}

export interface StockRequirementParams {
  sku_id: string;
  quantity: number;
  order_reference?: string | null;
}

export interface ReserveInventoryParams {
  requirements: StockRequirementParams[];
  warehouse_ids?: string[] | null;
  strategy?: 'ALL_OR_NOTHING' | 'PARTIAL';
  ttl_seconds?: number | null;
}

export const inventoryApi = {
  listWarehouses: async (params: ListWarehousesParams = {}): Promise<Page<WarehouseResponse>> => {
    const query = new URLSearchParams();
    if (params.limit) query.set('limit', String(params.limit));
    if (params.cursor) query.set('cursor', params.cursor);
    const qs = query.toString();
    return apiClient.get<Page<WarehouseResponse>>(`/warehouses${qs ? `?${qs}` : ''}`);
  },

  getWarehouse: async (warehouseId: string): Promise<WarehouseResponse> => {
    return apiClient.get<WarehouseResponse>(`/warehouses/${warehouseId}`);
  },

  registerWarehouse: async (params: RegisterWarehouseParams): Promise<WarehouseResponse> => {
    return apiClient.post<WarehouseResponse>('/warehouses', params);
  },

  getAvailability: async (params: GetAvailabilityParams = {}): Promise<Page<AvailabilityResponse>> => {
    const query = new URLSearchParams();
    if (params.warehouse_id) query.set('warehouse_id', params.warehouse_id);
    if (params.sku_id) query.set('sku_id', params.sku_id);
    if (params.limit) query.set('limit', String(params.limit));
    if (params.cursor) query.set('cursor', params.cursor);
    const qs = query.toString();
    return apiClient.get<Page<AvailabilityResponse>>(`/inventory/availability${qs ? `?${qs}` : ''}`);
  },

  adjustInventory: async (params: AdjustInventoryParams): Promise<AdjustmentResponse> => {
    return apiClient.post<AdjustmentResponse>('/inventory/adjustments', params);
  },

  createReservation: async (params: ReserveInventoryParams, idempotencyKey?: string): Promise<ReservationResultResponse> => {
    return apiClient.post<ReservationResultResponse>('/reservations', params, { idempotencyKey });
  },

  getReservation: async (reservationId: string): Promise<ReservationResponse> => {
    return apiClient.get<ReservationResponse>(`/reservations/${reservationId}`);
  },

  releaseReservation: async (reservationId: string): Promise<ReservationResponse> => {
    return apiClient.delete<ReservationResponse>(`/reservations/${reservationId}`);
  },
};
