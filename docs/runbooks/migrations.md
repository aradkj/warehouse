# Runbook: migrations

## Applying

```bash
export ARAD_DATABASE_URL="postgresql+asyncpg://arad:arad@localhost:5432/arad"

alembic current                 # what is applied now
alembic history --verbose       # the full chain
alembic upgrade head            # apply everything pending
alembic downgrade -1            # one revision back
alembic upgrade head --sql      # offline SQL, for review or a DBA ticket
```

The URL comes from `ARAD_DATABASE_URL` (or `-x db-url=…`), never from `alembic.ini`, so
a migration always targets the database the application uses. If the variable is unset,
the settings default (`postgresql+asyncpg://arad:arad@localhost:5432/arad`) applies —
which is fine on a developer machine and is why every deployed environment sets the
variable explicitly.

## Writing a revision

```bash
alembic revision --autogenerate -m "add x to y"
```

Then, before committing:

1. Read the generated file. Autogenerate
   * adds `postgresql.JSONB(...)` and friends — make sure the revision imports
     `from sqlalchemy.dialects import postgresql`;
   * does not guess `CHECK` constraints, partial indexes or data moves — write them by
     hand;
   * must keep the parents-first / children-first ordering for creates and drops.
2. Name constraints explicitly. A name PostgreSQL generated differently on another
   environment is a `DROP CONSTRAINT` waiting to fail.
3. Implement `downgrade()` honestly. If a change cannot be reversed (dropped column with
   data), say so in the revision docstring and raise rather than pretending.
4. Never edit a revision that is already part of history ([ADR-0005](../decisions/0005-migration-strategy.md)).
5. Format it: `ruff format migrations/ && ruff check migrations/`. The alembic
   `post_write_hooks` ruff hook does not work and was removed.

## Verifying

```bash
pytest tests/integration/test_migrations.py -q
```

That suite builds the schema on a scratch database from empty, asserts
`compare_metadata` reports **no drift** against the SQLAlchemy metadata, round-trips
`upgrade → downgrade base → upgrade`, and checks that the constraints the domain relies
on really exist.

## Emergency

A migration that fails mid-way on PostgreSQL rolls back by itself (transactional DDL).
If a data migration is destructive and half-applied, stop the application, restore from
the backup taken before the deploy, and fix the revision — do not hand-edit rows.
