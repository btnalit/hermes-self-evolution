"""hermes-self-evolution — runtime context bridge for self-evolution-governor.

This plugin uses the ``on_session_start`` hook to log the presence of the
``runtime_digest.md`` file produced by the self-evolution-governor cron job.
The actual injection of digest content into the agent's prompt is handled
by the skill's own ``SKILL.md`` instructions (loaded automatically on every
session).  This plugin simply monitors and logs the file's state so the
operator can verify the bridge is operational.

The digest file lives at ``$HERMES_HOME/state/evolution/runtime_digest.md``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DIGEST_NAME = "runtime_digest.md"


def _get_digest_path() -> Path | None:
    """Resolve the canonical runtime_digest.md path from the environment.

    Returns ``None`` if ``HERMES_HOME`` is unset (shouldn't happen in
    normal operation).
    """
    import os

    home = os.environ.get("HERMES_HOME", "").strip()
    if not home:
        return None
    return Path(home) / "state" / "evolution" / _DIGEST_NAME


def _parse_valid_until(digest_text: str) -> datetime | None:
    """Parse the ``Valid until:`` line from the digest file body.

    Returns a timezone-aware datetime if found, ``None`` otherwise.
    """
    for line in digest_text.splitlines():
        line = line.strip()
        if line.lower().startswith("valid until"):
            # Handle "Valid until: 2026-05-06T18:00:00+00:00" or similar
            parts = line.split(":", 1)
            if len(parts) == 2:
                candidate = parts[1].strip()
                # Try ISO-8601 first (includes timezone)
                try:
                    return datetime.fromisoformat(candidate)
                except ValueError:
                    pass
                # Try common date-only fallback
                for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        naive = datetime.strptime(candidate, fmt)
                        return naive.replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
    return None


# ---------------------------------------------------------------------------
# Hook
# ---------------------------------------------------------------------------

def _on_session_start(
    session_id: str = "",
    model: str = "",
    platform: str = "",
    **_: object,
) -> None:
    """Check runtime_digest.md availability and log its state.

    This runs at the very beginning of every agent session.  It does **not**
    inject the digest into the conversation — that is handled by the skill's
    ``SKILL.md`` instructions which the plugin system loads automatically.
    """
    digest_path = _get_digest_path()

    if digest_path is None:
        logger.debug(
            "[self-evolution] HERMES_HOME not set; cannot check runtime_digest.md"
        )
        return

    if not digest_path.exists():
        logger.debug(
            "[self-evolution] runtime_digest.md not found at %s "
            "(session=%s, model=%s, platform=%s) — "
            "self-evolution-governor cron may not be running.",
            digest_path,
            session_id,
            model,
            platform,
        )
        return

    try:
        digest_text = digest_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "[self-evolution] Cannot read runtime_digest.md at %s: %s",
            digest_path,
            exc,
        )
        return

    # Log that the digest is available for this session
    logger.info(
        "[self-evolution] runtime_digest.md loaded (%d bytes) for session=%s, "
        "model=%s, platform=%s",
        len(digest_text),
        session_id,
        model,
        platform,
    )

    # Check expiry if present
    valid_until = _parse_valid_until(digest_text)
    if valid_until is not None:
        now = datetime.now(timezone.utc)
        if valid_until < now:
            logger.warning(
                "[self-evolution] runtime_digest.md (Valid until: %s) "
                "has expired as of %s for session=%s. "
                "Run self-evolution-governor to refresh.",
                valid_until.isoformat(),
                now.isoformat(),
                session_id,
            )
        else:
            remaining = valid_until - now
            logger.debug(
                "[self-evolution] runtime_digest.md valid for another %s "
                "(expires %s) — session=%s",
                remaining,
                valid_until.isoformat(),
                session_id,
            )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the ``on_session_start`` hook."""
    ctx.register_hook("on_session_start", _on_session_start)
