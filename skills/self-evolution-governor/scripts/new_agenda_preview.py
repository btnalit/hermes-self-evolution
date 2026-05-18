#!/usr/bin/env python3
"""CW-022 new agenda preview writer.

Reads the unmatched cluster diagnostics ledger and appends capped
candidate_kind=new_agenda preview records to agenda_candidates.yaml.

This is advisory state only. It never writes self_agenda.yaml, proposal_queue,
or status=approved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

TZ = timezone(timedelta(hours=8))
STATE_DIR = Path("/vol1/.hermes/state/evolution")
LEDGER_FILE = STATE_DIR / "diagnostics" / "unmatched_clusters.yaml"
AGENDA_CANDIDATES_FILE = STATE_DIR / "agenda_candidates.yaml"
SELF_AGENDA_FILE = STATE_DIR / "self_agenda.yaml"
PROPOSAL_FILE = STATE_DIR / "proposal_queue.yaml"

DEFAULT_CAP = 3
CAP_WINDOW_DAYS = 7


def now_iso(now: str | None = None) -> str:
    if now:
        parsed = _parse_ts(now)
        if parsed:
            return parsed.isoformat()
    return datetime.now(TZ).isoformat()


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TZ)
    return parsed


def _read_yaml(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return fallback
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or fallback
    return data if isinstance(data, dict) else fallback


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate_id(cluster_id: str) -> str:
    safe = "".join(ch for ch in cluster_id if ch.isalnum() or ch in "-_")
    return f"NA-CW022-{safe}"


def _safe_evidence_refs(cluster: dict[str, Any]) -> list[dict[str, Any]]:
    refs = cluster.get("evidence_refs") or []
    result: list[dict[str, Any]] = []
    blocked = {"body", "content", "prompt", "raw_output", "summary", "text", "token", "secret", "cookie", "headers"}
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        result.append({key: value for key, value in ref.items() if key not in blocked})
    return result


def _agenda_type_for_cluster(cluster: dict[str, Any]) -> str:
    signal_type = str(cluster.get("signal_type") or "")
    if signal_type.startswith("cron") or signal_type in {"gateway_health", "mcp_health", "tool_reliability"}:
        return "quality_improvement"
    return "automation_opportunity"


def _title_for_cluster(cluster: dict[str, Any]) -> str:
    signal_type = cluster.get("signal_type") or "unmatched_signal"
    source = cluster.get("source") or "unknown-source"
    parts = cluster.get("fingerprint_parts") or {}
    detail = parts.get("job_id") or parts.get("server_name") or parts.get("skill_name") or parts.get("skill")
    if detail:
        return f"Repeated unmatched {signal_type} from {source} ({detail})"
    return f"Repeated unmatched {signal_type} from {source}"


def _question_for_cluster(cluster: dict[str, Any]) -> str:
    signal_type = cluster.get("signal_type") or "unmatched_signal"
    source = cluster.get("source") or "unknown-source"
    return (
        f"Should recurring unmatched {signal_type} signals from {source} become "
        "a new observed agenda, or should they be routed to an existing agenda matcher?"
    )


def _why_not_existing(cluster: dict[str, Any]) -> str:
    return (
        "No active agenda matcher matched this cluster, it has no suggested existing target, "
        f"and it passed the deterministic recurrence threshold with "
        f"{cluster.get('total_signals', 0)} signals across "
        f"{cluster.get('distinct_seen_days', 0)} distinct days."
    )


def _build_candidate(cluster: dict[str, Any], rank: int, now: str) -> dict[str, Any]:
    cluster_id = str(cluster.get("cluster_id") or "")
    evidence_count = int(cluster.get("total_signals") or 0)
    actionable_count = int(cluster.get("actionable_like_count") or evidence_count)
    distinct_days = int(cluster.get("distinct_seen_days") or 0)
    return {
        "candidate_id": _candidate_id(cluster_id),
        "agenda_id": None,
        "title": _title_for_cluster(cluster),
        "question": _question_for_cluster(cluster),
        "type": _agenda_type_for_cluster(cluster),
        "maturity_score": 0.72,
        "evidence_strength": 1.0,
        "action": "create_agenda",
        "status": "new_agenda_preview_ready",
        "candidate_kind": "new_agenda",
        "source_decision": "new_agenda_candidate",
        "advisory_only": True,
        "requires_owner_approval": False,
        "execution_requires_owner_approval": True,
        "proposal_queue_write": False,
        "self_agenda_write": False,
        "source_cluster_id": cluster_id,
        "source_cluster_status": cluster.get("status"),
        "source_cluster_routing_decision": cluster.get("routing_decision"),
        "why_not_existing_agenda": _why_not_existing(cluster),
        "evidence_summary": {
            "signal_type": cluster.get("signal_type"),
            "source": cluster.get("source"),
            "total_signals": evidence_count,
            "distinct_seen_days": distinct_days,
            "first_seen_at": cluster.get("first_seen_at"),
            "last_seen_at": cluster.get("last_seen_at"),
            "classification": cluster.get("classification"),
        },
        "evidence_refs": _safe_evidence_refs(cluster),
        "evidence_count": evidence_count,
        "qualified_evidence_count": evidence_count,
        "actionable_qualified_count": actionable_count,
        "observation_days": distinct_days,
        "candidate_cap_window": {
            "window_days": CAP_WINDOW_DAYS,
            "cap": DEFAULT_CAP,
            "selected_rank": rank,
            "cap_reason": "initial_cw022_new_agenda_preview_cap",
        },
        "suggested_message": (
            "新议题候选："
            f"{_title_for_cluster(cluster)}\n"
            f"问题：{_question_for_cluster(cluster)}\n"
            f"为什么不是现有议题：{_why_not_existing(cluster)}"
        ),
        "generated_at": now,
    }


def _eligible_clusters(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    clusters = [
        cluster for cluster in ledger.get("clusters") or []
        if isinstance(cluster, dict)
        and cluster.get("status") == "preview_ready"
        and cluster.get("routing_decision") == "new_agenda_candidate"
        and cluster.get("threshold_passed") is True
        and not cluster.get("suggested_target")
    ]
    return sorted(
        clusters,
        key=lambda item: (
            -int(item.get("total_signals") or 0),
            str(item.get("cluster_id") or ""),
        ),
    )


def _candidate_cap_keys(
    candidates: list[dict[str, Any]],
    generated_at: datetime,
    cap_window_days: int,
) -> set[str]:
    cutoff = generated_at - timedelta(days=cap_window_days)
    keys: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("candidate_kind") != "new_agenda":
            continue
        if candidate.get("status") == "archived":
            continue
        ts = _parse_ts(candidate.get("generated_at"))
        if ts is not None and ts < cutoff:
            continue
        key = str(candidate.get("source_cluster_id") or candidate.get("candidate_id") or "")
        if key:
            keys.add(key)
    return keys


def _auto_discovered_agendas(
    agenda_data: dict[str, Any],
    *,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    agendas = []
    for item in agenda_data.get("agenda_items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("auto_discovered") is not True:
            continue
        if item.get("source") != "auto_discovery":
            continue
        if not include_archived and item.get("status") == "archived":
            continue
        agendas.append(item)
    return agendas


def _auto_discovered_agenda_cap_keys(
    agendas: list[dict[str, Any]],
    generated_at: datetime,
    cap_window_days: int,
) -> set[str]:
    cutoff = generated_at - timedelta(days=cap_window_days)
    keys: set[str] = set()
    for item in agendas:
        ts = _parse_ts(item.get("first_seen_at") or item.get("created_at") or item.get("generated_at"))
        if ts is not None and ts < cutoff:
            continue
        key = str(item.get("source_cluster_id") or item.get("source_candidate_id") or item.get("id") or "")
        if key:
            keys.add(key)
    return keys


def write_new_agenda_previews(
    *,
    apply: bool = False,
    cap: int = DEFAULT_CAP,
    now: str | None = None,
) -> dict[str, Any]:
    generated_at = _parse_ts(now) or datetime.now(TZ)
    now_text = generated_at.isoformat()
    ledger = _read_yaml(LEDGER_FILE, {"version": 1, "clusters": []})
    data = _read_yaml(AGENDA_CANDIDATES_FILE, {"version": 3, "candidates": []})
    agenda_data = _read_yaml(SELF_AGENDA_FILE, {"version": "1.4", "agenda_items": []})
    data.setdefault("version", 3)
    candidates = [item for item in data.get("candidates") or [] if isinstance(item, dict)]
    auto_discovered_agendas = _auto_discovered_agendas(agenda_data)
    all_auto_discovered_agendas = _auto_discovered_agendas(agenda_data, include_archived=True)
    existing_cluster_ids = {
        str(item.get("source_cluster_id"))
        for item in candidates
        if item.get("candidate_kind") == "new_agenda" and item.get("source_cluster_id")
    }
    existing_cluster_ids.update(
        str(item.get("source_cluster_id"))
        for item in all_auto_discovered_agendas
        if item.get("source_cluster_id")
    )
    existing_candidate_keys = _candidate_cap_keys(candidates, generated_at, CAP_WINDOW_DAYS)
    existing_auto_discovered_agenda_keys = _auto_discovered_agenda_cap_keys(
        auto_discovered_agendas,
        generated_at,
        CAP_WINDOW_DAYS,
    )
    existing_candidate_count = len(existing_candidate_keys)
    existing_auto_discovered_agenda_count = len(existing_auto_discovered_agenda_keys)
    existing_count = len(existing_candidate_keys | existing_auto_discovered_agenda_keys)
    existing_applied_cluster_count = len(
        {
            str(item.get("source_cluster_id"))
            for item in all_auto_discovered_agendas
            if item.get("source_cluster_id")
        }
    )
    remaining = max(0, cap - existing_count)
    before_hash = _file_hash(AGENDA_CANDIDATES_FILE)
    if remaining <= 0:
        return {
            "written": False,
            "would_write": False,
            "reason": "cap_exhausted",
            "selected_count": 0,
            "cap": cap,
            "existing_new_agenda_count": existing_count,
            "existing_candidate_count": existing_candidate_count,
            "existing_auto_discovered_agenda_count": existing_auto_discovered_agenda_count,
            "existing_applied_cluster_count": existing_applied_cluster_count,
            "before_sha256": before_hash,
            "after_sha256": before_hash,
        }

    selected = []
    for cluster in _eligible_clusters(ledger):
        cluster_id = str(cluster.get("cluster_id") or "")
        if not cluster_id or cluster_id in existing_cluster_ids:
            continue
        selected.append(cluster)
        if len(selected) >= remaining:
            break

    if not selected:
        return {
            "written": False,
            "would_write": False,
            "reason": "no_new_preview_candidates",
            "selected_count": 0,
            "cap": cap,
            "existing_new_agenda_count": existing_count,
            "existing_candidate_count": existing_candidate_count,
            "existing_auto_discovered_agenda_count": existing_auto_discovered_agenda_count,
            "existing_applied_cluster_count": existing_applied_cluster_count,
            "before_sha256": before_hash,
            "after_sha256": before_hash,
        }

    new_candidates = [
        _build_candidate(cluster, existing_count + idx + 1, now_text)
        for idx, cluster in enumerate(selected)
    ]
    preview_data = dict(data)
    preview_data["candidates"] = candidates + new_candidates
    preview_data["updated_at"] = now_text
    preview_data["cw022_new_agenda_preview"] = {
        "generated_at": now_text,
        "selected_count": len(new_candidates),
        "cap": cap,
        "cap_window_days": CAP_WINDOW_DAYS,
        "source": str(LEDGER_FILE),
    }
    rendered = yaml.safe_dump(preview_data, allow_unicode=True, sort_keys=False)
    after_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    result = {
        "written": False,
        "would_write": True,
        "reason": "dry_run" if not apply else "created",
        "selected_count": len(new_candidates),
        "candidate_ids": [item["candidate_id"] for item in new_candidates],
        "source_cluster_ids": [item["source_cluster_id"] for item in new_candidates],
        "cap": cap,
        "existing_new_agenda_count": existing_count,
        "existing_candidate_count": existing_candidate_count,
        "existing_auto_discovered_agenda_count": existing_auto_discovered_agenda_count,
        "existing_applied_cluster_count": existing_applied_cluster_count,
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "candidates": new_candidates,
    }
    if not apply:
        return result

    if AGENDA_CANDIDATES_FILE.exists():
        backup = AGENDA_CANDIDATES_FILE.with_name(
            f"{AGENDA_CANDIDATES_FILE.name}.cw022-new-agenda-preview-backup-{generated_at.strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(AGENDA_CANDIDATES_FILE, backup)
        result["backup"] = str(backup)
    _write_yaml(AGENDA_CANDIDATES_FILE, preview_data)
    result["written"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cap", type=int, default=DEFAULT_CAP)
    parser.add_argument("--now", default=None)
    args = parser.parse_args(argv)
    result = write_new_agenda_previews(apply=args.apply, cap=args.cap, now=args.now)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
