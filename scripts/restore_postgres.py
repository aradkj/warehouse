#!/usr/bin/env python3
"""PostgreSQL restoration and integrity verification utility (Phase 5 Workstream G).

Guarantees:
* Checksum validation: Verifies SHA-256 before executing restore (fails closed if missing unless --allow-unverified).
* Identifier safety: Validates database name with strict regex and applies PostgreSQL identifier quoting.
* Fail-closed target policy: Rejects non-isolated targets unless explicit destructive confirmation is given.
* Migration compatibility: Asserts alembic_version revision matches current codebase head revision.
* Domain invariant verification: Verifies non-negative stock (available >= 0) and essential schema invariants.
* Clean failure exit codes (0 for success, non-zero for failure).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

EXPECTED_HEAD_REVISION: str = "0009_harden_rls_and_roles"

_SAFE_IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_$]{0,62}$")

_SAFE_TARGET_PATTERNS = (
    "_test",
    "_restore",
    "_dr",
    "_stage",
    "_dev",
    "_isolated",
)


def validate_sql_identifier(identifier: str) -> str:
    """Validate and quote a PostgreSQL database identifier.

    Guarantees:
    - Rejects invalid characters, SQL injection metacharacters, spaces, control characters.
    - Limits length to PostgreSQL standard NAMEDATALEN limit (63 chars).
    - Properly quotes the identifier for SQL usage.
    """
    if not identifier or not _SAFE_IDENTIFIER_REGEX.match(identifier):
        raise ValueError(
            f"Invalid PostgreSQL database identifier '{identifier}'. "
            "Must start with a letter or underscore, contain only alphanumeric characters, "
            "underscores, or dollar signs, and be at most 63 characters long."
        )
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def is_safe_non_production_target(db_name: str) -> bool:
    """Check if the database target matches approved isolated non-production patterns."""
    lower = db_name.lower()
    return any(lower.endswith(suffix) for suffix in _SAFE_TARGET_PATTERNS) or lower.startswith(
        ("test_", "restore_", "dr_", "stage_", "dev_")
    )


def compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_checksum(sql_path: Path, *, allow_unverified: bool = False) -> bool:
    """Verify SHA-256 checksum file matches backup artifact.

    Fails closed if the checksum file does not exist, unless allow_unverified is True.
    """
    sha_file = sql_path.with_suffix(sql_path.suffix + ".sha256")
    if not sha_file.exists():
        if allow_unverified:
            print(
                f"WARNING: Checksum file '{sha_file}' not found. "
                "Integrity verification bypassed by explicit --allow-unverified flag.",
                file=sys.stderr,
            )
            return True
        raise FileNotFoundError(
            f"Checksum file '{sha_file}' not found. Refusing unverified backup restoration. "
            "Pass --allow-unverified to bypass (NOT recommended in production)."
        )

    expected_sha = sha_file.read_text(encoding="utf-8").strip().split()[0]
    actual_sha = compute_sha256(sql_path)
    if expected_sha.lower() != actual_sha.lower():
        raise ValueError(
            f"Backup checksum mismatch for {sql_path}! Expected: {expected_sha}, Actual: {actual_sha}"
        )
    print(f"Checksum verified: {actual_sha}")
    return True


def parse_database_url(url: str) -> dict[str, str | int]:
    cleaned_url = url.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(cleaned_url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "arad",
        "password": parsed.password or "arad",
        "database": parsed.path.lstrip("/") or "arad_restore_test",
    }


def run_restore(
    backup_file: Path,
    target_database_url: str,
    container_name: str | None = None,
    *,
    allow_unverified: bool = False,
    confirm_destructive_restore: bool = False,
    expected_revision: str | None = EXPECTED_HEAD_REVISION,
) -> bool:
    """Execute restoration and verify post-restore database integrity."""
    if not backup_file.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_file}")

    verify_checksum(backup_file, allow_unverified=allow_unverified)

    params = parse_database_url(target_database_url)
    db_name = str(params["database"])

    # SQL identifier validation and quoting
    quoted_db = validate_sql_identifier(db_name)

    # Fail-closed target policy:
    # 1. Reject known production names without both env var and explicit flag
    # 2. Reject ANY database name that does not match safe isolated patterns without both env var and explicit flag
    is_safe_target = is_safe_non_production_target(db_name)
    env_confirmed = bool(os.environ.get("CONFIRM_PRODUCTION_RESTORE"))

    if (
        not is_safe_target or db_name.lower() in ("arad", "production", "prod", "warehouse")
    ) and not (confirm_destructive_restore and env_confirmed):
        raise PermissionError(
            f"Refusing destructive restore over non-isolated target '{db_name}'. "
            "Target does not match safe non-production patterns (_test, _restore, _dr, _stage). "
            "To proceed, pass --confirm-destructive-restore AND set CONFIRM_PRODUCTION_RESTORE=true."
        )

    print(f"Restoring {backup_file} into {quoted_db} on {params['host']}:{params['port']}...")

    psql_bin = shutil.which("psql")
    env = os.environ.copy()
    env["PGPASSWORD"] = str(params["password"])
    use_local_psql = psql_bin is not None and not container_name

    c_name = container_name or "arad-postgres-1"

    def _exec_psql(cmd_args: list[str], stdin_f=None) -> subprocess.CompletedProcess:
        if use_local_psql:
            full_cmd = [
                str(psql_bin),
                "-h",
                str(params["host"]),
                "-p",
                str(params["port"]),
                "-U",
                str(params["user"]),
            ] + cmd_args
            return subprocess.run(full_cmd, env=env, stdin=stdin_f, capture_output=True, text=True)
        else:
            full_cmd = [
                "docker",
                "exec",
            ]
            if stdin_f is not None:
                full_cmd.append("-i")
            full_cmd.extend(
                [
                    "-e",
                    f"PGPASSWORD={params['password']}",
                    c_name,
                    "psql",
                    "-U",
                    str(params["user"]),
                ]
            )
            full_cmd.extend(cmd_args)
            return subprocess.run(full_cmd, stdin=stdin_f, capture_output=True, text=True)

    # Recreate target database safely using quoted identifier
    drop_res = _exec_psql(["-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {quoted_db};"])
    if drop_res.returncode != 0:
        raise RuntimeError(f"Failed to drop database {db_name}: {drop_res.stderr}")

    create_res = _exec_psql(["-d", "postgres", "-c", f"CREATE DATABASE {quoted_db};"])
    if create_res.returncode != 0:
        raise RuntimeError(f"Failed to create target database {db_name}: {create_res.stderr}")

    # Stream SQL dump into target database
    with open(backup_file, "rb") as in_f:
        proc = _exec_psql(["-d", db_name, "-v", "ON_ERROR_STOP=1"], stdin_f=in_f)
        if proc.returncode != 0:
            raise RuntimeError(f"psql restore failed (code {proc.returncode}): {proc.stderr}")

    print("SQL restore completed. Validating schema and invariants...")

    # Verification queries
    verify_queries = [
        ("Checking Alembic migration table", "SELECT version_num FROM alembic_version LIMIT 1;"),
        (
            "Checking Inventory Item available stock non-negative invariant",
            "SELECT count(*) FROM inventory_items WHERE on_hand - reserved < 0;",
        ),
        ("Checking Order count", "SELECT count(*) FROM orders;"),
        ("Checking Product count", "SELECT count(*) FROM products;"),
    ]

    for desc, query in verify_queries:
        res = _exec_psql(["-d", db_name, "-t", "-c", query])
        if res.returncode != 0:
            raise RuntimeError(f"Post-restore verification query failed ({desc}): {res.stderr}")
        val = res.stdout.strip()
        print(f"  [OK] {desc}: result = {val}")

        if "alembic" in desc.lower():
            if not val:
                raise AssertionError("FATAL: alembic_version table is empty after restore!")
            if expected_revision and val != expected_revision:
                raise AssertionError(
                    f"FATAL: Restored Alembic revision '{val}' does not match expected revision '{expected_revision}'!"
                )
        elif "invariant" in desc.lower():
            if int(val) > 0:
                raise AssertionError(
                    f"FATAL: Invariant violated after restore: {val} rows with negative available stock!"
                )

    print("Database restoration and integrity verification SUCCESSFUL!")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Arad PostgreSQL Restore & Verify Utility")
    parser.add_argument("backup_file", help="Path to .sql or .sql.gz backup file")
    parser.add_argument(
        "--target-url",
        default="postgresql://arad:arad@localhost:5432/arad_restore_test",
        help="Target PostgreSQL database URL (defaults to isolated arad_restore_test)",
    )
    parser.add_argument(
        "--container",
        default="arad-postgres-1",
        help="PostgreSQL Docker container name",
    )
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        default=False,
        help="Allow restore if checksum file is missing (UNSAFE)",
    )
    parser.add_argument(
        "--confirm-destructive-restore",
        action="store_true",
        default=False,
        help="Confirm destructive restore into non-isolated database targets",
    )
    parser.add_argument(
        "--expected-revision",
        default=EXPECTED_HEAD_REVISION,
        help="Expected Alembic revision after restoration",
    )

    args = parser.parse_args()
    try:
        run_restore(
            Path(args.backup_file),
            args.target_url,
            container_name=args.container,
            allow_unverified=args.allow_unverified,
            confirm_destructive_restore=args.confirm_destructive_restore,
            expected_revision=args.expected_revision,
        )
        return 0
    except Exception as exc:
        print(f"RESTORE FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
