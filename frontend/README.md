# Arad Warehouse Platform — Frontend Application

Modern administrative single-page application (SPA) for the Arad Distributed Order and Inventory Orchestration Platform.

Built with **React 18**, **TypeScript 5**, **Vite 6**, and **React Router 6**, the frontend provides a complete, restrained, and fully-functional interface for all operational and administrative capabilities exposed by the backend.

---

## 1. Architecture & Design Principles

- **Zero Mock Data / Real CRUD:** Every form, filter, search, button, and table is wired directly to backend API endpoints (`/v1/*`). There are no decorative or mock controls.
- **Tenant Isolation:** Multi-tenant security invariants are strictly enforced. The active tenant is maintained in the authenticated JWT context, and all tenant-scoped caches are immediately cleared whenever tenant context switches.
- **Idempotency Protection:** Order creations and state-changing mutations generate stable `Idempotency-Key` headers that are preserved across retries of ambiguous network failures, preventing duplicate operations.
- **Restrained Administrative Aesthetic:** Designed for high-density warehouse operations with clear typography, high color contrast, accessible keyboard navigation, and explicit error states displaying backend correlation IDs.
- **Unified Single-Image Deployment:** The built frontend assets (`frontend/dist`) are embedded directly into the production Docker image (`ghcr.io/aradkj/warehouse`) and served via FastAPI with SPA client-side fallback routing, avoiding the complexity of a separate web server container.

---

## 2. Directory Structure

```text
frontend/
├── public/                 # Static public assets (e.g. vite.svg)
├── src/
│   ├── api/
│   │   ├── client.ts       # Typed HTTP client with token handling, tenant headers, and retries
│   │   ├── errors.ts       # Centralized ApiError mapping and user-friendly error formatting
│   │   ├── types.ts        # Comprehensive TypeScript types matching backend Pydantic schemas
│   │   └── endpoints/      # Domain-scoped API modules (auth, orders, catalog, inventory, etc.)
│   ├── app/
│   │   ├── App.tsx         # Root component with Providers (Auth, Tenant, Toast)
│   │   ├── router.tsx      # React Router definition covering all operations
│   │   └── providers/      # React contexts (AuthContext, TenantContext, ToastContext)
│   ├── components/
│   │   ├── feedback/       # ToastContainer, EmptyState, SystemStatusIndicator
│   │   ├── layout/         # AppLayout, Header, Sidebar, Breadcrumbs
│   │   └── ui/             # Button, Input, Select, Modal, Card, Badge, Alert, Pagination
│   ├── features/
│   │   ├── access/         # RolesPage, PoliciesPage, PolicySimulatorPage
│   │   ├── audit/          # AuditEventsPage
│   │   ├── auth/           # LoginPage, RegisterPage
│   │   ├── catalog/        # ProductListPage, ProductDetailPage, CreateProductModal
│   │   ├── dashboard/      # DashboardPage (Overview of active operations and stock)
│   │   ├── fulfillment/    # FulfillmentLookupPage, FulfillmentDetailPage, RequestFulfillmentModal
│   │   ├── identity/       # TenantMembershipsPage, ServiceAccountsPage, SecuritySettingsPage
│   │   ├── inventory/      # InventoryPage, WarehouseListPage, ReservationsPage, StockAdjustmentModal
│   │   ├── orders/         # OrderListPage, CreateOrderPage, OrderDetailPage
│   │   ├── search/         # OrderSearchPage, ProductSearchPage, AuditSearchPage, SearchMaintenancePage
│   │   └── tenants/        # SelectTenantPage
│   ├── styles/
│   │   └── index.css       # Core design system and accessible neutral styling
│   ├── main.tsx            # Application entry point
│   └── vite-env.d.ts       # Vite environment types
├── tests/
│   ├── components/         # Interaction tests (AuthFlow, TenantSwitch, OrderForm, StockAdjustment, PolicySimulator)
│   ├── unit/               # Unit tests for ApiClient and error normalization
│   └── setup.ts            # Vitest environment setup and Storage mock
├── index.html              # HTML5 host document
├── package.json            # Pinned dependencies and build scripts
├── package-lock.json       # Deterministic dependency lockfile
├── tsconfig.json           # Strict TypeScript configuration
└── vite.config.ts          # Vite build and proxy configuration
```

---

## 3. Local Development

### Prerequisites
- Node.js 22+
- npm 10+
- Running backend on `http://127.0.0.1:8000` (or Docker Compose)

### Setup & Run
```powershell
# In repository root
cd frontend

# Install dependencies from committed lockfile
npm ci

# Start local development server with Vite proxying to http://127.0.0.1:8000
npm run dev
```

The Vite dev server will start at `http://localhost:3000`. Requests to `/v1/*`, `/healthz`, and `/readyz` are automatically proxied to the backend target configured in `VITE_BACKEND_TARGET` (defaulting to `http://127.0.0.1:8000`).

---

## 4. Testing & Verification

The frontend test suite includes unit tests for the typed API client, error translation, session handling, and component interaction tests for critical domain workflows.

```powershell
# Run the complete test suite
npm test

# Run tests in watch mode
npm run test:watch

# Verify TypeScript types and execute production build
npm run build
```

---

## 5. Production Build & Docker Serving

In production, Vite compiles the TypeScript SPA into static HTML, JS, and CSS bundles in `frontend/dist`.

When building the container image (`Dockerfile`), a multi-stage Docker build runs:
1. `node:22-bookworm-slim AS frontend-builder`: executes `npm ci` and `npm run build`.
2. `runtime`: copies `/build/frontend/dist` to `/app/frontend/dist` with `chown arad:arad`.
3. FastAPI mounts `/app/frontend/dist` using `mount_frontend_spa` from `src/bootstrap/frontend.py`.

### Routing Precedence
1. Health and readiness probes (`/healthz`, `/readyz`) match first.
2. OpenAPI documentation and schemas (`/docs`, `/openapi.json`) match first.
3. All API routes under `/v1/*` match first. Any unmatched path under `/v1/*` returns an authoritative JSON 404 error, never HTML.
4. Static assets under `/assets/*` and root assets (e.g. `vite.svg`) are served directly.
5. All other application navigation paths (e.g. `/orders/new`, `/catalog/products/123`) fall back to `index.html` for client-side routing.
6. Path traversal attempts (e.g. `/../../etc/passwd`) are strictly prevented via `is_relative_to` path verification.
