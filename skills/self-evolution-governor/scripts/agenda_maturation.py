#!/usr/bin/env python3
"""
Agenda Maturation Engine — V1.4.1c init-qualified semantic fix + CW-004 signal classification.

Changes from V1.4.1b:
- Split qualified evidence into structural_qualified and actionable_qualified.
- self_agenda_init source creates structural_qualified ONLY (keeps item alive,
  does NOT drive trend/recurrence/maturity/candidate_ready).
- trend_strength cap level depends on actionable_qualified count/strength:
  0 actionable → cap ≤ 0.10; 1 actionable or weak → cap ≤ 0.35.
- recurrence_density based on actionable evidence only.
- candidate_ready gate requires actionable_qualified_evidence_count >= 2.
- score_explanations show structural/actionable classification per evidence.
- CW-004 hard filter: structural and ops-review-only signal types cannot drive
  scoring through evidence_strength, trend, recurrence, or candidate_ready.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
STATE_DIR = Path("/vol1/.hermes/state/evolution")
AGENDA_FILE = STATE_DIR / "self_agenda.yaml"
SIGNALS_FILE = STATE_DIR / "signals.jsonl"
CANDIDATES_FILE = STATE_DIR / "agenda_candidates.yaml"
JOURNAL_FILE = STATE_DIR / "evolution_journal.md"
SCORE_EXPL_DIR = STATE_DIR / "score_explanations"

# ── Hardcoded type→action mapping ──
MATURITY_ACTION_MAP = {
    "strategic_positioning": "ask_user_confirmation",
    "automation_opportunity": "create_proposal",
    "risk_watch": "bypass_maturation_to_speak_gate",
    "quality_improvement": "create_proposal",
    "cleanup_candidate": "surface_in_digest",
}

# ── Weight config ──
WEIGHTS = {
    "evidence_strength": 0.30,
    "trend_strength": 0.25,
    "recurrence_density": 0.20,
    "unresolved_cost": 0.15,
    "actionability": 0.10,
}
TIME_PRESSURE_MAX = 0.12
TIME_PRESSURE_LOG_FACTOR = 0.03

# ── V1.4.1b: Type-specific evidence_strength lower bound for candidate_ready ──
MIN_EV_STRENGTH_BY_TYPE = {
    "strategic_positioning": 0.30,
    "automation_opportunity": 0.35,
    "quality_improvement": 0.30,
    "cleanup_candidate": 0.25,
}

# CW-018: Evidence strength should reflect accumulated actionable signal
# strength, not the average weight of low-weight telemetry. With the old
# average-weight formula, many repeated actionable entries at 0.05/0.10 could
# never pass the 0.25-0.30 type gate. A strength total of 1.5 represents enough
# repeated, relevant actionable evidence for a full evidence-strength signal.
ACTIONABLE_STRENGTH_FULL_EVIDENCE = 1.50

# ── V1.4.1c: Actionable-qualified caps for trend_strength ──
# 0 actionable qualified evidence → cap trend ≤ 0.10 (only init/structural)
# 1 actionable qualified evidence or weak actionable strength → cap ≤ 0.35
ZERO_ACTIONABLE_TREND_CEILING = 0.10
SINGLE_ACTIONABLE_TREND_CEILING = 0.35
ACTIONABLE_STRENGTH_SINGLE_CAP_THRESHOLD = 0.10

# ── V1.4.1c: Recurrence ceiling when no actionable evidence ──
ZERO_QUALIFIED_RECUR_CEILING = 0.10

# ── V1.4.1b: Maturity ceiling when zero qualified at all ──
ZERO_QUALIFIED_MATURITY_CEILING = 0.50

# ── CW-004: Signal classification registry ──
# This table is the implementation source of truth. Structural evidence can
# keep an agenda item explainable, but must not drive maturity scoring.
# Ops-review-only signals are surfaced to operators without becoming scoring
# evidence.
SIGNAL_CLASSIFICATION = {
    "ops_gate_result": "actionable",
    "ops-gate": "actionable",
    "proposal_feedback": "actionable",
    "verified_proposal": "actionable",
    "config_change": "actionable",
    "cron_result": "actionable",
    "cron": "actionable",
    "cron_failure": "actionable",
    "cron_recovery": "actionable",
    "cron_prompt_scan_block": "actionable",
    "mcp_health": "actionable",
    "gateway_health": "actionable",
    "tool_reliability": "actionable",
    "recent_session_mention": "conditional_actionable",
    "skill_health_delta": "conditional_actionable",
    "self_agenda_init": "structural",
    "cron_signal_summary": "structural",
    "skill_health_snapshot": "structural",
    "skill_lifecycle_state": "structural",
    "skill_lifecycle_summary": "structural",
    "session_metadata": "structural",
    "cron_no_agent_candidates": "ops_review_only",
}

STRUCTURAL_EVIDENCE_SOURCES = {
    source for source, cls in SIGNAL_CLASSIFICATION.items() if cls == "structural"
}

OPS_REVIEW_ONLY_EVIDENCE_SOURCES = {
    source for source, cls in SIGNAL_CLASSIFICATION.items() if cls == "ops_review_only"
}

ACTIONABLE_EVIDENCE_SOURCES = {
    source for source, cls in SIGNAL_CLASSIFICATION.items() if cls == "actionable"
}


# ── I/O helpers ──

def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def load_yaml(path: Path) -> dict:
    import yaml
    if not path.exists():
        return {}
    content = path.read_text()
    if not content.strip():
        return {}
    return yaml.safe_load(content) or {}


def write_yaml(path: Path, data: dict):
    import yaml
    path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False))


def load_signals(days: int = 3) -> list[dict]:
    if not SIGNALS_FILE.exists():
        return []
    cutoff = datetime.now(TZ) - timedelta(days=days)
    result = []
    for line in SIGNALS_FILE.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            sig = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_str = sig.get("ts", "")
        if ts_str:
            try:
                sig_ts = datetime.fromisoformat(ts_str)
                if sig_ts >= cutoff:
                    result.append(sig)
            except (ValueError, TypeError):
                pass
    return result


def load_proposals() -> list[dict]:
    data = load_yaml(STATE_DIR / "proposal_queue.yaml")
    return data.get("proposals", [])


# ── Evidence dedup key ──

def _build_signal_dedup_key(sig: dict) -> str:
    sig_type = sig.get("type", "")
    if sig_type == "skill_health":
        return (f"skill_health:{sig.get('skill','?')}:{sig.get('ts','')}:{sig.get('stale','')}")
    elif sig_type == "skill_health_delta":
        newly = ",".join(sorted(sig.get("newly_stale", []) or []))
        recovered = ",".join(sorted(sig.get("recovered", []) or []))
        return f"skill_health_delta:{sig.get('ts','')}:{newly}:{recovered}"
    elif sig_type == "cron_result":
        return (f"cron:{sig.get('job_id','?')}:{sig.get('mtime','')}:{sig.get('has_error','')}")
    elif sig_type in ("cron_recovery", "cron_failure"):
        return (f"{sig_type}:{sig.get('job_id','?')}:{sig.get('mtime','')}:"
                f"{sig.get('previous_status','')}:{sig.get('current_status','')}:"
                f"{sig.get('failure_kind','')}")
    elif sig_type == "cron_prompt_scan_block":
        return (f"cron_prompt_scan_block:{sig.get('profile','?')}:"
                f"{sig.get('job_id','?')}:{sig.get('mtime','')}:{sig.get('source_path','')}")
    elif sig_type == "ops_gate_result":
        return (f"ops_gate:{sig.get('task_id','?')}:{sig.get('ts','')}:{sig.get('pass','')}")
    elif sig_type == "mcp_health":
        return (f"mcp_health:{sig.get('server_name','?')}:{sig.get('ts','')}:"
                f"{sig.get('connect_ok','')}:{sig.get('tool_count','')}:"
                f"{sig.get('latency_bucket','')}:{sig.get('error_class','')}")
    elif sig_type == "gateway_health":
        return (f"gateway_health:{sig.get('ts','')}:"
                f"{sig.get('alert_fingerprint','')}:{sig.get('alert_count','')}")
    elif sig_type == "config_change":
        return (f"config:{sig.get('path','?')}:{sig.get('mtime','')}")
    elif sig_type == "proposal_feedback":
        return (f"proposal:{sig.get('ts','')}:{sig.get('total_proposals',0)}:{sig.get('pending',0)}")
    elif sig_type == "tool_reliability":
        return (f"tool:{sig.get('ts','')}:{sig.get('today_failure_count',0)}")
    elif sig_type == "memory_quality":
        return (f"memory:{sig.get('target','?')}:{sig.get('ts','')}")
    elif sig_type == "session_metadata":
        return (f"session:{sig.get('ts','')}:{sig.get('total_journal_entries',0)}")
    else:
        return (f"unknown:{hash(json.dumps(sig, sort_keys=True))}")


def _build_evidence_dedup_key(ev: dict) -> str:
    source = ev.get("source", "")
    at = ev.get("at", "")
    summary = ev.get("summary", "")
    stored_key = ev.get("evidence_dedup_key")
    if stored_key and source in {
        "skill_health_delta",
        "cron_recovery",
        "cron_failure",
        "cron_prompt_scan_block",
        "mcp_health",
        "gateway_health",
    }:
        return stored_key

    if source == "skill_health":
        skill_match = re.search(r'"skill"\s*:\s*"([^"]+)"', summary)
        stale_match = re.search(r'"stale"\s*:\s*(true|false)', summary)
        skill = skill_match.group(1) if skill_match else "?"
        stale = stale_match.group(1) if stale_match else "?"
        return f"skill_health:{skill}:{at}:{stale}"
    elif source in ("cron_result", "cron"):
        job_match = re.search(r'"job_id"\s*:\s*"([^"]+)"', summary)
        err_match = re.search(r'"has_error"\s*:\s*(true|false)', summary)
        job = job_match.group(1) if job_match else "?"
        err = err_match.group(1) if err_match else "?"
        return f"cron:{job}:{at}:{err}"
    elif source in ("ops_gate_result", "ops-gate"):
        tid = summary.split(":")[0] if ":" in summary else summary[:30]
        return f"ops_gate:{tid}:{at}"
    elif source == "config_change":
        path_match = re.search(r'"path"\s*:\s*"([^"]+)"', summary)
        fpath = path_match.group(1) if path_match else summary[:60]
        return f"config:{fpath}:{at}"
    elif source == "verified_proposal":
        return f"proposal:{summary}"
    elif source == "self_agenda_init":
        return f"init:{summary[:60]}:{at}"
    else:
        return f"{source}:{hash(summary)}:{at}"


# ── V1.4.1b: Qualified evidence determination ──

def _is_qualified_evidence(item: dict, ev: dict) -> tuple[bool, str, float]:
    """
    Determine if evidence is qualified for this agenda item.

    Qualification criteria (ANY of):
      1. contribution >= 0.10  (weight × relevance_factor)
      2. relevance >= 0.50     (how directly the evidence relates to the agenda)
      3. source_type in strong_sources for this item type

    Returns (is_qualified, reason, relevance_score).
    """
    item_type = item.get("type", "")
    source = ev.get("source", "")
    weight = ev.get("weight", 0.1)
    summary = ev.get("summary", "")

    if source in OPS_REVIEW_ONLY_EVIDENCE_SOURCES:
        return (False, f"ops_review_only ({source})", 0.0)

    # Compute relevance based on source type and content
    relevance = _compute_relevance(item_type, source, summary)
    contribution = round(weight * relevance, 3)

    strong_sources = {
        "strategic_positioning": {"session_metadata", "recent_session_mention", "verified_proposal", "config_change"},
        "automation_opportunity": {"repeated_manual_work", "user_correction",
                                    "opportunity_for_automation"},
        "quality_improvement": {"ops_gate_result", "proposal_feedback", "verified_proposal",
                                "cron_recovery", "cron_failure", "cron_prompt_scan_block",
                                "mcp_health", "gateway_health"},
        "cleanup_candidate": {"recent_session_mention", "config_change", "skill_health_delta"},
        "risk_watch": {"ops_gate_result", "cron_result", "tool_reliability",
                       "cron_recovery", "cron_failure", "cron_prompt_scan_block",
                       "mcp_health", "gateway_health"},
    }

    item_strong = strong_sources.get(item_type, set())

    is_strong_source = source in item_strong
    is_qualified = (contribution >= 0.10) or (relevance >= 0.50) or is_strong_source

    if is_strong_source:
        reason = f"strong_source ({source})"
    elif contribution >= 0.10:
        reason = f"contribution={contribution:.3f} >= 0.10"
    elif relevance >= 0.50:
        reason = f"relevance={relevance:.2f} >= 0.50"
    else:
        reason = (f"weak: contribution={contribution:.3f} < 0.10, "
                  f"relevance={relevance:.2f} < 0.50, source={source} not in strong_sources")

    return (is_qualified, reason, relevance)


def _compute_relevance(item_type: str, source: str, summary: str) -> float:
    """
    Compute relevance score (0.0–1.0) for evidence to agenda item type.

    Higher = more directly relevant to answering the agenda question.
    """
    sl = summary.lower()

    if item_type == "cleanup_candidate":
        if source == "self_agenda_init":
            return 0.50
        if source == "skill_health":
            idle_indicators = ["last_used", "no_recent_mention", "no_dependency",
                                "disabled", "stale"]
            if any(kw in sl for kw in idle_indicators):
                return 0.60
            return 0.20
        if source == "skill_health_delta":
            if "newly_stale" in sl and '"newly_stale":[]' not in sl.replace(" ", ""):
                return 0.75
            return 0.20
        if source == "config_change":
            if "archive" in sl or "cleanup" in sl:
                return 0.70
            return 0.30
        if source == "recent_session_mention":
            return 0.65  # real skill usage = direct evidence of what's unused
        return 0.10

    elif item_type == "strategic_positioning":
        if source == "verified_proposal":
            return 0.85
        if source == "config_change":
            if any(kw in sl for kw in ("skill", "proposal", "agenda", "governance")):
                return 0.60
            return 0.30
        if source == "session_metadata":
            return 0.65  # platform/activity distribution = direct user context
        if source == "recent_session_mention":
            return 0.70  # real conversation topics = direct user intent signal
        return 0.10

    elif item_type == "quality_improvement":
        if source in ("ops_gate_result", "gateway_health"):
            return 0.80
        if source in ("cron_recovery", "cron_failure", "cron_prompt_scan_block"):
            return 0.70
        if source == "mcp_health":
            return 0.65
        if source == "proposal_feedback":
            return 0.75
        if source == "verified_proposal":
            return 0.70
        if source == "self_agenda_init":
            return 0.50
        return 0.20

    elif item_type == "risk_watch":
        if source in ("ops_gate_result", "ops-gate"):
            if "fail" in sl or "timeout" in sl or "error" in sl:
                return 0.90
            return 0.30
        if source in ("cron_result", "cron_recovery", "cron_failure", "cron_prompt_scan_block"):
            return 0.50
        if source == "gateway_health":
            return 0.75
        if source == "mcp_health":
            if "false" in sl or "error" in sl or "timeout" in sl:
                return 0.80
            return 0.40
        if source == "tool_reliability":
            return 0.60
        return 0.10

    elif item_type == "automation_opportunity":
        if source in ("repeated_manual_work", "user_correction",
                       "opportunity_for_automation"):
            return 0.85
        return 0.20

    return 0.10


def _is_negative_skill_health_delta(summary: str) -> bool:
    """Return true only when skill health moved worse, not when it recovered."""
    normalized = summary.replace(" ", "").lower()
    if '"newly_stale":[]' in normalized:
        return False
    return "newly_stale" in normalized or "stale" in normalized


def _is_actionable_evidence(ev: dict) -> bool:
    """Hard filter for evidence allowed to drive agenda maturity scoring."""
    source = ev.get("source", "")
    summary = ev.get("summary", "")

    if source in OPS_REVIEW_ONLY_EVIDENCE_SOURCES:
        return False
    if source in STRUCTURAL_EVIDENCE_SOURCES:
        return False
    if source == "skill_health_delta":
        return _is_negative_skill_health_delta(summary)
    if source == "recent_session_mention":
        return bool(ev.get("actionable_qualified") or ev.get("quality_concern"))
    if source in ACTIONABLE_EVIDENCE_SOURCES:
        return True
    return False


# ── Evidence matching (V1.4.1b: stores qualified flag) ──

def match_evidence(item: dict, signals: list[dict], proposals: list[dict]) -> tuple[list[dict], int]:
    matchers = item.get("evidence_matchers", {})
    signal_types = set(matchers.get("signal_types", []))
    include_kw = matchers.get("include_keywords", [])
    exclude_kw = [str(kw) for kw in matchers.get("exclude_keywords", []) if kw]

    existing_evidence = item.get("evidence", [])
    existing_dedup_keys = set()
    for ev in existing_evidence:
        existing_dedup_keys.add(_build_evidence_dedup_key(ev))

    new_evidence = []
    skipped_dup = 0

    max_new = 3
    signal_hits = 0
    for sig in signals:
        if signal_hits >= max_new:
            break
        st = sig.get("type", "")
        if signal_types and st not in signal_types:
            continue
        summary = sig.get("summary", json.dumps(sig, ensure_ascii=False)[:200])
        text = json.dumps(sig, ensure_ascii=False)
        if include_kw:
            if not any(kw.lower() in text.lower() for kw in include_kw):
                continue
        if exclude_kw:
            if any(kw.lower() in text.lower() for kw in exclude_kw):
                continue

        dedup_key = _build_signal_dedup_key(sig)
        if dedup_key in existing_dedup_keys:
            skipped_dup += 1
            continue

        weight = min(0.35, max(0.05, sum(
            0.05 for kw in include_kw if kw.lower() in text.lower()
        )))

        # V1.4.1b: Determine qualified status at match time
        dummy_ev = {"source": st, "summary": summary[:150], "weight": round(weight, 2)}
        is_qual, qual_reason, relevance = _is_qualified_evidence(item, dummy_ev)

        evidence_entry = {
            "at": sig.get("ts", now_iso()),
            "source": st,
            "summary": summary[:150],
            "weight": round(weight, 2),
            "evidence_dedup_key": dedup_key,
            # V1.4.1b metadata
            "qualified": is_qual,
            "qualify_reason": qual_reason,
            "relevance": round(relevance, 2),
            "contribution": round(weight * relevance, 3),
        }
        if st == "recent_session_mention":
            for meta_key in (
                "quality_concern",
                "actionable_qualified",
                "quality_concern_count",
                "quality_concern_keywords",
                "quality_concern_refs",
                "quality_concern_fingerprint",
            ):
                if meta_key in sig:
                    evidence_entry[meta_key] = sig.get(meta_key)
        new_evidence.append(evidence_entry)
        existing_dedup_keys.add(dedup_key)
        signal_hits += 1

    max_proposal = 2
    proposal_hits = 0
    for p in proposals:
        if proposal_hits >= max_proposal:
            break
        p_status = p.get("status", "")
        if p_status not in ("verified", "implemented"):
            continue
        p_text = json.dumps(p, ensure_ascii=False)
        if include_kw and not any(kw.lower() in p_text.lower() for kw in include_kw):
            continue
        if exclude_kw and any(kw.lower() in p_text.lower() for kw in exclude_kw):
            continue
        p_summary = f"verified proposal: {p.get('title', '')[:100]}"
        p_key = f"proposal:{p_summary}"
        if p_key in existing_dedup_keys:
            skipped_dup += 1
            continue

        dummy_ev = {"source": "verified_proposal", "summary": p_summary, "weight": 0.25}
        is_qual, qual_reason, relevance = _is_qualified_evidence(item, dummy_ev)

        new_evidence.append({
            "at": p.get("timestamps", {}).get("updated_at", now_iso()),
            "source": "verified_proposal",
            "summary": p_summary,
            "weight": 0.25,
            "evidence_dedup_key": p_key,
            "qualified": is_qual,
            "qualify_reason": qual_reason,
            "relevance": round(relevance, 2),
            "contribution": round(0.25 * relevance, 3),
        })
        existing_dedup_keys.add(p_key)
        proposal_hits += 1

    return new_evidence, skipped_dup


# ── Score calculation (V1.4.1b) ──

def _calc_evidence_stats(item: dict) -> dict:
    """
    Calculate evidence statistics for an agenda item.

    V1.4.1b: Adds qualified evidence tracking.
    """
    evidence_list = item.get("evidence", [])
    total_count = len(evidence_list)

    seen_keys = set()
    unique_entries = []
    duplicate_entries = []
    for ev in evidence_list:
        key = _build_evidence_dedup_key(ev)
        if key in seen_keys:
            duplicate_entries.append(ev)
        else:
            seen_keys.add(key)
            unique_entries.append(ev)

    unique_count = len(unique_entries)
    duplicate_count = total_count - unique_count

    # V1.4.1b: Qualified evidence (among unique entries only)
    qualified_entries = []
    unqualified_entries = []
    for ev in unique_entries:
        # Use qualified flag from match_evidence if available
        is_qual = ev.get("qualified")
        if is_qual is not None:
            pass  # Already determined at match time
        else:
            # Legacy evidence without qualified flag — compute on the fly
            is_qual, _, _ = _is_qualified_evidence(item, ev)
        if is_qual:
            qualified_entries.append(ev)
        else:
            unqualified_entries.append(ev)

    qualified_count = len(qualified_entries)

    # V1.4.1c + CW-004: Split into structural_qualified and actionable_qualified.
    # Structural evidence keeps the item explainable but contributes zero score.
    structural_qualified_entries = []
    actionable_qualified_entries = []
    for ev in qualified_entries:
        if _is_actionable_evidence(ev):
            actionable_qualified_entries.append(ev)
        else:
            structural_qualified_entries.append(ev)

    actionable_qualified_count = len(actionable_qualified_entries)
    structural_qualified_count = len(structural_qualified_entries)

    # V1.4.1c: Actionable qualified strength = sum(weight × relevance) for actionable
    actionable_qualified_strength = 0.0
    if actionable_qualified_entries:
        for e in actionable_qualified_entries:
            relevance = e.get("relevance", _compute_relevance(
                item.get("type", ""), e.get("source", ""), e.get("summary", "")))
            actionable_qualified_strength += e.get("weight", 0.1) * relevance
        actionable_qualified_strength = round(actionable_qualified_strength, 3)

    today = today_str()
    fresh_qualified = sum(1 for e in qualified_entries if e.get("at", "").startswith(today))
    # V1.4.1c: Fresh actionable qualified (for trend calculation)
    fresh_actionable = sum(1 for e in actionable_qualified_entries if e.get("at", "").startswith(today))

    avg_weight = 0.0
    actionable_avg_weight = 0.0
    sum_weighted_relevance = 0.0
    if qualified_entries:
        avg_weight = sum(e.get("weight", 0.1) for e in qualified_entries) / len(qualified_entries)
        for e in qualified_entries:
            relevance = e.get("relevance", _compute_relevance(
                item.get("type", ""), e.get("source", ""), e.get("summary", "")))
            sum_weighted_relevance += e.get("weight", 0.1) * relevance
    elif unique_entries:
        avg_weight = sum(e.get("weight", 0.1) for e in unique_entries) / len(unique_entries)

    if actionable_qualified_entries:
        actionable_avg_weight = (
            sum(e.get("weight", 0.1) for e in actionable_qualified_entries)
            / len(actionable_qualified_entries)
        )

    last_at = ""
    if unique_entries:
        last_at = max(e.get("at", "") for e in unique_entries)

    return {
        "total_count": total_count,
        "unique_count": unique_count,
        "duplicate_count": duplicate_count,
        "qualified_count": qualified_count,
        "structural_qualified_count": structural_qualified_count,
        "actionable_qualified_count": actionable_qualified_count,
        "actionable_qualified_strength": actionable_qualified_strength,
        "unqualified_among_unique": len(unqualified_entries),
        "fresh_qualified_count": fresh_qualified,
        "fresh_actionable_count": fresh_actionable,
        "avg_weight": round(avg_weight, 4),
        "actionable_avg_weight": round(actionable_avg_weight, 4),
        "sum_weighted_relevance": round(sum_weighted_relevance, 3),
        "last_evidence_at": last_at,
    }


def calculate_scores(item: dict) -> dict:
    """
    Calculate all score components and maturity_score.

    V1.4.1b:
    - evidence_strength: weighted by qualified evidence only
    - trend_strength: based on recent qualified evidence
    - recurrence_density: based on unique qualified evidence
    - qualified=0 ceiling: trend/recur/maturity capped
    """
    evidence_list = item.get("evidence", [])
    counters = item.get("counters", {})
    evidence_count = counters.get("evidence_count", len(evidence_list))
    observation_days = max(1, counters.get("observation_days", 1))
    recent_mentions = counters.get("recent_mentions_7d", 0)

    stats = _calc_evidence_stats(item)
    unique_count = stats["unique_count"]
    duplicate_count = stats["duplicate_count"]
    qualified_count = stats["qualified_count"]
    structural_qualified_count = stats["structural_qualified_count"]
    actionable_qualified_count = stats["actionable_qualified_count"]
    actionable_qualified_strength = stats["actionable_qualified_strength"]
    fresh_qualified = stats["fresh_qualified_count"]
    fresh_actionable = stats["fresh_actionable_count"]
    unqualified_among_unique = stats["unqualified_among_unique"]
    avg_weight = stats["avg_weight"]
    actionable_avg_weight = stats["actionable_avg_weight"]
    sum_weighted_relevance = stats["sum_weighted_relevance"]

    # ── evidence_strength ──
    # CW-004: only actionable qualified evidence can drive score. Structural
    # evidence and ops-review-only signals contribute zero, not a low weight.
    if actionable_qualified_count > 0:
        evidence_strength = min(
            1.0,
            actionable_qualified_strength / ACTIONABLE_STRENGTH_FULL_EVIDENCE,
        )
    else:
        evidence_strength = 0.0

    # ── V1.4.1c: trend_strength based on actionable qualified only ──
    has_qualified = qualified_count > 0
    has_actionable = actionable_qualified_count > 0
    if has_actionable:
        trend_ratio = min(1.0, fresh_actionable / max(1, actionable_qualified_count))
        trend_strength = min(1.0, trend_ratio * 0.8 + (recent_mentions / 20) * 0.2)
        # V1.4.1c: Progressive cap based on actionable count/strength
        if actionable_qualified_count == 1 or actionable_qualified_strength <= ACTIONABLE_STRENGTH_SINGLE_CAP_THRESHOLD:
            trend_strength = min(trend_strength, SINGLE_ACTIONABLE_TREND_CEILING)
    else:
        # No actionable evidence — cap tightly (even if structural/init exists)
        trend_strength = min(ZERO_ACTIONABLE_TREND_CEILING,
                             (fresh_qualified / max(1, unique_count)) * 0.5)

    # ── V1.4.1c: recurrence_density based on actionable qualified only ──
    if has_actionable:
        recurrence_density = min(1.0, actionable_qualified_count / max(5, observation_days * 2))
    else:
        recurrence_density = min(ZERO_QUALIFIED_RECUR_CEILING,
                                 unique_count / max(10, observation_days * 4))

    # ── unresolved_cost ──
    unresolved_cost = item.get("scores", {}).get("unresolved_cost", 0.30)

    # ── actionability ──
    action_val = {"ask_user_confirmation": 0.70, "create_proposal": 0.80,
                  "surface_in_digest": 0.50, "bypass_maturation_to_speak_gate": 0.90,
                  "archive_candidate": 0.30}.get(
        MATURITY_ACTION_MAP.get(item.get("type", ""), ""), 0.50)
    actionability = item.get("scores", {}).get("actionability", action_val)

    # ── time_pressure_bonus ──
    time_pressure_bonus = min(TIME_PRESSURE_MAX, math.log(observation_days + 1) * TIME_PRESSURE_LOG_FACTOR)
    if qualified_count == 0 and evidence_count == 0:
        time_pressure_bonus = 0.0

    # ── staleness_penalty ──
    staleness_penalty = 0.0
    if evidence_list:
        last_ev = max(e.get("at", "") for e in evidence_list)
        try:
            last_dt = datetime.fromisoformat(last_ev)
            days_since = (datetime.now(TZ) - last_dt).days
            if days_since >= 7:
                staleness_penalty = min(0.30, days_since * 0.01)
        except (ValueError, TypeError):
            pass

    contradiction_penalty = 0.0

    # ── Final maturity_score ──
    maturity_score = (
        WEIGHTS["evidence_strength"] * evidence_strength
        + WEIGHTS["trend_strength"] * trend_strength
        + WEIGHTS["recurrence_density"] * recurrence_density
        + WEIGHTS["unresolved_cost"] * unresolved_cost
        + WEIGHTS["actionability"] * actionability
        + time_pressure_bonus
        - staleness_penalty
        - contradiction_penalty
    )

    # V1.4.1c: Use actionable_qualified for maturity ceiling (still use all qualified as fallback)
    if not has_qualified and maturity_score > ZERO_QUALIFIED_MATURITY_CEILING:
        maturity_score = ZERO_QUALIFIED_MATURITY_CEILING

    maturity_score = max(0.0, min(1.0, maturity_score))

    return {
        "evidence_strength": round(evidence_strength, 2),
        "trend_strength": round(trend_strength, 2),
        "recurrence_density": round(recurrence_density, 2),
        "unresolved_cost": round(unresolved_cost, 2),
        "actionability": round(actionability, 2),
        "time_pressure_bonus": round(time_pressure_bonus, 2),
        "staleness_penalty": round(staleness_penalty, 2),
        "contradiction_penalty": round(contradiction_penalty, 2),
        "maturity_score": round(maturity_score, 2),
        "unique_evidence_count": unique_count,
        "duplicate_evidence_count": duplicate_count,
        "qualified_evidence_count": qualified_count,
        "structural_qualified_count": structural_qualified_count,
        "actionable_qualified_count": actionable_qualified_count,
        "actionable_qualified_strength": actionable_qualified_strength,
        "fresh_qualified_count": fresh_qualified,
        "has_qualified_evidence": has_qualified,
        "has_actionable_evidence": has_actionable,
    }


# ── State machine (V1.4.1b) ──

RISK_WATCH_PATTERN_KEY_VERSION = "v2"


def _strip_volatile_event_fields(value):
    if isinstance(value, dict):
        volatile = {
            "ts",
            "timestamp",
            "generated_at",
            "updated_at",
            "mtime",
            "last_run_at",
            "next_run_at",
        }
        return {
            key: _strip_volatile_event_fields(inner)
            for key, inner in sorted(value.items())
            if key not in volatile
        }
    if isinstance(value, list):
        return [_strip_volatile_event_fields(item) for item in value]
    return value


def _risk_watch_event_key(ev: dict) -> str:
    summary = ev.get("summary", "")
    source = ev.get("source", "")
    if isinstance(summary, dict):
        parsed = summary
    else:
        parsed = None
        if isinstance(summary, str):
            stripped = summary.strip()
            if stripped.startswith("{"):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    parsed = None

    if isinstance(parsed, dict):
        parts = [
            source,
            parsed.get("type"),
            parsed.get("server") or parsed.get("server_name") or parsed.get("job_id") or parsed.get("task_name"),
            (
                parsed.get("error_class")
                or parsed.get("status")
                or parsed.get("alert_fingerprint")
                or parsed.get("previous_status")
                or parsed.get("current_status")
            ),
        ]
        stable_parts = [str(part) for part in parts if part not in (None, "")]
        if stable_parts:
            return f"{RISK_WATCH_PATTERN_KEY_VERSION}:json:" + ":".join(stable_parts)

        stable_payload = _strip_volatile_event_fields(parsed)
        rendered = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]
        return f"{RISK_WATCH_PATTERN_KEY_VERSION}:json-hash:{source}:{digest}"

    if isinstance(summary, str):
        task_match = re.search(r"task\s+(\S+)", summary)
        if task_match:
            return f"{RISK_WATCH_PATTERN_KEY_VERSION}:text-task:{task_match.group(1)}"
        error_type = summary.split(":", 1)[0] if ":" in summary else summary[:80]
        return f"{RISK_WATCH_PATTERN_KEY_VERSION}:text:{error_type.strip()}"

    return f"{RISK_WATCH_PATTERN_KEY_VERSION}:unknown:{source}:{type(summary).__name__}"


def advance_state(item: dict, scores: dict) -> str:
    item_type = item.get("type", "")
    status = item.get("status", "observing")
    policy = item.get("maturity_policy", {})
    counters = item.get("counters", {})
    maturity_score = scores["maturity_score"]
    evidence_count = counters.get("evidence_count", 0)
    observation_days = max(1, counters.get("observation_days", 1))
    unique_count = scores.get("unique_evidence_count", 0)
    qualified_count = scores.get("qualified_evidence_count", 0)
    # V1.4.1c: Use actionable count for candidate_ready gate
    actionable_count = scores.get("actionable_qualified_count", 0)
    evidence_strength = scores.get("evidence_strength", 0.0)
    has_qualified = scores.get("has_qualified_evidence", False)

    min_score = policy.get("min_score_to_surface", 0.72)
    min_evidence = policy.get("min_evidence_count", 3)
    min_days = policy.get("min_observation_days", 3)
    max_review = policy.get("max_observation_days_before_review", 14)
    archive_days = policy.get("auto_archive_if_no_evidence_days", 21)

    # V1.4.1b: Risk_watch event-based rules (bypasses normal maturity gate)
    if item_type == "risk_watch":
        evidence_list = item.get("evidence", [])

        recent_24h = {}
        for ev in evidence_list:
            try:
                ev_dt = datetime.fromisoformat(ev.get("at", ""))
                if (datetime.now(TZ) - ev_dt) < timedelta(hours=24):
                    recent_24h.setdefault(_risk_watch_event_key(ev), []).append(ev)
            except (ValueError, TypeError):
                pass

        for event_key, failures in recent_24h.items():
            if len(failures) >= 2:
                return "bypass_maturation_to_speak_gate"

        recent_7d = {}
        for ev in evidence_list:
            try:
                ev_dt = datetime.fromisoformat(ev.get("at", ""))
                if (datetime.now(TZ) - ev_dt) < timedelta(days=7):
                    recent_7d.setdefault(_risk_watch_event_key(ev), []).append(ev)
            except (ValueError, TypeError):
                pass

        for event_key, failures in recent_7d.items():
            if len(failures) >= 3:
                return "generate_quality_proposal"

        return "continue_observing"

    # Already terminal
    if status in ("resolved", "archived", "surfaced"):
        return status

    # Auto-archive if long idle
    if observation_days >= archive_days and evidence_count == 0:
        return "archived"

    # Check cooldown
    last_surfaced = item.get("last_surfaced_at")
    if last_surfaced:
        try:
            surfaced_dt = datetime.fromisoformat(last_surfaced)
            cooldown = policy.get("same_agenda_cooldown_days", 7)
            if (datetime.now(TZ) - surfaced_dt).days < cooldown:
                return "continue_observing"
        except (ValueError, TypeError):
            pass

    # V1.4.1c: Candidate ready with actionable-qualified gate (init doesn't count)
    type_min_es = MIN_EV_STRENGTH_BY_TYPE.get(item_type, 0.0)
    if (maturity_score >= min_score
            and unique_count >= min_evidence
            and actionable_count >= 2  # V1.4.1c: actionable_qualified, not blanket qualified
            and evidence_strength >= type_min_es
            and observation_days >= min_days):
        return "candidate_ready"

    # Review because too old
    if observation_days >= max_review:
        if evidence_count == 0:
            return "archive_candidate"
        return "surface_in_digest_for_review"

    # Advance from observing → accumulating_evidence
    if status == "observing" and evidence_count >= 1:
        return "accumulating_evidence"

    return "continue_observing"


# ── Explain scores ──

def explain_scores(items: list[dict], signals: list[dict], proposals: list[dict]) -> dict:
    now_ts = now_iso()
    today = today_str()
    explanation = {
        "generated_at": now_ts,
        "mode": "explain-scores",
        "version": "V1.4.1c",
        "items": [],
    }

    for idx, item in enumerate(items):
        iid = item.get("id", f"item_{idx}")
        title = item.get("title", "")
        item_type = item.get("type", "")
        status = item.get("status", "observing")
        evidence_list = item.get("evidence", [])
        counters = item.get("counters", {})
        evidence_count = counters.get("evidence_count", len(evidence_list))
        observation_days = counters.get("observation_days", 1)

        scores = calculate_scores(item)
        stats = _calc_evidence_stats(item)

        es = scores["evidence_strength"]
        ts = scores["trend_strength"]
        rd = scores["recurrence_density"]
        uc = scores["unresolved_cost"]
        ab = scores["actionability"]
        tp = scores["time_pressure_bonus"]
        sp = scores["staleness_penalty"]
        ms = scores["maturity_score"]
        qc = scores["qualified_evidence_count"]
        sqc = scores.get("structural_qualified_count", 0)
        aqc = scores.get("actionable_qualified_count", 0)
        has_q = scores["has_qualified_evidence"]
        has_aq = scores.get("has_actionable_evidence", False)

        weighted_base = (
            WEIGHTS["evidence_strength"] * es
            + WEIGHTS["trend_strength"] * ts
            + WEIGHTS["recurrence_density"] * rd
            + WEIGHTS["unresolved_cost"] * uc
            + WEIGHTS["actionability"] * ab
        )

        ev_breakdown = []
        seen_keys = set()
        for i, ev in enumerate(evidence_list):
            dk = _build_evidence_dedup_key(ev)
            is_dup = dk in seen_keys
            seen_keys.add(dk)

            is_qual = ev.get("qualified")
            if is_qual is None:
                is_qual, qual_reason, relevance = _is_qualified_evidence(item, ev)
                qual_reason_str = qual_reason
            else:
                qual_reason_str = ev.get("qualify_reason", "matched at collection")
                relevance = ev.get("relevance", 0.2)

            # V1.4.1c + CW-004: Structural vs actionable classification
            is_actionable = is_qual and _is_actionable_evidence(ev)
            is_structural = is_qual and not is_actionable
            drives_trend = is_actionable
            drives_recurrence = is_actionable
            drives_candidate_ready = is_actionable

            ev_breakdown.append({
                "index": i,
                "duplicate": is_dup,
                "source": ev.get("source", "?"),
                "weight": ev.get("weight", 0.1),
                "qualified": is_qual,
                "structural_qualified": is_structural,
                "actionable_qualified": is_actionable,
                "drives_trend": drives_trend,
                "drives_recurrence": drives_recurrence,
                "drives_candidate_ready": drives_candidate_ready,
                "relevance": round(relevance, 2),
                "contribution": round(ev.get("weight", 0.1) * relevance, 3),
                "reason": qual_reason_str,
                "summary_truncated": ev.get("summary", "")[:60],
                "timestamp": ev.get("at", ""),
            })

        type_min_es = MIN_EV_STRENGTH_BY_TYPE.get(item_type, 0.0)

        item_explanation = {
            "agenda_id": iid,
            "title": title,
            "type": item_type,
            "status": status,
            "score_summary": {
                "evidence_strength": es,
                "trend_strength": ts,
                "recurrence_density": rd,
                "unresolved_cost": uc,
                "actionability": ab,
                "time_pressure_bonus": tp,
                "staleness_penalty": sp,
                "maturity_score": ms,
                "weighted_base": round(weighted_base, 3),
            },
            "evidence_counts": {
                "raw_evidence_count": evidence_count,
                "unique_evidence_count": stats["unique_count"],
                "duplicate_evidence_count": stats["duplicate_count"],
                "qualified_evidence_count": qc,
                "structural_qualified_count": sqc,
                "actionable_qualified_count": aqc,
                "unqualified_among_unique": stats["unqualified_among_unique"],
                "fresh_qualified_count": stats["fresh_qualified_count"],
                "has_qualified_evidence": has_q,
                "has_actionable_evidence": has_aq,
            },
            "candidate_ready_check": {
                "maturity_score_passed": ms >= item.get("maturity_policy", {}).get("min_score_to_surface", 0.72),
                "unique_evidence_passed": stats["unique_count"] >= item.get("maturity_policy", {}).get("min_evidence_count", 3),
                "qualified_evidence_passed": qc >= 2,
                "actionable_qualified_passed": aqc >= 2,  # V1.4.1c: actual gate for candidate_ready
                "evidence_strength_passed": es >= type_min_es,
                "type_min_evidence_strength": type_min_es,
                "observation_days_passed": observation_days >= item.get("maturity_policy", {}).get("min_observation_days", 3),
            },
            "evidence_breakdown": ev_breakdown,
            "score_formula": (
                f"maturity = {WEIGHTS['evidence_strength']}×{es} + {WEIGHTS['trend_strength']}×{ts} + "
                f"{WEIGHTS['recurrence_density']}×{rd} + {WEIGHTS['unresolved_cost']}×{uc} + "
                f"{WEIGHTS['actionability']}×{ab} + {tp} - {sp} = {ms}"
                + (f" [no actionable: trend cap={ZERO_ACTIONABLE_TREND_CEILING}]" if not has_aq else
                   f" [limited actionable: trend cap={SINGLE_ACTIONABLE_TREND_CEILING}]" if aqc == 1 else "")
            ),
        }

        explanation["items"].append(item_explanation)

    return explanation


def print_explanation(explanation: dict):
    print("=" * 72)
    print(f"  V1.4.1c Evidence Calibration — Score Explanation")
    print(f"  Generated: {explanation['generated_at']}")
    print("=" * 72)

    for item in explanation["items"]:
        iid = item["agenda_id"]
        sc = item["score_summary"]
        ec = item["evidence_counts"]
        cr = item["candidate_ready_check"]

        print(f"\n{'─' * 72}")
        print(f"  {iid}: {item['title']}")
        print(f"  Type: {item['type']}  |  Status: {item['status']}")
        print(f"  Has qualified: {'✅' if ec['has_qualified_evidence'] else '⛔ (ceiling active)'}")
        print(f"{'─' * 72}")

        print(f"\n  Evidence Counts:")
        print(f"    Raw: {ec['raw_evidence_count']}  |  Unique: {ec['unique_evidence_count']}  |  "
              f"Qual: {ec['qualified_evidence_count']}  |  "
              f"S: {ec.get('structural_qualified_count', 0)}  |  "
              f"A: {ec.get('actionable_qualified_count', 0)}  |  "
              f"Unqual: {ec['unqualified_among_unique']}  |  "
              f"Dup: {ec['duplicate_evidence_count']}")
        print(f"    Fresh qualified today: {ec['fresh_qualified_count']}")

        print(f"\n  Score Components:")
        print(f"    evidence_strength  = {sc['evidence_strength']:.2f}  "
              f"(w={WEIGHTS['evidence_strength']:.2f} → {WEIGHTS['evidence_strength'] * sc['evidence_strength']:.3f})")
        print(f"    trend_strength     = {sc['trend_strength']:.2f}  "
              f"(w={WEIGHTS['trend_strength']:.2f} → {WEIGHTS['trend_strength'] * sc['trend_strength']:.3f})")
        print(f"    recurrence_density = {sc['recurrence_density']:.2f}  "
              f"(w={WEIGHTS['recurrence_density']:.2f} → {WEIGHTS['recurrence_density'] * sc['recurrence_density']:.3f})")
        print(f"    unresolved_cost    = {sc['unresolved_cost']:.2f}  "
              f"(w={WEIGHTS['unresolved_cost']:.2f} → {WEIGHTS['unresolved_cost'] * sc['unresolved_cost']:.3f})")
        print(f"    actionability      = {sc['actionability']:.2f}  "
              f"(w={WEIGHTS['actionability']:.2f} → {WEIGHTS['actionability'] * sc['actionability']:.3f})")
        print(f"    time_pressure      = +{sc['time_pressure_bonus']:.2f}  |  "
              f"staleness = -{sc['staleness_penalty']:.2f}")
        print(f"    Weighted base: {sc['weighted_base']:.3f} → maturity: {sc['maturity_score']:.2f}")

        type_min_es = cr.get('type_min_evidence_strength', 0.0)
        print(f"\n  Candidate Ready Check:")
        print(f"    maturity_score {sc['maturity_score']:.2f} >= {item.get('maturity_policy', {}).get('min_score_to_surface', 0.72)}:  "
              f"{'✅' if cr['maturity_score_passed'] else '⛔'}")
        print(f"    unique_evidence {ec['unique_evidence_count']} >= min_ev:          "
              f"{'✅' if cr['unique_evidence_passed'] else '⛔'}")
        print(f"    qualified_evidence {ec['qualified_evidence_count']} >= 2:               "
              f"{'✅' if cr['qualified_evidence_passed'] else '⛔'}")
        print(f"    actionable_qualified {ec.get('actionable_qualified_count', 0)} >= 2:           "
              f"{'✅' if cr.get('actionable_qualified_passed', False) else '⛔'}")
        print(f"    evidence_strength {sc['evidence_strength']:.2f} >= {type_min_es:.2f}:  "
              f"{'✅' if cr['evidence_strength_passed'] else '⛔'}")

        print(f"\n  Evidence Entries (last 8):")
        for ev in item["evidence_breakdown"][-8:]:
            if ev.get("structural_qualified", False):
                q_mark = "🔷S"
            elif ev.get("actionable_qualified", False):
                q_mark = "✅A"
            else:
                q_mark = "⛔w"
            d_mark = " DUP" if ev["duplicate"] else "    "
            print(f"    [{ev['index']}] {q_mark}{d_mark} {ev['source']} w={ev['weight']} "
                  f"rel={ev['relevance']:.2f} c={ev['contribution']:.3f} — {ev['summary_truncated']}")
            if not ev["qualified"] and not ev["duplicate"]:
                print(f"         ↳ {ev['reason']}")

    print(f"\n{'=' * 72}")
    print("  End of explanation")
    print(f"{'=' * 72}")


def write_score_explanations(explanation: dict):
    today = today_str()
    SCORE_EXPL_DIR.mkdir(parents=True, exist_ok=True)
    filepath = SCORE_EXPL_DIR / f"{today}.yaml"
    write_yaml(filepath, explanation)
    return filepath


# ── Journal writing ──

def write_journal(items: list[dict], changed_ids: list[str],
                  candidates: list[dict], decisions: dict[str, str],
                  old_scores: dict[str, float],
                  mode: str = "shadow",
                  total_duplicates: int = 0,
                  explanation_path: str = ""):
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append(f"### Agenda Maturation Run ({mode} mode, V1.4.1c)")
    lines.append(f"**Time:** {now}")
    lines.append(f"**Items scanned:** {len(items)}")
    lines.append(f"**Items updated:** {len(changed_ids)}")
    lines.append(f"**Candidates emitted:** {len(candidates)}")
    lines.append(f"**Total duplicates identified:** {total_duplicates}")
    if explanation_path:
        lines.append(f"**Score explanations:** {explanation_path}")
    lines.append("")

    if candidates:
        lines.append("#### Candidates Ready")
        for c in candidates:
            lines.append(f"- **{c['agenda_id']}** ({c['type']}): score={c['maturity_score']:.2f}, action={c['action']}")
        lines.append("")

    score_changes = []
    for item in items:
        iid = item.get("id", "?")
        new = item.get("scores", {}).get("maturity_score", 0)
        old = old_scores.get(iid, None)
        if old is not None and abs(old - new) > 0.01:
            score_changes.append({
                "id": iid,
                "title": item.get("title", "")[:40],
                "old_score": round(old, 2),
                "new_score": round(new, 2),
                "status": item.get("status", ""),
                "evidence_count": item.get("counters", {}).get("evidence_count", 0),
                "decision": decisions.get(iid, ""),
            })

    if score_changes:
        lines.append("#### Score Changes")
        for sc in score_changes:
            delta = sc["new_score"] - sc["old_score"]
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
            lines.append(f"- {sc['id']} ({sc['title']}): {sc['old_score']:.2f} {arrow} {sc['new_score']:.2f} | status={sc['status']} | ev={sc['evidence_count']} | {sc['decision']}")
        lines.append("")

    # V1.4.1b: Evidence breakdown with qualified count
    breakdown = []
    for item in items:
        iid = item.get("id", "?")
        if iid not in changed_ids and iid not in [c.get("agenda_id") for c in candidates]:
            continue
        evidence_list = item.get("evidence", [])
        total = len(evidence_list)
        stats = _calc_evidence_stats(item)
        scores_meta = item.get("scores", {})
        es = scores_meta.get("evidence_strength", 0)
        rd = scores_meta.get("recurrence_density", 0)
        ts = scores_meta.get("trend_strength", 0)
        qc = scores_meta.get("qualified_evidence_count", 0)
        hq = scores_meta.get("has_qualified_evidence", False)

        breakdown.append(
            f"  {iid}: raw={total}, uq={stats['unique_count']}, "
            f"qual(S/A)={stats.get('structural_qualified_count', 0)}/{stats.get('actionable_qualified_count', 0)}, "
            f"dup={stats['duplicate_count']}, "
            f"ev_str={es:.2f}, trend={ts:.2f}, recur={rd:.2f}"
            + ("" if hq else " [ceiling active]")
        )

    if breakdown:
        lines.append("#### Evidence Breakdown (V1.4.1c)")
        lines.extend(breakdown)
        lines.append("")

    lines.append("---")
    lines.append("")

    content = "\n".join(lines)
    if JOURNAL_FILE.exists():
        existing = JOURNAL_FILE.read_text()
        existing += "\n" + content
    else:
        existing = content
    JOURNAL_FILE.write_text(existing)


# ── Candidate emission ──

def emit_candidates(items: list[dict], *, write: bool = True) -> list[dict]:
    now = now_iso()
    candidates = []
    for item in items:
        decision = item.get("_decision", "")
        if decision == "candidate_ready":
            status = "candidate_ready"
            action_type = MATURITY_ACTION_MAP.get(item.get("type", ""), "")
            candidate_kind = "agenda_candidate"
        elif decision == "generate_quality_proposal":
            status = "quality_proposal_ready"
            action_type = "generate_quality_proposal"
            candidate_kind = "quality_proposal"
        else:
            continue
        agenda_id = item.get("id", "?")
        scores = item.get("scores", {})
        candidates.append({
            "candidate_id": agenda_id,
            "agenda_id": agenda_id,
            "title": item.get("title", ""),
            "type": item.get("type", ""),
            "maturity_score": scores.get("maturity_score", 0),
            "evidence_strength": scores.get("evidence_strength", 0),
            "action": action_type,
            "status": status,
            "candidate_kind": candidate_kind,
            "source_decision": decision,
            "requires_owner_approval": True,
            "proposal_queue_write": False,
            "evidence_count": item.get("counters", {}).get("evidence_count", 0),
            "qualified_evidence_count": scores.get("qualified_evidence_count", 0),
            "actionable_qualified_count": scores.get("actionable_qualified_count", 0),
            "observation_days": item.get("counters", {}).get("observation_days", 0),
            "suggested_message": _build_suggested_message(item),
            "generated_at": now,
        })

    data = {
        "version": 3,
        "generated_at": now,
        "shadow_mode": True,
        "candidates": candidates,
    }
    if write:
        write_yaml(CANDIDATES_FILE, data)
    return candidates


def _build_suggested_message(item: dict) -> str:
    scores = item.get("scores", {})
    ev = item.get("evidence", [])
    top_evidence = ev[-3:] if ev else []
    lines = [
        f"议题：{item.get('title', '')}",
        f"观察窗口：{item.get('counters', {}).get('observation_days', 0)} 天",
        f"证据：{len(ev)} 条 (qualified: {scores.get('qualified_evidence_count', 0)})",
        f"成熟分数：{scores.get('maturity_score', 0):.2f}",
        "",
        "主要证据：",
    ]
    for e in top_evidence:
        q = "✅" if e.get("qualified", True) else "⛔"
        lines.append(f"- {q} {e.get('source', '?')}: {e.get('summary', '')[:100]}")
    return "\n".join(lines)


def count_mentions(item: dict, signals: list[dict]) -> int:
    matchers = item.get("evidence_matchers", {})
    include_kw = matchers.get("include_keywords", [])
    if not include_kw:
        return 0
    count = 0
    cutoff = datetime.now(TZ) - timedelta(days=7)
    for sig in signals:
        ts_str = sig.get("ts", "")
        if ts_str:
            try:
                if datetime.fromisoformat(ts_str) < cutoff:
                    continue
            except (ValueError, TypeError):
                continue
        text = json.dumps(sig, ensure_ascii=False)
        if any(kw.lower() in text.lower() for kw in include_kw):
            count += 1
    return count


# ── Main ──

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mature self-evolution agenda items from collected signals."
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing agenda state.")
    parser.add_argument("--write-journal", action="store_true", help="Write the maturation journal.")
    parser.add_argument("--emit-candidates", action="store_true", help="Use candidate-ready journal mode.")
    parser.add_argument("--explain-scores", action="store_true", help="Print and write score explanations.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    args = parse_args(argv)
    explain_mode = args.explain_scores
    dry_run = args.dry_run
    write_journal_flag = args.write_journal
    emit_flag = args.emit_candidates or write_journal_flag

    agenda = load_yaml(AGENDA_FILE)
    items = agenda.get("agenda_items", [])
    signals = load_signals(days=3)
    signals.reverse()  # Newest first: ensures recent session signals match before old config_change
    proposals = load_proposals()

    print(f"Agenda Maturation Engine — V1.4.1c")
    print(f"Mode: {'explain' if explain_mode else 'dry-run' if dry_run else 'live'} ({'shadow' if not emit_flag else 'candidate-ready'} mode)")
    print(f"Loaded: {len(items)} agenda items, {len(signals)} signals (3d), {len(proposals)} proposals")
    print()

    if explain_mode:
        explanation = explain_scores(items, signals, proposals)
        print_explanation(explanation)
        filepath = write_score_explanations(explanation)
        print(f"\n  Score explanation saved to {filepath}")
        if not dry_run:
            print(f"\n  Note: Running --explain-scores without --dry-run will also update self_agenda.yaml")
        return

    changed_ids = []
    decisions = {}
    old_scores = {}
    total_duplicates = 0

    for item in items:
        iid = item.get("id", "?")
        title = item.get("title", "")[:50]

        # Skip terminal items — no need to re-score archived/resolved/surfaced
        if item.get("status") in ("archived", "resolved", "surfaced"):
            continue

        old_scores[iid] = item.get("scores", {}).get("maturity_score", 0)

        first_seen = item.get("first_seen_at")
        if first_seen:
            try:
                seen = datetime.fromisoformat(first_seen)
                item.setdefault("counters", {})["observation_days"] = max(
                    1, (datetime.now(TZ) - seen).days
                )
            except (ValueError, TypeError):
                pass

        new_ev, skipped = match_evidence(item, signals, proposals)
        total_duplicates += skipped
        if new_ev:
            item.setdefault("evidence", []).extend(new_ev)
            item["last_evidence_at"] = now_iso()
            item.setdefault("counters", {})["evidence_count"] = len(item["evidence"])

        stats = _calc_evidence_stats(item)
        total_duplicates += stats["duplicate_count"]

        item.setdefault("counters", {})["recent_mentions_7d"] = count_mentions(item, signals)

        # V1.4.1b: Store qualified count in counters
        item.setdefault("counters", {})["qualified_evidence_count"] = stats["qualified_count"]

        scores = calculate_scores(item)
        item["scores"] = scores

        decision = advance_state(item, scores)
        decisions[iid] = decision
        item["_decision"] = decision

        state_map = {
            "accumulating_evidence": "accumulating_evidence",
            "candidate_ready": "candidate_ready",
            "archived": "archived",
            "archived_": "archived",
            "surface_in_digest_for_review": "review_pending",
        }
        if decision in state_map and not dry_run:
            item["status"] = state_map[decision]
        elif decision == "candidate_ready" and dry_run:
            pass

        new_score = scores["maturity_score"]
        old_score = old_scores[iid]
        if abs(new_score - old_score) > 0.01 or new_ev:
            changed_ids.append(iid)

        status_icon = {
            "continue_observing": "⏳",
            "accumulating_evidence": "📊",
            "candidate_ready": "✅",
            "archived": "🗄️",
            "bypass_maturation_to_speak_gate": "⚠️",
            "surface_in_digest_for_review": "📋",
            "archive_candidate": "🗑️",
            "generate_quality_proposal": "🎯",
        }.get(decision, "⏳")
        delta = scores["maturity_score"] - old_score
        arrow = "↑" if delta > 0.01 else ("↓" if delta < -0.01 else "→")
        qc = scores.get("qualified_evidence_count", 0)
        aqc = scores.get("actionable_qualified_count", 0)
        hq = scores.get("has_qualified_evidence", False)
        haq = scores.get("has_actionable_evidence", False)
        ceiling_mark = " [ceil]" if not hq else (" [aq cap]" if not haq else "")
        print(f"  {status_icon} {iid} ({title}): {old_score:.2f} {arrow} {scores['maturity_score']:.2f} | "
              f"ev={item.get('counters',{}).get('evidence_count',0)} "
              f"(uq={scores.get('unique_evidence_count',0)}, "
              f"qual={qc}, "
              f"aq={aqc}, "
              f"dup={scores.get('duplicate_evidence_count',0)}){ceiling_mark} | {decision}")

    print()

    candidates = emit_candidates(items, write=not dry_run)
    if candidates:
        print(f"  Candidates emitted: {len(candidates)}")
        for c in candidates:
            print(f"    - {c['agenda_id']}: {c['title']} (score={c['maturity_score']:.2f}, action={c['action']})")
    else:
        print("  No candidates ready (V1.4.1c gate active)")

    explanation = None
    explanation_path = ""
    if not dry_run:
        explanation = explain_scores(items, signals, proposals)
        explanation_path = str(write_score_explanations(explanation))
        print(f"\n  Score explanation saved to {explanation_path}")

    if not dry_run:
        agenda["updated_at"] = now_iso()
        write_yaml(AGENDA_FILE, agenda)

    if write_journal_flag or (not dry_run):
        mode = "shadow" if not emit_flag else "candidate-ready"
        write_journal(items, changed_ids, candidates, decisions, old_scores,
                      mode=mode, total_duplicates=total_duplicates,
                      explanation_path=explanation_path)
        print(f"\n  Journal written to {JOURNAL_FILE}")

    ready_count = sum(1 for c in candidates if c.get("status") == "candidate_ready")
    print(f"\n{'='*50}")
    print(f"Summary: {len(items)} items, {len(changed_ids)} changed, {ready_count} ready, "
          f"{total_duplicates} duplicates, V1.4.1c gate active")
    print(f"{'DRY RUN' if dry_run else 'LIVE'} | V1.4.1c")


if __name__ == "__main__":
    main()
