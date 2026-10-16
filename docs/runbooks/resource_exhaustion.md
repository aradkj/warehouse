# Runbook: CPU, Memory, and Disk Resource Exhaustion

## 1. Symptoms
- Prometheus alerts: `HighCpuLoad` (>85% for >5m), `HostMemoryExhaustion` (>90%), or `DiskSpaceRunningLow` (<15% free).
- Container exits unexpectedly with exit code 137 (SIGKILL by OOM killer).
- System disk fills up, causing database writes, Elasticsearch indices, and RabbitMQ to block.

## 2. Immediate Diagnostic Commands
```bash
# 1. Inspect live container CPU and Memory usage
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.BlockIO}}"

# 2. Check disk space across volumes
docker compose exec arad-postgres df -h
docker compose exec arad-elasticsearch df -h
docker compose exec arad-rabbitmq df -h

# 3. Check Docker system disk consumption
docker system df
```

## 3. Investigation
1. **Container Memory Leaks**: Compare memory usage over time. Python processes accumulating uncollected objects or large unpaged query result sets.
2. **Elasticsearch JVM Heap**: Check if Elasticsearch heap is hitting maximum allocated limits and triggering constant GC pauses:
   ```bash
   docker compose exec arad-elasticsearch curl -s http://localhost:9200/_nodes/stats/jvm | jq '.nodes[].jvm.mem'
   ```
3. **PostgreSQL WAL Accumulation**: If WAL archiving or replication is stuck, `pg_wal` directory in PostgreSQL will grow indefinitely until disk is full:
   ```bash
   docker compose exec arad-postgres du -sh /var/lib/postgresql/data/pg_wal
   ```
4. **Log File Bloat**: Unrotated container stdout/stderr log files in `/var/lib/docker/containers/`.

## 4. Safe Mitigation
- If Docker disk is exhausted by dangling build layers or stopped test containers:
  ```bash
  # Prune unused containers and build caches (SAFE: does not remove active volumes)
  docker system prune -f
  ```
- If container log files are bloated:
  - Ensure logging max-size driver is active in `docker-compose.prod.yml`:
    ```yaml
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "5"
    ```
- If a runaway query is saturating 100% CPU on PostgreSQL:
  ```sql
  SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE state = 'active' AND now() - query_start > interval '1 minute';
  ```

## 5. Verification
```bash
# Verify all container resource usages are back within normal thresholds
docker stats --no-stream
```
