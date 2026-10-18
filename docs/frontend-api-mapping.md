# Arad Warehouse Platform — Frontend API Mapping

This document provides a comprehensive, verified mapping between frontend pages/components and the backend API endpoints exposed by the Arad Warehouse Platform.

**API Base URL**: Configured via `VITE_API_BASE_URL` (defaults to `/v1` for same-origin production and Vite dev proxy).  
**Backend Route Prefix**: `/v1` (configured via `ARAD_API_PREFIX`).

---

## 1. Authentication & Identity (`identity` Router)

### 1.1 User Login
- **Frontend Page/Component**: `LoginPage` (`/login`), `LoginForm`
- **Backend Endpoint**: `POST /v1/auth/login`
- **Request Schema**: `LoginRequest` (`{ email: string, password: string, tenant_id?: string | null }`)
- **Response Schema**: `AuthTokensResponse` (`{ access_token: string, refresh_token: string, token_type: string, expires_in: number, user: UserResponse, active_tenant_id: string | null, active_membership_id: string | null }`)
- **Authentication Requirement**: Public (Unauthenticated)
- **Required Permissions**: None
- **Interaction Behaviors**:
  - *Loading*: Disable submit button, show loading spinner.
  - *Success*: Store bearer access token in memory/session, store refresh token, update active tenant context, redirect to Dashboard or Tenant Selection (`/select-tenant`).
  - *Empty/Validation Error*: Display inline validation error messages for missing/invalid email or empty password.
  - *Authentication Error (401)*: Display "Invalid email or password" alert banner.
  - *Authorization Error (403)*: Display "User account or tenant is disabled" banner.
- **Automated Tests**:
  - Unit/Component: `tests/features/auth/LoginForm.test.tsx`
  - Integration: `tests/integration/auth_flow.test.ts`

### 1.2 User Registration
- **Frontend Page/Component**: `RegisterPage` (`/register`), `RegisterForm`
- **Backend Endpoint**: `POST /v1/auth/register`
- **Request Schema**: `RegisterUserRequest` (`{ email: string, password: string, tenant_id?: string | null, invitation_token?: string | null }`)
- **Response Schema**: `UserResponse` (`{ id: string, email: string, status: string, created_at: string, updated_at: string }`)
- **Authentication Requirement**: Public (Unauthenticated)
- **Required Permissions**: None
- **Interaction Behaviors**:
  - *Loading*: Button disabled with loading indicator.
  - *Success*: Success notification, navigate to login with email prefilled.
  - *Validation Error (400/422)*: Display inline password length (min 8) or email format validation errors.
  - *Conflict Error (409)*: Display "User with this email already exists".
- **Automated Tests**:
  - Unit/Component: `tests/features/auth/RegisterForm.test.tsx`

### 1.3 Token Refresh
- **Frontend Page/Component**: `apiClient` token refresh interceptor
- **Backend Endpoint**: `POST /v1/auth/refresh`
- **Request Schema**: `RefreshTokenRequest` (`{ refresh_token: string }`)
- **Response Schema**: `AuthTokensResponse`
- **Authentication Requirement**: Public with refresh token
- **Required Permissions**: None
- **Interaction Behaviors**:
  - *Loading*: Queue concurrent pending requests while token refresh completes.
  - *Success*: Update stored access token and retry queued requests with new token.
  - *Authentication Error (401)*: Clear stored credentials, clear tenant cache, redirect to `/login` with session-expired notification.
- **Automated Tests**:
  - Unit: `tests/api/client_auth_refresh.test.ts`

### 1.4 Logout
- **Frontend Page/Component**: `UserMenu` / `LogoutButton` in App Header
- **Backend Endpoint**: `POST /v1/auth/logout`
- **Request Schema**: `RefreshTokenRequest` (`{ refresh_token: string }`)
- **Response Status**: `204 No Content`
- **Authentication Requirement**: Authenticated caller
- **Required Permissions**: None
- **Interaction Behaviors**:
  - *Loading*: Session clearing indicator.
  - *Success/Completion*: Wipe in-memory and local tokens, clear active tenant state, clear query client cache, navigate to `/login`.
- **Automated Tests**:
  - Component: `tests/components/UserMenu.test.tsx`

