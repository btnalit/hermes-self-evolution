#!/usr/bin/env python3
"""
Self-Evolution Governor - Signal Collector.
Collects all 10 signal sources and outputs structured signals as JSON to stdout.
Designed to be run by cron daily (04:00) and event-triggered on ops-gate failure.
"""
from __future__ import annotations

from _paths import (
    STATE_DIR, OPS_GATE_DIR, EVOLUTION_DIR, SCRIPTS_DIR, SKILLS_DIR,
    CRON_OUTPUT_DIR, SIGNALS_FILE, AGENDA_FILE, PROPOSAL_FILE,
    JOURNAL_FILE, HERMES_CONFIG, HERMES_ENV, CRON_JOBS_FILE,
    CURATOR_LOG_DIR, USAGE_FILE, ARCHIVE_DIR, BUNDLED_MANIFEST,
    HUB_LOCK_DIR, HUB_LOCK_FILE, GATEWAY_LOG, CORE_GOVERNANCE_SKILLS,
    LIFECYCLE_CACHE, HEALTH_CACHE, ABSENT_CACHE,
    HERMES_HOME,
)

import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from inspect import signature
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

TZ = timezone(timedelta(hours=8))


def _load_cache(cache_file: Path) -> dict:
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except (json.JSONDecodeError, Exception):
            pass
    return {}


def _save_cache(cache_file: Path, state: dict):
    cache_file.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


# ── Signal dedup key (V1.4.1a: signal-type-aware composite key) ──


def _build_signal_dedup_key(sig: dict) -> str:
    """
    Build a deterministic dedup key based on signal type and entity identity.

    Key fields per signal type (not simple source+timestamp):
      skill_health: type + skill_name + observed_at + stale_status
      cron:         type + job_id + run_mtime + has_error
      ops_gate:     type + task_id + date + pass_status
      config:       type + file_path + mtime
      proposal:     type + timestamp + proposal_counts
      tool:         type + timestamp + failure_count
      memory:       type + target + timestamp
      session:      type + timestamp + entry_count
    """
    sig_type = sig.get("type", "")
    ts = sig.get("ts", "")

    if sig_type == "skill_health":
        return (f"skill_health:{sig.get('skill','?')}:{sig.get('stale','')}")
    elif sig_type == "cron_result":
        return (f"cron:{sig.get('job_id','?')}:"
                f"{sig.get('mtime','')}:{sig.get('has_error','')}")
    elif sig_type == "ops_gate_result":
        return (f"ops_gate:{sig.get('task_id','?')}:"
                f"{sig.get('ts','')}:{sig.get('pass','')}")
    elif sig_type == "config_change":
        return (f"config:{sig.get('path','?')}:"
                f"{sig.get('mtime','')}")
    elif sig_type == "proposal_feedback":
        return (f"proposal:{sig.get('ts','')}:"
                f"{sig.get('total_proposals',0)}:{sig.get('pending',0)}")
    elif sig_type == "tool_reliability":
        return (f"tool:{sig.get('ts','')}:"
                f"{sig.get('today_failure_count',0)}")
    elif sig_type == "memory_quality":
        return (f"memory:{sig.get('target','?')}:"
                f"{sig.get('ts','')}")
    elif sig_type == "session_metadata":
        return (f"session:{sig.get('ts','')}:"
                f"{sig.get('total_journal_entries',0)}")
    elif sig_type == "curator_run":
        return (f"curator:{sig.get('run_id','?')}:"
                f"{sig.get('run_at','')}")
    elif sig_type == "skill_usage_telemetry":
        return (f"usage:{sig.get('skill_name','?')}:"
                f"{sig.get('last_used_at','')}")
    elif sig_type == "skill_lifecycle_state":
        return (f"lifecycle:{sig.get('skill_name','?')}:"
                f"{sig.get('state','')}")
    elif sig_type == "source_absent":
        return (f"source_absent:{sig.get('source_path','?')}:"
                f"{sig.get('reason','?')}")
    elif sig_type == "source_absent_report":
        return ("source_absent_report:" +
                str(hash(json.dumps(sig.get('absent_sources', []), sort_keys=True))))
    elif sig_type == "skill_health_snapshot":
        return f"skill_health_snapshot:{sig.get('stale_count',0)}:{sig.get('total_skills',0)}"
    elif sig_type == "skill_health_delta":
        return ("skill_health_delta:" +
                str(hash(json.dumps(sig.get('newly_stale',[]), sort_keys=True)) +
                    hash(json.dumps(sig.get('recovered',[]), sort_keys=True))))
    elif sig_type == "skill_lifecycle_summary":
        return f"lifecycle_summary:{sig.get('active_count',0)}:{sig.get('archived_count',0)}:{sig.get('transitions_this_run',0)}"
    elif sig_type == "source_absent_notes":
        return f"source_absent_notes:{hash(json.dumps(sig.get('notes',[]), sort_keys=True))}"
    # Phase O2-lite (Stage 3) dedup keys
    elif sig_type == "active_cron_dependency":
        return "active_cron_dependency:" + str(hash(
            json.dumps([(s["skill"], s["bound_job_count"]) for s in sig.get("skills", [])],
                       sort_keys=True)))
    elif sig_type == "platform_enabled_status":
        return "platform_enabled_status:" + str(hash(
            json.dumps(sorted([(p["platform"], p["enabled"]) for p in sig.get("platforms", [])]),
                       sort_keys=True)))
    elif sig_type == "protected_skill_status":
        return "protected_skill_status:" + str(hash(
            json.dumps(sorted([(s["skill"], s["is_protected"]) for s in sig.get("skills", [])]),
                       sort_keys=True)))
    elif sig_type == "protected_skill_anomaly":
        return f"protected_skill_anomaly:{hash(json.dumps(sig.get('unprotected_skills',[]), sort_keys=True))}"
    elif sig_type == "recent_session_mention":
        return "recent_session_mention:" + str(hash(
            json.dumps([(m["skill"], m["mention_count"]) for m in sig.get("top_mentions", [])],
                       sort_keys=True)))
    elif sig_type == "gateway_health":
        return "gateway_health:" + str(hash(
            json.dumps(sig.get("hourly_alerts", []), sort_keys=True)))
    else:
        return (f"unknown:{hash(json.dumps(sig, sort_keys=True))}")


