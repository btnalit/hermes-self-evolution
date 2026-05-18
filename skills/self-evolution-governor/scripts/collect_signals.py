#!/usr/bin/env python3
"""
Self-Evolution Governor - Signal Collector.
Collects all 10 signal sources and outputs structured signals as JSON to stdout.
Designed to be run by cron daily (04:00) and event-triggered on ops-gate failure.
"""
from __future__ import annotations

import json
import argparse
import hashlib
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from inspect import signature
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

TZ = timezone(timedelta(hours=8))

STATE_DIR = Path("/vol1/.hermes/state")
OPS_GATE_DIR = STATE_DIR / "ops-gate"
EVOLUTION_DIR = STATE_DIR / "evolution"
SCRIPTS_DIR = Path("/vol1/.hermes/scripts")
SKILLS_DIR = Path("/vol1/.hermes/skills")
CRON_OUTPUT_DIR = Path("/vol1/.hermes/cron/output")

# Real session data sources (replaces evolution_journal proxy)
SESSION_DIR = Path(os.path.expanduser("~/.hermes/sessions"))
SESSIONS_INDEX = SESSION_DIR / "sessions.json"

# Phase O1: Official Hermes signal sources (read-only probes)
CURATOR_LOG_DIR = Path("/vol1/.hermes/logs/curator")
USAGE_FILE = SKILLS_DIR / ".usage.json"
ARCHIVE_DIR = SKILLS_DIR / ".archive"
BUNDLED_MANIFEST = SKILLS_DIR / ".bundled_manifest"
HUB_LOCK_DIR = SKILLS_DIR / ".hub"
HUB_LOCK_FILE = HUB_LOCK_DIR / "lock.json"

# Gateway log for communication health monitoring
GATEWAY_LOG = Path("/vol1/.hermes/logs/gateway.log")

# Skills that must never be flagged for archive by self-evolution governance
# These are core governance/infrastructure skills managed by the user
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

SIGNALS_FILE = EVOLUTION_DIR / "signals.jsonl"
AGENDA_FILE = EVOLUTION_DIR / "self_agenda.yaml"
PROPOSAL_FILE = EVOLUTION_DIR / "proposal_queue.yaml"
JOURNAL_FILE = EVOLUTION_DIR / "evolution_journal.md"
HERMES_CONFIG = Path("/vol1/.hermes/config.yaml")
HERMES_ENV = Path("/vol1/.hermes/.env")
CRON_JOBS_FILE = Path("/vol1/.hermes/cron/jobs.json")
SANNAI_CRON_JOBS_FILE = Path("/root/.hermes/profiles/sannai/cron/jobs.json")

# ── V1.5: Signal delta-state tracking (Stage 2 denoising) ──
# Cache files track last-known state per signal source.
# Collectors emit signals ONLY on state transitions (delta).
LIFECYCLE_CACHE = EVOLUTION_DIR / ".lifecycle_delta_cache.json"
HEALTH_CACHE = EVOLUTION_DIR / ".skill_health_delta_cache.json"
ABSENT_CACHE = EVOLUTION_DIR / ".source_absent_delta_cache.json"
CRON_SIGNAL_CACHE = EVOLUTION_DIR / ".cron_signal_delta_cache.json"
CRON_NO_AGENT_CACHE = EVOLUTION_DIR / ".cron_no_agent_delta_cache.json"
MCP_HEALTH_CACHE = EVOLUTION_DIR / ".mcp_health_delta_cache.json"
GATEWAY_HEALTH_CACHE = EVOLUTION_DIR / ".gateway_health_delta_cache.json"

DRY_RUN = "--dry-run" in sys.argv or os.environ.get("COLLECT_DRY_RUN") == "1"


def _load_cache(cache_file: Path) -> dict:
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except (json.JSONDecodeError, Exception):
            pass
    return {}


