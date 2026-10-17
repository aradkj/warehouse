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
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent


def get_current_head_revision(
    alembic_ini_path: Path | str | None = None,
    migrations_dir: Path | str | None = None,
) -> str:
    """Resolve the latest Alembic migration head revision offline from migration scripts.

    Guarantees:
    - Zero network or database access: inspects migration script graph on disk.
    - Resolves alembic.ini or migrations directory relative to codebase / environment.
    - Asserts that exactly one migration head exists (single linear history).
    - Fails closed if multiple heads exist or if no migration head is found.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    if alembic_ini_path is not None or migrations_dir is not None:
        ini_path = Path(alembic_ini_path) if alembic_ini_path else None
        mig_dir = Path(migrations_dir) if migrations_dir else None
        if ini_path and ini_path.exists():
            config = Config(str(ini_path))
        elif mig_dir and mig_dir.exists():
            config = Config()
            config.set_main_option("script_location", str(mig_dir))
        else:
            raise FileNotFoundError(
                f"Could not locate alembic.ini or migrations directory at specified path: {ini_path or mig_dir}."
            )
    else:
        ini_path = REPO_ROOT / "alembic.ini"
        mig_dir = REPO_ROOT / "migrations"
        if ini_path.exists():
            config = Config(str(ini_path))
        elif mig_dir.exists():
            config = Config()
            config.set_main_option("script_location", str(mig_dir))
        else:
            cwd_ini = Path("alembic.ini")
            cwd_mig = Path("migrations")
            if cwd_ini.exists():
                config = Config(str(cwd_ini))
            elif cwd_mig.exists():
                config = Config()
                config.set_main_option("script_location", str(cwd_mig))
            else:
                raise FileNotFoundError(
                    f"Could not locate alembic.ini or migrations directory. Checked {ini_path} and {mig_dir}."
                )

    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if not heads:
        raise RuntimeError("No Alembic migration heads found in migration scripts directory.")
    if len(heads) > 1:
        raise RuntimeError(
            f"Multiple Alembic migration heads detected: {heads}. Expected a single linear migration head."
        )
    return heads[0]


def __getattr__(name: str) -> Any:
    """Lazily resolve module attributes (PEP 562).

    Avoids eager head resolution and disk inspection on initial module import,
    preventing side effects in environments where migrations are not present.
    """
    if name == "EXPECTED_HEAD_REVISION":
        return get_current_head_revision()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


MAINTENANCE_DATABASES: tuple[str, ...] = ("postgres", "template0", "template1")

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


def build_terminate_connections_sql(quoted_db_name: str, unquoted_db_name: str) -> list[str]:
    """Construct SQL statements to safely disallow new connections and terminate active sessions.

    Scoped strictly to the target database; never affects other databases or the maintenance session.
    """
    disallow_sql = f"ALTER DATABASE {quoted_db_name} WITH ALLOW_CONNECTIONS false;"
    escaped_literal = unquoted_db_name.replace("'", "''")
    terminate_sql = (
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{escaped_literal}' AND pid <> pg_backend_pid();"
    )
    return [disallow_sql, terminate_sql]


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
    expected_revision: str | None = None,
) -> bool:
    """Execute restoration and verify post-restore database integrity."""
    target_revision = (
        expected_revision if expected_revision is not None else get_current_head_revision()
    )

    if not backup_file.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_file}")

    verify_checksum(backup_file, allow_unverified=allow_unverified)

    params = parse_database_url(target_database_url)
    db_name = str(params["database"])

    # Reject system maintenance / template databases
    if db_name.lower() in MAINTENANCE_DATABASES:
        raise ValueError(
            f"Refusing to restore over system maintenance/template database '{db_name}'. "
            "Target must be an application or tenant database."
        )

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

    def _exec_psql(cmd_args: list[str], stdin_f: Any = None) -> subprocess.CompletedProcess[str]:
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

    # Terminate active client connections targeting db_name safely before dropping
    print(f"Terminating active sessions for target database {quoted_db}...")
    disallow_sql, terminate_sql = build_terminate_connections_sql(quoted_db, db_name)
    # Ignore errors from disallow in case database does not exist yet
    _exec_psql(["-d", "postgres", "-c", disallow_sql])
    term_res = _exec_psql(["-d", "postgres", "-c", terminate_sql])
    if term_res.returncode != 0:
        print(
            f"Notice: Session termination returned code {term_res.returncode}: "
            f"{term_res.stderr.strip()}",
            file=sys.stderr,
        )

    # Recreate target database safely using quoted identifier with FORCE
    drop_res = _exec_psql(
        ["-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {quoted_db} WITH (FORCE);"]
    )
    if drop_res.returncode != 0:
        # Fallback to standard drop without WITH (FORCE) if older postgres
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
        ("Checking Alembic migration table", "SELECT version_num FROM alembic_version;"),
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
        raw_val = res.stdout.strip()
        print(f"  [OK] {desc}: result = {raw_val}")

        if "alembic" in desc.lower():
            revisions = [line.strip() for line in raw_val.splitlines() if line.strip()]
            if not revisions:
                raise AssertionError("FATAL: alembic_version table is empty after restore!")
            if len(revisions) > 1:
                raise AssertionError(
                    f"FATAL: Multiple Alembic revisions found in restored database: {revisions}. "
                    f"Expected single linear head '{target_revision}'!"
                )
            val = revisions[0]
            if target_revision and val != target_revision:
                raise AssertionError(
                    f"FATAL: Restored Alembic revision '{val}' does not match expected revision '{target_revision}'!"
                )
        elif "invariant" in desc.lower():
            if int(raw_val) > 0:
                raise AssertionError(
                    f"FATAL: Invariant violated after restore: {raw_val} rows with negative available stock!"
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
        default=None,
        help="Expected Alembic revision after restoration (defaults to current codebase head)",
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