def load_recent_dedup_keys(n: int = 1000) -> set[str]:
    """Load dedup keys from the last N lines of signals.jsonl."""
    if not SIGNALS_FILE.exists():
        return set()
    lines = SIGNALS_FILE.read_text().strip().split("\n")
    recent = lines[-n:] if len(lines) > n else lines
    keys = set()
    for line in recent:
        if not line.strip():
            continue
        try:
            sig = json.loads(line)
            keys.add(_build_signal_dedup_key(sig))
        except (json.JSONDecodeError, Exception):
            pass
    return keys


def collect_ops_gate_signals(days: int = 1) -> list[dict]:
    signals = []
    cutoff = datetime.now(TZ) - timedelta(days=days)
    today = today_str()

    today_dir = OPS_GATE_DIR / today
    if today_dir.exists():
        for run_dir in sorted(today_dir.iterdir()):
            postcheck_file = run_dir / "postcheck.json"
            if postcheck_file.exists():
                data = json.loads(postcheck_file.read_text())
                signals.append({
                    "ts": now_iso(),
                    "type": "ops_gate_result",
                    "source": "ops-gate",
                    "task_name": data.get("task_name", "unknown"),
                    "task_id": run_dir.name,
                    "exec_success": data.get("exec_success", False),
                    "verify_success": data.get("verify_success", False),
                    "pass": data.get("pass", False),
                    "duration_sec": data.get("duration_sec", 0),
                    "manual_intervention": data.get("manual_intervention", False),
                })

    # Check yesterday too
    yesterday = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_dir = OPS_GATE_DIR / yesterday
    if yesterday_dir.exists() and days > 1:
        for run_dir in sorted(yesterday_dir.iterdir()):
            postcheck_file = run_dir / "postcheck.json"
            if postcheck_file.exists():
                data = json.loads(postcheck_file.read_text())
                signals.append({
                    "ts": now_iso(),
                    "type": "ops_gate_result",
                    "source": "ops-gate",
                    "task_name": data.get("task_name", "unknown"),
                    "task_id": run_dir.name,
                    "exec_success": data.get("exec_success", False),
                    "verify_success": data.get("verify_success", False),
                    "pass": data.get("pass", False),
                    "duration_sec": data.get("duration_sec", 0),
                    "manual_intervention": data.get("manual_intervention", False),
                })

    return signals


# ── Signal Source 2: cron task status ────────────────────────────────


def collect_cron_signals(days: int = 1) -> list[dict]:
    signals = []
    cutoff = time.time() - days * 86400

    if not CRON_OUTPUT_DIR.exists():
        return signals

    for job_dir in CRON_OUTPUT_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        for f in sorted(job_dir.glob("*.md")):
            if f.stat().st_mtime < cutoff:
                continue
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=TZ).isoformat()
            content = f.read_text()
            lines = content.split('\n')

            # ── has_error detection (V1.3a: P-20260428-av-v13a) ──
            # Layer 1 (PRIMARY): exit code != 0
            # This is the most reliable signal — a non-zero exit means the job failed.
            _exit_re = re.compile(
                r'\b(?:exit\s+code\s*:\s*[1-9]\d*|exit\s+status\s*:\s*[1-9]\d*)',
                re.IGNORECASE
            )
            has_exit_code_error = bool(_exit_re.search(content))

            # Layer 2 (ALWAYS): Python traceback — never a false positive
            _traceback_re = re.compile(
                r'Traceback\s*\(most recent call last\)|Traceback:\s*/',
                re.IGNORECASE
            )
            has_traceback = bool(_traceback_re.search(content))

            # Layer 3 (FALLBACK): regex context-aware error detection
            # Only activates when exit_code is 0 or not found AND no traceback.
            # Uses precise markers (ERROR:, FAILED:, etc.) with 8 guard layers
            # to exclude tables, status reports, documentation.
            has_context_error = False
            if not has_exit_code_error and not has_traceback:
                _error_marker_re = re.compile(
                    r'\b(?:ERROR:|FATAL:|FAILED:|Error:|Failed:)',
                    re.IGNORECASE
                )
                _clean_marker_re = re.compile(
                    r'\b(PASS|passed|success|successfully|null|none|✅|exit\s+code\s*:\s*0)\b',
                    re.IGNORECASE
                )
                for line in lines:
                    if not _error_marker_re.search(line):
                        continue
                    s = line.strip()
                    if _clean_marker_re.search(line):
                        continue
                    if s.startswith('|') and s.endswith('|'):
                        continue
                    if re.search(r'\b(risk_level|priority|severity)\b', line, re.I):
                        continue
                    if re.search(r'\berror:\s*(null|none|false|0|无)\b', line, re.I):
                        continue
                    if re.search(r'\bfailed:\s*(false|null|none|0)\b', line, re.I):
                        continue
                    if re.search(r'\b(lesson|behavioral|instruction|concept|definition|explanation|suggestion|proposal)\b', line, re.I):
                        if not re.search(r'\b(occurred|thrown|raised|detected|caused)\b', line, re.I):
                            continue
                    if '`' in s and re.search(r'`(ERROR|FAILED|CRITICAL)`', s, re.IGNORECASE):
                        continue
                    if re.search(r'[建议替换检测使用]', s):
                        continue
                    has_context_error = True
                    break

            # Final determination: exit_code is PRIMARY, regex is FALLBACK
            has_error = has_exit_code_error or has_traceback or has_context_error
            signals.append({
                "ts": now_iso(),
                "type": "cron_result",
                "source": "cron-output",
                "job_id": job_dir.name,
                "file": str(f),
                "mtime": mtime,
                "has_error": has_error,
            })

    return signals


