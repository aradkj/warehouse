# Runbook: Elasticsearch Outage and Indexing Lag

## 1. Symptoms
- Prometheus alert: `ElasticsearchDown` (`elasticsearch_up == 0`) or `ElasticsearchIndexingLagHigh` (`elasticsearch_indexing_lag_seconds > 5.0`).
- Search queries returning HTTP 503 or empty result sets for recently created products/orders.
- Search projection consumer queue `arad.search` accumulating unconsumed messages.

## 2. Immediate Diagnostic Commands
```bash
# 1. Check Elasticsearch cluster health and status
docker compose exec arad-elasticsearch curl -s http://localhost:9200/_cluster/health | jq .

# 2. Inspect node disk watermarks and heap usage
docker compose exec arad-elasticsearch curl -s http://localhost:9200/_nodes/stats/jvm,fs | jq .

# 3. Check status of search projection consumer
docker compose ps arad-search-worker
docker compose logs --tail=100 -f arad-search-worker
```

## 3. Investigation
1. **Authoritative Truth Guarantee**:
   - PostgreSQL is the immutable source of transactional business truth. Elasticsearch is a rebuildable projection.
   - If Elasticsearch is offline, orders and inventory updates continue committing normally in PostgreSQL.
2. **Disk Flood Watermarks**:
   - If disk usage exceeds 90% (high watermark) or 95% (flood stage), Elasticsearch marks indices as `read-only-allow-delete` and rejects indexing requests.
3. **Bulk Indexing Rejections**:
   - High mutation volume saturating the `write` thread pool, causing HTTP 429 Too Many Requests responses.

## 4. Safe Mitigation
- If cluster is yellow/red due to index read-only lock:
  ```bash
  # Unlock indices after clearing disk space
  docker compose exec arad-elasticsearch curl -X PUT "http://localhost:9200/_all/_settings" \
    -H 'Content-Type: application/json' \
    -d '{"index.blocks.read_only_allow_delete": null}'
  ```
- If search projection consumer crashed:
  ```bash
  docker compose restart arad-search-worker
  ```

## 5. Verification
```bash
# Confirm cluster status is GREEN or YELLOW
docker compose exec arad-elasticsearch curl -s http://localhost:9200/_cluster/health | jq '.status'
# Execute test tenant search query
curl -i "http://localhost:8000/v1/search/products?q=test" -H "X-Tenant-Id: <tenant_id>"
```
