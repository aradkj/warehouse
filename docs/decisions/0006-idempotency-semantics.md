# 6. Idempotency: one key, one operation, one result

* Status: Accepted
* Date: 2026-10-06
* Phase: 1

## Context

Every mutation is reachable over HTTP, so every mutation can be retried: by a client
that timed out, by a proxy, by an operator pressing the button twice. The
specification requires that `(tenant_id, idempotency_key)` prevents duplicate
operations, that a replay must not create another order or reserve inventory a second
time, that both attempts resolve to the same business result, and that the exact
semantics are documented. It also requires the guarantee to be **tenant-scoped**:
two tenants using the same key must not interfere.

## Decision

A mutation request may carry `Idempotency-Key: <1–255 chars>`; `POST /v1/orders`
*requires* it (`CreateOrder.idempotency_required = True`), every other mutation
accepts it. The pair `(tenant_id, idempotency_key)` is recorded in
`idempotency_records`, **inside the same transaction as the business change**.

### The flow

```text
use case enters the unit of work (one transaction)
  claim(tenant, key):
      INSERT INTO idempotency_records … ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
        ├─ inserted                    → STARTED   — run the business write
        └─ nothing inserted → read the existing row
              ├─ completed + same fingerprint    → REPLAY
              ├─ completed + different fingerprint→ DuplicateIdempotencyKey   (409)
              └─ in_progress                     → IdempotencyOperationInProgress (409)
  on STARTED:  _execute() the business operation
               dispatch domain events (same transaction)
               complete(record: resource_type, resource_id, response_status)
  on no durable resource:  abandon()  (deletes the claim)
  commit  — the claim and the business change become visible together, or not at all
```

* **Fingerprint.** The request fingerprint is a SHA-256 over the command's business
  fields, canonicalised (`shared.application.serialization.canonical`), *excluding*
  the request context (correlation id, actor, key itself). Same key + same payload ⇒
  replay; same key + different payload ⇒ `409 idempotency_key_reused`. Silently
  returning the first result for a different request would be worse than an error.
* **Replay returns the resource, not a stored body.** The record stores
  `resource_type`, `resource_id` and `response_status`; `UseCase._reload()` re-reads
  the resource and returns its *current* representation with
  `Idempotent-Replay: true` and the original status code. The table therefore has no
  response blob to become stale, and it stays small.
* **Errors are not memorised.** Only a committed operation is recorded. If the
  transaction rolls back — a domain error, a constraint violation, a crash — the claim
  disappears with it, so the client's retry is a clean first attempt rather than a
  permanent "in progress" tombstone. The API replies to the failed attempt with the
  normal error envelope and **no** `Idempotent-Replay` header, so a client can always
  tell a replay from a real execution.
* **Tenant scoping is structural.** The unique index is on
  `(tenant_id, idempotency_key)`; the same key used by another tenant is an unrelated
  record. A key without a tenant is rejected (`IdempotencyKeyRequired` /
  `InvalidRequest`) because an unscoped key would be a cross-tenant leak.
* **Retention.** Records are kept for `ARAD_IDEMPOTENCY_RETENTION_SECONDS`
  (default 7 days, `DEFAULT_IDEMPOTENCY_RETENTION`). Cleanup by `expires_at` is a
  Phase 2 job; nothing in Phase 1 deletes records, and the column plus its index are
  in place.
* **No nested keys.** A cross-context call made *inside* a use case
  (`InProcessInventoryOperations`) runs on the caller's scope and deliberately does
  **not** inherit the caller's `Idempotency-Key`: the key identifies the caller's
  operation, not each internal step.

### Guarantees the tests assert

| Claim | Test |
| --- | --- |
| the same key twice creates one order, one reservation, one hold | `tests/application/test_order_submission.py::TestIdempotentSubmission`, `tests/api/test_api.py::TestIdempotencyOverHttp`, `tests/concurrency/test_inventory_concurrency.py::TestConcurrentOrdersAndIdempotency` |
| the same key with a different payload is refused | `TestIdempotencyOverHttp` |
| keys are per tenant | `TestIdempotencyOverHttp`, `TestTenantIsolationOverHttp` |
| a duplicate arrives while the first is still running | `TestIdempotencyOverHttp` (planted `in_progress` row) |
| a rolled-back attempt leaves no claim | `TestIdempotencyOverHttp` |
| the record commits with the order, never before | `tests/repository/test_repositories.py::TestOrderPersistence` |
| two *different* keys produce two orders | `TestConcurrentOrdersAndIdempotency` |

## Consequences

* Retrying a request is safe, and the safety is a database invariant (a unique index),
  not an application convention that a second code path could forget.
* Because the claim is in the business transaction, concurrency is handled by
  PostgreSQL: the loser of a race sees the winner's in-progress row and answers 409
  instead of doing the work twice.
* Mutation endpoints must be able to rebuild their view from `resource_id`; that is the
  only extra obligation idempotency places on a use case.
* The record is not a general-purpose request log: it stores no bodies and is
  deliberately short-lived.

## Alternatives considered

* **Storing the response body** — rejected: it duplicates the representation, grows the
  table, needs invalidation semantics, and would let a replay serve data that has since
  changed.
* **An in-memory / Redis dedupe set** — rejected: it disappears on restart exactly when
  a retry is most likely, and the specification rules Redis out for Phase 1.
* **An "insert the record first, in its own transaction" design** — rejected: a crash
  between the claim and the work would leave a permanent in-progress record and the
  business write could still be lost.
* **Ignoring a mismatched payload (returning the first result regardless)** — rejected:
  it turns a client bug into silent data loss.
