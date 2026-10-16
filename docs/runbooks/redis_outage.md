# Runbook: Redis Outage, Eviction, and Cache Invalidation Failure

## 1. Symptoms
- Prometheus alert: `RedisDown` (`redis_up == 0`) or `RedisCacheHitRatioLow` (`redis_cache_hit_ratio < 0.20`).
- Increased latency on read endpoints (`/v1/products/{id}`, `/v1/inventory/availability`).
- Logs warning: `RedisCacheAdapter error, falling back to database...`.
- Stale data served if cache invalidation events failed to clear tenant keys.

## 2. Immediate Diagnostic Commands
```bash
# 1. Inspect Redis container health and ping
docker compose ps arad-redis
docker compose exec arad-redis redis-cli ping

# 2. Inspect Redis memory usage and eviction stats
docker compose exec arad-redis redis-cli info memory
docker compose exec arad-redis redis-cli info stats | grep -E "evicted_keys|keyspace_hits|keyspace_misses"

# 3. Check connected clients
docker compose exec arad-redis redis-cli info clients
```

## 3. Investigation
1. **Cache-Aside Fallback Guarantee**:
   - The platform architecture treats Redis as an ephemeral acceleration cache, NOT an authoritative system of record.
   - Redis unavailability degrades read latency to direct PostgreSQL queries, but transactional operations, reservations, and inventory invariants remain intact.
2. **Key Eviction Spike**:
   - `maxmemory` reached without an adequate eviction policy (`volatile-lru` or `allkeys-lru`), causing write errors (`OOM command not allowed when used memory > 'maxmemory'`).
3. **Cross-Tenant Key Safety**:
   - Verify that tenant-prefixed keys (`tenant:{id}:...`) are strictly partitioned.

## 4. Safe Mitigation
- If Redis service crashed:
  ```bash
  docker compose restart arad-redis
  ```
- If memory limit is saturated:
  ```bash
  # Check configured maxmemory and policy
  docker compose exec arad-redis redis-cli config get maxmemory
  docker compose exec arad-redis redis-cli config get maxmemory-policy
  ```
- If stale keys need targeted invalidation for a specific tenant:
  ```bash
  # Delete only the specific tenant's keys (NEVER run FLUSHALL in production)
  docker compose exec arad-redis redis-cli --scan --pattern "tenant:<tenant_id>:*" | xargs -r docker compose exec -T arad-redis redis-cli del
  ```

## 5. Verification
```bash
# Ensure Redis responds to PING with PONG
docker compose exec arad-redis redis-cli ping
# Test application read endpoint fallback
curl -i http://localhost:8000/v1/products/<product_id> -H "X-Tenant-Id: <tenant_id>"
```

## 6. Escalation & Warnings
> [!CAUTION]
> NEVER execute `FLUSHALL` or `FLUSHDB` in a production Redis cluster. Flushing drops all cached sessions, rate limits, and coordination leases simultaneously, inducing a database stampede on PostgreSQL.
