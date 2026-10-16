# Runbook: RabbitMQ Outage and Broken Connection Recovery

## 1. Symptoms
- Prometheus alert: `RabbitMQDown` (`rabbitmq_up == 0`).
- Celery workers logging: `kombu.exceptions.OperationalError: [Errno 111] Connection refused` or `AMQPConnectionError`.
- Outbox relay logs: `Broker connection failed, backing off...`.
- Search projection consumer halted; Elasticsearch indexing stops updating.

## 2. Immediate Diagnostic Commands
```bash
# 1. Inspect container status
docker compose ps arad-rabbitmq

# 2. Check broker health directly via rabbitmq-diagnostics
docker compose exec arad-rabbitmq rabbitmq-diagnostics -q ping
docker compose exec arad-rabbitmq rabbitmq-diagnostics status

# 3. Check node memory and disk alarms
docker compose exec arad-rabbitmq rabbitmq-diagnostics alarms

# 4. View broker logs
docker compose logs --tail=100 arad-rabbitmq
```

## 3. Investigation
1. **Memory / Disk Resource Alarm**: RabbitMQ blocks incoming publisher connections when memory exceeds the threshold or available disk space drops below 50MB (`disk_free_alarm`).
2. **File Descriptor Exhaustion**: Unclosed connections from leaked worker instances.
3. **Outbox Guarantee Check**:
   - Verify that business operations in PostgreSQL are continuing safely. The transactional outbox pattern guarantees that orders and inventory changes commit regardless of broker availability.

## 4. Safe Mitigation
- If blocked by resource alarms:
  ```bash
  # Check free disk space inside container volume
  docker compose exec arad-rabbitmq df -h /var/lib/rabbitmq
  ```
- If crashed or unresponsive:
  ```bash
  docker compose restart arad-rabbitmq
  ```
- Wait for broker to become healthy:
  ```bash
  docker compose exec arad-rabbitmq rabbitmqctl await_startup
  ```

## 5. Recovery & Backlog Drain
Once RabbitMQ is restored:
1. Outbox relay will automatically reconnect with exponential backoff and resume publication of pending outbox events from PostgreSQL:
   ```bash
   docker compose logs -f arad-relay
   ```
2. Search projections and asynchronous notification tasks will resume consumption without losing any committed state.

## 6. Verification
```bash
# Confirm broker is healthy and alarms cleared
docker compose exec arad-rabbitmq rabbitmq-diagnostics alarms
# Verify outbox backlog begins draining
docker compose exec arad-postgres psql -U arad -d arad -c "SELECT count(*) FROM outbox WHERE published_at IS NULL;"
```

## 7. Escalation & Warnings
> [!IMPORTANT]
> Never delete PostgreSQL outbox records (`DELETE FROM outbox;`) during a RabbitMQ outage. Doing so permanently destroys business events that must be published once the broker recovers.
