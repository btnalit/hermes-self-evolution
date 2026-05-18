#!/usr/bin/env python3
"""
Self-Evolution unmatched cluster ledger.

Phase 3 diagnostic/staging layer for agenda discovery:

signals.jsonl -> existing agenda matchers -> deterministic unmatched clusters

This script never approves proposals and does not create new agenda entries.
It only writes the diagnostics ledger when explicitly asked with --write. The
stale auto-discovered agenda cleanup helper is separately gated by
--apply-auto-archive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

TZ = timezone(timedelta(hours=8))
STATE_DIR = Path("/vol1/.hermes/state/evolution")
SIGNALS_FILE = STATE_DIR / "signals.jsonl"
AGENDA_FILE = STATE_DIR / "self_agenda.yaml"
LEDGER_FILE = STATE_DIR / "diagnostics" / "unmatched_clusters.yaml"

RECURRENCE_WINDOW_DAYS = 7
MIN_DISTINCT_DAYS = 2
MIN_TOTAL_SIGNALS = 3
MIN_EVIDENCE_REFS = 2
STRUCTURAL_COMPACT_DAYS = 30
AUTO_DISCOVERY_DECAY_DAYS = 30

SAFE_EVIDENCE_FIELDS = (
    "ts",
    "type",
    "source",
    "severity",
    "job_id",
    "job_name",
    "server_name",
    "skill_name",
    "skill",
    "profile",
    "state",
    "status",
    "changed",
)

SENSITIVE_OR_PATH_FIELDS = {
    "body",
    "content",
    "cookie",
    "file",
    "headers",
    "path",
    "prompt",
    "raw_output",
    "secret",
    "source_path",
    "text",
    "token",
}

SANNAI_PATH_MARKERS = (
    "/vol1/.hermes/profiles/sannai",
    "/root/.hermes/profiles/sannai",
    "/vol1/.hermes/state/sannai",
)


def import_unmatched_signal_review():
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import unmatched_signal_review  # type: ignore

    return unmatched_signal_review


def import_agenda_maturation():
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import agenda_maturation  # type: ignore

    return agenda_maturation


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TZ)
    return parsed


def now_dt(now: str | None = None) -> datetime:
    if now:
        parsed = parse_ts(now)
        if parsed:
            return parsed
    return datetime.now(TZ)


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        return {}
    data = yaml.safe_load(content) or {}
    return data if isinstance(data, dict) else {}


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def load_signals(days: int, now: str | None = None) -> list[dict[str, Any]]:
    if not SIGNALS_FILE.exists():
        return []
    cutoff = now_dt(now) - timedelta(days=days)
    signals: list[dict[str, Any]] = []
    for line in SIGNALS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            sig = json.loads(line)
        except json.JSONDecodeError:
            continue
        sig_ts = parse_ts(sig.get("ts"))
        if sig_ts and sig_ts >= cutoff:
            signals.append(sig)
    return signals


def active_agenda_items() -> list[dict[str, Any]]:
    data = load_yaml(AGENDA_FILE)
    items = data.get("agenda_items") or []
    return [
        item for item in items
        if isinstance(item, dict) and item.get("status") != "archived"
    ]


def auto_discovered_cluster_index() -> dict[str, list[dict[str, Any]]]:
    data = load_yaml(AGENDA_FILE)
    index: dict[str, list[dict[str, Any]]] = {}
    for item in data.get("agenda_items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("auto_discovered") is not True or item.get("source") != "auto_discovery":
            continue
        cluster_id = str(item.get("source_cluster_id") or "")
        if not cluster_id:
            continue
        index.setdefault(cluster_id, []).append(
            {
                "agenda_id": item.get("id"),
                "status": item.get("status"),
                "auto_archived": bool(item.get("auto_archived")),
                "auto_archive_reason": item.get("auto_archive_reason"),
                "false_positive_discovery": bool(item.get("false_positive_discovery")),
            }
        )
    return index


def annotate_applied_clusters(
    clusters: list[dict[str, Any]],
    applied_index: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for cluster in clusters:
        next_cluster = dict(cluster)
        refs = applied_index.get(str(cluster.get("cluster_id") or ""))
        if refs:
            next_cluster["already_applied_to_self_agenda"] = True
            next_cluster["applied_agenda_refs"] = refs
        else:
            next_cluster["already_applied_to_self_agenda"] = False
        annotated.append(next_cluster)
    return annotated


def is_sannai_origin(sig: dict[str, Any]) -> bool:
    for key in ("profile", "agent_profile", "source_profile"):
        if str(sig.get(key, "")).lower() == "sannai":
            return True
    for key in ("source_path", "session_path", "home", "path", "file"):
        value = str(sig.get(key, "")).replace("\\", "/").lower()
        if any(marker in value for marker in SANNAI_PATH_MARKERS):
            return True
    return False


def safe_evidence_ref(sig: dict[str, Any]) -> dict[str, Any]:
    ref = {key: sig.get(key) for key in SAFE_EVIDENCE_FIELDS if key in sig}
    digest = hashlib.sha256(json_dumps(sig).encode("utf-8")).hexdigest()[:16]
    ref["signal_hash"] = digest
    return ref


def fingerprint_parts(sig: dict[str, Any], classification: str) -> dict[str, Any]:
    parts: dict[str, Any] = {
        "type": str(sig.get("type", "")),
        "source": str(sig.get("source", "")),
        "classification": classification,
    }
    for key in (
        "severity",
        "job_id",
        "job_name",
        "server_name",
        "skill_name",
        "skill",
        "state",
        "status",
        "failure_kind",
        "error_class",
        "latency_bucket",
    ):
        if sig.get(key) not in (None, ""):
            parts[key] = sig.get(key)
    return parts


def cluster_id(parts: dict[str, Any]) -> str:
    digest = hashlib.sha256(json_dumps(parts).encode("utf-8")).hexdigest()[:12]
    return f"uc-{digest}"


def signal_matches_any_agenda(sig: dict[str, Any], items: list[dict[str, Any]], review: Any) -> bool:
    return any(review.signal_matches_agenda(sig, item) for item in items)


def cluster_counts(entries: list[dict[str, Any]], generated_at: datetime) -> tuple[int, int, int]:
    count_24h = 0
    count_3d = 0
    days: set[str] = set()
    for entry in entries:
        ts = parse_ts(entry.get("ts"))
        if not ts:
            continue
        days.add(ts.date().isoformat())
        if ts >= generated_at - timedelta(hours=24):
            count_24h += 1
        if ts >= generated_at - timedelta(days=3):
            count_3d += 1
    return count_24h, count_3d, len(days)


def routing_decision(
    sig_type: str,
    classification: str,
    actionable_like_count: int,
    threshold_passed: bool,
    recommendation: dict[str, Any],
) -> tuple[str, str | None, str]:
    suggested_target = recommendation.get("suggested_target")
    recommendation_name = str(recommendation.get("recommendation") or "")
    if suggested_target:
        return (
            "route_existing_agenda",
            str(suggested_target),
            "Recommendation maps this recurring unmatched signal to an existing agenda.",
        )
    if recommendation_name.startswith("mark_structural") or recommendation_name in {
        "legacy_structural",
        "keep_structural",
    }:
        return "mark_structural", None, "Recommendation keeps this signal visible as structural metadata."
    if recommendation_name == "keep_ops_review_only":
        return "ops_review_only", None, "Recommendation keeps this signal visible for ops review only."
    if classification == "structural":
        return "mark_structural", None, "Structural signal stays visible but cannot mature agendas."
    if classification == "ops_review_only":
        return "ops_review_only", None, "Ops-review-only signal stays out of agenda scoring."
    if threshold_passed and actionable_like_count >= MIN_EVIDENCE_REFS:
        return (
            "new_agenda_candidate",
            None,
            "Recurring actionable-like unmatched cluster passed the deterministic preview threshold.",
        )
    return "ignore_noise", None, "Cluster has not passed the deterministic recurrence threshold."


def build_cluster(
    cid: str,
    parts: dict[str, Any],
    entries: list[dict[str, Any]],
    sample_limit: int,
    generated_at: datetime,
    review: Any,
) -> dict[str, Any]:
    entries = sorted(entries, key=lambda item: str(item.get("ts", "")))
    sig_type = str(parts.get("type", ""))
    classification_counts = Counter(str(item.get("_classification", "unknown")) for item in entries)
    classification = classification_counts.most_common(1)[0][0] if classification_counts else "unknown"
    actionable_like_count = sum(1 for item in entries if item.get("_actionable_like"))
    count_24h, count_3d, distinct_days = cluster_counts(entries, generated_at)
    evidence_refs = [safe_evidence_ref(item) for item in entries[:sample_limit]]
    threshold_passed = (
        len(entries) >= MIN_TOTAL_SIGNALS
        and distinct_days >= MIN_DISTINCT_DAYS
        and len(evidence_refs) >= MIN_EVIDENCE_REFS
    )
    rec = review.recommendation_for_type(sig_type, classification, actionable_like_count)
    route, suggested_target, rationale = routing_decision(
        sig_type,
        classification,
        actionable_like_count,
        threshold_passed,
        rec,
    )

    cluster: dict[str, Any] = {
        "cluster_id": cid,
        "status": "preview_ready" if route == "new_agenda_candidate" and threshold_passed else "observing",
        "routing_decision": route,
        "routing_decision_source": "deterministic_rule",
        "fingerprint_parts": parts,
        "signal_type": sig_type,
        "source": parts.get("source"),
        "classification": classification,
        "classification_counts": dict(classification_counts),
        "total_signals": len(entries),
        "counts_24h": count_24h,
        "counts_3d": count_3d,
        "distinct_seen_days": distinct_days,
        "first_seen_at": entries[0].get("ts"),
        "last_seen_at": entries[-1].get("ts"),
        "recurrence_threshold": {
            "window_days": RECURRENCE_WINDOW_DAYS,
            "min_distinct_days": MIN_DISTINCT_DAYS,
            "min_total_signals": MIN_TOTAL_SIGNALS,
            "min_evidence_refs": MIN_EVIDENCE_REFS,
        },
        "threshold_passed": threshold_passed,
        "actionable_like_count": actionable_like_count,
        "recommendation": rec.get("recommendation"),
        "recommendation_reason": rec.get("reason"),
        "suggested_target": suggested_target,
        "rationale": rationale,
        "evidence_refs": evidence_refs,
    }
    return cluster


def compact_previous_cluster(cluster: dict[str, Any], generated_at: datetime) -> dict[str, Any]:
    last_seen = parse_ts(cluster.get("last_seen_at") or cluster.get("first_seen_at"))
    if (
        cluster.get("routing_decision") == "mark_structural"
        and cluster.get("status") != "archived"
        and last_seen
        and last_seen < generated_at - timedelta(days=STRUCTURAL_COMPACT_DAYS)
    ):
        compacted = {
            key: value for key, value in cluster.items()
            if key not in {"evidence_refs", "samples"}
        }
        compacted["status"] = "archived"
        compacted["retention_summary"] = {
            "reason": "inactive_structural_cluster",
            "compacted_at": generated_at.isoformat(),
            "inactive_days": (generated_at - last_seen).days,
        }
        return compacted
    return cluster


def merge_previous_clusters(
    current: list[dict[str, Any]],
    generated_at: datetime,
) -> list[dict[str, Any]]:
    previous = load_yaml(LEDGER_FILE)
    previous_clusters = previous.get("clusters") or []
    current_by_id = {cluster.get("cluster_id"): cluster for cluster in current}
    merged = list(current)
    for old in previous_clusters:
        if not isinstance(old, dict):
            continue
        cid = old.get("cluster_id")
        if cid in current_by_id:
            existing = current_by_id[cid]
            existing["first_seen_at"] = old.get("first_seen_at") or existing.get("first_seen_at")
            continue
        merged.append(compact_previous_cluster(old, generated_at))
    return sorted(
        merged,
        key=lambda item: (
            str(item.get("status", "")),
            str(item.get("routing_decision", "")),
            str(item.get("cluster_id", "")),
        ),
    )


def build_unmatched_cluster_ledger(
    days: int = RECURRENCE_WINDOW_DAYS,
    sample_limit: int = 3,
    now: str | None = None,
) -> dict[str, Any]:
    generated_at = now_dt(now)
    review = import_unmatched_signal_review()
    maturation = import_agenda_maturation()
    signals = load_signals(days, now=generated_at.isoformat())
    agenda_items = active_agenda_items()

    grouped: dict[str, list[dict[str, Any]]] = {}
    parts_by_id: dict[str, dict[str, Any]] = {}
    matched_existing = 0
    sannai_excluded = 0

    for sig in signals:
        if is_sannai_origin(sig):
            sannai_excluded += 1
            continue
        if signal_matches_any_agenda(sig, agenda_items, review):
            matched_existing += 1
            continue
        classification = review.classify_signal(sig, maturation)
        annotated = dict(sig)
        annotated["_classification"] = classification
        annotated["_actionable_like"] = review.is_actionable_like(sig, classification)
        parts = fingerprint_parts(sig, classification)
        cid = cluster_id(parts)
        parts_by_id[cid] = parts
        grouped.setdefault(cid, []).append(annotated)

    clusters = [
        build_cluster(cid, parts_by_id[cid], entries, sample_limit, generated_at, review)
        for cid, entries in grouped.items()
    ]
    clusters = merge_previous_clusters(clusters, generated_at)
    applied_index = auto_discovered_cluster_index()
    clusters = annotate_applied_clusters(clusters, applied_index)
    preview_ready_total = sum(1 for item in clusters if item.get("status") == "preview_ready")
    preview_ready_already_applied = sum(
        1
        for item in clusters
        if item.get("status") == "preview_ready" and item.get("already_applied_to_self_agenda")
    )
    preview_ready_unapplied = preview_ready_total - preview_ready_already_applied

    return {
        "version": 1,
        "generated_at": generated_at.isoformat(),
        "window_days": days,
        "purpose": "deterministic_unmatched_signal_cluster_ledger",
        "summary": {
            "signals_total": len(signals),
            "matched_existing_agenda": matched_existing,
            "sannai_excluded": sannai_excluded,
            "unmatched_clustered": sum(len(entries) for entries in grouped.values()),
            "clusters_total": len(clusters),
            "preview_ready": preview_ready_total,
            "preview_ready_already_applied": preview_ready_already_applied,
            "preview_ready_unapplied": preview_ready_unapplied,
            "applied_auto_discovery_clusters": len(applied_index),
            "by_routing_decision": dict(Counter(str(item.get("routing_decision")) for item in clusters)),
        },
        "clusters": clusters,
    }


def write_ledger(data: dict[str, Any], apply: bool = False) -> dict[str, Any]:
    if not apply:
        return {"written": False, "path": str(LEDGER_FILE), "would_write": True}
    write_yaml(LEDGER_FILE, data)
    return {"written": True, "path": str(LEDGER_FILE)}


def agenda_item_stale(item: dict[str, Any], generated_at: datetime) -> bool:
    if item.get("auto_discovered") is not True:
        return False
    if item.get("status") != "observing":
        return False
    for key in ("last_evidence_at", "last_maturity_progress_at", "updated_at", "created_at", "first_seen_at"):
        seen_at = parse_ts(item.get(key))
        if seen_at:
            return seen_at < generated_at - timedelta(days=AUTO_DISCOVERY_DECAY_DAYS)
    return False


def archive_stale_auto_discovered_agendas(
    now: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    generated_at = now_dt(now)
    agenda = load_yaml(AGENDA_FILE)
    items = agenda.get("agenda_items") or []
    stale_ids: list[str] = []
    for item in items:
        if isinstance(item, dict) and agenda_item_stale(item, generated_at):
            stale_ids.append(str(item.get("id") or item.get("agenda_id") or item.get("title")))
            if apply:
                item["status"] = "archived"
                item["auto_archived"] = True
                item["auto_archived_at"] = generated_at.isoformat()
                item["auto_archive_reason"] = "auto_discovered_observing_stale"
    if apply and stale_ids:
        write_yaml(AGENDA_FILE, agenda)
    return {
        "written": bool(apply and stale_ids),
        "would_archive": stale_ids if not apply else [],
        "archived": stale_ids if apply else [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build deterministic unmatched signal cluster diagnostics.")
    parser.add_argument("--days", type=int, default=RECURRENCE_WINDOW_DAYS)
    parser.add_argument("--sample-limit", type=int, default=3)
    parser.add_argument("--write", action="store_true", help="write diagnostics/unmatched_clusters.yaml")
    parser.add_argument("--apply-auto-archive", action="store_true", help="archive stale auto-discovered observing agendas")
    parser.add_argument("--now", default=None)
    parser.add_argument("--json", action="store_true", help="print JSON output")
    args = parser.parse_args(argv)

    ledger = build_unmatched_cluster_ledger(args.days, args.sample_limit, now=args.now)
    write_result = write_ledger(ledger, apply=args.write)
    archive_result = archive_stale_auto_discovered_agendas(now=args.now, apply=args.apply_auto_archive)
    output = {
        "summary": ledger["summary"],
        "write": write_result,
        "auto_archive": archive_result,
    }
    if args.json:
        print(json_dumps(output))
    else:
        print(
            "unmatched clusters: "
            f"{ledger['summary']['clusters_total']} total, "
            f"{ledger['summary']['preview_ready']} preview_ready; "
            f"written={write_result['written']}; "
            f"auto_archived={len(archive_result['archived'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