# ── Signal Source 4: config changes ──────────────────────────────────


def collect_config_signals(days: int = 1) -> list[dict]:
    signals = []
    cutoff = time.time() - days * 86400

    # Scan skills directory for recent changes
    if SKILLS_DIR.exists():
        for category_dir in SKILLS_DIR.iterdir():
            if not category_dir.is_dir():
                continue
            for skill_dir in category_dir.iterdir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists() and skill_md.stat().st_mtime > cutoff:
                    signals.append({
                        "ts": now_iso(),
                        "type": "config_change",
                        "source": "skills",
                        "path": str(skill_md),
                        "change": "modified",
                        "mtime": datetime.fromtimestamp(
                            skill_md.stat().st_mtime, tz=TZ
                        ).isoformat(),
                    })

    # Scan scripts directory
    if SCRIPTS_DIR.exists():
        for f in SCRIPTS_DIR.glob("*.py"):
            if f.stat().st_mtime > cutoff:
                signals.append({
                    "ts": now_iso(),
                    "type": "config_change",
                    "source": "scripts",
                    "path": str(f),
                    "change": "modified",
                    "mtime": datetime.fromtimestamp(
                        f.stat().st_mtime, tz=TZ
                    ).isoformat(),
                })

    return signals


# ── Signal Source 5: memory quality ──────────────────────────────────


def collect_memory_signals() -> list[dict]:
    """Check memory file sizes and estimate quality."""
    signals = []
    hermes_dir = HERMES_HOME / "hermes-agent"
    memory_file = hermes_dir / "memory.json"
    user_file = hermes_dir / "user.json"

    for fname, label in [(memory_file, "memory"), (user_file, "user")]:
        if fname.exists():
            size = fname.stat().st_size
            signals.append({
                "ts": now_iso(),
                "type": "memory_quality",
                "source": "memory-file",
                "target": label,
                "size_bytes": size,
                "size_kb": round(size / 1024, 1),
                # Flag if over 2KB (potentially too bloated)
                "warning": size > 2048,
            })

    return signals


# ── Signal Source 6: skill health ────────────────────────────────────


def collect_skill_health_signals() -> list[dict]:
    """V1.5: Aggregated + delta-only skill health.
    Emits one snapshot per run (replaces 161 per-skill signals).
    Emits delta signal only when skills transition stale <-> not_stale.
    """
    signals = []
    if not SKILLS_DIR.exists():
        return signals

    prev_state = _load_cache(HEALTH_CACHE)
    curr_state: dict[str, bool] = {}
    total = 0
    stale_count = 0
    newly_stale: list[str] = []
    recovered: list[str] = []

    for category_dir in SKILLS_DIR.iterdir():
        if not category_dir.is_dir():
            continue
        for skill_dir in category_dir.iterdir():
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            age_days = (time.time() - skill_md.stat().st_mtime) / 86400
            has_scripts = (skill_dir / "scripts").exists()
            is_stale = age_days > 30 and not has_scripts
            total += 1
            if is_stale:
                stale_count += 1
            curr_state[skill_dir.name] = is_stale

            prev_stale = prev_state.get(skill_dir.name)
            if prev_stale is None and is_stale:
                newly_stale.append(skill_dir.name)
            elif prev_stale is True and not is_stale:
                recovered.append(skill_dir.name)

    _save_cache(HEALTH_CACHE, curr_state)

    if newly_stale or recovered:
        signals.append({
            "ts": now_iso(),
            "type": "skill_health_delta",
            "source": "skills",
            "total_skills": total,
            "stale_count": stale_count,
            "newly_stale": newly_stale,
            "recovered": recovered,
        })

    signals.append({
        "ts": now_iso(),
        "type": "skill_health_snapshot",
        "source": "skills",
        "total_skills": total,
        "stale_count": stale_count,
    })

    return signals


# ── Signal Source 7: tool reliability ────────────────────────────────


def collect_tool_signals() -> list[dict]:
    """Check if we can detect any tool reliability issues."""
    signals = []
    # Check ops gate recent failures as proxy for tool issues
    today = today_str()
    today_dir = OPS_GATE_DIR / today
    failure_count = 0
    if today_dir.exists():
        for run_dir in sorted(today_dir.iterdir()):
            stderr_file = run_dir / "main_stderr.txt"
            if stderr_file.exists() and stderr_file.stat().st_size > 0:
                failure_count += 1

    if failure_count > 0:
        signals.append({
            "ts": now_iso(),
            "type": "tool_reliability",
            "source": "ops-gate-stderr",
            "today_failure_count": failure_count,
            "warning": failure_count > 2,
        })

    return signals


# ── Signal Source 9: session metadata ────────────────────────────────


def collect_session_signals() -> list[dict]:
    """Check evolution journal for session volume proxy."""
    signals = []
    if JOURNAL_FILE.exists():
        lines = JOURNAL_FILE.read_text().strip().split("\n")
        # Count journal entries per day as proxy for session activity
        entry_count = len([l for l in lines if l.startswith("## ")])
        signals.append({
            "ts": now_iso(),
            "type": "session_metadata",
            "source": "evolution-journal",
            "total_journal_entries": entry_count,
        })
    return signals


