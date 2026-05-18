#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

TZ = timezone(timedelta(hours=8))
HERMES_HOME = Path("/vol1/.hermes")
SESSIONS_DIR = HERMES_HOME / "sessions"
STATE_DIR = HERMES_HOME / "state" / "evolution"
PROPOSAL_QUEUE_FILE = STATE_DIR / "proposal_queue.yaml"
OUTPUT_FILE = STATE_DIR / "weekly_strategy_facts.json"

KEYWORDS = ["三奶", "Sannai", "memory", "记忆", "skill", "cron", "监控", "media", "douyin", "视频", "告警", "automation"]
AUTO_REVIEW_MARKERS = (
    "Review the conversation above and consider saving to memory",
    "Review the conversation above and update the skill library",
)
REPORT_CONSTRAINTS = {
    "final_response_must_start_with": "📊 本周战略观察",
    "forbid_model_process_preamble": True,
    "voice": "neutral_observer",
    "proposal_state_source": "/vol1/.hermes/state/evolution/proposal_queue.yaml",
    "do_not_infer_proposals_from": ["runtime_digest", "runtime_context", "model_judgment"],
    "no_second_person_blame": True,
    "priority_rule": "omit priority unless it comes from a structured scorer trace in weekly_strategy_facts.json",
}


def _parse_now(value: str | None = None) -> datetime:
    if value:
        return datetime.fromisoformat(value)
    return datetime.now(TZ)


def _session_date(path: Path) -> datetime | None:
    match = re.search(r"session(?:_cron_[^_]+)?_(\d{8})_", path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=TZ)
    except ValueError:
        return None


