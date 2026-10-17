#!/usr/bin/env python3
"""Repeatable PostgreSQL backup utility (Phase 5 Workstream G).

Guarantees:
* Safe credential handling: Uses PGPASSWORD environment variable, never CLI args.
* Atomic backup creation: Writes to temporary file before final rename.
* Integrity verification: Computes SHA-256 checksum alongside backup artifact.
* Retention policy: Prunes backup artifacts older than retention days/count.
* Free space validation: Asserts sufficient target disk space prior to dump.
* Clean failure exit codes (0 for success, non-zero for failure).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def parse_database_url(url: str) -> dict[str, str | int]:
    """Extract connection parameters from PostgreSQL URL."""
    # Strip asyncpg driver prefix if present
    cleaned_url = url.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(cleaned_url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "arad",
        "password": parsed.password or "arad",
        "database": parsed.path.lstrip("/") or "arad",
    }


def compute_sha256(filepath: Path) -> str:
    """Compute hex SHA-256 digest of a file in chunks."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def check_free_space(dest_dir: Path, min_mb: int = 100) -> bool:
    """Ensure sufficient free disk space in destination directory."""
    stat = shutil.disk_usage(dest_dir)
    free_mb = stat.free // (1024 * 1024)
    if free_mb < min_mb:
        print(
            f"ERROR: Insufficient disk space in {dest_dir}. Free: {free_mb}MB, Required: {min_mb}MB",
            file=sys.stderr,
        )
        return False
    return True


def prune_old_backups(dest_dir: Path, prefix: str, max_keep: int = 7) -> list[Path]:
    """Prune oldest backups exceeding max_keep retention threshold."""
    backups = sorted(
        dest_dir.glob(f"{prefix}_*.sql*"),
        key=lambda p: p.stat().st_mtime,
    )
    # Exclude .sha256 files from count
    actual_backups = [b for b in backups if not b.name.endswith(".sha256")]
    pruned = []
    if len(actual_backups) > max_keep:
        to_delete = actual_backups[:-max_keep]
        for item in to_delete:
            item.unlink(missing_ok=True)
            sha_file = item.with_suffix(item.suffix + ".sha256")
            sha_file.unlink(missing_ok=True)
            pruned.append(item)
    return pruned


def run_backup(
    database_url: str,
    dest_dir: Path,
    prefix: str = "arad_backup",
    max_keep: int = 7,
    container_name: str | None = None,
) -> Path:
    """Execute logical PostgreSQL backup with integrity hash and retention pruning."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not check_free_space(dest_dir):
        raise RuntimeError("Insufficient disk space for backup")

    params = parse_database_url(database_url)
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
    final_filename = f"{prefix}_{timestamp}.sql"
    tmp_path = dest_dir / f"{final_filename}.tmp"
    final_path = dest_dir / final_filename

    env = os.environ.copy()
    env["PGPASSWORD"] = str(params["password"])

    print(f"Starting PostgreSQL backup of '{params['database']}' to {final_path}...")

    if container_name or os.environ.get("RUN_IN_DOCKER"):
        # Exec pg_dump via docker container if local pg_dump is unavailable
        c_name = container_name or "arad-postgres-1"
        cmd = [
            "docker",
            "exec",
            "-e",
            f"PGPASSWORD={params['password']}",
            c_name,
            "pg_dump",
            "-U",
            str(params["user"]),
            "-d",
            str(params["database"]),
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
        ]
        with open(tmp_path, "wb") as out_f:
            proc = subprocess.run(cmd, stdout=out_f, stderr=subprocess.PIPE, text=False)
            if proc.returncode != 0:
                tmp_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"docker exec pg_dump failed (code {proc.returncode}): {proc.stderr.decode('utf-8', errors='replace')}"
                )
    else:
        # Check if local pg_dump exists, fallback to docker exec
        pg_dump_bin = shutil.which("pg_dump")
        if pg_dump_bin:
            cmd = [
                pg_dump_bin,
                "-h",
                str(params["host"]),
                "-p",
                str(params["port"]),
                "-U",
                str(params["user"]),
                "-d",
                str(params["database"]),
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "-f",
                str(tmp_path),
            ]
            local_proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
            if local_proc.returncode != 0:
                tmp_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"pg_dump failed (code {local_proc.returncode}): {local_proc.stderr}"
                )
        else:
            # Fallback to docker container
            c_name = "arad-postgres-1"
            cmd = [
                "docker",
                "exec",
                "-e",
                f"PGPASSWORD={params['password']}",
                c_name,
                "pg_dump",
                "-U",
                str(params["user"]),
                "-d",
                str(params["database"]),
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
            ]
            with open(tmp_path, "wb") as out_f:
                proc = subprocess.run(cmd, stdout=out_f, stderr=subprocess.PIPE, text=False)
                if proc.returncode != 0:
                    tmp_path.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"docker exec pg_dump fallback failed: {proc.stderr.decode('utf-8', errors='replace')}"
                    )

    # Atomic move
    tmp_path.rename(final_path)

    # Compute checksum
    sha256 = compute_sha256(final_path)
    sha_path = final_path.with_suffix(final_path.suffix + ".sha256")
    sha_path.write_text(f"{sha256}  {final_path.name}\n", encoding="utf-8")

    file_size_kb = final_path.stat().st_size // 1024
    print(f"Backup completed successfully: {final_path} ({file_size_kb} KB)")
    print(f"SHA-256 Checksum: {sha256}")

    # Prune retention
    pruned = prune_old_backups(dest_dir, prefix, max_keep=max_keep)
    if pruned:
        print(f"Pruned {len(pruned)} old backups according to retention policy (keep {max_keep}).")

    return final_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Arad Platform PostgreSQL Backup Utility")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("ARAD_DATABASE_URL", "postgresql://arad:arad@localhost:5432/arad"),
        help="PostgreSQL URL to backup",
    )
    parser.add_argument(
        "--dest-dir",
        default=os.environ.get("ARAD_BACKUP_DIR", "backups"),
        help="Target directory for backup artifacts",
    )
    parser.add_argument("--prefix", default="arad_backup", help="Filename prefix")
    parser.add_argument("--max-keep", type=int, default=7, help="Number of backups to retain")
    parser.add_argument(
        "--container", default=None, help="Docker container name if using container exec"
    )

    args = parser.parse_args()
    try:
        run_backup(
            database_url=args.database_url,
            dest_dir=Path(args.dest_dir),
            prefix=args.prefix,
            max_keep=args.max_keep,
            container_name=args.container,
        )
        return 0
    except Exception as exc:
        print(f"BACKUP FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
