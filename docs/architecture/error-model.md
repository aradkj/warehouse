# Error model

One taxonomy, one HTTP contract, one place that maps between them. A caller should be
able to handle an Arad error without knowing which layer produced it, and should never
see an internal detail in exchange for it.

## The three kinds of failure

| Kind | Raised by | Examples | Framework-free? |
| --- | --- | --- | --- |
| **Domain** | aggregates and value objects, when a business rule is violated | `InsufficientInventory`, `InvalidOrderTransition`, `ReservationExpired`, `StockAdjustmentBelowReserved`, `InvalidValue`, `InvariantViolation`, `CrossTenantAccess`, `ProductNotPurchasable` | yes (`shared/domain`, `*/domain/errors.py`) |
| **Application** | use cases and the application kernel | `NotFound`, `InvalidRequest`, `ConcurrencyConflict`, `DuplicateResource`, `DuplicateIdempotencyKey`, `IdempotencyOperationInProgress`, `IdempotencyKeyRequired`, `MissingReference` | yes (`shared/application/errors.py`) |
| **Request/framework** | FastAPI, Pydantic, PostgreSQL | request validation, payload too large, unhandled constraint violation | mapped at the edge only |

The first two are ordinary Python exceptions with a stable `code` and optional
`details`. Neither imports FastAPI; that is what lets the domain suite reason about
failures as values.

## The wire contract

```json
{
  "error": {
    "code": "insufficient_inventory",
    "message": "the requested quantity is not available",
    "details": {"sku_id": "…", "requested": 5, "available": 3},
    "correlation_id": "b0f1…",
    "type": "domain"
  }
}
```

* `code` — stable, machine-readable, snake_case. A client may branch on it; it does not
  change without a version bump.
* `message` — human-readable, safe to log, never a stack trace.
* `details` — structured context (never a formatted string), absent rather than null
  when there is nothing to say.
* `correlation_id` — the same value as the `X-Correlation-Id` response header and the
  access log line, so a user-reported error can be found in the logs.
* `type` — `domain`, `application`, `request`, `http` or `internal`.

## Status mapping

| Status | Codes |
| --- | --- |
| 400 | `invalid_request` (including an `Idempotency-Key` outside 1–255 characters), `idempotency_key_required` |
| 404 | `*_not_found` (`order_not_found`, `sku_not_found`, `reservation_not_found`, …), `cross_tenant_access` |
| 409 | `concurrency_conflict`, `insufficient_inventory`, `reservation_expired`, `invalid_*_transition`, `stock_adjustment_below_reserved`, `warehouse_not_active`, `order_lines_not_reserved`, `duplicate_resource`, `missing_reference`, `idempotency_key_reused`, `idempotency_in_progress` |
| 413 | `payload_too_large` |
| 422 | `validation_error` (Pydantic), `invalid_value`, `invalid_order`, `invalid_adjustment`, `invalid_product`, `invalid_reservation`, `duplicate_order_line` |
| 500 | `internal_error` — and `invariant_violation`, which is mapped to 500 on purpose: an aggregate invariant that fires means a bug reached the domain, not that the caller did something wrong. An unmapped code is a bug, not a shortcut. |

**A cross-tenant resource is reported as 404, never 403.** The answer to "does this
exist for you?" must not distinguish "another tenant owns it" from "it does not
exist" ([ADR-0010](../decisions/0010-multi-tenancy.md)).

**Mapped-by-code first, by-type second.** `bootstrap/errors.py` resolves a status from
`_DOMAIN_STATUS_BY_CODE` and falls back to an exception-type table; anything unknown
becomes `500 internal_error`. Adding a domain error without registering it therefore
fails loudly (a 500 in tests) instead of returning a plausible-but-wrong status.

## Rules

1. **Never leak internals.** No stack trace, SQL statement, parameter value, driver
   message, connection string or file path is ever serialised. Unexpected exceptions are
   logged in full server-side and answered with `internal_error` plus the correlation id.
2. **Never swallow.** A `try/except` that continues silently is forbidden; handlers
   either re-raise, translate deliberately, or record and re-raise. `except Exception:
   pass` does not exist in this codebase.
3. **Fail closed on tenant scope.** A missing tenant context is `400 invalid_request`,
   not "no filter".
4. **One envelope everywhere**, including 404 for an unknown path and 405 for a wrong
   method, so a client needs one error parser.
5. **Validation detail is grouped, not flat.** Pydantic failures become
   `details.errors[i] = {loc, type, msg, ctx}` so a client can render per-field messages.
6. **Readiness is not an error page.** `/readyz` answers 503 with
   `{"checks": {"database": "unreachable"}}` — deliberately terse; it exposes no
   version, no connection string and no driver text.

## Testing

* `tests/api/test_api.py::TestErrorContract` asserts the five keys, the correlation-id
  echo, and that no response body contains SQL, a traceback or a driver name.
* Each domain invariant has a test that provokes it through HTTP and asserts the code
  and status (409 for `insufficient_inventory`, `invalid_order_transition`, …).
* `tests/application/` asserts the same exceptions are raised by the use cases before
  any HTTP layer is involved — the mapping is thin, the behaviour is not.