### 1.5 Current Principal Profile
- **Frontend Page/Component**: `UserProfileView`, App Header Profile
- **Backend Endpoint**: `GET /v1/auth/me`
- **Request Schema**: None
- **Response Schema**: `PrincipalResponse` (`{ id: string, type: string, email: string | null, active_tenant_id: string | null, active_membership_id: string | null, account_status: string, role: string }`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`)
- **Required Permissions**: None
- **Interaction Behaviors**:
  - *Loading*: Header skeleton loader.
  - *Success*: Render user email, active tenant badge, account status.
  - *Authentication Error (401)*: Trigger session expiry redirect.
- **Automated Tests**:
  - Unit/Component: `tests/features/identity/UserProfileView.test.tsx`

### 1.6 Change Password
- **Frontend Page/Component**: `SecuritySettingsPage` (`/settings/security`), `ChangePasswordForm`
- **Backend Endpoint**: `POST /v1/auth/change-password`
- **Request Schema**: `ChangePasswordRequest` (`{ current_password: string, new_password: string }`)
- **Response Status**: `200 OK`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`)
- **Required Permissions**: Authenticated User
- **Interaction Behaviors**:
  - *Loading*: Submit button disabled with spinner.
  - *Success*: Success toast ("Password updated successfully. Other sessions have been revoked."), clear form fields.
  - *Validation Error (400/422)*: Display inline password policy guidance.
  - *Authentication Error (401)*: Display "Current password is incorrect".
- **Automated Tests**:
  - Component: `tests/features/identity/ChangePasswordForm.test.tsx`

### 1.7 List Tenant Memberships
- **Frontend Page/Component**: `TenantSelector`, `SelectTenantPage` (`/select-tenant`)
- **Backend Endpoint**: `GET /v1/auth/memberships`
- **Request Schema**: None
- **Response Schema**: `list[TenantMembershipResponse]` (`[{ id: string, user_id: string, tenant_id: string, status: string, role: string, created_at: string, tenant_name: string | null, tenant_slug: string | null, roles: string[] }]`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`)
- **Required Permissions**: Authenticated User
- **Interaction Behaviors**:
  - *Loading*: Tenant card skeletons.
  - *Success*: Render available tenants with badges for roles and status.
  - *Empty*: Render message "No tenant memberships found. Contact your administrator or register a tenant."
- **Automated Tests**:
  - Component: `tests/features/tenants/TenantSelector.test.tsx`

### 1.8 Select Active Tenant
- **Frontend Page/Component**: `TenantSelector`, `SelectTenantPage`
- **Backend Endpoint**: `POST /v1/auth/select-tenant`
- **Request Schema**: `SelectTenantRequest` (`{ tenant_id: string }`)
- **Response Schema**: `AuthTokensResponse` (with refreshed JWT token bound to `tenant_id`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`)
- **Required Permissions**: Membership in target tenant
- **Interaction Behaviors**:
  - *Loading*: Selection button spinner.
  - *Success*: Clear previous tenant query caches, update stored access tokens and active tenant ID, navigate to `/dashboard`.
  - *Authorization Error (403)*: Display "Membership disabled or tenant suspended".
- **Automated Tests**:
  - Component/Integration: `tests/features/tenants/SelectTenant.test.tsx`