def _save_cache(cache_file: Path, state: dict):
    if DRY_RUN:
        return
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = cache_file.with_name(f"{cache_file.name}.tmp")
    tmp_file.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_file.replace(cache_file)


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _stable_hash(value) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


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
    elif sig_type == "cron_signal_summary":
        return (f"cron_signal_summary:{sig.get('summary_day','?')}:"
                f"{sig.get('summary_fingerprint','?')}")
    elif sig_type == "cron_failure":
        return (f"cron_failure:{sig.get('job_id','?')}:"
                f"{sig.get('mtime','')}:{sig.get('failure_kind','')}")
    elif sig_type == "cron_recovery":
        return (f"cron_recovery:{sig.get('job_id','?')}:"
                f"{sig.get('mtime','')}")
    elif sig_type == "cron_prompt_scan_block":
        return (f"cron_prompt_scan_block:{sig.get('profile','?')}:"
                f"{sig.get('job_id','?')}:{sig.get('mtime','')}:"
                f"{sig.get('source_path','')}")
    elif sig_type == "cron_no_agent_candidates":
        return (f"cron_no_agent_candidates:{sig.get('profile_count',0)}:"
                f"{sig.get('candidate_fingerprint','?')}:"
                f"{sig.get('report_day','?')}")
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
        return (f"session:{sig.get('ts','')}:{sig.get('total_sessions',0)}:"
                f"{sig.get('active_sessions_24h',0)}")
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
            json.dumps([
                (m["skill"], m["mention_count"]) for m in sig.get("top_mentions", [])
            ] + [
                (k["keyword"], k["count"]) for k in sig.get("top_keywords", [])
            ] + [
                (
                    "quality_concern",
                    sig.get("quality_concern_count", 0),
                    sig.get("quality_concern_fingerprint", ""),
                )
            ], sort_keys=True)))
    elif sig_type == "gateway_health":
        return "gateway_health:" + str(hash(
            json.dumps(sig.get("hourly_alerts", []), sort_keys=True)))
    elif sig_type == "mcp_health":
        return (f"mcp_health:{sig.get('server_name','?')}:"
                f"{sig.get('connect_ok','')}:{sig.get('tool_count','')}:"
                f"{sig.get('latency_bucket','')}:{sig.get('error_class','')}")
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


_CRON_PROMPT_SCAN_BLOCK_RE = re.compile(
    r"CronPromptInjectionBlocked|prompt[- ]?injection|threat pattern|prompt scanner",
    re.IGNORECASE,
)


def _classify_cron_output(content: str) -> tuple[bool, str, bool]:
    """Return (has_error, failure_kind, prompt_scan_blocked) without exposing body text."""
    lines = content.split("\n")
    prompt_scan_blocked = bool(_CRON_PROMPT_SCAN_BLOCK_RE.search(content))
    if prompt_scan_blocked:
        return True, "prompt_scan_blocked", True

    exit_error_re = re.compile(
        r"\b(?:exit\s+code\s*:\s*[1-9]\d*|exit\s+status\s*:\s*[1-9]\d*)",
        re.IGNORECASE,
    )
    if exit_error_re.search(content):
        return True, "nonzero_exit", False

    traceback_re = re.compile(
        r"Traceback\s*\(most recent call last\)|Traceback:\s*/",
        re.IGNORECASE,
    )
    tb_match = traceback_re.search(content)
    if tb_match:
        tb_start = content.rfind("\n", 0, tb_match.start()) + 1
        tb_end = content.find("\n", tb_match.end())
        tb_line = content[tb_start:tb_end] if tb_end >= 0 else content[tb_start:]
        stripped = tb_line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            if not ("`" in tb_line and re.search(r"`.*Traceback.*`", tb_line, re.I)):
                if not re.search(
                    r"\b(?:use|example|like|such as|documentation|note|usage)\b.*Traceback",
                    tb_line,
                    re.I,
                ):
                    return True, "traceback", False

    error_marker_re = re.compile(r"\b(?:ERROR:|FATAL:|FAILED:|Error:|Failed:)", re.I)
    clean_marker_re = re.compile(
        r"\b(PASS|passed|success|successfully|null|none|exit\s+code\s*:\s*0)\b",
        re.IGNORECASE,
    )
    for line in lines:
        if not error_marker_re.search(line):
            continue
        stripped = line.strip()
        if clean_marker_re.search(line):
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            continue
        if re.search(r"\b(risk_level|priority|severity)\b", line, re.I):
            continue
        if re.search(r"\berror:\s*(null|none|false|0|无)\b", line, re.I):
            continue
        if re.search(r"\bfailed:\s*(false|null|none|0)\b", line, re.I):
            continue
        if re.search(
            r"\b(lesson|behavioral|instruction|concept|definition|explanation|suggestion|proposal)\b",
            line,
            re.I,
        ):
            if not re.search(r"\b(occurred|thrown|raised|detected|caused)\b", line, re.I):
                continue
        if "`" in stripped and re.search(r"`(ERROR|FAILED|CRITICAL)`", stripped, re.I):
            continue
        return True, "error_marker", False

    return False, "ok", False


