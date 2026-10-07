"""Process entry points.

Phase 1 runs one process (the API). Phase 2 adds ``arad-worker`` and
``arad-outbox-relay`` entry points next to this one; keeping the runner here means
the container command never has to know about import paths.
"""

from __future__ import annotations

import uvicorn

from bootstrap.config import get_settings


def serve() -> None:
    """Run the API with uvicorn (used by the ``arad-api`` console script)."""
    settings = get_settings()
    uvicorn.run(
        "bootstrap.app:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - containers must bind all interfaces
        port=8000,
        log_level=settings.log_level.lower(),
        access_log=False,  # replaced by the structured access-log middleware
    )


def main() -> None:
    serve()


if __name__ == "__main__":  # pragma: no cover
    main()
