from __future__ import annotations

import logging
import os
import resource

from noteflow_worker.config import settings

logger = logging.getLogger(__name__)


def initialize_parse_worker_sandbox() -> None:
    """Apply OS resource limits before a parser child accepts untrusted PDFs."""

    limits = (
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_NOFILE, max(64, settings.pdf_sandbox_max_open_files)),
        (resource.RLIMIT_FSIZE, max(16, settings.pdf_sandbox_max_output_mib) * 1024 * 1024),
    )
    for kind, requested in limits:
        try:
            _, hard = resource.getrlimit(kind)
            soft = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
            resource.setrlimit(kind, (soft, hard))
        except (OSError, ValueError) as exc:
            logger.warning("parse_sandbox_limit_unavailable kind=%s error=%s", kind, exc)
    os.umask(0o077)