def _read_session(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _user_messages(data: dict[str, Any]) -> list[tuple[int, str]]:
    result = []
    for idx, message in enumerate(data.get("messages") or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content") or ""
        if isinstance(content, str) and content.strip():
            result.append((idx, content))
    return result


def _exclude_reason(path: Path, data: dict[str, Any], user_messages: list[tuple[int, str]]) -> str | None:
    if path.name.startswith("session_cron_"):
        return "cron"
    for _, content in user_messages:
        if any(marker in content for marker in AUTO_REVIEW_MARKERS):
            return "auto_review"
    if not user_messages:
        return "no_user_messages"
    return None


def _keyword_counts(messages: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    lower_messages = [message.lower() for message in messages]
    for keyword in KEYWORDS:
        if keyword.isascii():
            needle = keyword.lower()
            counts[keyword] = sum(message.count(needle) for message in lower_messages)
        else:
            counts[keyword] = sum(message.count(keyword) for message in messages)
    return counts


def _matched_terms(content: str) -> list[str]:
    lower = content.lower()
    matches = []
    for keyword in KEYWORDS:
        if keyword.isascii():
            if keyword.lower() in lower:
                matches.append(keyword)
        elif keyword in content:
            matches.append(keyword)
    return matches


def _signal_hash(session_id: str, message_index: int, content: str) -> str:
    payload = f"{session_id}:{message_index}:{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _proposal_entry(proposal: dict[str, Any]) -> dict[str, Any]:
    source = proposal.get("source") or {}
    scores = proposal.get("scores") or {}
    timestamps = proposal.get("timestamps") or {}
    return {
        "id": proposal.get("id", ""),
        "title": proposal.get("title", ""),
        "type": proposal.get("type", ""),
        "status": proposal.get("status", ""),
        "candidate_kind": source.get("candidate_kind", ""),
        "candidate_status": source.get("candidate_status", ""),
        "candidate_action": source.get("candidate_action", ""),
        "maturity_score": scores.get("maturity_score"),
        "evidence_count": scores.get("evidence_count"),
        "qualified_evidence_count": scores.get("qualified_evidence_count"),
        "actionable_qualified_count": scores.get("actionable_qualified_count"),
        "created_at": timestamps.get("created_at", ""),
        "expires_at": timestamps.get("expires_at", ""),
        "stale_reason": proposal.get("stale_reason", ""),
    }


def load_proposal_states() -> dict[str, Any]:
    result = {
        "source": str(PROPOSAL_QUEUE_FILE),
        "status_counts": {},
        "pending_user_approval": [],
        "stale_pending": [],
        "approved": [],
        "other": [],
        "parse_error": None,
    }
    try:
        data = yaml.safe_load(PROPOSAL_QUEUE_FILE.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        result["parse_error"] = str(exc)
        return result
    counts = Counter()
    for proposal in data.get("proposals") or []:
        if not isinstance(proposal, dict):
            continue
        status = proposal.get("status") or "unknown"
        counts[status] += 1
        entry = _proposal_entry(proposal)
        if status == "pending_user_approval":
            result["pending_user_approval"].append(entry)
        elif status == "stale_pending":
            result["stale_pending"].append(entry)
        elif status == "approved":
            result["approved"].append(entry)
        else:
            result["other"].append(entry)
    result["status_counts"] = dict(sorted(counts.items()))
    return result


def _empty_window(window_index: int, start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "window_index": window_index,
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
        "included_sessions": 0,
        "user_messages": 0,
        "keyword_counts": {keyword: 0 for keyword in KEYWORDS},
    }


def collect_session_facts(now_dt: datetime, days: int = 7) -> dict[str, Any]:
    included_messages: list[str] = []
    evidence_refs: list[dict[str, Any]] = []
    included_sessions = 0
    excluded = Counter()
    baseline = [
        _empty_window(i, now_dt - timedelta(days=(i + 1) * days), now_dt - timedelta(days=i * days))
        for i in range(4)
    ]

    for path in sorted(SESSIONS_DIR.glob("session*.json")):
        session_dt = _session_date(path)
        if session_dt is None:
            continue
        age_days = (now_dt.date() - session_dt.date()).days
        if age_days < 0 or age_days >= days * 4:
            continue

        data = _read_session(path)
        if data is None:
            excluded["parse_error"] += 1
            continue
        user_messages = _user_messages(data)
        reason = _exclude_reason(path, data, user_messages)
        if reason:
            excluded[reason] += 1
            continue

        window_index = age_days // days
        session_messages = [content for _, content in user_messages]
        baseline[window_index]["included_sessions"] += 1
        baseline[window_index]["user_messages"] += len(session_messages)
        window_counts = _keyword_counts(session_messages)
        for keyword, count in window_counts.items():
            baseline[window_index]["keyword_counts"][keyword] += count

        if window_index == 0:
            included_sessions += 1
            included_messages.extend(session_messages)
            session_id = str(data.get("session_id") or path.stem)
            for message_index, content in user_messages:
                matches = _matched_terms(content)
                if not matches:
                    continue
                evidence_refs.append(
                    {
                        "session_id": session_id,
                        "message_index": message_index,
                        "source_path": str(path),
                        "signal_hash": _signal_hash(session_id, message_index, content),
                        "matched_terms": matches,
                        "snippet": content.replace("\n", " ")[:180],
                    }
                )
                if len(evidence_refs) >= 30:
                    break

    return {
        "session_counts": {
            "included_sessions_7d": included_sessions,
            "included_user_messages_7d": len(included_messages),
            "excluded_sessions_7d": int(sum(excluded.values())),
            "excluded_by_reason": dict(sorted(excluded.items())),
        },
        "keyword_counts_7d": _keyword_counts(included_messages),
        "baselines_4w": baseline,
        "evidence_refs": evidence_refs,
    }


def generate_facts(*, now: str | None = None, days: int = 7, write: bool = False) -> dict[str, Any]:
    now_dt = _parse_now(now)
    session_facts = collect_session_facts(now_dt, days=days)
    facts = {
        "version": 1,
        "generated_at": now_dt.isoformat(),
        "window_days": days,
        "filtering_rules": {
            "exclude_cron_sessions": True,
            "exclude_auto_review_sessions": list(AUTO_REVIEW_MARKERS),
            "keyword_counts_use_included_sessions_only": True,
            "baseline_windows": 4,
        },
        **session_facts,
        "proposal_states": load_proposal_states(),
        "priority_scoring": {
            "mode": "disabled_without_structured_scorer",
            "reason": "Weekly strategy reports must not invent priority scores or bonuses without a persisted scorer trace.",
        },
        "report_constraints": REPORT_CONSTRAINTS,
    }
    if write:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(facts, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build deterministic weekly strategy evidence facts.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--now")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    facts = generate_facts(now=args.now, days=args.days, write=args.write)
    print(json.dumps(facts, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