def collect_cron_signals(days: int = 1) -> list[dict]:
    signals = []
    cutoff = time.time() - days * 86400

    if not CRON_OUTPUT_DIR.exists():
        return signals

    summary = {
        "summary_day": today_str(),
        "period_days": days,
        "jobs_scanned": 0,
        "runs_scanned": 0,
        "ok_runs": 0,
        "failure_runs": 0,
        "prompt_scan_blocks": 0,
        "failed_jobs": [],
    }
    failed_jobs: set[str] = set()
    job_states = {}

    for job_dir in sorted(CRON_OUTPUT_DIR.iterdir()):
        if not job_dir.is_dir():
            continue
        job_seen = False
        job_run_count = 0
        job_failure_count = 0
        last_mtime = ""
        for f in sorted(job_dir.glob("*.md")):
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue

            if not job_seen:
                summary["jobs_scanned"] += 1
                job_seen = True

            mtime = datetime.fromtimestamp(st.st_mtime, tz=TZ).isoformat()
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            has_error, failure_kind, prompt_scan_blocked = _classify_cron_output(content)
            summary["runs_scanned"] += 1
            job_run_count += 1
            last_mtime = mtime
            if has_error:
                summary["failure_runs"] += 1
                job_failure_count += 1
                failed_jobs.add(job_dir.name)
                signals.append({
                    "ts": now_iso(),
                    "type": "cron_failure",
                    "source": "cron-output",
                    "job_id": job_dir.name,
                    "source_path": str(f),
                    "mtime": mtime,
                    "failure_kind": failure_kind,
                })
            else:
                summary["ok_runs"] += 1

            if prompt_scan_blocked:
                summary["prompt_scan_blocks"] += 1
                signals.append({
                    "ts": now_iso(),
                    "type": "cron_prompt_scan_block",
                    "source": "cron-output",
                    "profile": "main",
                    "job_id": job_dir.name,
                    "source_path": str(f),
                    "mtime": mtime,
                    "reason": "cron prompt scanner blocked assembled prompt",
                })

        if job_run_count:
            job_states[job_dir.name] = {
                "last_status": "failed" if job_failure_count else "ok",
                "failure_count": job_failure_count,
                "last_mtime": last_mtime,
            }

    summary["failed_jobs"] = sorted(failed_jobs)
    summary_fingerprint = _stable_hash(summary)
    cache = _load_cache(CRON_SIGNAL_CACHE)
    prev_job_states = cache.get("job_states", {}) if isinstance(cache, dict) else {}
    for job_id, state in sorted(job_states.items()):
        prev_state = prev_job_states.get(job_id, {})
        if prev_state.get("last_status") == "failed" and state.get("last_status") == "ok":
            signals.append({
                "ts": now_iso(),
                "type": "cron_recovery",
                "source": "cron-output",
                "job_id": job_id,
                "mtime": state.get("last_mtime", ""),
                "previous_status": "failed",
                "current_status": "ok",
            })

    should_emit_summary = (
        cache.get("last_summary_day") != summary["summary_day"]
        or cache.get("last_summary_fingerprint") != summary_fingerprint
        or summary["failure_runs"] > 0
    )
    if should_emit_summary:
        _save_cache(CRON_SIGNAL_CACHE, {
            "last_summary_day": summary["summary_day"],
            "last_summary_fingerprint": summary_fingerprint,
            "job_states": job_states,
        })
        signals.append({
            "ts": now_iso(),
            "type": "cron_signal_summary",
            "source": "cron-output",
            "signal_weight": 0.65,
            "summary_fingerprint": summary_fingerprint,
            **summary,
        })
    elif cache.get("job_states") != job_states:
        _save_cache(CRON_SIGNAL_CACHE, {
            "last_summary_day": cache.get("last_summary_day"),
            "last_summary_fingerprint": cache.get("last_summary_fingerprint"),
            "job_states": job_states,
        })

    return signals