# ── Signal Source 10: proposal feedback loop ─────────────────────────


def collect_proposal_feedback() -> list[dict]:
    """Check proposal_queue for pending items and their status."""
    signals = []
    if PROPOSAL_FILE.exists():
        try:
            data = json.loads(PROPOSAL_FILE.read_text())
            proposals = data.get("proposals", [])
            pending = [p for p in proposals if p.get("status") == "pending"]
            approved = [p for p in proposals if p.get("status") == "approved"]
            rejected = [p for p in proposals if p.get("status") == "rejected"]

            signals.append({
                "ts": now_iso(),
                "type": "proposal_feedback",
                "source": "proposal-queue",
                "total_proposals": len(proposals),
                "pending": len(pending),
                "approved": len(approved),
                "rejected": len(rejected),
                "needs_review": len(pending) > 0,
            })
        except (json.JSONDecodeError, KeyError):
            pass
    return signals


# ── Phase O1: Official Hermes signal sources (read-only probes) ──────


def collect_curator_signals() -> list[dict]:
    """
    Probe for Curator run reports. Read-only — does not trigger curator runs.
    Signal type: curator_run
    Data source: $HERMES_HOME/logs/curator/<timestamp>/run.json
    """
    signals = []
    ts = now_iso()

    if not CURATOR_LOG_DIR.exists():
        signals.append({
            "ts": ts,
            "type": "source_absent",
            "source": "official_curator",
            "source_path": str(CURATOR_LOG_DIR),
            "reason": "curator_log_dir_not_found",
            "note": "Hermes Curator has never run in this environment. No run reports available.",
        })
        return signals

    run_dirs = sorted(CURATOR_LOG_DIR.iterdir())
    if not run_dirs:
        signals.append({
            "ts": ts,
            "type": "source_absent",
            "source": "official_curator",
            "source_path": str(CURATOR_LOG_DIR),
            "reason": "curator_log_dir_empty",
            "note": "Curator directory exists but contains no run reports.",
        })
        return signals

    # Only read the most recent run (avoid flooding signals)
    latest_run = run_dirs[-1]
    run_json = latest_run / "run.json"
    report_md = latest_run / "REPORT.md"

    if not run_json.exists():
        signals.append({
            "ts": ts,
            "type": "source_absent",
            "source": "official_curator",
            "source_path": str(latest_run),
            "reason": "curator_run_json_not_found",
            "note": f"Curator run dir {latest_run.name} exists but no run.json found.",
        })
        return signals

    try:
        data = json.loads(run_json.read_text())

        # Extract curator auto-counts
        auto = data.get("auto", {}) or {}
        llm = data.get("llm_review", {}) or {}
        risk_flags = []
        archived = auto.get("archived", 0) or 0
        marked_stale = auto.get("marked_stale", 0) or 0

        # Safety: check if any core governance skill was affected
        checked_skills = auto.get("checked", []) or []
        for s in checked_skills:
            if s in CORE_GOVERNANCE_SKILLS:
                risk_flags.append(f"core_skill_in_curator_scope:{s}")

        signals.append({
            "ts": ts,
            "type": "curator_run",
            "source": "official_hermes_curator",
            "run_id": latest_run.name,
            "run_at": data.get("run_at", ""),
            "dry_run": data.get("dry_run", True),
            "duration_seconds": data.get("duration_seconds", 0),
            "auto_counts": {
                "checked": auto.get("checked_count", auto.get("checked", 0)) or 0,
                "marked_stale": marked_stale,
                "archived": archived,
                "reactivated": auto.get("reactivated", 0) or 0,
            },
            "llm_review": {
                "consolidations": llm.get("consolidations", 0) or 0,
                "prunings": llm.get("prunings", 0) or 0,
                "patches": llm.get("patches", 0) or 0,
                "created": llm.get("created", 0) or 0,
            },
            "report_path": str(report_md) if report_md.exists() else "",
            "risk_flags": risk_flags,
            "core_skills_affected": len(risk_flags) > 0,
        })

    except (json.JSONDecodeError, KeyError, Exception) as e:
        signals.append({
            "ts": ts,
            "type": "source_absent",
            "source": "official_curator",
            "source_path": str(run_json),
            "reason": "curator_run_json_parse_error",
            "error": str(e)[:200],
        })

    return signals


