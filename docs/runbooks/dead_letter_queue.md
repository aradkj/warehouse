# Runbook: Dead-Letter Queue (DLQ) Growth and Poison Messages

## 1. Symptoms
- Prometheus alert: `DeadLetterQueueGrowth` (`rabbitmq_dead_letter_messages > 0`).
- Grafana alert shows messages routing to `arad.dlq` or `*.dlq`.
- Celery / worker logs show max retries exceeded for task.

## 2. Immediate Diagnostic Commands
```bash
# 1. Inspect Dead-Letter queue message count
docker compose exec arad-rabbitmq rabbitmqctl list_queues name messages | grep -i dlq

# 2. Inspect Dead-Letter messages payload without consuming destructively
# (Using management HTTP API or rabbitmqadmin if installed)
curl -s -u arad:arad http://localhost:15672/api/queues/%2F/arad.dlq | jq '.messages'

# 3. Check worker exception logs
docker compose logs --tail=100 arad-worker | grep -E "MaxRetriesExceededError|RetryTask|Exception"
```

## 3. Investigation
1. **Schema Mismatch**: Upstream publisher emitted an event schema incompatible with current consumer version.
2. **Permanent Downstream Failure**: External API (e.g., shipping carrier or webhook) permanently returning 4xx or 5xx.
3. **Poison Data**: Missing mandatory fields, corrupted UUIDs, or unparseable payloads.

## 4. Safe Mitigation
1. **Do not re-queue blindly**: Blindly requeuing poison messages causes infinite crash loops and CPU spikes.
2. **Isolate and inspect the poison payload**:
   - Extract the payload and headers to analyze the rejection reason (`x-death` header in RabbitMQ indicates original queue, timestamp, and rejection cause).
3. **Hotfix & Replay**:
   - Apply necessary application patch to handle the edge-case schema or data format.
   - Once patch is deployed, replay the messages from DLQ back to the primary processing queue.

## 5. Verification
```bash
# Confirm DLQ message count has cleared after replay
docker compose exec arad-rabbitmq rabbitmqctl list_queues name messages | grep -i dlq
```

## 6. Escalation & Warnings
> [!WARNING]
> Never purge dead-letter queues before recording their contents. Every dead letter represents an unhandled business mutation or projection failure that may require audit or manual reconciliation.