def _load_cron_jobs(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    jobs = data.get("jobs", data) if isinstance(data, dict) else data
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    return [j for j in jobs if isinstance(j, dict)]


def _active_cron_job(job: dict) -> bool:
    if job.get("enabled") is False:
        return False
    if job.get("paused") is True:
        return False
    status = str(job.get("status") or job.get("state") or "").lower()
    return status not in {"disabled", "paused", "complete", "completed"}


def _script_for_job(job: dict) -> str:
    return str(job.get("script") or job.get("script_path") or job.get("command") or "")


def collect_cron_policy_signals(days: int = 1) -> list[dict]:
    """Emit low-noise cron policy visibility: no_agent candidates and prompt-scan blocks."""
    del days
    signals = []
    candidates = []
    job_files = [
        ("main", CRON_JOBS_FILE),
        ("sannai", SANNAI_CRON_JOBS_FILE),
    ]

    for profile, path in job_files:
        for job in _load_cron_jobs(path):
            job_id = str(job.get("id") or job.get("job_id") or job.get("name") or "?")
            job_name = str(job.get("name") or job_id)
            last_error = str(job.get("last_error") or job.get("delivery_error") or "")
            if last_error and _CRON_PROMPT_SCAN_BLOCK_RE.search(last_error):
                signals.append({
                    "ts": now_iso(),
                    "type": "cron_prompt_scan_block",
                    "source": "cron-jobs",
                    "profile": profile,
                    "job_id": job_id,
                    "job_name": job_name,
                    "mtime": str(job.get("last_run_at") or job.get("updated_at") or ""),
                    "source_path": str(path),
                    "reason": "cron job metadata reports prompt scanner block",
                })

            script = _script_for_job(job)
            if not script or job.get("no_agent") is True or not _active_cron_job(job):
                continue
            skills = job.get("skills") or ([job["skill"]] if job.get("skill") else [])
            if not isinstance(skills, list):
                skills = [str(skills)]
            toolsets = job.get("enabled_toolsets") or job.get("toolsets") or []
            if not isinstance(toolsets, list):
                toolsets = [str(toolsets)]
            delivery = str(job.get("deliver") or job.get("delivery") or job.get("target") or "")

            reasons = ["script_backed_without_no_agent"]
            confidence = "high"
            if skills:
                reasons.append("uses_skills")
                confidence = "review_required"
            if delivery in {"origin", "owner", "platform"}:
                reasons.append(f"delivery_{delivery}")
                confidence = "review_required"

            candidates.append({
                "profile": profile,
                "job_id": job_id,
                "job_name": job_name,
                "script": script,
                "confidence": confidence,
                "reasons": reasons,
                "skills": sorted(str(s) for s in skills),
                "enabled_toolsets": sorted(str(t) for t in toolsets),
                "delivery": delivery,
                "recommended_action": "review_before_setting_no_agent",
            })

    if candidates:
        candidates = sorted(candidates, key=lambda c: (c["profile"], c["job_id"]))
        fingerprint = _stable_hash(candidates)
        report_day = today_str()
        cache = _load_cache(CRON_NO_AGENT_CACHE)
        if (
            cache.get("candidate_fingerprint") != fingerprint
            or cache.get("last_report_day") != report_day
        ):
            _save_cache(CRON_NO_AGENT_CACHE, {
                "candidate_fingerprint": fingerprint,
                "last_report_day": report_day,
            })
            signals.append({
                "ts": now_iso(),
                "type": "cron_no_agent_candidates",
                "source": "cron-jobs",
                "signal_weight": 0.70,
                "report_day": report_day,
                "candidate_fingerprint": fingerprint,
                "candidate_count": len(candidates),
                "profile_count": len({c["profile"] for c in candidates}),
                "candidates": candidates,
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
    hermes_dir = Path("/vol1/.hermes/hermes-agent")
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
    """Read sessions.json for real session metadata.

    Outputs platform distribution, active session counts, and most recent
    session info. Replaced evolution_journal proxy with real session index.
    Privacy: only aggregate counts, no message content.
    """
    signals = []
    ts = now_iso()

    if not SESSIONS_INDEX.exists():
        signals.append({
            "ts": ts,
            "type": "source_absent",
            "source": "sessions",
            "source_path": str(SESSIONS_INDEX),
            "reason": "sessions_json_not_found",
        })
        return signals

    try:
        data = json.loads(SESSIONS_INDEX.read_text())
    except (json.JSONDecodeError, Exception):
        return signals

    total = len(data)
    now_dt = datetime.now(TZ)

    platform_counts: dict[str, int] = {}
    chat_type_counts: dict[str, int] = {}
    active_24h = 0
    active_7d = 0
    most_recent = None

    for sess in data.values():
        platform = sess.get("platform", "unknown")
        chat_type = sess.get("chat_type", "unknown")
        platform_counts[platform] = platform_counts.get(platform, 0) + 1
        chat_type_counts[chat_type] = chat_type_counts.get(chat_type, 0) + 1

        updated_str = sess.get("updated_at")
        if updated_str:
            try:
                updated_dt = datetime.fromisoformat(updated_str)
                # Make timezone-aware: assume stored times are local TZ
                if updated_dt.tzinfo is None:
                    updated_dt = updated_dt.replace(tzinfo=TZ)
                delta = now_dt - updated_dt
                if delta.total_seconds() < 86400:
                    active_24h += 1
                if delta.total_seconds() < 604800:
                    active_7d += 1

                if most_recent is None or updated_dt > most_recent["_dt"]:
                    most_recent = {
                        "_dt": updated_dt,
                        "platform": platform,
                        "chat_type": chat_type,
                        "display_name": sess.get("display_name", "?"),
                        "updated_at": updated_str,
                    }
            except (ValueError, TypeError):
                pass

    signal = {
        "ts": ts,
        "type": "session_metadata",
        "source": "sessions-json",
        "total_sessions": total,
        "active_sessions_24h": active_24h,
        "active_sessions_7d": active_7d,
        "platforms": [
            {"platform": p, "count": c}
            for p, c in sorted(platform_counts.items(), key=lambda x: -x[1])
        ],
        "chat_types": [
            {"type": t, "count": c}
            for t, c in sorted(chat_type_counts.items(), key=lambda x: -x[1])
        ],
        "signal_weight": 0.75,
    }
    if most_recent:
        signal["most_recent_session"] = {
            "platform": most_recent["platform"],
            "chat_type": most_recent["chat_type"],
            "display_name": most_recent["display_name"],
            "updated_at": most_recent["updated_at"],
        }

    signals.append(signal)
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
    Data source: /vol1/.hermes/logs/curator/<timestamp>/run.json
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
    Data source: /vol1/.hermes/skills/.usage.json
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
            "recommended_action": "Run: chattr -R +i /vol1/.hermes/skills/<category>/<skill_name>",
        })

    return signals


# ── O2-lite: Real session mention scanner ────────────────────────────

# Common English stopwords for topic filtering
_EN_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
    "my", "your", "his", "its", "our", "their", "this", "that", "these",
    "those", "in", "on", "at", "to", "for", "of", "and", "or", "not",
    "no", "but", "if", "so", "as", "with", "about", "from", "by", "do",
    "did", "does", "has", "have", "had", "will", "would", "can", "could",
    "should", "may", "might", "just", "like", "also", "very", "really",
    "too", "now", "then", "up", "out", "off", "over", "all", "any",
    "each", "every", "some", "more", "most", "other", "such", "only",
    "own", "same", "what", "which", "who", "where", "when", "why", "how",
    "here", "there", "after", "before", "into", "than", "get", "got",
    "make", "made", "need", "use", "used", "let", "look", "know", "see",
    "say", "come", "take", "one", "two", "first", "last", "please",
    "ok", "okay", "yes", "yeah", "sure", "right", "well", "way",
})


