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
