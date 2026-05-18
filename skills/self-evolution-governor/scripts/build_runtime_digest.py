#!/usr/bin/env python3
"""
Self-Evolution Governor - Build Runtime Digest + HERMES_FOCUS.

Reads signals, agenda, and proposal queue to produce:

1. runtime_digest.md  — Short (<2KB) context digest for Hermes session injection
2. HERMES_FOCUS.md    — Current operating focus (updated only if changed)

Usage:
  python3 build_runtime_digest.py          # Full update
  python3 build_runtime_digest.py --dry-run  # Preview only, don't write files
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

TZ = timezone(timedelta(hours=8))

EVOLUTION_DIR = Path("/vol1/.hermes/state/evolution")
SIGNALS_FILE = EVOLUTION_DIR / "signals.jsonl"
AGENDA_FILE = EVOLUTION_DIR / "self_agenda.yaml"
PROPOSAL_FILE = EVOLUTION_DIR / "proposal_queue.yaml"
JOURNAL_FILE = EVOLUTION_DIR / "evolution_journal.md"
FOCUS_FILE = EVOLUTION_DIR / "HERMES_FOCUS.md"
DIGEST_FILE = EVOLUTION_DIR / "runtime_digest.md"


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def load_proposals() -> list[dict]:
    """Load proposals from proposal_queue.yaml."""
    if not PROPOSAL_FILE.exists():
        return []
    content = PROPOSAL_FILE.read_text()
    if not content.strip() or content.strip() == "{}":
        return []
    try:
        data = yaml.safe_load(content) or {}
        if isinstance(data, dict):
            proposals = data.get("proposals") or []
        elif isinstance(data, list):
            proposals = data
        else:
            proposals = []
        return [item for item in proposals if isinstance(item, dict)]
    except Exception:
        return []


def get_recent_signals(hours: int = 24) -> list[dict]:
    """Get signals from the last N hours."""
    cutoff = datetime.now(TZ) - timedelta(hours=hours)
    signals = []
    if not SIGNALS_FILE.exists():
        return signals
    for line in SIGNALS_FILE.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            sig = json.loads(line.strip())
            signals.append(sig)
        except json.JSONDecodeError:
            pass
    return signals


def get_focus_proposals(proposals: list[dict]) -> tuple:
    """Extract pending/approved proposals for digest."""
    pending = []
    approved_not_done = []
    
    for p in proposals:
        status = p.get("status", "").lower()
        if status in ("pending_user_approval", "pending"):
            pending.append(p)
        elif status == "approved":
            approved_not_done.append(p)
    
    # Sort by priority_score descending if available
    pending.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
    approved_not_done.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
    
    return pending[:3], approved_not_done[:3]


def get_recent_errors(signals: list[dict], hours: int = 24) -> list[dict]:
    """Get error/warning signals from the last N hours."""
    cutoff = datetime.now(TZ) - timedelta(hours=hours)
    errors = []
    seen = set()
    
    for sig in signals:
        ts_str = sig.get("ts", "")
        try:
            sig_ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now(TZ)
        except ValueError:
            sig_ts = datetime.now(TZ)
        
        if sig_ts < cutoff:
            continue
        
        is_error = False
        if sig.get("type") == "cron_result" and sig.get("has_error"):
            is_error = True
            dedup_key = f"cron_{sig.get('job_id', '')}_{sig.get('mtime', '')}"
        elif sig.get("type") == "ops_gate_result" and not sig.get("pass"):
            is_error = True
            dedup_key = f"ops_{sig.get('task_id', '')}"
        else:
            continue
        
        if dedup_key not in seen:
            seen.add(dedup_key)
            errors.append(sig)
    
    return errors[:5]


def build_digest(
    proposals: list[dict],
    signals: list[dict],
    focus_content: str | None,
    pending: list[dict],
    approved: list[dict],
    errors: list[dict],
    dry_run: bool = False,
) -> str:
    """Build runtime_digest.md content."""
    now = datetime.now(TZ)
    
    lines = []
    lines.append("# Hermes Runtime Digest")
    lines.append("")
    lines.append(f"Last updated: {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Valid until: {(now + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    
    # Section 1: Current Focus — only emitted when there are actual focus items
    if focus_content:
        in_focus = False
        has_focus = False
        for line in focus_content.split("\n"):
            if line.startswith("## Current Operating Focus"):
                in_focus = True
                continue
            elif line.startswith("## ") and "Current" not in line:
                in_focus = False
            elif in_focus and line.strip().startswith(("1.", "2.", "3.")):
                if not has_focus:
                    lines.append("## Current Focus")
                    lines.append("")
                    has_focus = True
                lines.append(line.strip())
        
        if has_focus:
            lines.append("")
    
    # Section 2: Pending Proposals (high-value)
    if pending:
        lines.append("## Proposals Awaiting Your Decision")
        lines.append("")
        for p in pending:
            title = p.get("title", p.get("id", "Unknown"))
            score = p.get("priority_score", "?")
            risk = p.get("risk_level", "?")
            lines.append(f"- **{title}** (priority={score}, risk={risk})")
        lines.append("")
    
    # Section 3: Recent Issues
    if errors:
        lines.append("## Recent Issues (24h)")
        lines.append("")
        for e in errors:
            if e.get("type") == "cron_result":
                lines.append(f"- ⚠ cron `{e.get('job_id', '?')}` error at {e.get('mtime', '?')}")
            elif e.get("type") == "ops_gate_result":
                lines.append(f"- ⚠ task `{e.get('task_name', '?')}` failed (pass={e.get('pass', '?')})")
        lines.append("")
    
    # Section 4: Runtime Guidance
    lines.append("## Runtime Guidance")
    lines.append("")
    lines.append("- Self-evolution outputs are advisory unless approved by the user.")
    lines.append("- Check `HERMES_FOCUS.md` for strategic priorities.")
    lines.append("- Check `proposal_queue.yaml` before creating duplicate proposals.")
    lines.append("- Route executable changes through user approval and ops-gate.")
    lines.append("")
    
    digest = "\n".join(lines)
    
    if not dry_run:
        DIGEST_FILE.write_text(digest)
    
    return digest


def build_focus(
    proposals: list[dict],
    signals: list[dict],
    errors: list[dict],
    dry_run: bool = False,
) -> tuple[str, str]:
    """Build or update HERMES_FOCUS.md content from actual signal data.
    
    Focus items are derived entirely from signal data — errors, user corrections,
    project shifts, and gateway issues. No hardcoded defaults.
    If nothing is wrong, the focus section states that explicitly.
    Only writes if content has changed meaningfully.
    """
    now = datetime.now(TZ)
    valid_until = now + timedelta(days=7)
    
    lines = []
    lines.append("# HERMES_FOCUS.md")
    lines.append("")
    lines.append(f"Last updated: {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Generated by: self-evolution-governor")
    lines.append(f"Valid until: {valid_until.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("## Current Operating Focus")
    lines.append("")
    
    # Derive focus items from actual signal data — no hardcoded defaults
    focus_items = []
    
    # 1. Error-driven focus (unconditional — real problems)
    cron_errors = [e for e in errors if e.get("type") == "cron_result"]
    ops_errors = [e for e in errors if e.get("type") == "ops_gate_result"]
    llm_wiki_errors = [e for e in errors if "wiki" in str(e).lower()]
    
    if cron_errors:
        focus_items.append(
            ("Stabilize cron automation",
             f"{len(cron_errors)} cron failures in last 24h"))
    
    if ops_errors:
        focus_items.append(
            ("Harden ops-gate task execution",
             f"{len(ops_errors)} ops-gate task failures"))
    
    if llm_wiki_errors:
        focus_items.append(
            ("Stabilize LLM-Wiki automation",
             f"{len(llm_wiki_errors)} LLM-Wiki related issues"))
    
    # 2. Signal-driven focus (user corrections, gateway issues, project shifts)
    corrections = [s for s in signals if s.get("type") == "user_correction"]
    gateway_issues = [s for s in signals if s.get("type") in ("gateway_instability", "platform_offline")]
    project_shifts = [s for s in signals if s.get("type") == "project_importance_change"]
    
    if corrections:
        focus_items.append(
            ("User corrections detected",
             f"{len(corrections)} corrections in last 24h"))
    
    if gateway_issues:
        focus_items.append(
            ("Gateway stability",
             f"{len(gateway_issues)} gateway/connection issues"))
    
    if project_shifts and not focus_items:
        latest = max(project_shifts, key=lambda s: s.get("ts", ""))
        project_name = latest.get("summary", "Unknown")
        focus_items.append(
            ("Project focus shift",
             f"Activity shift: {project_name}"))
    
    if focus_items:
        for i, (title, reason) in enumerate(focus_items[:3], 1):
            lines.append(f"{i}. **{title}**")
            lines.append(f"   - Reason: {reason}")
            lines.append("")
    else:
        lines.append("_None — no errors, corrections, or project shifts detected._")
        lines.append("")
    
    lines.append("## Current Non-Goals")
    lines.append("")
    lines.append("- Do not create new high-risk automation without user approval.")
    lines.append("- Do not modify production config from self-evolution proposals.")
    lines.append("- Do not increase proactive-message volume unless quality remains high.")
    lines.append("")
    lines.append("## Suggested Behavior")
    lines.append("")
    lines.append("- Before proposing new automation, check if a similar proposal already exists.")
    lines.append("- Prefer improving existing automation over creating new unrelated skills.")
    lines.append("- Route executable changes through approval and ops-gate.")
    lines.append("")
    
    focus = "\n".join(lines)
    
    # Only write if content changed (check existing)
    if not dry_run:
        existing = ""
        if FOCUS_FILE.exists():
            existing = FOCUS_FILE.read_text()
        if existing.strip() != focus.strip():
            FOCUS_FILE.write_text(focus)
            return "UPDATED", focus
    
    return "UNCHANGED", focus


def main():
    parser = argparse.ArgumentParser(
        description="Build the self-evolution runtime digest and focus file."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview digest/focus output without writing files.",
    )
    args = parser.parse_args()
    dry_run = args.dry_run
    
    proposals = load_proposals()
    signals = get_recent_signals(hours=24)
    
    pending, approved = get_focus_proposals(proposals)
    errors = get_recent_errors(signals, hours=24)
    
    # Build/update focus first so runtime_digest reflects the same run's focus.
    focus_status, focus_content = build_focus(proposals, signals, errors, dry_run)

    # Build digest from the freshly computed focus content.
    digest = build_digest(proposals, signals, focus_content, pending, approved, errors, dry_run)
    
    result = {
        "ts": now_iso(),
        "dry_run": dry_run,
        "digest_written": not dry_run,
        "focus_status": focus_status,
        "pending_proposals": len(pending),
        "approved_pending_execution": len(approved),
        "recent_errors_24h": len(errors),
        "digest_size_bytes": len(digest),
        "digest": digest,
    }
    
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