def collect_skill_usage_signals() -> list[dict]:
    """
    Read .usage.json for skill usage telemetry.
    Read-only — does not modify usage data.
    Signal type: skill_usage_telemetry
    Data source: $HERMES_HOME/skills/.usage.json
    """
    signals = []
    ts = now_iso()

    if not USAGE_FILE.exists():
        signals.append({
            "ts": ts,
            "type": "source_absent",
            "source": "official_skill_usage",
            "source_path": str(USAGE_FILE),
            "reason": "usage_file_not_found",
            "note": "No .usage.json found. Skill usage telemetry is not available.",
        })
        return signals

    try:
        usage_data = json.loads(USAGE_FILE.read_text())

        # Determine reliability of usage data
        usage_reliability = "available"
        if not usage_data:
            usage_reliability = "empty"

        for skill_name, meta in usage_data.items():
            if not isinstance(meta, dict):
                continue

            state = meta.get("state", "unknown")
            pinned = meta.get("pinned", False)
            use_count = meta.get("use_count", 0)
            view_count = meta.get("view_count", 0)
            patch_count = meta.get("patch_count", 0)
            last_used = meta.get("last_used_at", "")
            last_viewed = meta.get("last_viewed_at", "")
            last_patched = meta.get("last_patched_at", "")
            provenance = meta.get("provenance", "")

            # Compute days since last activity
            days_since = None
            activity_ts = meta.get("last_activity_at", last_used or last_viewed)
            if activity_ts:
                try:
                    last = datetime.fromisoformat(activity_ts)
                    days_since = (datetime.now(TZ) - last).days
                except (ValueError, TypeError):
                    pass

            signals.append({
                "ts": ts,
                "type": "skill_usage_telemetry",
                "source": "official_skill_usage",
                "skill_name": skill_name,
                "state": state,
                "pinned": pinned,
                "use_count": use_count,
                "view_count": view_count,
                "patch_count": patch_count,
                "last_used_at": last_used,
                "last_viewed_at": last_viewed,
                "last_patched_at": last_patched,
                "last_activity_at": activity_ts,
                "days_since_last_activity": days_since,
                "usage_reliability": usage_reliability,
                "provenance": {
                    "agent_created": provenance == "agent_created",
                    "bundled": provenance == "bundled",
                    "hub_installed": provenance == "hub",
                },
            })

    except (json.JSONDecodeError, KeyError, Exception) as e:
        signals.append({
            "ts": ts,
            "type": "source_absent",
            "source": "official_skill_usage",
            "source_path": str(USAGE_FILE),
            "reason": "usage_file_parse_error",
            "error": str(e)[:200],
        })

    return signals


def collect_skill_lifecycle_signals() -> list[dict]:
    """V1.5: Delta-only lifecycle state.
    Emits signals ONLY on state transitions (active <-> archived).
    Uses LIFECYCLE_CACHE to track last-known state per skill.
    """
    signals = []
    ts = now_iso()

    prev_state = _load_cache(LIFECYCLE_CACHE)
    curr_state: dict[str, str] = {}

    probes = {
        "archive_dir": (ARCHIVE_DIR, True),
        "bundled_manifest": (BUNDLED_MANIFEST, True),
        "hub_lock": (HUB_LOCK_FILE, True),
    }

    source_absent_notes = []

    for probe_name, (path, _) in probes.items():
        if not path.exists():
            source_absent_notes.append(f"{probe_name}_not_found")

    # ── Archived skills ──
    archived_skills: dict[str, bool] = {}
    if ARCHIVE_DIR.exists():
        for archive_item in ARCHIVE_DIR.iterdir():
            skill_name = archive_item.name
            archived_skills[skill_name] = True

    # ── Bundled manifest ──
    bundled_skills = set()
    if BUNDLED_MANIFEST.exists():
        try:
            bm_data = json.loads(BUNDLED_MANIFEST.read_text())
            if isinstance(bm_data, list):
                bundled_skills = set(bm_data)
            elif isinstance(bm_data, dict):
                bundled_skills = set(bm_data.keys())
        except (json.JSONDecodeError, Exception):
            pass

    # ── Hub lock ──
    hub_skills = set()
    if HUB_LOCK_FILE.exists():
        try:
            hub_data = json.loads(HUB_LOCK_FILE.read_text())
            if isinstance(hub_data, dict):
                hub_skills = set(hub_data.keys())
        except (json.JSONDecodeError, Exception):
            pass

    # ── Scan active skills ──
    seen_skills = set()
    if SKILLS_DIR.exists():
        for category_dir in SKILLS_DIR.iterdir():
            if not category_dir.is_dir() or category_dir.name.startswith("."):
                continue
            for skill_dir in category_dir.iterdir():
                skill_name = skill_dir.name
                seen_skills.add(skill_name)

                # Determine state
                if skill_name in archived_skills:
                    state = "archived"
                else:
                    state = "active"

                curr_state[skill_name] = state

                # Delta detection: only emit on transition
                prev_state_val = prev_state.get(skill_name)
                if prev_state_val != state:
                    is_core = skill_name in CORE_GOVERNANCE_SKILLS
                    is_bundled = skill_name in bundled_skills
                    is_hub = skill_name in hub_skills
                    pinned = (skill_dir / ".pinned").exists()

                    risk_flags = []
                    if is_core and not pinned:
                        risk_flags.append("unpinned_core_skill")
                    if is_bundled and skill_name in CORE_GOVERNANCE_SKILLS:
                        risk_flags.append("bundled_skill_in_governance")

                    signals.append({
                        "ts": ts,
                        "type": "skill_lifecycle_state",
                        "source": "official_curator_usage",
                        "skill_name": skill_name,
                        "state": state,
                        "pinned": pinned,
                        "archived_at": "",
                        "archive_path": "",
                        "is_core_governance_skill": is_core,
                        "is_bundled": is_bundled,
                        "is_hub_installed": is_hub,
                        "is_agent_created": not is_bundled and not is_hub,
                        "protected_reason": "core_governance_skill" if is_core else "",
                        "risk_flags": risk_flags,
                        "transition_from": prev_state_val,  # key field: evidence of change
                    })

    # Save current state for next run
    _save_cache(LIFECYCLE_CACHE, curr_state)

    # Emit lifecycle summary (1 signal, not per-skill)
    signals.append({
        "ts": ts,
        "type": "skill_lifecycle_summary",
        "source": "official_curator_usage",
        "total_skills": len(seen_skills),
        "active_count": sum(1 for s in curr_state.values() if s == "active"),
        "archived_count": sum(1 for s in curr_state.values() if s == "archived"),
        "transitions_this_run": len([s for s in signals if s.get("transition_from")]),
    })

    # Collect source_absent info for aggregation (no per-source signals here)
    if source_absent_notes:
        signals.append({
            "ts": ts,
            "type": "source_absent_notes",
            "source": "official_lifecycle",
            "notes": source_absent_notes,
        })

    return signals


