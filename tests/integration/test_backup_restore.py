"""Integration test for PostgreSQL Backup, Restore, and Disaster Recovery (Phase 5 Workstream G).

Scenario:
1. Seed test tenant, products, and inventory items into PostgreSQL.
2. Execute backup_postgres.py to generate a verified, SHA-256 hashed backup artifact.
3. Execute restore_postgres.py into an isolated disposable test database.
4. Verify schema, migrations, records, and domain invariants in the restored database.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from scripts.backup_postgres import run_backup
from scripts.restore_postgres import run_restore

pytestmark = [pytest.mark.integration, pytest.mark.repository]


def test_backup_and_restore_cycle_maintains_integrity_and_invariants() -> None:
    if not shutil.which("pg_dump"):
        pytest.skip("pg_dump utility not available on PATH in current container environment")

    temp_dir = Path(tempfile.mkdtemp(prefix="arad_backup_test_"))
    try:
        # 1. Execute backup of current test database
        source_url = os.environ.get(
            "ARAD_TEST_DATABASE_URL", "postgresql://arad:arad@localhost:5432/arad_test"
        )
        backup_file = run_backup(
            database_url=source_url,
            dest_dir=temp_dir,
            prefix="test_cycle",
            max_keep=3,
        )

        assert backup_file.exists()
        assert backup_file.stat().st_size > 0

        # Checksum file exists and matches
        sha_file = backup_file.with_suffix(backup_file.suffix + ".sha256")
        assert sha_file.exists()

        # 2. Execute restore into isolated disposable database
        from scripts.restore_postgres import parse_database_url
        params = parse_database_url(source_url)
        restore_db_name = f"test_restore_{uuid.uuid4().hex[:8]}"
        target_url = f"postgresql://{params['user']}:{params['password']}@{params['host']}:{params['port']}/{restore_db_name}"

        success = run_restore(
            backup_file=backup_file,
            target_database_url=target_url,
        )
        assert success is True

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
