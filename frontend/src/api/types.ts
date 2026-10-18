/**
 * Arad Platform — Complete TypeScript Data Models
 * Strictly aligned with backend Pydantic schemas in src/bootstrap/schemas/
 */

export interface Money {
  amount_minor: number;
  currency: string;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  details: Record<string, unknown>;
  correlation_id?: string | null;
  type: string;
}

export interface ApiErrorResponse {
  error: ApiErrorDetail;
}

export interface HealthResponse {
  status: string;
  environment: string;
  checks: Record<string, string>;
}

// --- Identity & Auth ---

export interface UserResponse {
  id: string;
  email: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface TenantMembershipResponse {
  id: string;
  user_id: string;
  tenant_id: string;
  status: string;
  role: string;
  created_at: string;
  tenant_name?: string | null;
  tenant_slug?: string | null;
  roles: string[];
}

export interface AuthTokensResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: UserResponse;
  active_tenant_id?: string | null;
  active_membership_id?: string | null;
}

export interface PrincipalResponse {
  id: string;
  type: string;
  email?: string | null;
  active_tenant_id?: string | null;
  active_membership_id?: string | null;
  account_status: string;
  role: string;
}

export interface InvitationResponse {
  invitation_token: string;
  tenant_id: string;
  email?: string | null;
  role: string;
  expires_in_days: number;
}

export interface ServiceAccountResponse {
  id: string;
  tenant_id: string;
  name: string;
  client_id: string;
  status: string;
  scopes: string[];
  created_at: string;
}

export interface ServiceAccountCreatedResponse extends ServiceAccountResponse {
  client_secret: string;
}

// --- Access Management ---

export interface PermissionResponse {
  name: string;
  description: string;
  category: string;
}

export interface PermissionListResponse {
  permissions: PermissionResponse[];
  total: number;
}

export interface RoleResponse {
  id: string;
  tenant_id?: string | null;
  name: string;
  description: string;
  is_system: boolean;
  permissions: string[];
  created_at: string;
  updated_at: string;
}

export interface PolicyResponse {
  id: string;
  tenant_id?: string | null;
  name: string;
  description: string;
  effect: 'ALLOW' | 'DENY';
  actions: string[];
  resource_types: string[];
  condition: Record<string, unknown>;
  status: 'ACTIVE' | 'INACTIVE';
  version: number;
  created_at: string;
  updated_at: string;
}

export interface DecisionEvaluationResponse {
  allowed: boolean;
  reason: string;
  reason_code: string;
  action: string;
  resource_type: string;
  policy_id?: string | null;
  missing_permissions: string[];
}

// --- Platform & Tenant ---

export interface TenantResponse {
  id: string;
  slug: string;
  name: string;
  status: 'ACTIVE' | 'SUSPENDED';
  created_at: string;
  updated_at: string;
  version: number;
  invitation_token?: string | null;
}

// --- Catalog ---

export type ProductStatus = 'DRAFT' | 'ACTIVE' | 'ARCHIVED';
export type SkuStatus = 'ACTIVE' | 'INACTIVE';