# ═════════════════════════════════════════════════════════════════════
# V1.5 O2-lite: New Signal Sources (Stage 3)
# ═════════════════════════════════════════════════════════════════════


def collect_cron_dependency_signals() -> list[dict]:
    """O2-lite: Map cron jobs to their bound skills.

    Reads CRON_JOBS_FILE, extracts each job's `skills` field,
    outputs mapping: skill_name -> [job_id, job_name].
    Signal weight 0.80 (strong_keep).
    Delta-only: emits one snapshot per run.
    """
    signals = []
    if not CRON_JOBS_FILE.exists():
        return signals

    try:
        data = json.loads(CRON_JOBS_FILE.read_text())
        jobs = data.get("jobs", data) if isinstance(data, dict) else data
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
    except (json.JSONDecodeError, Exception):
        return signals

    # Build skill->jobs mapping
    skill_jobs: dict[str, list[dict]] = {}
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = job.get("id", "?")
        job_name = job.get("name", "?")

        skills = job.get("skills", [])
        if not skills and job.get("skill"):
            skills = [job["skill"]]

        for skill in skills:
            if skill not in skill_jobs:
                skill_jobs[skill] = []
            skill_jobs[skill].append({
                "job_id": job_id,
                "job_name": job_name,
                "enabled": job.get("enabled", False),
                "schedule": job.get("schedule_display", ""),
                "last_status": job.get("last_status", ""),
            })

    # Snapshot: skills that are actively bound to cron jobs
    if skill_jobs:
        signals.append({
            "ts": now_iso(),
            "type": "active_cron_dependency",
            "source": "cron-jobs",
            "total_jobs": len(jobs),
            "total_bound_skills": len(skill_jobs),
            "signal_weight": 0.80,
            "skills": [
                {
                    "skill": skill,
                    "jobs": jobs_info,
                    "bound_job_count": len(jobs_info),
                }
                for skill, jobs_info in sorted(skill_jobs.items())
            ],
        })

    return signals


def collect_platform_status_signals() -> list[dict]:
    """O2-lite: Check which messaging platforms are enabled.

    Reads HERMES_CONFIG for platform_toolsets entries and HERMES_ENV
    for API tokens. Outputs each platform's enabled status.
    Delta-only: emits one snapshot per run.
    """
    signals = []
    ts = now_iso()

    # Read config for platform_toolsets
    platforms_configured: dict[str, list[str]] = {}
    if HERMES_CONFIG.exists():
        try:
            cfg = yaml.safe_load(HERMES_CONFIG.read_text())
            pt = cfg.get("platform_toolsets", {}) if cfg else {}
            for pname, tools in pt.items():
                if tools:
                    platforms_configured[pname] = tools
        except Exception:
            pass

    # Read .env for token presence
    env_tokens: dict[str, bool] = {}
    if HERMES_ENV.exists():
        for line in HERMES_ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "_TOKEN" in line or "_SECRET" in line or "_KEY" in line:
                parts = line.split("=", 1)
                if len(parts) == 2:
                    key = parts[0].lower()
                    if "telegram" in key:
                        env_tokens["telegram"] = True
                    elif "weixin" in key or "wechat" in key:
                        env_tokens["weixin"] = True
                    elif "wecom" in key:
                        env_tokens["wecom"] = True
                    elif "whatsapp" in key:
                        env_tokens["whatsapp"] = True
                    elif "discord" in key:
                        env_tokens["discord"] = True

    # Check WHATSAPP_ENABLED specifically
    if HERMES_ENV.exists():
        for line in HERMES_ENV.read_text().splitlines():
            line = line.strip()
            if line.startswith("WHATSAPP_ENABLED=true"):
                env_tokens["whatsapp"] = True

    # Build platform status list
    detected = sorted(set(list(platforms_configured.keys()) + list(env_tokens.keys())))
    platforms = []
    for p in detected:
        has_config = p in platforms_configured
        has_token = env_tokens.get(p, False)
        tools = platforms_configured.get(p, [])
        enabled = has_config and has_token
        platforms.append({
            "platform": p,
            "enabled": enabled,
            "has_config": has_config,
            "has_token": has_token,
            "toolset_count": len(tools),
        })

    if platforms:
        signals.append({
            "ts": ts,
            "type": "platform_enabled_status",
            "source": "hermes-config",
            "total_platforms": len(detected),
            "enabled_count": sum(1 for p in platforms if p["enabled"]),
            "signal_weight": 0.60,
            "platforms": platforms,
        })

    return signals


