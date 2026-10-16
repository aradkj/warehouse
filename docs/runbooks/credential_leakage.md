# Runbook: Suspected Credential Leakage and Secret Rotation

## 1. Symptoms
- Git security alert, secret scanner alert (TruffleHog, Gitleaks), or public exposure report.
- Unauthorized administrative requests in API access logs (`/v1/search/reindex`, `/v1/tenants`).
- Suspicious activity detected on database or message broker port from unexpected IP addresses.

## 2. Immediate Diagnostic Commands
```bash
# 1. Search access logs for suspicious administrative access
docker compose logs arad-api | grep -E "401|403|admin|reindex"

# 2. Check active PostgreSQL connections by IP and user
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT client_addr, usename, application_name, count(*) 
FROM pg_stat_activity 
GROUP BY client_addr, usename, application_name;"

# 3. Check active RabbitMQ connections
docker compose exec arad-rabbitmq rabbitmqctl list_connections user peer_host
```

## 3. Secret Rotation Procedure

### 3.1. JWT Secret Rotation (`ARAD_AUTH_JWT_SECRET`)
1. Generate a new high-entropy 64-character secret:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. Update `ARAD_AUTH_JWT_SECRET` in production `.env` / secret store.
3. Restart `arad-api` and `arad-worker`:
   ```bash
   docker compose up -d arad-api arad-worker
   ```
4. *Effect*: All previously issued tokens are immediately invalidated, forcing re-authentication.

### 3.2. Database Password Rotation (`ARAD_DATABASE_URL`)
1. Connect to PostgreSQL as superuser:
   ```sql
   ALTER USER arad WITH PASSWORD 'NewSecurePasswordGenerated123!';
   ```
2. Update `ARAD_DATABASE_URL` in `.env.production`.
3. Restart all application services participating in database connections:
   ```bash
   docker compose restart arad-api arad-worker arad-relay arad-search-worker
   ```

### 3.3. RabbitMQ & Redis Credentials
1. Change password on broker/cache and update corresponding `ARAD_RABBITMQ_URL` and `ARAD_REDIS_URL`.
2. Restart connected services.

## 4. Verification
```bash
# Verify API starts up cleanly and readyz is healthy with new credentials
docker compose logs --tail=50 arad-api
curl -f http://localhost:8000/readyz
```

## 5. Escalation & Warnings
> [!CAUTION]
> Treat exposed secrets as compromised immediately. Never attempt to "verify if anyone used the leaked secret" before rotating it. Rotate first, investigate logs second.