export interface SkuResponse {
  id: string;
  product_id: string;
  code: string;
  name: string;
  status: SkuStatus;
  unit_price: Money;
  attributes: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface ProductResponse {
  id: string;
  slug: string;
  name: string;
  description?: string | null;
  status: ProductStatus;
  attributes: Record<string, unknown>;
  skus: SkuResponse[];
  created_at: string;
  updated_at: string;
  version: number;
}

// --- Inventory ---

export interface WarehouseResponse {
  id: string;
  code: string;
  name: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface AvailabilityResponse {
  inventory_item_id: string;
  warehouse_id: string;
  sku_id: string;
  on_hand: number;
  reserved: number;
  available: number;
  updated_at: string;
  version: number;
}

export interface MovementResponse {
  id: string;
  inventory_item_id: string;
  warehouse_id: string;
  sku_id: string;
  movement_type: string;
  reason?: string | null;
  quantity_delta: number;
  on_hand_after: number;
  reserved_after: number;
  reference_type?: string | null;
  reference_id?: string | null;
  note?: string | null;
  occurred_at: string;
}

export interface AdjustmentResponse {
  item: AvailabilityResponse;
  movement: MovementResponse;
}

export interface ReservationAllocationResponse {
  id: string;
  inventory_item_id: string;
  warehouse_id: string;
  sku_id: string;
  order_line_id?: string | null;
  quantity: number;
}

export interface ReservationResponse {
  id: string;
  order_id?: string | null;
  status: 'HELD' | 'CONFIRMED' | 'RELEASED' | 'EXPIRED';
  expires_at: string;
  total_quantity: number;
  allocations: ReservationAllocationResponse[];
  created_at: string;
  updated_at: string;
  confirmed_at?: string | null;
  released_at?: string | null;
  release_reason?: string | null;
  version: number;
}

export interface ShortfallResponse {
  sku_id: string;
  required: number;
  allocated: number;
  missing: number;
}

export interface ReservationResultResponse {
  reservation: ReservationResponse | null;
  shortfalls: ShortfallResponse[];
  is_complete: boolean;
}

// --- Ordering ---

export type OrderStatus = 'PENDING' | 'CONFIRMED' | 'CANCELLED';

export interface OrderLineResponse {
  id: string;
  line_number: number;
  sku_id: string;
  product_id: string;
  sku_code: string;
  product_name: string;
  quantity: number;
  unit_price: Money | null;
  line_total: Money | null;
}

export interface ReservationSummaryResponse {
  id: string;
  status: string;
  expires_at: string;
  allocated_quantity: number;
  warehouse_ids: string[];
}

export interface OrderResponse {
  id: string;
  status: OrderStatus;
  currency: string;
  customer_reference?: string | null;
  total: Money | null;
  total_quantity: number;
  lines: OrderLineResponse[];
  reservation?: ReservationSummaryResponse | null;
  placed_at: string;
  updated_at: string;
  confirmed_at?: string | null;
  cancelled_at?: string | null;
  cancellation_reason?: string | null;
  version: number;
}

export type OrderPageResponse = Page<OrderResponse>;

// --- Fulfillment ---

export interface FulfillmentTaskResponse {
  task_id: string;
  warehouse_id: string;
  sku_id: string;
  quantity: number;
  is_completed: boolean;
}

export interface FulfillmentResponse {
  id: string;
  tenant_id: string;
  order_id: string;
  warehouse_id: string;
  status: string;
  tasks: FulfillmentTaskResponse[];
  tracking_number?: string | null;
  carrier?: string | null;
  failure_reason?: string | null;
  retry_count: number;
  max_retries: number;
  created_at: string;
  updated_at: string;
  version: number;
}

// --- Search Projections ---

export interface ProductSearchSkuResponse {
  sku_id: string;
  sku_code: string;
  unit_price_minor: number;
  currency: string;
  status: string;
}

export interface ProductSearchResultResponse {
  id: string;
  tenant_id: string;
  slug: string;
  name: string;
  description?: string | null;
  status: string;
  attributes: Record<string, unknown>;
  skus: ProductSearchSkuResponse[];
  created_at: string;
  updated_at: string;
  version: number;
  score?: number | null;
}

export interface ProductSearchPageResponse {
  items: ProductSearchResultResponse[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  next_cursor?: string | null;
}

export interface OrderSearchSkuResponse {
  sku_id: string;
  sku_code: string;
  quantity: number;
  unit_price_minor: number;
}

export interface OrderSearchResultResponse {
  id: string;
  tenant_id: string;
  order_id: string;
  customer_id?: string | null;
  customer_reference?: string | null;
  status: string;
  currency: string;
  total_minor: number;
  warehouse_ids: string[];
  skus: OrderSearchSkuResponse[];
  created_at: string;
  updated_at: string;
  version: number;
  score?: number | null;
}

export interface OrderSearchPageResponse {
  items: OrderSearchResultResponse[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  next_cursor?: string | null;
}

export interface AuditSearchResultResponse {
  id: string;
  tenant_id?: string | null;
  actor_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  correlation_id?: string | null;
  causation_id?: string | null;
  policy_decision: string;
  decision_reason: string;
  occurred_at: string;
  details: Record<string, unknown>;
  score?: number | null;
}

export interface AuditSearchPageResponse {
  items: AuditSearchResultResponse[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  next_cursor?: string | null;
}

export interface ReindexResponse {
  index_name?: string | null;
  alias?: string | null;
  total_indexed: number;
  status: string;
  task_id?: string | null;
}

export interface ReconciliationDiscrepancyResponse {
  document_id: string;
  tenant_id: string;
  discrepancy_type: string;
  details: Record<string, unknown>;
}

export interface ReconcileResponse {
  total_authoritative: number;
  total_search: number;
  matched: number;
  discrepancies: ReconciliationDiscrepancyResponse[];
  repaired_count: number;
  status: string;
  task_id?: string | null;
}

// --- Audit ---

export interface AuditEntryResponse {
  id: string;
  tenant_id?: string | null;
  actor_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  correlation_id?: string | null;
  causation_id?: string | null;
  occurred_at: string;
  details?: Record<string, unknown> | null;
}

export interface AuditPageResponse {
  items: AuditEntryResponse[];
  total: number;
}
