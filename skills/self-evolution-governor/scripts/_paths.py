#!/usr/bin/env python3
"""
Portable path definitions for Self-Evolution Governor scripts.

Resolves $HERMES_HOME environment variable, defaulting to ~/.hermes.
All scripts should import paths from this module instead of hardcoding.
"""
from __future__ import annotations

import os
from pathlib import Path


def get_hermes_home() -> Path:
    """Return the Hermes home directory from env or default."""
    env_val = os.environ.get("HERMES_HOME", "")
    if env_val:
        return Path(env_val).expanduser().resolve()
    return Path.home() / ".hermes"


# ── Root paths (resolved once at import time) ──
HERMES_HOME = get_hermes_home()

STATE_DIR = HERMES_HOME / "state"
EVOLUTION_DIR = STATE_DIR / "evolution"
OPS_GATE_DIR = STATE_DIR / "ops-gate"
SCRIPTS_DIR = HERMES_HOME / "scripts"
SKILLS_DIR = HERMES_HOME / "skills"
CRON_OUTPUT_DIR = HERMES_HOME / "cron" / "output"
CRON_JOBS_FILE = HERMES_HOME / "cron" / "jobs.json"
LOGS_DIR = HERMES_HOME / "logs"

# ── Phase O1: Official Hermes signal sources ──
CURATOR_LOG_DIR = LOGS_DIR / "curator"
GATEWAY_LOG = LOGS_DIR / "gateway.log"
USAGE_FILE = SKILLS_DIR / ".usage.json"
ARCHIVE_DIR = SKILLS_DIR / ".archive"
BUNDLED_MANIFEST = SKILLS_DIR / ".bundled_manifest"
HUB_LOCK_DIR = SKILLS_DIR / ".hub"
HUB_LOCK_FILE = HUB_LOCK_DIR / "lock.json"

# ── Config files ──
HERMES_CONFIG = HERMES_HOME / "config.yaml"
HERMES_ENV = HERMES_HOME / ".env"

# ── Self-evolution state files ──
SIGNALS_FILE = EVOLUTION_DIR / "signals.jsonl"
AGENDA_FILE = EVOLUTION_DIR / "self_agenda.yaml"
PROPOSAL_FILE = EVOLUTION_DIR / "proposal_queue.yaml"
JOURNAL_FILE = EVOLUTION_DIR / "evolution_journal.md"
CANDIDATES_FILE = EVOLUTION_DIR / "agenda_candidates.yaml"
FOCUS_FILE = EVOLUTION_DIR / "HERMES_FOCUS.md"
DIGEST_FILE = EVOLUTION_DIR / "runtime_digest.md"
QUOTA_FILE = EVOLUTION_DIR / "speak_quota.json"
AGENDA_QUOTA_FILE = EVOLUTION_DIR / "agenda_speak_quota.json"
AGENDA_DECISIONS_FILE = EVOLUTION_DIR / "agenda_speak_decisions.yaml"
SCORE_EXPL_DIR = EVOLUTION_DIR / "score_explanations"

# ── Delta cache files ──
LIFECYCLE_CACHE = EVOLUTION_DIR / ".lifecycle_delta_cache.json"
HEALTH_CACHE = EVOLUTION_DIR / ".skill_health_delta_cache.json"
ABSENT_CACHE = EVOLUTION_DIR / ".source_absent_delta_cache.json"

# ── Skills that must never be flagged for archive ──
CORE_GOVERNANCE_SKILLS = frozenset({
    "self-evolution-governor",
    "ops-gate-automation",
    "memory-change-approval-gate",
    "skills-platform-scoping",
    "skill-scene-management",
    "hermes-agent",
    "systematic-debugging",
    "subagent-driven-development",
    "plan",
    "writing-plans",
})
