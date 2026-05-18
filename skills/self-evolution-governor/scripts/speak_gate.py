#!/usr/bin/env python3
"""
Self-Evolution Governor - Speak Gate V1.2 + Phase 2 (Agenda Candidates).
Two-score system: priority_score + speak_score.
Phase 2 adds --include-agenda-candidates for agenda_speak controlled activation.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))

# ── Constants ──────────────────────────────────────────────────────────

RISK_DAMPENER = {
    "none": 1.00, "low": 0.97, "medium": 0.82,
    "high": 0.55, "critical": 0.00,
}

WEIGHT_IMPACT = 0.40
WEIGHT_RECURRENCE = 0.25
WEIGHT_CONFIDENCE = 0.35
INTERRUPTION_COST = 0.20
STRATEGIC_BONUS = 0.12
URGENCY_BONUS = 0.15

SPEAK_THRESHOLD = 0.60
PRIORITY_QUEUE_THRESHOLD = 0.60
DAILY_DIGEST_THRESHOLD = 0.40

DAILY_SUGGESTION_LIMIT = 3
DAILY_STRATEGIC_LIMIT = 1

QUOTA_FILE = "/vol1/.hermes/state/evolution/speak_quota.json"
AGENDA_QUOTA_FILE = "/vol1/.hermes/state/evolution/agenda_speak_quota.json"
AGENDA_CANDIDATES_FILE = "/vol1/.hermes/state/evolution/agenda_candidates.yaml"
AGENDA_DECISIONS_FILE = "/vol1/.hermes/state/evolution/agenda_speak_decisions.yaml"
JOURNAL_FILE = "/vol1/.hermes/state/evolution/evolution_journal.md"

# Default agenda_speak mode (override via AGENDA_SPEAK_MODE env var)
DEFAULT_AGENDA_SPEAK_MODE = "controlled"

# Fixed agenda type → action mapping
AGENDA_TYPE_ACTION_MAP = {
    "strategic_positioning": "ask_user_confirmation",
    "automation_opportunity": "proposal_preview",
    "quality_improvement": "proposal_preview",
    "cleanup_candidate": "digest_only",
    "risk_watch": "bypass_agenda_channel",
}

# Type-specific min_evidence_strength thresholds
TYPE_MIN_EVIDENCE_STRENGTH = {
    "strategic_positioning": 0.30,
    "automation_opportunity": 0.25,
    "quality_improvement": 0.25,
    "cleanup_candidate": 0.25,
    "risk_watch": 0.20,
}

AGENDA_DAILY_SURFACE_LIMIT = 1
AGENDA_COOLDOWN_DAYS = 7
ACTIVE_CANDIDATE_STATUSES = {
    "candidate_ready",
    "quality_proposal_ready",
    "new_agenda_preview_ready",
}

# ── Helpers ────────────────────────────────────────────────────────────


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def now_date() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def load_json(path: str, default: dict = None) -> dict:
    if default is None:
        default = {}
    if os.path.exists(path):
        with open(path) as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return default
    return default


def save_json(path: str, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def append_journal(entry: str):
    """Append a timestamped entry to evolution_journal.md."""
    ts = now_iso()
    text = f"\n### Speak Gate Phase 2 — {now_date()}\n**Time:** {ts}\n\n{entry}\n"
    try:
        with open(JOURNAL_FILE, "a") as f:
            f.write(text)
    except Exception:
        pass


def load_yaml_simple(path: str) -> dict | None:
    """Load agenda_candidates.yaml with a real YAML parser."""
    if not os.path.exists(path):
        return None
    try:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        return {"candidates": [], "_load_error": str(exc)}

    if not isinstance(data, dict):
        return {"candidates": []}

    candidates = data.get("candidates")
    if candidates is None:
        return data
    if not isinstance(candidates, list):
        data["candidates"] = []
    else:
        data["candidates"] = [c for c in candidates if isinstance(c, dict)]
    return data


def _parse_yaml_val(val: str):
    """Parse YAML value to appropriate Python type."""
    val = val.strip().strip("'\"")
    if val.replace(".", "", 1).replace("-", "", 1).isdigit():
        if "." in val:
            return float(val)
        return int(val)
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    if val.lower() == "null" or val == "~":
        return None
    return val


def load_yaml_flat(path: str) -> dict | None:
    """Load score_explanations YAML-like file."""
    if not os.path.exists(path):
        return None
    result = {}
    with open(path) as f:
        content = f.read()
    # Simple line-by-line parser
    stack = [result]
    current_key = None
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if val == "" or val.startswith("#"):
                # Sub-object
                new_obj = {}
                if current_key:
                    stack[-1][current_key] = new_obj
                stack.append(new_obj)
                current_key = key
            else:
                parent = stack[-1]
                # Try to parse as number or bool
                if val.lower() == "true":
                    parent[key] = True
                elif val.lower() == "false":
                    parent[key] = False
                elif val.lower() == "null":
                    parent[key] = None
                else:
                    try:
                        if "." in val:
                            parent[key] = float(val)
                        else:
                            parent[key] = int(val)
                    except ValueError:
                        parent[key] = val.strip("'\"")
        elif stripped.startswith("- "):
            # List item
            if current_key and isinstance(stack[-1].get(current_key), list):
                stack[-1][current_key].append(stripped[2:].strip().strip("'\""))
    return result


# ── Quota ──────────────────────────────────────────────────────────────


def load_quota() -> dict:
    data = load_json(QUOTA_FILE, {})
    today = now_date()
    if data.get("date") != today:
        data = {"date": today, "suggestions": 0, "strategic": 0}
    return data


def save_quota(data: dict):
    save_json(QUOTA_FILE, data)


def load_agenda_quota() -> dict:
    data = load_json(AGENDA_QUOTA_FILE, {})
    today = now_date()
    if data.get("date") != today:
        data = {"date": today, "surfaces": 0, "surface_history": {}}
    return data


def save_agenda_quota(data: dict):
    save_json(AGENDA_QUOTA_FILE, data)


# ── Scoring (V1.2, unchanged) ──────────────────────────────────────────


def score_evolution_proposal(
    impact: float,
    recurrence: float,
    confidence: float,
    risk_level: str = "none",
    is_strategic: bool = False,
    is_urgent: bool = False,
    repeat_penalty: float = 0.0,
) -> tuple:
    reasons = []
    weighted = (impact * WEIGHT_IMPACT
                + recurrence * WEIGHT_RECURRENCE
                + confidence * WEIGHT_CONFIDENCE)
    weighted = round(weighted, 4)
    reasons.append(
        f"weighted = {impact}×0.40 + {recurrence}×0.25 + {confidence}×0.35 = {weighted}"
    )

    dampener = RISK_DAMPENER.get(risk_level, 0.55)
    dampened = round(weighted * dampener, 4)
    reasons.append(f"× risk_dampener[{risk_level}={dampener}] → {dampened}")

    s_bonus = STRATEGIC_BONUS if is_strategic else 0.0
    u_bonus = URGENCY_BONUS if is_urgent else 0.0
    bonus_total = s_bonus + u_bonus
    bonus_parts = []
    if s_bonus:
        bonus_parts.append(f"strategic=+{s_bonus}")
    if u_bonus:
        bonus_parts.append(f"urgent=+{u_bonus}")
    if bonus_parts:
        reasons.append(f"+ bonuses: {' + '.join(bonus_parts)} = +{bonus_total}")
    else:
        reasons.append("+ bonuses: none")

    priority_raw = dampened + bonus_total
    priority_clamped = max(0.0, min(1.0, priority_raw))
    priority_final = round(priority_clamped, 4)

    if priority_raw > 1.0:
        reasons.append(f"priority_raw = {round(priority_raw, 4)} → clamped to 1.0 (cap)")
    else:
        reasons.append(f"priority_score = {priority_final}")
    reasons.append(f"  │ >= {PRIORITY_QUEUE_THRESHOLD} (queue)     {'✓' if priority_final >= PRIORITY_QUEUE_THRESHOLD else '✗'}")
    reasons.append(f"  │ >= {DAILY_DIGEST_THRESHOLD} (digest)     {'✓' if priority_final >= DAILY_DIGEST_THRESHOLD else '✗'}")

    speak_raw = priority_raw - INTERRUPTION_COST - repeat_penalty
    speak_clamped = max(0.0, min(1.0, speak_raw))
    speak_final = round(speak_clamped, 4)

    if speak_raw < 0:
        reasons.append(
            f"speak = priority_raw - {INTERRUPTION_COST}{' - '+str(repeat_penalty) if repeat_penalty > 0 else ''} = {round(speak_raw, 4)} → clamped to {speak_final}"
        )
    else:
        reasons.append(
            f"speak_score = priority{'' if priority_final == priority_raw else '_raw'} - {INTERRUPTION_COST}{' - '+str(repeat_penalty) if repeat_penalty > 0 else ''} = {speak_final}"
        )
    reasons.append(f"  │ >= {SPEAK_THRESHOLD} (speak)         {'✓' if speak_final >= SPEAK_THRESHOLD else '✗'}")

    return priority_final, speak_final, reasons


def decide_action(
    priority_score: float,
    speak_score: float,
    risk_level: str,
    actionability: float,
    is_urgent: bool = False,
) -> tuple:
    reasons = []

    if is_urgent:
        reasons.append("urgent=true → bypass all gates")
        reasons.append("action: speak_now_risk_alert")
        return "speak_now_risk_alert", reasons

    if risk_level == "critical":
        reasons.append("risk_level=critical → alert only, do not act")
        reasons.append("action: risk_alert_only")
        return "risk_alert_only", reasons

    speak_pass = speak_score >= SPEAK_THRESHOLD
    actionability_pass = actionability >= SPEAK_THRESHOLD

    if speak_pass and actionability_pass:
        if risk_level in ("medium", "high"):
            reasons.append(
                f"speak_score({speak_score}) >= {SPEAK_THRESHOLD} ✓, "
                f"actionability({actionability}) >= {SPEAK_THRESHOLD} ✓, "
                f"risk_level({risk_level}) requires approval"
            )
            reasons.append("action: speak_now_with_approval")
            return "speak_now_with_approval", reasons
        else:
            reasons.append(
                f"speak_score({speak_score}) >= {SPEAK_THRESHOLD} ✓, "
                f"actionability({actionability}) >= {SPEAK_THRESHOLD} ✓, "
                f"risk_level({risk_level}) → safe to speak directly"
            )
            reasons.append("action: speak_now")
            return "speak_now", reasons

    if not speak_pass:
        reasons.append(
            f"speak_score({speak_score}) < {SPEAK_THRESHOLD}: "
            f"interruption_cost({INTERRUPTION_COST}) exceeds available score headroom"
        )
    if not actionability_pass:
        reasons.append(
            f"actionability({actionability}) < {SPEAK_THRESHOLD}: "
            f"proposal lacks concrete actionable content"
        )

    if priority_score >= PRIORITY_QUEUE_THRESHOLD:
        reasons.append(f"priority_score({priority_score}) >= {PRIORITY_QUEUE_THRESHOLD}: enter proposal queue")
        reasons.append("action: proposal_queue")
        return "proposal_queue", reasons

    if priority_score >= DAILY_DIGEST_THRESHOLD:
        reasons.append(f"priority_score({priority_score}) >= {DAILY_DIGEST_THRESHOLD}: enter daily digest")
        reasons.append("action: daily_digest")
        return "daily_digest", reasons

    reasons.append(f"priority_score({priority_score}) < {DAILY_DIGEST_THRESHOLD}: below all thresholds")
    reasons.append("action: silent_log_only")
    return "silent_log_only", reasons


def apply_quota(action: str, is_strategic: bool, quota: dict) -> tuple:
    if action not in ("speak_now", "speak_now_with_approval", "speak_now_risk_alert"):
        return action, action, "no_quota_needed"
    if is_strategic:
        if quota["strategic"] >= DAILY_STRATEGIC_LIMIT:
            return "proposal_queue", action, "strategic_quota_exceeded"
        quota["strategic"] += 1
        return action, action, "speak_approved"
    if quota["suggestions"] >= DAILY_SUGGESTION_LIMIT:
        return "proposal_queue", action, "suggestion_quota_exceeded"
    quota["suggestions"] += 1
    return action, action, "speak_approved"


# ── Phase 2: Agenda Candidates Processing ──────────────────────────────


def get_agenda_speak_mode() -> str:
    env_mode = os.environ.get("AGENDA_SPEAK_MODE", "").strip().lower()
    if env_mode in ("disabled", "dry_run", "controlled", "active"):
        return env_mode
    return DEFAULT_AGENDA_SPEAK_MODE


def process_agenda_candidates() -> dict:
    """
    Phase 2: Process agenda_candidates.yaml through the secondary gate.
    Returns agenda_speak_decisions dict.
    """
    mode = get_agenda_speak_mode()

    result = {
        "version": 1,
        "generated_at": now_iso(),
        "agenda_speak_mode": mode,
        "allow_external_send": False,
        "candidates_found": 0,
        "decisions": [],
        "quota": {},
        "summary": {},
    }

    # Load candidates
    candidates_data = load_yaml_simple(AGENDA_CANDIDATES_FILE)
    if not candidates_data or "candidates" not in candidates_data:
        candidates = []
    else:
        candidates = candidates_data["candidates"]

    result["candidates_found"] = len(candidates)

    # ── Empty candidates: clean exit ──
    if not candidates:
        result["summary"] = {
            "total_candidates": 0, "surfaced": 0, "suppressed": 0,
            "note": "no candidates to process",
        }
        _write_agenda_decisions(result)
        if mode != "disabled":
            append_journal(
                f"- **Agenda candidates:** 0 found — no candidates to process\n"
                f"- **Mode:** {mode}\n"
                f"- **Result:** empty (no decisions needed)"
            )
        return result

    # ── Load quotas ──
    quota = load_agenda_quota()

    decisions = []
    surfaced_count = 0
    suppressed_count = 0

    for candidate in candidates:
        decision = _evaluate_single_candidate(candidate, quota, mode)
        decisions.append(decision)
        if decision.get("decision") == "surface":
            surfaced_count += 1
        else:
            suppressed_count += 1

    save_agenda_quota(quota)

    result["decisions"] = decisions
    result["quota"] = {
        "date": quota["date"],
        "surfaces_used_today": quota["surfaces"],
        "surface_history": quota["surface_history"],
    }
    result["summary"] = {
        "total_candidates": len(candidates),
        "surfaced": surfaced_count,
        "suppressed": suppressed_count,
        "candidate_kind_counts": _candidate_kind_counts(decisions),
        "surfaced_candidates": _decision_summaries(
            [item for item in decisions if item.get("decision") == "surface"]
        ),
        "ready_not_surfaced_candidates": _decision_summaries(
            [
                item
                for item in decisions
                if item.get("decision") != "surface"
                and item.get("candidate_status") in (
                    "candidate_ready",
                    "quality_proposal_ready",
                    "new_agenda_preview_ready",
                )
            ]
        ),
    }

    _write_agenda_decisions(result)

    # ── Journal ──
    if mode != "disabled":
        lines = [
            f"- **Agenda candidates:** {len(candidates)} processed → {surfaced_count} surfaced, {suppressed_count} suppressed",
            f"- **Mode:** {mode}",
            f"- **Quota:** {quota['surfaces']}/{AGENDA_DAILY_SURFACE_LIMIT} surfaces used today",
        ]
        for d in decisions:
            status = "🟢 SURFACE" if d["decision"] == "surface" else "🔴 SUPPRESSED"
            lines.append(f"  {status} | {d['candidate_id']} ({d['title']}) → {d['mapped_action']}")
            lines.append(f"    Reason: {d['reason']}")
        append_journal("\n".join(lines))

    return result


def _evaluate_single_candidate(candidate: dict, quota: dict, mode: str) -> dict:
    """
    Evaluate one agenda candidate through the secondary gate.
    Returns a full decision dict including reason even when suppressed.
    """
    candidate_id = candidate.get("candidate_id") or candidate.get("id") or candidate.get("agenda_id") or "unknown"
    title = candidate.get("title", "")
    ag_type = candidate.get("type", "")
    candidate_kind = candidate.get("candidate_kind") or "agenda_candidate"
    candidate_status = candidate.get("status") or ""
    candidate_action = candidate.get("action") or ""
    maturity_score = candidate.get("maturity_score", 0)
    evidence_strength = candidate.get("evidence_strength", 0)
    actionable_qualified_count = candidate.get("actionable_qualified_count", 0)

    if candidate_status not in ACTIVE_CANDIDATE_STATUSES:
        return {
            "candidate_id": candidate_id,
            "title": title,
            "type": ag_type,
            "candidate_kind": candidate_kind,
            "candidate_status": candidate_status,
            "candidate_action": candidate_action,
            "proposal_queue_write": bool(candidate.get("proposal_queue_write", False)),
            "decision": "ignored",
            "reason": f"inactive candidate status: {candidate_status}",
            "mapped_action": "none (inactive)",
            "secondary_gate": {
                "actionable_qualified_count": actionable_qualified_count,
                "actionable_qualified_count_pass": False,
                "evidence_strength": evidence_strength,
                "min_evidence_strength": TYPE_MIN_EVIDENCE_STRENGTH.get(ag_type, 0.25),
                "evidence_strength_pass": False,
                "cooldown_passed": True,
                "cooldown_days_remaining": 0,
                "quota_available": True,
                "is_risk_watch": ag_type == "risk_watch",
                "maturity_score": maturity_score,
            },
            "message_preview": None,
            "quota": {"would_consume": False, "consumed": False},
        }

    mapped_action = AGENDA_TYPE_ACTION_MAP.get(ag_type, "digest_only")
    is_quality_proposal = (
        candidate.get("status") == "quality_proposal_ready"
        or candidate.get("action") == "generate_quality_proposal"
    )
    if is_quality_proposal:
        mapped_action = "quality_proposal_preview"
    if candidate_kind == "new_agenda" or candidate.get("status") == "new_agenda_preview_ready":
        mapped_action = "new_agenda_preview"
    reason_parts = []

    # ── risk_watch: bypass agenda channel ──
    if ag_type == "risk_watch" and not is_quality_proposal:
        return {
            "candidate_id": candidate_id,
            "title": title,
            "type": ag_type,
            "candidate_kind": candidate_kind,
            "candidate_status": candidate_status,
            "candidate_action": candidate_action,
            "decision": "bypass",
            "reason": "risk_watch: bypass_agenda_channel — routed to direct risk alert path",
            "mapped_action": "bypass_agenda_channel",
            "secondary_gate": {
                "actionable_qualified_count": actionable_qualified_count,
                "actionable_qualified_count_pass": False,
                "evidence_strength": evidence_strength,
                "evidence_strength_pass": False,
                "is_risk_watch": True,
            },
            "message_preview": None,
            "quota": {"would_consume": False},
        }

    # ── Secondary gate ──
    gate = {}

    # 1. actionable_qualified_count >= 2
    aq_pass = actionable_qualified_count >= 2
    gate["actionable_qualified_count"] = actionable_qualified_count
    gate["actionable_qualified_count_pass"] = aq_pass
    if not aq_pass:
        reason_parts.append(f"actionable_qualified_count({actionable_qualified_count}) < 2")

    # 2. evidence_strength meets type threshold
    min_ev = TYPE_MIN_EVIDENCE_STRENGTH.get(ag_type, 0.25)
    ev_pass = evidence_strength >= min_ev
    gate["evidence_strength"] = evidence_strength
    gate["min_evidence_strength"] = min_ev
    gate["evidence_strength_pass"] = ev_pass
    if not ev_pass:
        reason_parts.append(f"evidence_strength({evidence_strength}) < {min_ev} (min for {ag_type})")

    # 3. Cooldown: 7 days since last surface
    history = quota.get("surface_history", {})
    last_surfaced = history.get(candidate_id, {}).get("last_surfaced_at", "")
    cooldown_passed = True
    cooldown_remaining = 0
    if last_surfaced:
        try:
            last_date = datetime.strptime(last_surfaced[:10], "%Y-%m-%d")
            today = datetime.strptime(now_date(), "%Y-%m-%d")
            days_since = (today - last_date).days
            cooldown_remaining = max(0, AGENDA_COOLDOWN_DAYS - days_since)
            cooldown_passed = days_since >= AGENDA_COOLDOWN_DAYS
        except (ValueError, IndexError):
            pass
    gate["cooldown_passed"] = cooldown_passed
    gate["cooldown_days_remaining"] = cooldown_remaining
    if not cooldown_passed:
        reason_parts.append(f"cooldown: {cooldown_remaining}d remaining (need {AGENDA_COOLDOWN_DAYS}d)")

    # 4. Daily quota
    quota_available = quota.get("surfaces", 0) < AGENDA_DAILY_SURFACE_LIMIT
    gate["quota_available"] = quota_available
    gate["daily_surfaces_used"] = quota.get("surfaces", 0)
    gate["daily_surface_limit"] = AGENDA_DAILY_SURFACE_LIMIT
    if not quota_available:
        reason_parts.append(f"daily surface quota exhausted ({quota.get('surfaces', 0)}/{AGENDA_DAILY_SURFACE_LIMIT})")

    gate["is_risk_watch"] = False
    gate["maturity_score"] = maturity_score

    # ── Decision ──
    all_pass = aq_pass and ev_pass and cooldown_passed and quota_available
    is_surface = all_pass

    if is_surface:
        if mode in ("controlled", "active"):
            _consume_agenda_quota(quota, candidate_id)
        reason = "all secondary gates passed"
    else:
        reason = "; ".join(reason_parts) if reason_parts else "suppressed by secondary gate"

    # message_preview: generated for all non-disabled modes (debugging/audit)
    # In controlled: preview is generated but NOT sent externally
    message_preview = None
    if mode != "disabled":
        message_preview = _generate_message_preview(candidate, mapped_action)

    return {
        "candidate_id": candidate_id,
        "title": title,
        "type": ag_type,
        "candidate_kind": candidate_kind,
        "candidate_status": candidate_status,
        "candidate_action": candidate_action,
        "proposal_queue_write": bool(candidate.get("proposal_queue_write", False)),
        "decision": "surface" if is_surface else "suppressed",
        "reason": reason,
        "mapped_action": mapped_action if is_surface else "none (suppressed)",
        "secondary_gate": gate,
        "message_preview": message_preview,
        "quota": {
            "would_consume": is_surface,
            "consumed": is_surface and mode in ("controlled", "active"),
        },
    }


def _decision_summaries(decisions: list[dict]) -> list[dict]:
    result = []
    for item in decisions:
        result.append(
            {
                "candidate_id": item.get("candidate_id", ""),
                "title": item.get("title", ""),
                "candidate_kind": item.get("candidate_kind", ""),
                "candidate_status": item.get("candidate_status", ""),
                "candidate_action": item.get("candidate_action", ""),
                "mapped_action": item.get("mapped_action", ""),
                "decision": item.get("decision", ""),
                "reason": item.get("reason", ""),
            }
        )
    return result


def _candidate_kind_counts(decisions: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in decisions:
        key = str(item.get("candidate_kind") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _consume_agenda_quota(quota: dict, candidate_id: str):
    quota["surfaces"] = quota.get("surfaces", 0) + 1
    history = quota.setdefault("surface_history", {})
    entry = history.get(candidate_id, {})
    entry["last_surfaced_at"] = now_iso()
    entry["surface_count"] = entry.get("surface_count", 0) + 1
    history[candidate_id] = entry


def _generate_message_preview(candidate: dict, mapped_action: str) -> str | None:
    title = candidate.get("title", "")
    ag_type = candidate.get("type", "")
    question = candidate.get("question", candidate.get("summary", ""))
    evidence_count = candidate.get("evidence_count", 0)
    score = candidate.get("maturity_score", 0)

    if mapped_action == "digest_only":
        return (
            f"[digest_only] agenda '{title}' has sufficient evidence ({evidence_count}) "
            f"but is cleanup_candidate — included in daily digest only."
        )
    elif mapped_action == "ask_user_confirmation":
        return (
            f"[ask_user_confirmation] 我观察到一个值得关注的方向：\n"
            f"• 议题：{title}\n"
            f"• 问题：{question}\n"
            f"• 证据强度：{evidence_count} 条信号，成熟度 {score}\n"
            f"• 建议：请在适当时确认当前优先级方向\n"
            f"• 风险：低\n"
            f"• 是否需要你批准：是 — 需要你确认是否继续关注此方向"
        )
    elif mapped_action == "proposal_preview":
        return (
            f"[proposal_preview] 自进化发现可改进点：\n"
            f"• 议题：{title}\n"
            f"• 问题：{question}\n"
            f"• 证据：{evidence_count} 条信号\n"
            f"• 建议：考虑将此方向转化为具体改进提案"
        )
    elif mapped_action == "quality_proposal_preview":
        return (
            f"[quality_proposal_preview] Monitoring trend is ready for owner approval:\n"
            f"• Agenda: {title}\n"
            f"• Question: {question}\n"
            f"• Evidence: {evidence_count} signals\n"
            f"• Recommendation: approve or reject drafting a concrete quality proposal\n"
            f"• Proposal queue write: no automatic write before owner approval"
        )
    elif mapped_action == "new_agenda_preview":
        why_not_existing = candidate.get("why_not_existing_agenda", "")
        return (
            f"[new_agenda_preview] 新议题候选：\n"
            f"• 议题：{title}\n"
            f"• 问题：{question}\n"
            f"• 证据：{evidence_count} 条安全引用信号\n"
            f"• 为什么不是现有议题：{why_not_existing}\n"
            f"• 建议：进入观察/新增议题预览；这不是执行，也不会自动 approved"
        )
    return f"[{mapped_action}] agenda '{title}' — {question}"


# ── YAML output ────────────────────────────────────────────────────────


def _write_agenda_decisions(data: dict):
    """Write agenda_speak_decisions.yaml for full audit trail."""
    lines = [
        f"version: {data['version']}",
        f"generated_at: '{data['generated_at']}'",
        f"agenda_speak_mode: {data['agenda_speak_mode']}",
        f"allow_external_send: {str(data.get('allow_external_send', False)).lower()}",
        f"candidates_found: {data['candidates_found']}",
        "decisions:",
    ]

    if not data.get("decisions"):
        lines.append("  []")
    else:
        for d in data["decisions"]:
            lines.append(f"  - candidate_id: '{d['candidate_id']}'")
            lines.append(f"    title: '{d['title']}'")
            lines.append(f"    type: '{d['type']}'")
            lines.append(f"    candidate_kind: '{d.get('candidate_kind', '')}'")
            lines.append(f"    candidate_status: '{d.get('candidate_status', '')}'")
            lines.append(f"    candidate_action: '{d.get('candidate_action', '')}'")
            lines.append(f"    decision: {d['decision']}")
            lines.append(f"    reason: '{d['reason']}'")
            lines.append(f"    mapped_action: '{d['mapped_action']}'")
            lines.append("    secondary_gate:")
            sg = d.get("secondary_gate", {})
            lines.append(f"      actionable_qualified_count: {sg.get('actionable_qualified_count', 0)}")
            lines.append(f"      actionable_qualified_count_pass: {str(sg.get('actionable_qualified_count_pass', False)).lower()}")
            lines.append(f"      evidence_strength: {sg.get('evidence_strength', 0)}")
            lines.append(f"      min_evidence_strength: {sg.get('min_evidence_strength', 0.25)}")
            lines.append(f"      evidence_strength_pass: {str(sg.get('evidence_strength_pass', False)).lower()}")
            lines.append(f"      cooldown_passed: {str(sg.get('cooldown_passed', True)).lower()}")
            lines.append(f"      cooldown_days_remaining: {sg.get('cooldown_days_remaining', 0)}")
            lines.append(f"      quota_available: {str(sg.get('quota_available', True)).lower()}")
            lines.append(f"      is_risk_watch: {str(sg.get('is_risk_watch', False)).lower()}")
            lines.append(f"    message_preview: {json.dumps(d.get('message_preview'), ensure_ascii=False) if d.get('message_preview') else 'null'}")

    lines.append("")
    lines.append("summary:")
    s = data.get("summary", {})
    lines.append(f"  total_candidates: {s.get('total_candidates', 0)}")
    lines.append(f"  surfaced: {s.get('surfaced', 0)}")
    lines.append(f"  suppressed: {s.get('suppressed', 0)}")
    lines.append("  candidate_kind_counts:")
    for key, value in (s.get("candidate_kind_counts") or {}).items():
        lines.append(f"    {key}: {value}")
    for field in ("surfaced_candidates", "ready_not_surfaced_candidates"):
        lines.append(f"  {field}:")
        items = s.get(field) or []
        if not items:
            lines.append("    []")
            continue
        for item in items:
            lines.append(f"    - candidate_id: '{item.get('candidate_id', '')}'")
            lines.append(f"      title: '{item.get('title', '')}'")
            lines.append(f"      candidate_kind: '{item.get('candidate_kind', '')}'")
            lines.append(f"      candidate_status: '{item.get('candidate_status', '')}'")
            lines.append(f"      candidate_action: '{item.get('candidate_action', '')}'")
            lines.append(f"      mapped_action: '{item.get('mapped_action', '')}'")
            lines.append(f"      decision: '{item.get('decision', '')}'")
            lines.append(f"      reason: {json.dumps(item.get('reason', ''), ensure_ascii=False)}")

    with open(AGENDA_DECISIONS_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")


# ── Main ───────────────────────────────────────────────────────────────


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print("usage: speak_gate.py <proposal.json | --stdin> [--include-agenda-candidates]")
        return

    if "--include-agenda-candidates" in sys.argv:
        result = process_agenda_candidates()
        remaining = [a for a in sys.argv[1:] if a != "--include-agenda-candidates"]
        if remaining:
            _process_proposal(remaining, result)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if len(sys.argv) < 2:
        print(json.dumps({
            "error": "Usage: speak_gate.py <proposal.json | --stdin> [--include-agenda-candidates]",
            "version": "v1.2 + phase 2",
        }))
        sys.exit(1)

    _process_proposal(sys.argv[1:])


def _process_proposal(args: list, existing_result: dict = None):
    """Original V1.2 proposal scoring pipeline (unchanged)."""
    if args[0] == "--stdin":
        proposal = json.loads(sys.stdin.read())
    else:
        with open(args[0]) as f:
            proposal = json.loads(f.read())

    quota = load_quota()

    impact = proposal.get("impact", 0.5)
    recurrence = proposal.get("recurrence", 0.5)
    confidence = proposal.get("confidence", 0.5)
    risk_level = proposal.get("risk_level", "none")
    actionability = proposal.get("actionability", 0.5)
    is_strategic = proposal.get("type", "") == "strategic_reflection"
    is_urgent = proposal.get("urgent", False)
    repeat_penalty = proposal.get("repeat_penalty", 0.0)

    priority_score, speak_score, score_reasons = score_evolution_proposal(
        impact, recurrence, confidence, risk_level,
        is_strategic, is_urgent, repeat_penalty,
    )

    action, action_reasons = decide_action(
        priority_score, speak_score, risk_level,
        actionability, is_urgent,
    )

    final_action, original_action, quota_reason = apply_quota(action, is_strategic, quota)

    if quota_reason != "no_quota_needed" and quota_reason.startswith("speak"):
        save_quota(quota)

    would_have_spoken = (
        original_action in ("speak_now", "speak_now_with_approval", "speak_now_risk_alert")
        and final_action != original_action
    )

    decision_reason = score_reasons + [""] + action_reasons
    if "exceeded" in quota_reason:
        decision_reason.append(f"  ⚠ quota: {quota_reason} → downgraded to {final_action}")
    else:
        decision_reason.append(f"  quota: {quota_reason}")

    result = {
        "version": "v1.2",
        "ts": now_iso(),
        "proposal_title": proposal.get("title", ""),
        "proposal_type": proposal.get("type", ""),
        "priority_score": priority_score,
        "speak_score": speak_score,
        "speak_threshold": SPEAK_THRESHOLD,
        "actionability_threshold": SPEAK_THRESHOLD,
        "risk_level": risk_level,
        "actionability": actionability,
        "action": final_action,
        "would_have_spoken_without_quota": would_have_spoken,
        "decision_reason": decision_reason,
        "daily_suggestions_used": quota["suggestions"],
        "daily_strategic_used": quota["strategic"],
    }

    if existing_result is not None:
        existing_result["proposal_decision"] = result
        print(json.dumps(existing_result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