### 1.9 Create Tenant Invitation
- **Frontend Page/Component**: `TenantMembershipsPage` (`/settings/members`), `InviteMemberModal`
- **Backend Endpoint**: `POST /v1/auth/invitations`
- **Request Schema**: `CreateInvitationRequest` (`{ email?: string | null, role: "ADMIN" | "MEMBER" }`)
- **Response Schema**: `InvitationResponse` (`{ invitation_token: string, tenant_id: string, email: string | null, role: string, expires_in_days: number }`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: Tenant Admin (`require_tenant_admin`)
- **Interaction Behaviors**:
  - *Loading*: Modal submit spinner.
  - *Success*: Modal reveals generated invitation token with copy-to-clipboard button and expiry notice.
  - *Authorization Error (403)*: "Only tenant administrators can generate invitations."
- **Automated Tests**:
  - Component: `tests/features/tenants/InviteMemberModal.test.tsx`

### 1.10 Provision User Membership
- **Frontend Page/Component**: `TenantMembershipsPage`, `AddMemberModal`
- **Backend Endpoint**: `POST /v1/auth/memberships`
- **Request Schema**: `ProvisionMembershipRequest` (`{ user_id?: string | null, email?: string | null, role: "ADMIN" | "MEMBER" }`)
- **Response Schema**: `TenantMembershipResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: Tenant Admin (`require_tenant_admin`)
- **Interaction Behaviors**:
  - *Loading*: Submit spinner.
  - *Success*: Toast notification, refresh memberships list.
  - *Conflict (409)*: "User is already a member of this tenant."
- **Automated Tests**:
  - Component: `tests/features/tenants/AddMemberModal.test.tsx`

### 1.11 Service Accounts Management
- **Frontend Page/Component**: `ServiceAccountsPage` (`/settings/service-accounts`)
- **Backend Endpoints**:
  - Create: `POST /v1/auth/service-accounts` (`CreateServiceAccountRequest`: `{ name: string, client_id: string, scopes: string[] }`) -> `ServiceAccountCreatedResponse` (displays client secret once)
  - Revoke: `DELETE /v1/auth/service-accounts/{service_account_id}` -> `204 No Content`
  - Rotate Secret: `POST /v1/auth/service-accounts/{service_account_id}/rotate` -> `ServiceAccountCreatedResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: Tenant Admin (`require_tenant_admin`)
- **Interaction Behaviors**:
  - *Loading*: Table/modal loading state.
  - *Success*: In create/rotate, render one-time dialog with client credentials and copy buttons.
  - *Empty*: "No service accounts configured for this tenant."
- **Automated Tests**:
  - Component: `tests/features/identity/ServiceAccountsPage.test.tsx`

---

## 2. Access Control & Authorization (`access` Router)

### 2.1 Effective Permissions & Roles Inspection
- **Frontend Page/Component**: `AccessOverviewCard`, Permissions Badge
- **Backend Endpoint**: `GET /v1/access/me`
- **Request Schema**: None
- **Response Schema**: `{ principal_id: string, email: string | null, active_tenant_id: string | null, roles: string[], permissions: string[] }`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`)
- **Required Permissions**: None (inspects caller's own permissions)
- **Interaction Behaviors**:
  - *Loading*: Placeholder badge.
  - *Success*: Populate permissions context provider to control UI action visibility.
- **Automated Tests**:
  - Unit/Component: `tests/features/access/AccessContext.test.tsx`

### 2.2 List Canonical System Permissions
- **Frontend Page/Component**: `RoleEditorModal`, `PolicyEditorModal`
- **Backend Endpoint**: `GET /v1/access/permissions`
- **Request Schema**: None
- **Response Schema**: `PermissionListResponse` (`{ permissions: [{ name: string, description: string, category: string }], total: number }`)
- **Authentication Requirement**: Authenticated
- **Required Permissions**: `access.roles.read`
- **Interaction Behaviors**:
  - *Loading*: Checkbox group skeleton.
  - *Success*: Render grouped selectable permission options by category (orders, inventory, catalog, fulfillment, audit, access).
- **Automated Tests**:
  - Component: `tests/features/access/PermissionSelector.test.tsx`

### 2.3 Roles Management
- **Frontend Page/Component**: `RolesPage` (`/access/roles`), `CreateRoleModal`, `RoleDetailsView`
- **Backend Endpoints**:
  - List: `GET /v1/access/roles` -> `list[RoleResponse]`
  - Get: `GET /v1/access/roles/{role_id}` -> `RoleResponse`
  - Create: `POST /v1/access/roles` (`CreateRoleRequest`: `{ name: string, description: string, permissions: string[] }`) -> `RoleResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `access.roles.read` (for GET), `access.roles.manage` (for POST)
- **Interaction Behaviors**:
  - *Loading*: Role table skeleton.
  - *Success*: Render roles table highlighting system roles vs. custom roles.
  - *Empty*: Show standard system roles (ADMIN, MEMBER, SUPPORT_AGENT, AUDITOR).
- **Automated Tests**:
  - Component: `tests/features/access/RolesPage.test.tsx`

### 2.4 Membership Role Assignment & Revocation
- **Frontend Page/Component**: `TenantMembershipsPage` role pills
- **Backend Endpoints**:
  - Assign: `POST /v1/access/memberships/{membership_id}/roles` (`AssignRoleRequest`: `{ role_name: string }`) -> `200 OK`
  - Revoke: `DELETE /v1/access/memberships/{membership_id}/roles/{role_name}` -> `204 No Content`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `access.roles.manage`
- **Interaction Behaviors**:
  - *Loading*: Role toggle spinner.
  - *Success*: Refresh membership roles list.
  - *Conflict (409)*: Display "Cannot remove last administrator of the tenant."
- **Automated Tests**:
  - Component: `tests/features/access/MembershipRoles.test.tsx`

### 2.5 Access Policies (ABAC) Management
- **Frontend Page/Component**: `PoliciesPage` (`/access/policies`), `CreatePolicyModal`, `PolicyDetailsView`
- **Backend Endpoints**:
  - List: `GET /v1/access/policies` -> `list[PolicyResponse]`
  - Get: `GET /v1/access/policies/{policy_id}` -> `PolicyResponse`
  - Create: `POST /v1/access/policies` (`CreatePolicyRequest`: `{ name: string, description: string, effect: "ALLOW"|"DENY", actions: string[], resources: string[], conditions: object }`) -> `PolicyResponse`
  - Toggle Status: `PATCH /v1/access/policies/{policy_id}/status` & `POST /v1/access/policies/{policy_id}/status` (`UpdatePolicyStatusRequest`: `{ status: "ACTIVE"|"INACTIVE" }`) -> `PolicyResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `access.policies.read` (for GET), `access.policies.manage` (for POST/PATCH)
- **Interaction Behaviors**:
  - *Loading*: Policy cards skeleton.
  - *Success*: Render policy cards with effect badge (ALLOW green, DENY red), actions, status toggle switch.
  - *Empty*: "No ABAC policies configured for this tenant."
- **Automated Tests**:
  - Component: `tests/features/access/PoliciesPage.test.tsx`

### 2.6 Policy Decision Evaluation (Simulator)
- **Frontend Page/Component**: `PolicySimulatorPage` (`/access/evaluate`), `EvaluationResultCard`
- **Backend Endpoint**: `POST /v1/access/evaluate`
- **Request Schema**: `DecisionEvaluationRequest` (`{ action: string, resource_type: string, resource_id?: string, resource_attributes?: object, environment?: object }`)
- **Response Schema**: `DecisionEvaluationResponse` (`{ allowed: bool, reason: string, reason_code: string, action: string, resource_type: string, policy_id?: string, missing_permissions: string[] }`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `access.policies.read`
- **Interaction Behaviors**:
  - *Loading*: Evaluation spinner.
  - *Success*: Render decision outcome: Allowed (Green) or Denied (Red), matching policy ID, and explicit reason code.
- **Automated Tests**:
  - Component: `tests/features/access/PolicySimulatorPage.test.tsx`

---

## 3. Platform & Tenant Provisioning (`platform` Router)

### 3.1 Provision Tenant
- **Frontend Page/Component**: `CreateTenantPage` (`/tenants/new`), `RegisterTenantModal`
- **Backend Endpoint**: `POST /v1/tenants`
- **Request Schema**: `RegisterTenantRequest` (`{ slug: string, name: string }`)
- **Response Schema**: `TenantResponse` (`{ id: string, slug: string, name: string, status: string, created_at: string, updated_at: string, version: number, invitation_token?: string }`)
- **Authentication Requirement**: Public onboarding or Authenticated Admin
- **Required Permissions**: None (Unauthenticated onboarding) or authenticated platform admin
- **Interaction Behaviors**:
  - *Loading*: Creation spinner.
  - *Success*: Displays tenant created message and bootstrap administrator invitation token.
  - *Conflict (409)*: "Tenant with this slug already exists."
- **Automated Tests**:
  - Component: `tests/features/tenants/CreateTenantPage.test.tsx`

### 3.2 Get Tenant Detail
- **Frontend Page/Component**: `TenantDetailsPage` (`/tenants/:id`)
- **Backend Endpoint**: `GET /v1/tenants/{tenant_id}`
- **Request Schema**: None
- **Response Schema**: `TenantResponse`
- **Authentication Requirement**: Authenticated
- **Required Permissions**: Member of target tenant
- **Interaction Behaviors**:
  - *Loading*: Details card skeleton.
  - *Success*: Displays tenant name, slug, status, creation date.
- **Automated Tests**:
  - Component: `tests/features/tenants/TenantDetailsPage.test.tsx`

---

## 4. Catalog & Products (`catalog` Router)

### 4.1 List Products
- **Frontend Page/Component**: `ProductListPage` (`/catalog/products`), `ProductTable`
- **Backend Endpoint**: `GET /v1/products`
- **Query Parameters**: `limit` (int, 1-200), `cursor` (string), `status` (`DRAFT`|`ACTIVE`|`ARCHIVED`)
- **Response Schema**: `PageSchema[ProductResponse]` (`{ items: ProductResponse[], next_cursor: string | null }`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `catalog.products.read`
- **Interaction Behaviors**:
  - *Loading*: Table skeleton with 5 rows.
  - *Success*: Render product rows with slug, name, status pill, SKU count, updated timestamp.
  - *Empty*: "No products found in catalog." with "Create Product" CTA.
  - *Pagination*: Next Page button active when `next_cursor` is present.
- **Automated Tests**:
  - Component: `tests/features/catalog/ProductListPage.test.tsx`

### 4.2 Create Product
- **Frontend Page/Component**: `CreateProductModal`
- **Backend Endpoint**: `POST /v1/products`
- **Request Schema**: `CreateProductRequest` (`{ slug: string, name: string, description?: string, attributes?: object }`)
- **Response Schema**: `ProductResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `catalog.products.manage`
- **Interaction Behaviors**:
  - *Loading*: Submit button spinner.
  - *Success*: Toast notification, modal close, prepend new product to table.
  - *Conflict (409)*: "A product with this slug already exists in this tenant."
- **Automated Tests**:
  - Component: `tests/features/catalog/CreateProductModal.test.tsx`

### 4.3 Product Detail & Lifecycle Operations
- **Frontend Page/Component**: `ProductDetailPage` (`/catalog/products/:id`)
- **Backend Endpoints**:
  - Get: `GET /v1/products/{product_id}` -> `ProductResponse`
  - Revise: `PUT /v1/products/{product_id}` (`ReviseProductRequest`: `{ name?: string, description?: string, attributes?: object }`) -> `ProductResponse`
  - Activate: `POST /v1/products/{product_id}/activate` -> `ProductResponse`
  - Archive: `POST /v1/products/{product_id}/archive` -> `ProductResponse`
  - Add SKU: `POST /v1/products/{product_id}/skus` (`CreateSkuRequest`: `{ code: string, name: string, unit_price_minor: number, currency: string, attributes?: object }`) -> `SkuResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `catalog.products.read` (GET), `catalog.products.manage` (Mutations)
- **Interaction Behaviors**:
  - *Loading*: Page skeleton.
  - *Lifecycle Buttons*: "Activate" visible only when status is `DRAFT`; "Archive" visible when status is `ACTIVE` or `DRAFT`.
  - *Confirmation Dialog*: Destructive action "Archive Product" requires modal confirmation.
- **Automated Tests**:
  - Component: `tests/features/catalog/ProductDetailPage.test.tsx`

### 4.4 SKU Operations
- **Frontend Page/Component**: `SkuDetailsModal`, `ChangePriceModal`
- **Backend Endpoints**:
  - Get: `GET /v1/skus/{sku_id}` -> `SkuResponse`
  - Change Price: `PUT /v1/skus/{sku_id}/price` (`ChangeSkuPriceRequest`: `{ unit_price_minor: number, currency: string }`) -> `SkuResponse`
  - Activate SKU: `POST /v1/skus/{sku_id}/activate` -> `SkuResponse`
  - Deactivate SKU: `POST /v1/skus/{sku_id}/deactivate` -> `SkuResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `catalog.products.read` (GET), `catalog.products.manage` (Mutations)
- **Interaction Behaviors**:
  - *Price Form*: Currency input (3-letter ISO code) and minor units (cents/pence) with formatted display.
  - *Success*: Update SKU price in table and toast notification.
- **Automated Tests**:
  - Component: `tests/features/catalog/SkuOperations.test.tsx`

---

## 5. Inventory & Warehouses (`inventory` Router)

### 5.1 Warehouses Management
- **Frontend Page/Component**: `WarehouseListPage` (`/inventory/warehouses`), `RegisterWarehouseModal`
- **Backend Endpoints**:
  - List: `GET /v1/warehouses` (Query: `limit`, `cursor`) -> `WarehousePageResponse`
  - Get: `GET /v1/warehouses/{warehouse_id}` -> `WarehouseResponse`
  - Register: `POST /v1/warehouses` (`RegisterWarehouseRequest`: `{ code: string, name: string }`) -> `WarehouseResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `inventory.read` (List/Get), `inventory.adjust` (Register)
- **Interaction Behaviors**:
  - *Loading*: Warehouse card skeletons.
  - *Success*: Render warehouse cards with active status, warehouse code, and creation date.
  - *Conflict (409)*: "Warehouse with this code already registered."
- **Automated Tests**:
  - Component: `tests/features/inventory/WarehouseListPage.test.tsx`

### 5.2 Stock Availability & Inventory Levels
- **Frontend Page/Component**: `InventoryPage` (`/inventory/availability`), `StockLevelTable`
- **Backend Endpoint**: `GET /v1/inventory/availability`
- **Query Parameters**: `warehouse_id` (UUID), `sku_id` (UUID), `limit` (int), `cursor` (string)
- **Response Schema**: `AvailabilityPageResponse` (`{ items: AvailabilityResponse[], next_cursor: string | null }`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `inventory.read`
- **Interaction Behaviors**:
  - *Table Columns*: Warehouse ID, SKU ID, On Hand, Reserved, Available Quantity (`on_hand - reserved`), Last Updated.
  - *Filter*: Warehouse dropdown and SKU filter.
  - *Empty*: "No inventory records matching selected filters."
- **Automated Tests**:
  - Component: `tests/features/inventory/InventoryPage.test.tsx`

### 5.3 Stock Adjustments
- **Frontend Page/Component**: `StockAdjustmentModal` (`/inventory/adjust`)
- **Backend Endpoint**: `POST /v1/inventory/adjustments`
- **Request Schema**: `AdjustInventoryRequest` (`{ warehouse_id: string, sku_id: string, quantity_delta: number, reason: AdjustmentReason, note?: string }`)
- **Response Schema**: `AdjustmentResponse` (`{ item: AvailabilityResponse, movement: MovementResponse }`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `inventory.adjust`
- **Interaction Behaviors**:
  - *Delta Validation*: Positive number for additions (GOODS_RECEIPT, CUSTOMER_RETURN), negative for write-offs (DAMAGE, CYCLE_COUNT). Delta cannot be zero.
  - *Confirmation Step*: Review dialog before submitting mutation.
  - *Error (409)*: "Stock adjustment below reserved quantity is rejected."
- **Automated Tests**:
  - Component: `tests/features/inventory/StockAdjustmentModal.test.tsx`

### 5.4 Standalone Stock Reservations
- **Frontend Page/Component**: `ReservationsPage` (`/inventory/reservations`), `CreateReservationModal`
- **Backend Endpoints**:
  - Create: `POST /v1/reservations` (`ReserveInventoryRequest`: `{ requirements: [{ sku_id: string, quantity: number, order_reference?: string }], warehouse_ids?: string[], strategy: "ALL_OR_NOTHING"|"PARTIAL", ttl_seconds?: number }`) -> `ReservationResultResponse`
  - Get: `GET /v1/reservations/{reservation_id}` -> `ReservationResponse`
  - Release: `DELETE /v1/reservations/{reservation_id}` -> `ReservationResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `reservations.create` (Create), `reservations.release` (Release), `inventory.read` (Get)
- **Interaction Behaviors**:
  - *Shortfall Display*: If `is_complete` is false, render detailed shortfall table showing required, allocated, and missing counts.
  - *Expiry Pill*: Show remaining TTL countdown or EXPIRED badge.
- **Automated Tests**:
  - Component: `tests/features/inventory/ReservationsPage.test.tsx`

---

## 6. Ordering & Lifecycle (`ordering` Router)

### 6.1 List Orders
- **Frontend Page/Component**: `OrderListPage` (`/orders`), `OrderTable`
- **Backend Endpoint**: `GET /v1/orders`
- **Query Parameters**: `limit` (int, 1-200), `cursor` (string), `status` (`PENDING`|`CONFIRMED`|`CANCELLED`)
- **Response Schema**: `OrderPageResponse` (`{ items: OrderResponse[], next_cursor: string | null }`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `orders.read` (Financials redacted if caller lacks `orders.financials.read`)
- **Interaction Behaviors**:
  - *Loading*: Table skeleton.
  - *Status Badges*: PENDING (amber), CONFIRMED (blue), CANCELLED (neutral grey).
  - *Financials Redaction*: Shows "—" or "Restricted" when user lacks financials permission.
- **Automated Tests**:
  - Component: `tests/features/orders/OrderListPage.test.tsx`

### 6.2 Submit Order (with Idempotency)
- **Frontend Page/Component**: `CreateOrderPage` (`/orders/new`), `OrderBuilder`
- **Backend Endpoint**: `POST /v1/orders`
- **Header**: `Idempotency-Key: <unique-uuid-v4>`
- **Request Schema**: `CreateOrderRequest` (`{ lines: [{ sku_id: string, quantity: number }], customer_reference?: string }`)
- **Response Schema**: `OrderResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `orders.create`
- **Interaction Behaviors**:
  - *Idempotency*: Generates a stable UUID v4 for the submission attempt. If submission fails due to ambiguous network timeout, the retry retains the exact same `Idempotency-Key`.
  - *Shortage / Backorder*: If backend configured with `BACKORDER`, order is created in `PENDING` with shortfall notes. If `REJECT`, server returns 409 `insufficient_inventory` with shortfalls breakdown.
- **Automated Tests**:
  - Component: `tests/features/orders/CreateOrderPage.test.tsx`
  - Unit: `tests/features/orders/idempotency.test.ts`

### 6.3 Order Details & Lifecycle Actions
- **Frontend Page/Component**: `OrderDetailPage` (`/orders/:id`)
- **Backend Endpoints**:
  - Get: `GET /v1/orders/{order_id}` -> `OrderResponse`
  - Confirm: `POST /v1/orders/{order_id}/confirm` -> `OrderResponse`
  - Cancel: `POST /v1/orders/{order_id}/cancel` (`CancelOrderRequest`: `{ reason: string }`) -> `OrderResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `orders.read` (GET), `orders.confirm` (Confirm), `orders.cancel` (Cancel)
- **Interaction Behaviors**:
  - *Actions*: "Confirm Order" button enabled only if status is `PENDING`. "Cancel Order" prompts for cancellation reason modal.
  - *Lines Table*: Line number, product name, SKU code, quantity, unit price, total price.
  - *Reservation Summary*: Shows allocation status, allocated quantity, warehouse IDs, expiry timestamp.
- **Automated Tests**:
  - Component: `tests/features/orders/OrderDetailPage.test.tsx`

---

## 7. Fulfillment & Carrier Dispatch (`fulfillment` Router)

### 7.1 Request Fulfillment
- **Frontend Page/Component**: `RequestFulfillmentModal` on Order Detail Page
- **Backend Endpoint**: `POST /v1/fulfillments`
- **Header**: Optional `Idempotency-Key`
- **Request Schema**: `RequestFulfillmentRequest` (`{ order_id: string, warehouse_id: string, requirements: [{ sku_id: string, quantity: number }], max_retries?: number }`)
- **Response Schema**: `FulfillmentResponse` (`{ id: string, tenant_id: string, order_id: string, warehouse_id: string, status: string, tasks: FulfillmentTaskResponse[], tracking_number: string | null, carrier: string | null, failure_reason: string | null, retry_count: number, max_retries: number, created_at: string, updated_at: string, version: number }`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `fulfillments.create`
- **Interaction Behaviors**:
  - *Warehouse Selection*: Warehouse-scoped ABAC applies.
  - *Success*: Toast notification, redirect to Fulfillment Details.
- **Automated Tests**:
  - Component: `tests/features/fulfillment/RequestFulfillmentModal.test.tsx`

### 7.2 Fulfillment Detail & Status Inspection
- **Frontend Page/Component**: `FulfillmentDetailPage` (`/fulfillments/:id`)
- **Backend Endpoint**: `GET /v1/fulfillments/{fulfillment_id}`
- **Response Schema**: `FulfillmentResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `fulfillments.read`
- **Interaction Behaviors**:
  - *Status Card*: Status pill (`PENDING`, `ALLOCATED`, `DISPATCHED`, `FAILED`, `CANCELLED`).
  - *Carrier Info*: Displays carrier name and tracking number if dispatched.
  - *Failure Reason*: Render alert box with error explanation if status is `FAILED`.
- **Automated Tests**:
  - Component: `tests/features/fulfillment/FulfillmentDetailPage.test.tsx`

### 7.3 Retry Failed Fulfillment
- **Frontend Page/Component**: `FulfillmentDetailPage` Retry Button
- **Backend Endpoint**: `POST /v1/fulfillments/{fulfillment_id}/retry`
- **Header**: Optional `Idempotency-Key`
- **Response Schema**: `FulfillmentResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `fulfillments.modify`
- **Interaction Behaviors**:
  - *Loading*: Button disabled with "Scheduling retry...".
  - *Success*: Status updates from `FAILED` to updated state; incremented retry count displayed.
  - *Error (409)*: "Maximum retry attempts exceeded or fulfillment not in failed state."
- **Automated Tests**:
  - Component: `tests/features/fulfillment/FulfillmentDetailPage.test.tsx`

---

## 8. Search & Read Models (`search` Router)

### 8.1 Search Products
- **Frontend Page/Component**: `ProductSearchPage` (`/search/products`), Search Header Bar
- **Backend Endpoint**: `GET /v1/search/products`
- **Query Parameters**: `q`, `status`, `sku`, `page`, `page_size`, `cursor`, `limit`, `sort_by`, `sort_order`
- **Response Schema**: `ProductSearchPageResponse` (`{ items: ProductSearchResultResponse[], total: number, page: number, page_size: number, total_pages: number, next_cursor: string | null }`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `catalog.products.read`
- **Interaction Behaviors**:
  - *Free-text Search*: Debounced search input (300ms).
  - *Relevance Scoring*: Shows score indicator if query string provided.
  - *Catch-up Notice*: Explains that newly created products appear authoritative immediately in Postgres and will index to search shortly.
- **Automated Tests**:
  - Component: `tests/features/search/ProductSearchPage.test.tsx`

### 8.2 Search Orders
- **Frontend Page/Component**: `OrderSearchPage` (`/search/orders`)
- **Backend Endpoints**: `GET /v1/search/orders` & `GET /v1/orders/search`
- **Query Parameters**: `q`, `status`, `sku`, `customer`, `warehouse`, `date_from`, `date_to`, `page`, `page_size`, `cursor`, `limit`, `sort_by`, `sort_order`
- **Response Schema**: `OrderSearchPageResponse` (`{ items: OrderSearchResultResponse[], total: number, page: number, page_size: number, total_pages: number, next_cursor: string | null }`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `orders.read`
- **Interaction Behaviors**:
  - *Filters*: Date range picker, order status multi-select, warehouse selector, customer reference query.
- **Automated Tests**:
  - Component: `tests/features/search/OrderSearchPage.test.tsx`

### 8.3 Search Audit Logs
- **Frontend Page/Component**: `AuditSearchPage` (`/search/audit`)
- **Backend Endpoint**: `GET /v1/search/audit`
- **Query Parameters**: `q`, `action`, `actor_id`, `resource_type`, `resource_id`, `date_from`, `date_to`, `page`, `page_size`, `cursor`, `limit`, `sort_by`, `sort_order`
- **Response Schema**: `AuditSearchPageResponse` (`{ items: AuditSearchResultResponse[], total: number, page: number, page_size: number, total_pages: number, next_cursor: string | null }`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `audit.events.read`
- **Interaction Behaviors**:
  - *Decision Filtering*: Filter by policy decision ALLOW or DENY.
  - *Details Viewer*: Safe collapsible JSON viewer for audit payload and decision reason.
- **Automated Tests**:
  - Component: `tests/features/search/AuditSearchPage.test.tsx`

### 8.4 Search Index Maintenance (Admin Only)
- **Frontend Page/Component**: `SearchMaintenancePage` (`/settings/search-maintenance`)
- **Backend Endpoints**:
  - Reindex: `POST /v1/search/reindex` (`ReindexRequest`: `{ tenant_id?: string, retain_old_indices?: boolean, async_mode?: boolean }`) -> `ReindexResponse`
  - Reconcile: `POST /v1/search/reconcile` (`ReconcileRequest`: `{ tenant_id?: string, auto_repair?: boolean, async_mode?: boolean }`) -> `ReconcileResponse`
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: Tenant Administrator / Platform Admin (`get_admin_context`)
- **Interaction Behaviors**:
  - *Reconcile Output*: Displays total authoritative vs. total search items, discrepancy count, repaired count.
  - *Confirmation Modal*: Warning before rebuilding index.
- **Automated Tests**:
  - Component: `tests/features/search/SearchMaintenancePage.test.tsx`

---

## 9. Immutable Audit Trail (`audit` Router)

### 9.1 Tenant Audit Events Stream
- **Frontend Page/Component**: `AuditEventsPage` (`/audit`), `AuditEventTable`
- **Backend Endpoint**: `GET /v1/audit/events`
- **Query Parameters**: `limit` (int, 1-200, default 50)
- **Response Schema**: `AuditPageResponse` (`{ items: AuditEntryResponse[], total: number }`)
- **Authentication Requirement**: Authenticated (`Authorization: Bearer <token>`, `X-Tenant-Id: <tenant_id>`)
- **Required Permissions**: `audit.events.read`
- **Interaction Behaviors**:
  - *Read-only Invariant*: Strictly read-only; no edit, delete, or mutation controls exist.
  - *Event Detail Drawer*: Inspects actor ID, action, resource type, resource ID, correlation ID, occurred timestamp, and details JSON.
- **Automated Tests**:
  - Component: `tests/features/audit/AuditEventsPage.test.tsx`

---

## 10. Health & Readiness Probes (`health` Router)

### 10.1 Probes
- **Frontend Page/Component**: `SystemStatusIndicator` in App Footer / Top Bar
- **Backend Endpoints**:
  - Liveness: `GET /healthz` -> `HealthResponse` (`{ status: "ok", environment: string, checks: { database: string } }`)
  - Readiness: `GET /readyz` -> `HealthResponse`
- **Authentication Requirement**: Public
- **Required Permissions**: None
- **Interaction Behaviors**:
  - *Indicator*: Green dot when `status == "ok"`, Amber when degraded, Red when unreachable.
- **Automated Tests**:
  - Component: `tests/components/SystemStatusIndicator.test.tsx`

---

## 11. Documented Gaps & Deliberate Exclusions

| Capability | Documented In Spec | Usable Backend Endpoint | Resolution / Frontend Behavior |
|---|---|---|---|
| User Self-Service Password Reset (Forgot Password email token) | Mentioned as generic auth feature in some docs | No endpoint exists in identity router | Excluded: UI provides standard change password when authenticated. No mock forgot-password flow. |
| User Profile Picture Upload | General web UI pattern | No endpoint exists | Excluded: User profile renders monogram avatar based on email initials. |
| CSV / Excel Export of Orders & Catalog | General admin pattern | No backend export endpoints exist | Excluded: UI does not claim or simulate export download links. |
| Direct RabbitMQ / Celery Queue Flushing | Infrastructure | No application management API exists | Excluded: Infrastructure internals are not exposed in UI. |
| Public User Registration without Tenant/Invitation | Open registration | Backend requires invitation token or creates orphaned user | Supported: Registration allows invitation token; if absent, user must select or create a tenant. |