# Common Chinese noise tokens (particles, common fragments)
_CN_NOISE = frozenset({
    "一个", "什么", "时候", "你说", "可以", "没有", "就是", "不是",
    "这个", "那个", "知道", "看到", "觉得", "告诉", "因为", "所以",
    "然后", "虽然", "如果", "但是", "而且", "或者", "还是", "已经",
    "一直", "之后", "之前", "现在", "今天", "昨天", "明天", "哈哈",
    "嗯嗯", "好的", "是的", "是吧", "的话", "也是", "还有", "有点",
    "一些", "哪些", "怎么", "这样", "那样", "这里", "那里", "一边",
    "一起", "做什", "你说", "我说", "他说", "她说", "他们说",
    "过来", "上去", "下来", "进去", "出来", "回去", "看去",
    "看到", "听到", "想到", "进来", "用来",
    # Common noise fragments from 2-char sliding window
    "了一", "的时", "的窗", "你的", "我的", "他的", "她的", "它的", "们的",
    "一下", "可以", "没有", "就是", "一个", "什么", "这个", "那个", "不是",
    "这么", "那么", "之后", "之前", "哈哈", "嗯嗯", "是的", "好吧",
    "吧", "吗", "哦", "嗯", "哈", "嘿",
})


QUALITY_TOPIC_KEYWORDS = ("\u8bb0\u5fc6",)
QUALITY_CONCERN_KEYWORDS = (
    "\u6df7\u4e71",
    "\u4e71",
    "\u641e\u9519",
    "\u8bb0\u4e0d\u4f4f",
    "\u8bb0\u9519",
    "\u4e0d\u51c6",
    "\u53c8\u5fd8",
    "\u5fd8\u4e86",
)


def _quality_concern_matches(text: str) -> list[tuple[str, str]]:
    """Return safe keyword pairs for user memory-quality complaints.

    Privacy rule: callers store only the matched keyword pair plus session
    reference metadata, never the original user message body.
    """
    matches = []
    for topic in QUALITY_TOPIC_KEYWORDS:
        if topic not in text:
            continue
        for concern in QUALITY_CONCERN_KEYWORDS:
            if concern in text:
                matches.append((topic, concern))
                break
    return matches


