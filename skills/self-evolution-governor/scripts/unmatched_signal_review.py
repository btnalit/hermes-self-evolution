#!/usr/bin/env python3
"""
Self-Evolution unmatched signal review.

Read-only diagnostic for the agenda discovery gap:

signals.jsonl -> existing agenda matchers -> agenda evidence

This script does not create agendas, change matchers, or affect speak_gate. It
only reports recent signals that do not match any active agenda, with enough
metadata for an operator to decide whether the signal should become:

- evidence for an existing agenda,
- a new agenda candidate,
- structural metadata,
- ops-review-only material.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

TZ = timezone(timedelta(hours=8))
STATE_DIR = Path("/vol1/.hermes/state/evolution")
SIGNALS_FILE = STATE_DIR / "signals.jsonl"
AGENDA_FILE = STATE_DIR / "self_agenda.yaml"
OUT_YAML = STATE_DIR / "unmatched_signal_review.yaml"
OUT_MD = STATE_DIR / "unmatched_signal_review.md"

SENSITIVE_FIELDS = {
    "body",
    "content",
    "prompt",
    "raw_output",
    "text",
    "token",
    "secret",
    "cookie",
    "auth",
    "headers",
}

UNKNOWN_RECOMMENDATIONS = {
    "skill_usage_telemetry": {
        "recommendation": "mark_structural_or_feed_absence_detector",
        "reason": (
            "Skill usage telemetry is useful for A-002, but should not score "
            "directly. Convert stable absence into a bounded silence signal "
            "before it drives cleanup."
        ),
        "suggested_target": "A-20260429-002",
    },
    "skill_health": {
        "recommendation": "legacy_structural",
        "reason": (
            "Legacy per-skill health snapshots are too noisy for direct scoring; "
            "prefer skill_health_delta or a future bounded absence detector."
        ),
        "suggested_target": "A-20260429-002",
    },
    "source_absent": {
        "recommendation": "new_or_existing_collector_health_agenda_if_repeated",
        "reason": (
            "This is telemetry-source absence, not agenda-level silence. If it "
            "repeats, route to collector health or A-005 monitoring."
        ),
        "suggested_target": "A-20260429-005",
    },
    "source_absent_report": {
        "recommendation": "mark_structural",
        "reason": "Aggregate absence report; keep visible for operators but do not score directly.",
        "suggested_target": None,
    },
    "platform_enabled_status": {
        "recommendation": "mark_structural",
        "reason": "Platform status metadata is not an agenda event by itself.",
        "suggested_target": None,
    },
    "protected_skill_status": {
        "recommendation": "mark_structural",
        "reason": "Protected skill inventory is metadata unless an anomaly is emitted.",
        "suggested_target": None,
    },
    "active_cron_dependency": {
        "recommendation": "mark_structural_or_ops_review_only",
        "reason": "Cron dependency metadata should inform review, not mature an agenda alone.",
        "suggested_target": None,
    },
    "protected_skill_anomaly": {
        "recommendation": "route_to_existing_quality_agenda",
        "reason": "A protected skill anomaly is actionable governance or pipeline safety evidence.",
        "suggested_target": "A-20260429-003",
    },
}

UNKNOWN_ACTIONABLE_HINTS = (
    "anomaly",
    "blocked",
    "critical",
    "dependency",
    "error",
    "fail",
    "failed",
    "failure",
    "health",
    "missing",
    "protected",
    "timeout",
)

DOMAIN_GAP_HINTS = (
    "browser",
    "cloakbrowser",
    "chrome",
    "hindsight",
    "kanban",
    "mailbox",
    "media",
    "memory",
    "publish",
    "sannai",
    "worker",
)


def import_agenda_maturation():
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import agenda_maturation  # type: ignore

    return agenda_maturation


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.read_text().strip():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return data if isinstance(data, dict) else {}


def load_signals(days: int) -> list[dict[str, Any]]:
    if not SIGNALS_FILE.exists():
        return []
    cutoff = datetime.now(TZ) - timedelta(days=days)
    result: list[dict[str, Any]] = []
    for line in SIGNALS_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            sig = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = sig.get("ts")
        if not ts:
            continue
        try:
            sig_ts = datetime.fromisoformat(str(ts))
        except (TypeError, ValueError):
            continue
        if sig_ts >= cutoff:
            result.append(sig)
    return result


def active_agenda_items() -> list[dict[str, Any]]:
    agenda = load_yaml(AGENDA_FILE)
    items = agenda.get("agenda_items") or []
    return [
        item for item in items
        if isinstance(item, dict) and item.get("status") != "archived"
    ]


def signal_matches_agenda(sig: dict[str, Any], item: dict[str, Any]) -> bool:
    matchers = item.get("evidence_matchers") or {}
    signal_types = set(matchers.get("signal_types") or [])
    sig_type = sig.get("type", "")
    if signal_types and sig_type not in signal_types:
        return False

    text = json.dumps(sig, ensure_ascii=False).lower()
    include_kw = [str(kw).lower() for kw in matchers.get("include_keywords") or [] if kw]
    exclude_kw = [str(kw).lower() for kw in matchers.get("exclude_keywords") or [] if kw]

    if include_kw and not any(kw in text for kw in include_kw):
        return False
    if exclude_kw and any(kw in text for kw in exclude_kw):
        return False
    return True


def safe_signal_sample(sig: dict[str, Any]) -> dict[str, Any]:
    sample: dict[str, Any] = {}
    for key in (
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
    ):
        if key in sig:
            sample[key] = sig.get(key)

    summary = sig.get("summary")
    if summary:
        sample["summary_preview"] = str(summary).replace("\n", " ")[:160]

    for key, value in sig.items():
        lowered = key.lower()
        if key in sample or lowered in SENSITIVE_FIELDS:
            continue
        if any(sensitive in lowered for sensitive in SENSITIVE_FIELDS):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            if key in {"path", "file", "source_path"}:
                continue
            sample.setdefault("extra", {})[key] = value
    return sample


def classify_signal(sig: dict[str, Any], agenda_maturation: Any) -> str:
    sig_type = sig.get("type", "")
    if sig_type == "cron_result":
        return "actionable" if cron_result_is_actionable(sig) else "structural"
    cls = agenda_maturation.SIGNAL_CLASSIFICATION.get(sig_type, "unknown")
    if cls == "conditional_actionable" and sig_type == "skill_health_delta":
        summary = str(sig.get("summary") or json.dumps(sig, ensure_ascii=False)[:300])
        if agenda_maturation._is_negative_skill_health_delta(summary):
            return "actionable"
        return "structural"
    return cls


def is_actionable_like(sig: dict[str, Any], classification: str) -> bool:
    if sig.get("type") == "cron_result":
        return cron_result_is_actionable(sig)
    if classification == "actionable":
        return True
    if classification in {"structural", "ops_review_only"}:
        return False
    if sig.get("severity") in {"warning", "critical", "error"}:
        return True
    sig_type = str(sig.get("type", "")).lower()
    safe_text = json.dumps(safe_signal_sample(sig), ensure_ascii=False).lower()
    return any(hint in sig_type or hint in safe_text for hint in UNKNOWN_ACTIONABLE_HINTS)


def domain_gap_tags(sig: dict[str, Any]) -> list[str]:
    text = json.dumps(safe_signal_sample(sig), ensure_ascii=False).lower()
    return [hint for hint in DOMAIN_GAP_HINTS if hint in text]


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def cron_result_is_actionable(sig: dict[str, Any]) -> bool:
    """Return true only for cron_result records that indicate failure/risk."""
    if _truthy(sig.get("has_error")):
        return True
    severity = str(sig.get("severity") or "").strip().lower()
    if severity in {"warning", "error", "critical"}:
        return True
    status = str(sig.get("status") or sig.get("state") or sig.get("result") or "").strip().lower()
    if any(token in status for token in ("fail", "error", "timeout", "unhealthy")):
        return True
    return False


def recommendation_for_type(sig_type: str, classification: str, action_like_count: int) -> dict[str, Any]:
    if sig_type in UNKNOWN_RECOMMENDATIONS:
        return UNKNOWN_RECOMMENDATIONS[sig_type]
    if classification == "unknown" and action_like_count:
        return {
            "recommendation": "needs_operator_triage",
            "reason": "Unknown type has actionable-like signals; decide whether it maps to an existing agenda or deserves a new agenda.",
            "suggested_target": None,
        }
    if classification == "unknown":
        return {
            "recommendation": "classify_before_scoring",
            "reason": "Unknown type should be classified before it can affect agenda scoring.",
            "suggested_target": None,
        }
    if classification == "ops_review_only":
        return {
            "recommendation": "keep_ops_review_only",
            "reason": "Visible to operators but intentionally kept out of agenda scoring.",
            "suggested_target": None,
        }
    if classification == "structural":
        return {
            "recommendation": "keep_structural",
            "reason": "Structural metadata is useful context but should not mature an agenda.",
            "suggested_target": None,
        }
    return {
        "recommendation": "check_existing_matchers",
        "reason": "Actionable signal did not match an active agenda; add a matcher only if the event belongs to that agenda.",
        "suggested_target": None,
    }


def build_review(days: int, sample_limit: int) -> dict[str, Any]:
    agenda_maturation = import_agenda_maturation()
    signals = load_signals(days)
    items = active_agenda_items()

    matched_by_agenda: Counter[str] = Counter()
    matched_by_type: Counter[str] = Counter()
    unmatched_by_type: Counter[str] = Counter()
    unmatched_by_classification: Counter[str] = Counter()
    unmatched_class_by_type: dict[str, Counter[str]] = defaultdict(Counter)
    unknown_by_type: Counter[str] = Counter()
    actionable_like_by_type: Counter[str] = Counter()
    domain_gap_by_type: dict[str, Counter[str]] = defaultdict(Counter)
    samples_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)

    matched_count = 0
    unmatched_count = 0

    for sig in signals:
        sig_type = str(sig.get("type", ""))
        destinations = [
            str(item.get("id") or item.get("agenda_id") or item.get("title") or "unknown")
            for item in items
            if signal_matches_agenda(sig, item)
        ]
        if destinations:
            matched_count += 1
            matched_by_type[sig_type] += 1
            for dest in destinations:
                matched_by_agenda[dest] += 1
            continue

        unmatched_count += 1
        classification = classify_signal(sig, agenda_maturation)
        unmatched_by_type[sig_type] += 1
        unmatched_by_classification[classification] += 1
        unmatched_class_by_type[sig_type][classification] += 1
        if classification == "unknown":
            unknown_by_type[sig_type] += 1
        if is_actionable_like(sig, classification):
            actionable_like_by_type[sig_type] += 1
        for tag in domain_gap_tags(sig):
            domain_gap_by_type[sig_type][tag] += 1
        if len(samples_by_type[sig_type]) < sample_limit:
            samples_by_type[sig_type].append(safe_signal_sample(sig))

    type_recommendations = []
    for sig_type, count in sorted(unmatched_by_type.items(), key=lambda kv: (-kv[1], kv[0])):
        class_counts = unmatched_class_by_type.get(sig_type, Counter())
        if "actionable" in class_counts:
            cls = "actionable"
        elif "unknown" in class_counts:
            cls = "unknown"
        elif "ops_review_only" in class_counts:
            cls = "ops_review_only"
        else:
            cls = class_counts.most_common(1)[0][0] if class_counts else "unknown"
        rec = recommendation_for_type(sig_type, cls, actionable_like_by_type.get(sig_type, 0))
        type_recommendations.append({
            "type": sig_type,
            "count": count,
            "classification": cls,
            "classification_counts": dict(class_counts),
            "actionable_like_count": actionable_like_by_type.get(sig_type, 0),
            "domain_gap_tags": dict(domain_gap_by_type.get(sig_type, {})),
            **rec,
            "samples": samples_by_type.get(sig_type, []),
        })

    active_agendas = [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "status": item.get("status"),
            "signal_types": (item.get("evidence_matchers") or {}).get("signal_types") or [],
        }
        for item in items
    ]

    return {
        "generated_at": now_iso(),
        "window_days": days,
        "purpose": "read-only unmatched signal review; no agenda or matcher changes",
        "counts": {
            "signals_total": len(signals),
            "matched_existing_agenda": matched_count,
            "unmatched": unmatched_count,
        },
        "active_agendas": active_agendas,
        "matched_by_agenda": dict(sorted(matched_by_agenda.items())),
        "matched_by_type": dict(sorted(matched_by_type.items(), key=lambda kv: (-kv[1], kv[0]))),
        "unmatched_by_type": dict(sorted(unmatched_by_type.items(), key=lambda kv: (-kv[1], kv[0]))),
        "unmatched_by_classification": dict(sorted(unmatched_by_classification.items())),
        "unknown_by_type": dict(sorted(unknown_by_type.items(), key=lambda kv: (-kv[1], kv[0]))),
        "actionable_like_unmatched_by_type": dict(sorted(actionable_like_by_type.items(), key=lambda kv: (-kv[1], kv[0]))),
        "type_recommendations": type_recommendations,
        "operator_note": (
            "Do not auto-create agendas from this report. Observe several runs, "
            "then decide whether to add a new agenda, update an existing matcher, "
            "or classify the type as structural/ops-review-only."
        ),
    }


def write_report(review: dict[str, Any]) -> None:
    OUT_YAML.write_text(yaml.dump(review, allow_unicode=True, sort_keys=False))
    OUT_MD.write_text(render_markdown(review))


def render_markdown(review: dict[str, Any]) -> str:
    counts = review["counts"]
    lines = [
        "# Unmatched Signal Review",
        "",
        f"Generated at: {review['generated_at']}",
        f"Window: {review['window_days']} days",
        "",
        "## Summary",
        "",
        f"- signals_total: {counts['signals_total']}",
        f"- matched_existing_agenda: {counts['matched_existing_agenda']}",
        f"- unmatched: {counts['unmatched']}",
        "",
        "## Unmatched By Type",
        "",
    ]
    for sig_type, count in review["unmatched_by_type"].items():
        lines.append(f"- `{sig_type}`: {count}")
    if not review["unmatched_by_type"]:
        lines.append("- none")
    lines.extend(["", "## Unknown Types", ""])
    for sig_type, count in review["unknown_by_type"].items():
        lines.append(f"- `{sig_type}`: {count}")
    if not review["unknown_by_type"]:
        lines.append("- none")
    lines.extend(["", "## Actionable-Like Unmatched", ""])
    for sig_type, count in review["actionable_like_unmatched_by_type"].items():
        lines.append(f"- `{sig_type}`: {count}")
    if not review["actionable_like_unmatched_by_type"]:
        lines.append("- none")
    lines.extend(["", "## Recommendations", ""])
    for rec in review["type_recommendations"]:
        lines.append(
            f"- `{rec['type']}` ({rec['count']}): {rec['recommendation']} "
            f"-> {rec.get('suggested_target') or 'no direct target'}"
        )
        lines.append(f"  reason: {rec['reason']}")
    if not review["type_recommendations"]:
        lines.append("- none")
    lines.extend(["", "## Operator Note", "", review["operator_note"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--sample-limit", type=int, default=2)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    review = build_review(days=args.days, sample_limit=args.sample_limit)
    if args.write:
        write_report(review)
        print(f"wrote {OUT_YAML}")
        print(f"wrote {OUT_MD}")

    if args.json:
        print(json.dumps(review, ensure_ascii=False, indent=2))
    else:
        counts = review["counts"]
        print(
            "unmatched_signal_review: "
            f"window_days={review['window_days']} "
            f"signals_total={counts['signals_total']} "
            f"matched={counts['matched_existing_agenda']} "
            f"unmatched={counts['unmatched']} "
            f"unknown_types={len(review['unknown_by_type'])} "
            f"actionable_like_unmatched={sum(review['actionable_like_unmatched_by_type'].values())}"
        )
        if review["unknown_by_type"]:
            print("unknown_by_type:", review["unknown_by_type"])
        if review["actionable_like_unmatched_by_type"]:
            print("actionable_like_unmatched_by_type:", review["actionable_like_unmatched_by_type"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