def collect_protected_skills_signals() -> list[dict]:
    """O2-lite: Check chattr +i protection on core governance skills.

    For each skill in CORE_GOVERNANCE_SKILLS, runs lsattr to verify
    +i flag. Emits anomaly signal if expected protection is missing.
    Delta-only via dedup key (per-skill state cached).
    """
    signals = []
    ts = now_iso()

    protected = []
    anomalies = []

    for skill_name in sorted(CORE_GOVERNANCE_SKILLS):
        # Find skill directory
        skill_dir = None
        if SKILLS_DIR.exists():
            for cat_dir in SKILLS_DIR.iterdir():
                if not cat_dir.is_dir():
                    continue
                candidate = cat_dir / skill_name
                if candidate.exists() and candidate.is_dir():
                    skill_dir = candidate
                    break

        if skill_dir is None:
            protected.append({
                "skill": skill_name,
                "expected_protected": True,
                "is_protected": None,
                "error": "skill_dir_not_found",
            })
            continue

        # Check chattr +i
        try:
            result = subprocess.run(
                ["lsattr", "-d", str(skill_dir)],
                capture_output=True, text=True, timeout=5,
            )
            attrs = result.stdout.strip()
            has_i = attrs.startswith("----i") or "i" in attrs.split()[0] if attrs else False
        except Exception as e:
            has_i = False

        entry = {
            "skill": skill_name,
            "expected_protected": True,
            "is_protected": has_i,
        }
        protected.append(entry)

        if not has_i:
            anomalies.append(skill_name)

    # Always emit status snapshot
    signals.append({
        "ts": ts,
        "type": "protected_skill_status",
        "source": "skills-filesystem",
        "total_governance_skills": len(CORE_GOVERNANCE_SKILLS),
        "protected_count": sum(1 for p in protected if p.get("is_protected")),
        "signal_weight": 0.80,
        "skills": protected,
    })

    # Emit anomaly signal if any skill lost protection
    if anomalies:
        signals.append({
            "ts": ts,
            "type": "protected_skill_anomaly",
            "source": "skills-filesystem",
            "anomaly_count": len(anomalies),
            "signal_weight": 0.95,
            "unprotected_skills": anomalies,
            "risk_level": "high",
            "recommended_action": f"Run: chattr -R +i {SKILLS_DIR}/<category>/<skill_name>",
        })

    return signals


def collect_recent_session_mentions(days: int = 7) -> list[dict]:
    """O2-lite: Scan evolution_journal.md for skill name mentions.

    Reads journal file, counts mentions of known skill names
    over the last N days. Does NOT read signals.jsonl,
    score_explanations/, or console docs/.
    Delta-only via dedup key.
    """
    signals = []
    ts = now_iso()

    if not JOURNAL_FILE.exists():
        return signals

    journal_text = JOURNAL_FILE.read_text(encoding="utf-8")

    # Build a set of skill names to scan for
    skill_names: set[str] = set()
    if SKILLS_DIR.exists():
        for cat_dir in SKILLS_DIR.iterdir():
            if not cat_dir.is_dir() or cat_dir.name.startswith("."):
                continue
            for skill_dir in cat_dir.iterdir():
                skill_names.add(skill_dir.name)

    # Count mentions per skill in journal text
    mention_counts: dict[str, int] = {}
    for name in skill_names:
        count = journal_text.count(name)
        if count > 0:
            mention_counts[name] = count

    # Top 20 most mentioned
    top_skills = sorted(mention_counts.items(), key=lambda x: -x[1])[:20]

    if top_skills:
        signals.append({
            "ts": ts,
            "type": "recent_session_mention",
            "source": "evolution-journal",
            "journal_size_bytes": len(journal_text),
            "total_unique_skills_mentioned": len(mention_counts),
            "signal_weight": 0.60,
            "scan_days": days,
            "top_mentions": [
                {"skill": name, "mention_count": count}
                for name, count in top_skills
            ],
        })

    return signals


# ── Gateway Health ────────────────────────────────────────────────

# Thresholds for gateway health signals (per hour, except noted)
# Derived from 25 days of gateway log analysis:
#   - Normal baseline: network_error=2/hr, reconnect=1/hr
#   - 3x+ surge (6+/hr) signals real instability
#   - send_failed should be 0 after MEDIA prompt fix
#   - fallback_ip / polling_conflict are extremely rare (1-6 in 25 days)
GATEWAY_THRESHOLDS = {
    "network_error": 4,
    "reconnect": 2,
    "send_failed": 0,
    "send_timeout": 3,
    "fallback_ip": 0,
    "polling_conflict": 0,
}

_GATEWAY_PATTERNS = {
    "network_error": re.compile(
        r"Server disconnected without sending a response|"
        r"SSLV3_ALERT_HANDSHAKE_FAILURE|"
        r"ConnectError.*[Ss][Ss][Ll]",
        re.IGNORECASE,
    ),
    "reconnect": re.compile(r"scheduling reconnect", re.IGNORECASE),
    "send_failed": re.compile(r"Failed to send media", re.IGNORECASE),
    "send_timeout": re.compile(r"telegram\.error\.TimedOut", re.IGNORECASE),
    "fallback_ip": re.compile(r"using sticky fallback IP", re.IGNORECASE),
    "polling_conflict": re.compile(r"terminated by other getUpdates", re.IGNORECASE),
}

_GATEWAY_WINDOWS = {
    "network_error": "hour",
    "reconnect": "hour",
    "send_failed": "hour",
    "send_timeout": "hour",
    "fallback_ip": "day",
    "polling_conflict": "day",
}