def _tokenize_message(text: str) -> list[str]:
    """Split a user message into meaningful tokens for keyword counting.

    Handles English words (whitespace-separated) and Chinese text via
    2-4 char substring extraction. Filters stopwords and short tokens.
    Returns lowercase tokens only. Privacy: no original text retained.
    """
    tokens = []

    # Chinese text: extract 2-char substrings only (most Chinese words are 2 chars)
    chinese_chunk = ""
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            chinese_chunk += ch
        else:
            if len(chinese_chunk) >= 2:
                for start in range(len(chinese_chunk) - 1):
                    token = chinese_chunk[start:start+2]
                    # Filter: noise list + tokens starting with 的/了 (always noise)
                    if token not in _CN_NOISE and token[0] not in ("的", "了"):
                        tokens.append(token)
            chinese_chunk = ""
    if len(chinese_chunk) >= 2:
        for start in range(len(chinese_chunk) - 1):
            token = chinese_chunk[start:start+2]
            if token not in _CN_NOISE and token[0] not in ("的", "了"):
                tokens.append(token)

    # English words: whitespace split + clean
    for word in text.split():
        cleaned = word.strip(".,!?;:()[]{}'\"/\\<>@#$%^&*+=|~`").lower()
        if len(cleaned) >= 3 and cleaned not in _EN_STOPWORDS and cleaned.isalpha():
            tokens.append(cleaned)

    return tokens


def collect_recent_session_mentions(days: int = 3) -> list[dict]:
    """Scan real session JSONL files for skill mentions and topic keywords.

    Reads user messages from ~/.hermes/sessions/*.jsonl (last N days).
    Counts skill name frequency and keyword frequency from user messages.
    Privacy: only aggregate stats in signals.jsonl, never original text.
    Replaced evolution_journal.md scan with real session data.
    """
    signals = []
    ts = now_iso()

    if not SESSION_DIR.exists():
        return signals

    # Build skill name set (lowercase for case-insensitive matching)
    skill_names: set[str] = set()
    if SKILLS_DIR.exists():
        for cat_dir in SKILLS_DIR.iterdir():
            if not cat_dir.is_dir() or cat_dir.name.startswith("."):
                continue
            for skill_dir in cat_dir.iterdir():
                skill_names.add(skill_dir.name.lower())

    # Collect user messages from recent JSONL files
    now_dt = datetime.now(TZ)
    cutoff = now_dt - timedelta(days=days)
    quality_scan_days = max(days, int(os.environ.get("QUALITY_CONCERN_SCAN_DAYS", "7")))
    quality_cutoff = now_dt - timedelta(days=quality_scan_days)

    skill_mention_counts: dict[str, int] = {}
    keyword_counts: dict[str, int] = {}
    quality_concern_pairs: dict[tuple[str, str], int] = {}
    quality_concern_refs: list[dict] = []
    total_user_msgs = 0
    files_scanned = 0

    jsonl_files = sorted(SESSION_DIR.glob("*.jsonl"), reverse=True)
    for fpath in jsonl_files:
        try:
            mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=TZ)
            if mtime < quality_cutoff:
                break
        except OSError:
            continue

        files_scanned += 1
        in_recent_window = mtime >= cutoff
        try:
            raw_text = fpath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for line_no, line in enumerate(raw_text.strip().split("\n"), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("role") != "user":
                continue

            content = entry.get("content", "")
            if isinstance(content, str):
                msg_text = content
            elif isinstance(content, list):
                texts = [p.get("text", "") for p in content if isinstance(p, dict)]
                msg_text = " ".join(texts)
            else:
                continue

            if not msg_text:
                continue

            msg_lower = msg_text.lower()

            if in_recent_window:
                total_user_msgs += 1

                # Count skill name mentions
                for skill in skill_names:
                    if skill in msg_lower:
                        skill_mention_counts[skill] = skill_mention_counts.get(skill, 0) + 1

                # Count topic keywords (tokenized, never stores raw text)
                for token in _tokenize_message(msg_text):
                    if token not in skill_names:
                        keyword_counts[token] = keyword_counts.get(token, 0) + 1

            # Count explicit user quality complaints without storing message text.
            for topic, concern in _quality_concern_matches(msg_text):
                pair = (topic, concern)
                quality_concern_pairs[pair] = quality_concern_pairs.get(pair, 0) + 1
                if len(quality_concern_refs) < 20:
                    quality_concern_refs.append({
                        "session_ref": f"sessions/{fpath.name}:{line_no}",
                        "session_id": fpath.stem,
                        "line_number": line_no,
                        "topic_keyword": topic,
                        "concern_keyword": concern,
                    })

    # Top 20 mentioned skills
    top_skills = sorted(skill_mention_counts.items(), key=lambda x: -x[1])[:20]

    # Top 15 topic keywords
    top_keywords = sorted(keyword_counts.items(), key=lambda x: -x[1])[:15]
    quality_concern_keywords = [
        {
            "topic_keyword": topic,
            "concern_keyword": concern,
            "count": count,
        }
        for (topic, concern), count in sorted(
            quality_concern_pairs.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
    ]
    quality_concern_fingerprint = _stable_hash(quality_concern_keywords)
    quality_concern_count = sum(quality_concern_pairs.values())

    if total_user_msgs > 0 or quality_concern_count > 0:
        signal = {
            "ts": ts,
            "type": "recent_session_mention",
            "source": "sessions-jsonl",
            "summary": (
                f"recent session mentions: user_messages={total_user_msgs}, "
                f"quality_concern_count={quality_concern_count}"
            ),
            "total_user_messages": total_user_msgs,
            "files_scanned": files_scanned,
            "scan_days": days,
            "quality_concern_scan_days": quality_scan_days,
            "total_unique_skills_mentioned": len(skill_mention_counts),
            "total_unique_keywords": len(keyword_counts),
            "signal_weight": 0.70,
            "quality_concern": quality_concern_count > 0,
            "actionable_qualified": quality_concern_count > 0,
            "quality_concern_count": quality_concern_count,
            "quality_concern_keywords": quality_concern_keywords,
            "quality_concern_refs": quality_concern_refs,
            "quality_concern_fingerprint": quality_concern_fingerprint,
            "top_mentions": [
                {"skill": name, "mention_count": count}
                for name, count in top_skills
            ],
            "top_keywords": [
                {"keyword": kw, "count": c}
                for kw, c in top_keywords
            ],
        }
        signals.append(signal)

    return signals


# ── Gateway Health ────────────────────────────────────────────────

# ── MCP Health ────────────────────────────────────────────────────────

def _run_command(args: list[str], timeout: int = 10) -> dict:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "timeout",
            "error_class": "timeout",
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "command_not_found",
            "error_class": "command_not_found",
        }
    except Exception:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "command_failed",
            "error_class": "command_failed",
        }

    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
        "error_class": "" if result.returncode == 0 else "command_failed",
    }


