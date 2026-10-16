# Runbook: Failed Reindexing and Alias Rollback

## 1. Symptoms
- Administrative reindex job (`POST /v1/search/reindex`) failed with error or timed out.
- Celery worker logged `ReindexFailedException` or `BulkIndexError`.
- Production search queries returning partial results or pointing to a dead index.

## 2. Immediate Diagnostic Commands
```bash
# 1. Inspect active indices and their aliases
docker compose exec arad-elasticsearch curl -s "http://localhost:9200/_cat/aliases?v"

# 2. Check document counts across old and new indices
docker compose exec arad-elasticsearch curl -s "http://localhost:9200/_cat/indices?v"

# 3. Check recent reindex logs
docker compose logs --tail=100 arad-worker | grep -i "reindex"
```

## 3. Investigation
1. **Zero-Downtime Rebuild Semantics**:
   - The platform rebuilds search indices using isolated versioned indices (`products-v2`, `orders-v2`) and performs an atomic alias swap (`switch_alias`) only upon 100% successful completion.
   - If a reindex fails mid-way, the active alias MUST remain pointing to the prior stable index.
2. **Bulk Ingestion Timeout**:
   - Large catalog size (>100k items) exceeding batch timeout during PostgreSQL extraction.

## 4. Safe Mitigation
- If an incomplete new index was created but not swapped:
  ```bash
  # Delete only the orphan unaliased new index
  docker compose exec arad-elasticsearch curl -X DELETE "http://localhost:9200/products-v2-temp"
  ```
- If an alias was erroneously swapped to an incomplete index, execute immediate emergency rollback:
  ```bash
  # Atomically point alias back to the previous stable index
  docker compose exec arad-elasticsearch curl -X POST "http://localhost:9200/_aliases" \
    -H 'Content-Type: application/json' \
    -d '{
      "actions": [
        {"remove": {"index": "products-v2", "alias": "products"}},
        {"add":    {"index": "products-v1", "alias": "products"}}
      ]
    }'
  ```

## 5. Verification
```bash
# Verify alias points to the authoritative index
docker compose exec arad-elasticsearch curl -s "http://localhost:9200/_cat/aliases/products?v"
```

## 6. Escalation & Warnings
> [!CAUTION]
> NEVER delete an index that currently holds an active production alias without first swapping traffic away. Dropping an active index causes immediate 100% failure for all tenant search endpoints.
