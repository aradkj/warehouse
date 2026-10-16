# Runbook: Queue Backlog and Excessive Message Age

## 1. Symptoms
- Prometheus alert: `RabbitMQQueueBacklog` (`rabbitmq_individual_queue_messages > 1000` for >5m) or `RabbitMQMessageAgeHigh` (oldest message > 60s).
- Delayed order confirmation emails, stale search indices, or delayed inventory updates.
- Grafana dashboard shows ascending queue depth on `arad.search`, `arad.orders`, or `celery`.

## 2. Immediate Diagnostic Commands
```bash
# 1. Inspect queue message counts and consumer counts
docker compose exec arad-rabbitmq rabbitmqctl list_queues name messages messages_ready messages_unacknowledged consumers

# 2. Check worker container status
docker compose ps arad-worker arad-search-worker

# 3. Check worker CPU and memory utilization
docker stats --no-stream arad-worker arad-search-worker
```

## 3. Investigation
1. **Zero Active Consumers**: If `consumers == 0`, the consumer process crashed, hit an uncaught exception, or lost broker connection.
2. **Slow Consumer Throughput**: Workers spend excessive time per message (e.g., Elasticsearch slow queries, external webhook latency, database lock waits).
3. **Poison Messages**: Unacknowledged messages constantly cycling and blocking consumer prefetch limits (`prefetch_count`).

## 4. Safe Mitigation
- If consumer died:
  ```bash
  docker compose restart arad-search-worker arad-worker
  ```
- If worker scale is insufficient for traffic burst:
  ```bash
  # Scale worker processes safely
  docker compose up -d --scale arad-worker=3
  ```
- Tune Celery concurrency if CPU headroom is available:
  ```bash
  # Inspect active Celery worker concurrency
  docker compose exec arad-worker celery -A bootstrap.celery_app inspect stats
  ```

## 5. Verification
```bash
# Monitor queue drain in real time
watch -n 2 "docker compose exec arad-rabbitmq rabbitmqctl list_queues name messages consumers"
```

## 6. Escalation & Warnings
> [!WARNING]
> Never purge queues (`rabbitmqctl purge_queue`) unless the messages are verified to be disposable test artifacts. In production, purging queues causes dropped projections and missed notifications.