def _parse_mcp_servers(stdout: str) -> list[dict]:
    servers = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("MCP Servers:", "Name", "─")):
            continue
        if "enabled" not in stripped.lower() and "disabled" not in stripped.lower():
            continue
        parts = re.split(r"\s{2,}", stripped)
        if len(parts) < 4:
            continue
        name, transport, tool_scope, status = parts[:4]
        if not re.match(r"^[A-Za-z0-9_.-]+$", name):
            continue
        servers.append({
            "server_name": name,
            "transport": transport,
            "tool_scope": tool_scope,
            "enabled": "enabled" in status.lower(),
        })
    return servers


def _mcp_latency_bucket(latency_ms: int | None) -> str:
    if latency_ms is None:
        return "unavailable"
    if latency_ms < 1000:
        return "lt_1s"
    if latency_ms < 3000:
        return "1s_to_3s"
    if latency_ms < 8000:
        return "3s_to_8s"
    return "ge_8s"


def _mcp_error_class(result: dict, stdout: str) -> str:
    if result.get("error_class"):
        return str(result["error_class"])
    text = f"{stdout}\n{result.get('stderr','')}".lower()
    if "connection refused" in text:
        return "connection_refused"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "oauth" in text or "auth" in text:
        return "auth_error"
    if "tools discovered" not in text and result.get("ok"):
        return "tool_discovery_missing"
    return "command_failed"