def collect_gateway_health_signals() -> list[dict]:
    """Scan gateway.log for communication health anomalies.

    Scans the last 24 hours of gateway activity, aggregates events by hour,
    and emits one signal per anomaly type that exceeds its threshold.
    Delta-cached: only emits when the set of active alerts changes.
    """
    if not GATEWAY_LOG.exists():
        return []

    signals = []
    ts = now_iso()
    cutoff_24h = time.time() - 86400

    # ── Parse events from last 24h ──
    hourly_events: dict[str, dict[str, int]] = {}
    daily_events: dict[str, int] = {}
    ts_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}):")

    try:
        text = GATEWAY_LOG.read_text(encoding="utf-8")
    except Exception:
        return []

    for line in text.split("\n"):
        m = ts_pattern.match(line)
        if not m:
            continue
        hour_key = m.group(1)

        try:
            line_ts = datetime.strptime(hour_key, "%Y-%m-%d %H").timestamp()
        except ValueError:
            continue
        if line_ts < cutoff_24h:
            continue

        for ev_type, pattern in _GATEWAY_PATTERNS.items():
            if pattern.search(line):
                if _GATEWAY_WINDOWS[ev_type] == "hour":
                    hourly_events.setdefault(hour_key, {}).setdefault(ev_type, 0)
                    hourly_events[hour_key][ev_type] += 1
                else:
                    daily_events[ev_type] = daily_events.get(ev_type, 0) + 1
                break

    # ── Check thresholds and build alerts ──
    hourly_alerts: list[dict] = []

    for hour_key, events in sorted(hourly_events.items()):
        for ev_type, count in sorted(events.items()):
            threshold = GATEWAY_THRESHOLDS[ev_type]
            if count > threshold:
                hourly_alerts.append({
                    "hour": hour_key,
                    "type": ev_type,
                    "count": count,
                    "threshold": threshold,
                })

    for ev_type, count in sorted(daily_events.items()):
        threshold = GATEWAY_THRESHOLDS[ev_type]
        if count > threshold:
            hourly_alerts.append({
                "hour": "24h",
                "type": ev_type,
                "count": count,
                "threshold": threshold,
            })

    # ── Emit one signal per day with all alerts ──
    if hourly_alerts:
        signals.append({
            "ts": ts,
            "type": "gateway_health",
            "source": "gateway",
            "total_hours_scanned": len(hourly_events),
            "total_daily_events": dict(sorted(daily_events.items())),
            "alert_count": len(hourly_alerts),
            "hourly_alerts": hourly_alerts,
        })

    return signals


# ── Main ─────────────────────────────────────────────────────────────


def main():
    days = int(os.environ.get("COLLECT_DAYS", "1"))
    all_signals = []

    collectors = [
        ("ops-gate", collect_ops_gate_signals),
        ("cron", collect_cron_signals),
        ("config", collect_config_signals),
        ("memory", collect_memory_signals),
        ("skill-health", collect_skill_health_signals),
        ("tool-reliability", collect_tool_signals),
        ("session", collect_session_signals),
        ("proposal-feedback", collect_proposal_feedback),
        # Phase O1: Official Hermes signal sources (read-only probes)
        ("curator", collect_curator_signals),
        ("skill-usage", collect_skill_usage_signals),
        ("skill-lifecycle", collect_skill_lifecycle_signals),
        # Phase O2-lite: New signal sources (Stage 3)
        ("cron-dependency", collect_cron_dependency_signals),
        ("platform-status", collect_platform_status_signals),
        ("protected-skills", collect_protected_skills_signals),
        ("session-mentions", collect_recent_session_mentions),
        # Phase O3: Gateway communication health
        ("gateway-health", collect_gateway_health_signals),
    ]

    # Signal source 3 (user corrections) and 8 (user satisfaction)
    # are handled by the cron prompt's reasoning (session_search),
    # not by mechanical data collection.

    summary = {}
    for name, fn in collectors:
        try:
            # Only pass days param if the function accepts it
            params = list(signature(fn).parameters.keys())
            if params:
                sigs = fn(days)
            else:
                sigs = fn()
            all_signals.extend(sigs)
            summary[name] = len(sigs)
        except Exception as e:
            summary[name] = f"error: {e}"

    # ── V1.5: Aggregate source_absent signals (Stage 2 denoising) ──
    # Collect all per-source absent signals, aggregate into one,
    # emit only if the set of absent sources changed from last run.
    absent_signals = [s for s in all_signals if s.get("type") == "source_absent"]
    all_signals = [s for s in all_signals if s.get("type") != "source_absent"]

    # Also collect lifecycle source_absent_notes
    absent_notes = []
    for s in all_signals:
        if s.get("type") == "source_absent_notes":
            absent_notes.extend(s.get("notes", []))
    all_signals = [s for s in all_signals if s.get("type") != "source_absent_notes"]

    if absent_signals or absent_notes:
        aggregated = []
        for s in absent_signals:
            aggregated.append({
                "source": s.get("source", "?"),
                "path": s.get("source_path", "?"),
                "reason": s.get("reason", "?"),
                "note": s.get("note", ""),
            })
        for note in absent_notes:
            aggregated.append({
                "source": "official_lifecycle",
                "path": note,
                "reason": note,
                "note": "",
            })

        # Dedup by path:reason
        seen = set()
        unique_sources = []
        for src in aggregated:
            key = f"{src['path']}:{src['reason']}"
            if key not in seen:
                seen.add(key)
                unique_sources.append(src)

        # Compare with cache — only emit if the set changed
        cache_key = json.dumps(unique_sources, sort_keys=True)
        prev_cache = _load_cache(ABSENT_CACHE)
        if prev_cache.get("sources") != cache_key:
            _save_cache(ABSENT_CACHE, {"sources": cache_key})
            all_signals.append({
                "ts": now_iso(),
                "type": "source_absent_report",
                "source": "aggregated",
                "absent_count": len(unique_sources),
                "absent_sources": unique_sources,
            })
            summary["source_absent_aggregated"] = 1
        else:
            summary["source_absent_aggregated"] = "unchanged"

    # Write signals to file (append) — V1.4.1a: dedup by signal-type-aware key
    dedup_keys = load_recent_dedup_keys(n=2000)
    written_count = 0
    skipped_count = 0
    for sig in all_signals:
        key = _build_signal_dedup_key(sig)
        if key in dedup_keys:
            skipped_count += 1
            continue
        dedup_keys.add(key)
        line = json.dumps(sig, ensure_ascii=False)
        with open(SIGNALS_FILE, "a") as f:
            f.write(line + "\n")
        written_count += 1

    # Output machine-readable summary as JSON
    output = {
        "ts": now_iso(),
        "total_signals": len(all_signals),
        "written_count": written_count,
        "skipped_duplicates": skipped_count,
        "summary": summary,
        "signals": all_signals,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
