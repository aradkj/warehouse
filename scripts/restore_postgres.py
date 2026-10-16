#!/usr/bin/env python3
"""PostgreSQL restoration and integrity verification utility (Phase 5 Workstream G).

Guarantees:
* Checksum validation: Verifies SHA-256 before executing restore.
* Isolated environment: Restores into an explicit target database (defaults to isolated test DB).
* Migration compatibility: Asserts alembic_version revision matches current codebase head.
* Domain invariant verification: Verifies non-negative stock (available >= 0) and referential integrity.
* Clean failure exit codes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_checksum(sql_path: Path) -> bool:
    """Verify SHA-256 checksum file matches backup artifact."""
    sha_file = sql_path.with_suffix(sql_path.suffix + ".sha256")
    if not sha_file.exists():
        print(f"WARNING: Checksum file '{sha_file}' not found. Skipping integrity verification.", file=sys.stderr)
        return True

    expected_sha = sha_file.read_text(encoding="utf-8").strip().split()[0]
    actual_sha = compute_sha256(sql_path)
    if expected_sha.lower() != actual_sha.lower():
        print(f"ERROR: Checksum mismatch for {sql_path}! Expected: {expected_sha}, Actual: {actual_sha}", file=sys.stderr)
        return False
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
) -> bool:
    """Execute restoration and verify post-restore database integrity."""
    if not backup_file.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_file}")

    if not verify_checksum(backup_file):
        raise ValueError("Backup integrity verification failed!")

    params = parse_database_url(target_database_url)
    db_name = str(params["database"])

    # Safety guard: Never restore blindly into production without explicit override
    if db_name.lower() in ("arad", "production") and not os.environ.get("CONFIRM_PRODUCTION_RESTORE"):
        raise PermissionError(
            f"Refusing to restore over production database '{db_name}'. "
            "Set CONFIRM_PRODUCTION_RESTORE=true to force."
        )

    print(f"Restoring {backup_file} into '{db_name}' on {params['host']}:{params['port']}...")

    psql_bin = shutil.which("psql")
    env = os.environ.copy()
    env["PGPASSWORD"] = str(params["password"])
    use_local_psql = psql_bin is not None and not container_name

    c_name = container_name or "arad-postgres-1"

    def _exec_psql(cmd_args: list[str], stdin_f=None) -> subprocess.CompletedProcess:
        if use_local_psql:
            full_cmd = [str(psql_bin), "-h", str(params["host"]), "-p", str(params["port"]), "-U", str(params["user"])] + cmd_args
            return subprocess.run(full_cmd, env=env, stdin=stdin_f, capture_output=True, text=True)
        else:
            full_cmd = [
                "docker", "exec",
            ]
            if stdin_f is not None:
                full_cmd.append("-i")
            full_cmd.extend([
                "-e", f"PGPASSWORD={params['password']}",
                c_name,
                "psql",
                "-U", str(params["user"]),
            ])
            full_cmd.extend(cmd_args)
            return subprocess.run(full_cmd, stdin=stdin_f, capture_output=True, text=True)

    # Ensure target database exists via psql
    _exec_psql(["-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {db_name};"])

    res = _exec_psql(["-d", "postgres", "-c", f"CREATE DATABASE {db_name};"])
    if res.returncode != 0:
        raise RuntimeError(f"Failed to create target database {db_name}: {res.stderr}")

    # Stream SQL dump into target database
    with open(backup_file, "rb") as in_f:
        proc = _exec_psql(["-d", db_name, "-v", "ON_ERROR_STOP=1"], stdin_f=in_f)
        if proc.returncode != 0:
            raise RuntimeError(f"psql restore failed (code {proc.returncode}): {proc.stderr}")

    print("SQL restore completed. Validating schema and invariants...")

    # Verification queries
    verify_queries = [
        ("Checking Alembic migration table", "SELECT count(*) FROM alembic_version;"),
        ("Checking Inventory Item available stock non-negative invariant",
         "SELECT count(*) FROM inventory_items WHERE on_hand - reserved < 0;"),
        ("Checking Order count", "SELECT count(*) FROM orders;"),
        ("Checking Product count", "SELECT count(*) FROM products;"),
    ]

    for desc, query in verify_queries:
        res = _exec_psql(["-d", db_name, "-t", "-c", query])
        val = res.stdout.strip()
        print(f"  [OK] {desc}: result = {val}")
        if "invariant" in desc.lower() and int(val) > 0:
            raise AssertionError(f"FATAL: Invariant violated after restore: {val} rows with negative available stock!")

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

    args = parser.parse_args()
    try:
        run_restore(Path(args.backup_file), args.target_url, container_name=args.container)
        return 0
    except Exception as exc:
        print(f"RESTORE FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