def collect_mcp_health_signals() -> list[dict]:
    """Probe enabled MCP servers through Hermes CLI and emit only state changes."""
    signals = []
    list_result = _run_command(["hermes", "mcp", "list"], timeout=8)
    if not list_result["ok"]:
        state = {
            "__list__": {
                "connect_ok": False,
                "error_class": list_result.get("error_class") or "command_failed",
            }
        }
        cache = _load_cache(MCP_HEALTH_CACHE)
        if cache.get("servers") != state:
            _save_cache(MCP_HEALTH_CACHE, {"servers": state})
            signals.append({
                "ts": now_iso(),
                "type": "mcp_health",
                "source": "mcp",
                "server_name": "__list__",
                "enabled": False,
                "connect_ok": False,
                "latency_ms": None,
                "latency_bucket": "unavailable",
                "tool_count": None,
                "error_class": state["__list__"]["error_class"],
            })
        return signals

    servers = _parse_mcp_servers(list_result["stdout"])
    prev_cache = _load_cache(MCP_HEALTH_CACHE)
    prev_servers = prev_cache.get("servers", {}) if isinstance(prev_cache, dict) else {}
    curr_servers = {}

    for server in servers:
        name = server["server_name"]
        if not server["enabled"]:
            curr_servers[name] = {
                "enabled": False,
                "connect_ok": False,
                "latency_bucket": "disabled",
                "tool_count": None,
                "error_class": "disabled",
            }
            continue

        result = _run_command(["hermes", "mcp", "test", name], timeout=12)
        stdout = result["stdout"]
        latency_match = re.search(r"Connected\s*\((\d+)ms\)", stdout)
        tools_match = re.search(r"Tools discovered:\s*(\d+)", stdout)
        latency_ms = int(latency_match.group(1)) if latency_match else None
        tool_count = int(tools_match.group(1)) if tools_match else None
        connect_ok = bool(result["ok"] and latency_match and tools_match)
        error_class = "" if connect_ok else _mcp_error_class(result, stdout)
        latency_bucket = _mcp_latency_bucket(latency_ms)

        curr_servers[name] = {
            "enabled": True,
            "connect_ok": connect_ok,
            "latency_bucket": latency_bucket,
            "tool_count": tool_count,
            "error_class": error_class,
        }

        if prev_servers.get(name) != curr_servers[name]:
            signals.append({
                "ts": now_iso(),
                "type": "mcp_health",
                "source": "mcp",
                "server_name": name,
                "transport": server["transport"],
                "enabled": True,
                "connect_ok": connect_ok,
                "latency_ms": latency_ms,
                "latency_bucket": latency_bucket,
                "tool_count": tool_count,
                "error_class": error_class,
            })

    if prev_servers != curr_servers:
        _save_cache(MCP_HEALTH_CACHE, {"servers": curr_servers})

    return signals


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
    "auto_resume": 0,
    "session_restored": 0,
    "gateway_recovery": 0,
    "wedged_updater": 0,
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
    "auto_resume": re.compile(r"\bauto[- ]?resume\b|resuming session", re.IGNORECASE),
    "session_restored": re.compile(r"restored session|session restored", re.IGNORECASE),
    "gateway_recovery": re.compile(r"\brecovery\b|recovering session", re.IGNORECASE),
    "wedged_updater": re.compile(
        r"wedged updater|Updater not running after reconnect heartbeat|Polling heartbeat probe failed",
        re.IGNORECASE,
    ),
}

_GATEWAY_WINDOWS = {
    "network_error": "hour",
    "reconnect": "hour",
    "send_failed": "hour",
    "send_timeout": "hour",
    "fallback_ip": "day",
    "polling_conflict": "day",
    "auto_resume": "hour",
    "session_restored": "hour",
    "gateway_recovery": "hour",
    "wedged_updater": "hour",
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

    alert_fingerprint = _stable_hash(hourly_alerts)
    cache = _load_cache(GATEWAY_HEALTH_CACHE)
    if not hourly_alerts:
        if cache.get("alert_fingerprint"):
            _save_cache(GATEWAY_HEALTH_CACHE, {"alert_fingerprint": ""})
        return signals
    if cache.get("alert_fingerprint") == alert_fingerprint:
        return signals

    _save_cache(GATEWAY_HEALTH_CACHE, {"alert_fingerprint": alert_fingerprint})

    # ── Emit one signal per day with all alerts ──
    if hourly_alerts:
        signals.append({
            "ts": ts,
            "type": "gateway_health",
            "source": "gateway",
            "alert_fingerprint": alert_fingerprint,
            "total_hours_scanned": len(hourly_events),
            "total_daily_events": dict(sorted(daily_events.items())),
            "alert_count": len(hourly_alerts),
            "hourly_alerts": hourly_alerts,
        })

    return signals


# ── Main ─────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect privacy-safe self-evolution signals."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidate signals without appending signals or updating caches.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    global DRY_RUN
    args = parse_args(argv)
    if args.dry_run:
        DRY_RUN = True

    days = int(os.environ.get("COLLECT_DAYS", "1"))
    all_signals = []

    collectors = [
        ("ops-gate", collect_ops_gate_signals),
        ("cron", collect_cron_signals),
        ("cron-policy", collect_cron_policy_signals),
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
        ("mcp-health", collect_mcp_health_signals),
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
        if not DRY_RUN:
            with open(SIGNALS_FILE, "a") as f:
                f.write(line + "\n")
        written_count += 1

    # Output machine-readable summary as JSON
    output = {
        "ts": now_iso(),
        "dry_run": DRY_RUN,
        "total_signals": len(all_signals),
        "written_count": written_count,
        "skipped_duplicates": skipped_count,
        "summary": summary,
        "signals": all_signals,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
