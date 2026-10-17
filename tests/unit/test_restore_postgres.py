"""Unit tests for scripts/restore_postgres.py safety mechanisms."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from scripts.restore_postgres import (
    compute_sha256,
    is_safe_non_production_target,
    run_restore,
    validate_sql_identifier,
    verify_checksum,
)

pytestmark = pytest.mark.unit


def test_validate_sql_identifier_valid() -> None:
    assert validate_sql_identifier("arad_test") == '"arad_test"'
    assert validate_sql_identifier("mydb123") == '"mydb123"'
    assert validate_sql_identifier("_db_restore") == '"_db_restore"'
    assert validate_sql_identifier("db$prod") == '"db$prod"'


def test_validate_sql_identifier_rejects_injection_and_invalid() -> None:
    bad_identifiers = [
        "",
        "arad; DROP TABLE orders;--",
        "db name with spaces",
        "db'--",
        'db"DROP',
        "123startwithnumber",
        "db-with-hyphen",
        "a" * 64,  # Exceeds NAMEDATALEN limit (63)
    ]
    for bad in bad_identifiers:
        with pytest.raises(ValueError, match="Invalid PostgreSQL database identifier"):
            validate_sql_identifier(bad)


def test_is_safe_non_production_target() -> None:
    assert is_safe_non_production_target("arad_test") is True
    assert is_safe_non_production_target("test_db") is True
    assert is_safe_non_production_target("company_restore") is True
    assert is_safe_non_production_target("app_dr") is True
    assert is_safe_non_production_target("service_stage") is True
    assert is_safe_non_production_target("cluster_dev") is True
    assert is_safe_non_production_target("node_isolated") is True

    # Non-isolated / production targets
    assert is_safe_non_production_target("arad") is False
    assert is_safe_non_production_target("production") is False
    assert is_safe_non_production_target("warehouse") is False
    assert is_safe_non_production_target("customer_data") is False


def test_verify_checksum_missing_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        sql_file = Path(tmpdir) / "backup.sql"
        sql_file.write_text("SELECT 1;", encoding="utf-8")

        # Missing checksum sidecar must raise FileNotFoundError
        with pytest.raises(FileNotFoundError, match=r"Checksum file .* not found"):
            verify_checksum(sql_file, allow_unverified=False)

        # Explicit bypass works
        assert verify_checksum(sql_file, allow_unverified=True) is True


def test_verify_checksum_mismatch_fails() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        sql_file = Path(tmpdir) / "backup.sql"
        sql_file.write_text("SELECT 1;", encoding="utf-8")
        sha_file = Path(tmpdir) / "backup.sql.sha256"
        sha_file.write_text("bad_checksum_value  backup.sql\n", encoding="utf-8")

        with pytest.raises(ValueError, match="Backup checksum mismatch"):
            verify_checksum(sql_file)


def test_verify_checksum_valid() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        sql_file = Path(tmpdir) / "backup.sql"
        sql_file.write_text("SELECT 1;", encoding="utf-8")
        actual_sha = compute_sha256(sql_file)
        sha_file = Path(tmpdir) / "backup.sql.sha256"
        sha_file.write_text(f"{actual_sha}  backup.sql\n", encoding="utf-8")

        assert verify_checksum(sql_file) is True


def test_run_restore_fails_closed_on_unauthorized_target(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        sql_file = Path(tmpdir) / "backup.sql"
        sql_file.write_text("SELECT 1;", encoding="utf-8")
        actual_sha = compute_sha256(sql_file)
        sha_file = Path(tmpdir) / "backup.sql.sha256"
        sha_file.write_text(f"{actual_sha}  backup.sql\n", encoding="utf-8")

        # Target is "custom_prod_db" (not safe pattern)
        target_url = "postgresql://arad:arad@localhost:5432/custom_prod_db"

        # Without confirmation, fails closed
        monkeypatch.delenv("CONFIRM_PRODUCTION_RESTORE", raising=False)
        with pytest.raises(
            PermissionError, match="Refusing destructive restore over non-isolated target"
        ):
            run_restore(sql_file, target_url, confirm_destructive_restore=False)

        # With only flag but missing env var, still fails closed
        with pytest.raises(
            PermissionError, match="Refusing destructive restore over non-isolated target"
        ):
            run_restore(sql_file, target_url, confirm_destructive_restore=True)

        # With only env var but missing flag, still fails closed
        monkeypatch.setenv("CONFIRM_PRODUCTION_RESTORE", "true")
        with pytest.raises(
            PermissionError, match="Refusing destructive restore over non-isolated target"
        ):
            run_restore(sql_file, target_url, confirm_destructive_restore=False)


def test_build_terminate_connections_sql() -> None:
    from scripts.restore_postgres import build_terminate_connections_sql

    stmts = build_terminate_connections_sql('"test_db"', "test_db")
    assert len(stmts) == 2
    assert 'ALTER DATABASE "test_db" WITH ALLOW_CONNECTIONS false;' in stmts[0]
    assert "pg_terminate_backend" in stmts[1]
    assert "datname = 'test_db'" in stmts[1]
    assert "pid <> pg_backend_pid()" in stmts[1]


def test_run_restore_rejects_maintenance_databases() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        sql_file = Path(tmpdir) / "backup.sql"
        sql_file.write_text("SELECT 1;", encoding="utf-8")
        actual_sha = compute_sha256(sql_file)
        sha_file = Path(tmpdir) / "backup.sql.sha256"
        sha_file.write_text(f"{actual_sha}  backup.sql\n", encoding="utf-8")

        for m_db in ("postgres", "template0", "template1"):
            target_url = f"postgresql://arad:arad@localhost:5432/{m_db}"
            with pytest.raises(
                ValueError, match="Refusing to restore over system maintenance/template database"
            ):
                run_restore(sql_file, target_url, confirm_destructive_restore=True)


def test_get_current_head_revision_resolves_codebase_head() -> None:
    from scripts.restore_postgres import EXPECTED_HEAD_REVISION, get_current_head_revision

    head = get_current_head_revision()
    assert head == "0011_tenant_composite_fks"
    assert EXPECTED_HEAD_REVISION == "0011_tenant_composite_fks"


def test_get_current_head_revision_rejects_missing_directory(tmp_path: Path) -> None:
    from scripts.restore_postgres import get_current_head_revision

    non_existent_ini = tmp_path / "non_existent.ini"
    non_existent_mig = tmp_path / "non_existent_mig"
    with pytest.raises(FileNotFoundError, match=r"Could not locate alembic\.ini or migrations"):
        get_current_head_revision(
            alembic_ini_path=non_existent_ini, migrations_dir=non_existent_mig
        )


def test_get_current_head_revision_rejects_multiple_heads(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    from scripts.restore_postgres import get_current_head_revision

    alembic_script = importlib.import_module("alembic.script")
    monkeypatch.setattr(
        alembic_script.ScriptDirectory, "get_heads", lambda self: ["head_rev_1", "head_rev_2"]
    )
    with pytest.raises(RuntimeError, match="Multiple Alembic migration heads detected"):
        get_current_head_revision()


def test_get_current_head_revision_rejects_empty_heads(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    from scripts.restore_postgres import get_current_head_revision

    alembic_script = importlib.import_module("alembic.script")
    monkeypatch.setattr(alembic_script.ScriptDirectory, "get_heads", lambda self: [])
    with pytest.raises(RuntimeError, match="No Alembic migration heads found"):
        get_current_head_revision()


def test_run_restore_validates_alembic_revision_match_and_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    from scripts.restore_postgres import compute_sha256, run_restore

    sql_file = tmp_path / "backup.sql"
    sql_file.write_text("SELECT 1;", encoding="utf-8")
    actual_sha = compute_sha256(sql_file)
    sha_file = tmp_path / "backup.sql.sha256"
    sha_file.write_text(f"{actual_sha}  backup.sql\n", encoding="utf-8")

    target_url = "postgresql://arad:arad@localhost:5432/test_restore_mock"

    class MockCompletedProcess:
        def __init__(self, stdout: str = "", returncode: int = 0) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    # Mock subprocess.run to simulate psql calls
    alembic_output = ["0011_tenant_composite_fks"]

    def fake_subprocess_run(
        cmd: list[str], *args: object, **kwargs: object
    ) -> MockCompletedProcess:
        cmd_str = " ".join(str(c) for c in cmd)
        if "alembic_version" in cmd_str:
            return MockCompletedProcess(stdout="\n".join(alembic_output))
        if "inventory_items" in cmd_str:
            return MockCompletedProcess(stdout="0")
        if "orders" in cmd_str or "products" in cmd_str:
            return MockCompletedProcess(stdout="5")
        return MockCompletedProcess(stdout="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    # 1. Matching revision succeeds
    alembic_output = ["0011_tenant_composite_fks"]
    assert run_restore(sql_file, target_url) is True

    # 2. Explicit expected revision matches
    alembic_output = ["custom_branch_head"]
    assert run_restore(sql_file, target_url, expected_revision="custom_branch_head") is True

    # 3. Mismatched revision fails
    alembic_output = ["0010_durable_fulfillment"]
    with pytest.raises(
        AssertionError,
        match="FATAL: Restored Alembic revision '0010_durable_fulfillment' does not match expected revision '0011_tenant_composite_fks'",
    ):
        run_restore(sql_file, target_url)

    # 4. Empty alembic_version table fails
    alembic_output = [""]
    with pytest.raises(
        AssertionError, match="FATAL: alembic_version table is empty after restore!"
    ):
        run_restore(sql_file, target_url)

    # 5. Multiple heads in alembic_version table fails
    alembic_output = ["head_a", "head_b"]
    with pytest.raises(
        AssertionError, match="FATAL: Multiple Alembic revisions found in restored database"
    ):
        run_restore(sql_file, target_url)
