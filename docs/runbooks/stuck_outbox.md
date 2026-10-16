# Runbook: Stuck Outbox Records and Relay Recovery

## 1. Symptoms
- Prometheus alert: `OutboxBacklogHigh` (`outbox_backlog_count > 500` for >5m).
- Database contains old un-relayed rows in `outbox` table (`published_at IS NULL` with `occurred_at < NOW() - INTERVAL '5 minutes'`).
- Downstream workers report no new incoming events despite active API write mutations.

## 2. Immediate Diagnostic Commands
```bash
# 1. Inspect count and age of unpublished outbox records
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT count(*) AS unpublished_count,
       min(occurred_at) AS oldest_unpublished,
       now() - min(occurred_at) AS max_lag
FROM outbox 
WHERE published_at IS NULL;"

# 2. Inspect recent outbox rows and retry counts
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT id, event_type, occurred_at, retry_count, last_error
FROM outbox
WHERE published_at IS NULL
ORDER BY occurred_at ASC
LIMIT 10;"

# 3. Check outbox relay service status and logs
docker compose ps arad-relay
docker compose logs --tail=100 -f arad-relay
```

## 3. Investigation
1. **Relay Process Deadlock / Termination**: The `arad-relay` background worker crashed or hung.
2. **RabbitMQ Connection Block**: The relay cannot reach `amqp://...` or is blocked by RabbitMQ broker memory/disk alarm.
3. **Poison Outbox Payload**: An event serialization defect or corrupted JSON in `payload` column causing repeated publish exceptions.

## 4. Safe Mitigation
- If relay container is stopped or unresponsive:
  ```bash
  docker compose restart arad-relay
  ```
- If a poison message is repeatedly throwing serialization exceptions, inspect its payload:
  ```bash
  docker compose exec arad-postgres psql -U arad -d arad -c "
  SELECT id, event_type, payload 
  FROM outbox 
  WHERE published_at IS NULL AND retry_count > 10 
  LIMIT 1;"
  ```
- If network partition occurred between relay and broker, test broker connectivity from the container:
  ```bash
  docker compose exec arad-relay nc -zv rabbitmq 5672
  ```

## 5. Verification
```bash
# Check that unpublished count decreases monotonically toward 0
docker compose exec arad-postgres psql -U arad -d arad -c "SELECT count(*) FROM outbox WHERE published_at IS NULL;"
```

## 6. Escalation & Warnings
> [!CAUTION]
> NEVER execute `DELETE FROM outbox WHERE published_at IS NULL;` or `UPDATE outbox SET published_at = NOW();` without investigating. Doing so permanently drops un-relayed domain events and desynchronizes inventory and order projections.
