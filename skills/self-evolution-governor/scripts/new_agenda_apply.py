#!/usr/bin/env python3
"""CW-022 new agenda apply helper.

Applies a confirmed candidate_kind=new_agenda preview into self_agenda.yaml as
an observing, auto_discovered agenda item. This helper never writes proposals
and never approves execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yaml

TZ = timezone(timedelta(hours=8))
STATE_DIR = Path("/vol1/.hermes/state/evolution")
AGENDA_CANDIDATES_FILE = STATE_DIR / "agenda_candidates.yaml"
SELF_AGENDA_FILE = STATE_DIR / "self_agenda.yaml"
PROPOSAL_FILE = STATE_DIR / "proposal_queue.yaml"
AUDIT_FILE = STATE_DIR / "new_agenda_apply_audit.yaml"
CANONICAL_AGENDA_ID_RE = re.compile(r"^A-(\d{8})-(\d+)$")
LEGACY_CW022_AGENDA_PREFIX = "A-CW022-"


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


def _now(now: str | None = None) -> datetime:
    parsed = _parse_ts(now)
    return parsed or datetime.now(TZ)


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


def _canonical_id_state(agenda: dict[str, Any]) -> tuple[str, int]:
    prefix = ""
    max_number = 0
    for item in agenda.get("agenda_items") or []:
        if not isinstance(item, dict):
            continue
        match = CANONICAL_AGENDA_ID_RE.match(str(item.get("id") or ""))
        if not match:
            continue
        number = int(match.group(2))
        if number >= max_number:
            prefix = match.group(1)
            max_number = number
    return prefix, max_number


def _next_agenda_id(agenda: dict[str, Any], *, allocated: set[str] | None = None, now: str | None = None) -> str:
    prefix, max_number = _canonical_id_state(agenda)
    allocated = allocated or set()
    if not prefix:
        prefix = _now(now).strftime("%Y%m%d")
    number = max_number + 1
    while True:
        candidate_id = f"A-{prefix}-{number:03d}"
        if candidate_id not in allocated:
            return candidate_id
        number += 1


def _legacy_cw022_agenda_id(value: Any) -> bool:
    return str(value or "").startswith(LEGACY_CW022_AGENDA_PREFIX)


def _find_candidate(data: dict[str, Any], candidate_id: str) -> tuple[int, dict[str, Any]]:
    for idx, candidate in enumerate(data.get("candidates") or []):
        if isinstance(candidate, dict) and candidate.get("candidate_id") == candidate_id:
            return idx, candidate
    raise SystemExit(f"candidate not found: {candidate_id}")


def _existing_agenda_for_candidate(agenda: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any] | None:
    candidate_id = candidate.get("candidate_id")
    cluster_id = candidate.get("source_cluster_id")
    for item in agenda.get("agenda_items") or []:
        if not isinstance(item, dict):
            continue
        if candidate_id and item.get("source_candidate_id") == candidate_id:
            return item
        if cluster_id and item.get("source_cluster_id") == cluster_id:
            return item
    return None


def _validate_candidate(candidate: dict[str, Any]) -> None:
    if candidate.get("candidate_kind") != "new_agenda":
        raise SystemExit("candidate is not candidate_kind=new_agenda")
    if candidate.get("status") != "new_agenda_preview_ready":
        raise SystemExit("candidate is not new_agenda_preview_ready")
    if candidate.get("action") != "create_agenda":
        raise SystemExit("candidate action is not create_agenda")


def _matcher_keywords(candidate: dict[str, Any]) -> list[str]:
    keywords: list[str] = []
    refs = candidate.get("evidence_refs") or []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        for key in ("job_id", "server_name", "skill_name", "skill"):
            value = ref.get(key)
            if value and str(value) not in keywords:
                keywords.append(str(value))
    if not keywords:
        source = (candidate.get("evidence_summary") or {}).get("source")
        if source:
            keywords.append(str(source))
    return keywords


def _safe_evidence(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for ref in candidate.get("evidence_refs") or []:
        if not isinstance(ref, dict):
            continue
        evidence.append(
            {
                "at": ref.get("ts"),
                "source": "auto_discovery",
                "safe_ref": {
                    key: value
                    for key, value in ref.items()
                    if key not in {"summary", "body", "content", "prompt", "raw_output", "text"}
                },
                "weight": 0.1,
                "evidence_dedup_key": (
                    f"auto_discovery:{candidate.get('source_cluster_id')}:{ref.get('signal_hash') or ref.get('ts')}"
                ),
                "qualified": True,
                "qualify_reason": "auto_discovery_safe_ref",
            }
        )
    return evidence


def _build_agenda_item(candidate: dict[str, Any], applied_at: str, agenda_id: str) -> dict[str, Any]:
    summary = candidate.get("evidence_summary") or {}
    signal_type = summary.get("signal_type")
    return {
        "id": agenda_id,
        "title": candidate.get("title", ""),
        "question": candidate.get("question", ""),
        "type": candidate.get("type", "quality_improvement"),
        "status": "observing",
        "source": "auto_discovery",
        "auto_discovered": True,
        "first_seen_at": summary.get("first_seen_at") or candidate.get("generated_at") or applied_at,
        "last_evidence_at": summary.get("last_seen_at") or candidate.get("generated_at") or applied_at,
        "last_matured_at": None,
        "last_surfaced_at": None,
        "source_candidate_id": candidate.get("candidate_id"),
        "source_cluster_id": candidate.get("source_cluster_id"),
        "why_not_existing_agenda": candidate.get("why_not_existing_agenda", ""),
        "evidence_matchers": {
            "signal_types": [signal_type] if signal_type else [],
            "include_keywords": _matcher_keywords(candidate),
            "exclude_keywords": [],
        },
        "evidence": _safe_evidence(candidate),
        "auto_discovery": {
            "applied_at": applied_at,
            "candidate_id": candidate.get("candidate_id"),
            "source_cluster_id": candidate.get("source_cluster_id"),
            "candidate_generated_at": candidate.get("generated_at"),
            "safe_evidence_ref_count": len(candidate.get("evidence_refs") or []),
        },
        "proposal_queue_write": False,
        "execution_requires_owner_approval": True,
    }


def _load_proposal_hash() -> str:
    return _file_hash(PROPOSAL_FILE)


def _append_audit(record: dict[str, Any]) -> None:
    data = _read_yaml(AUDIT_FILE, {"version": 1, "records": []})
    data.setdefault("version", 1)
    records = data.setdefault("records", [])
    records.append(record)
    _write_yaml(AUDIT_FILE, data)


def _check_expected_hashes(
    *,
    expected_self_agenda_sha256: str | None,
    expected_candidates_sha256: str | None,
    current_self_hash: str,
    current_candidates_hash: str,
    apply: bool,
) -> None:
    if not apply:
        return
    if not expected_self_agenda_sha256 or not expected_candidates_sha256:
        raise SystemExit("apply requires expected self_agenda and agenda_candidates hashes")
    if expected_self_agenda_sha256 != current_self_hash:
        raise SystemExit("self_agenda hash mismatch")
    if expected_candidates_sha256 != current_candidates_hash:
        raise SystemExit("agenda_candidates hash mismatch")


def _check_expected_audit_hash(
    *,
    expected_audit_sha256: str | None,
    current_audit_hash: str,
    apply: bool,
) -> None:
    if not apply:
        return
    if not expected_audit_sha256:
        raise SystemExit("apply requires expected audit hash")
    if expected_audit_sha256 != current_audit_hash:
        raise SystemExit("new_agenda_apply_audit hash mismatch")


def apply_new_agenda_candidate(
    candidate_id: str,
    *,
    expected_self_agenda_sha256: str | None = None,
    expected_candidates_sha256: str | None = None,
    apply: bool = False,
    now: str | None = None,
) -> dict[str, Any]:
    applied_at = _now(now).isoformat()
    candidates_data = _read_yaml(AGENDA_CANDIDATES_FILE, {"version": 3, "candidates": []})
    self_agenda = _read_yaml(SELF_AGENDA_FILE, {"version": "1.4", "agenda_items": []})
    before = {
        "self_agenda_sha256": _file_hash(SELF_AGENDA_FILE),
        "agenda_candidates_sha256": _file_hash(AGENDA_CANDIDATES_FILE),
        "proposal_queue_sha256": _load_proposal_hash(),
    }
    _check_expected_hashes(
        expected_self_agenda_sha256=expected_self_agenda_sha256,
        expected_candidates_sha256=expected_candidates_sha256,
        current_self_hash=before["self_agenda_sha256"],
        current_candidates_hash=before["agenda_candidates_sha256"],
        apply=apply,
    )

    idx, candidate = _find_candidate(candidates_data, candidate_id)
    existing = _existing_agenda_for_candidate(self_agenda, candidate)
    if existing:
        if candidate.get("status") == "applied_to_self_agenda":
            return {
                "candidate_id": candidate_id,
                "target_agenda_id": existing.get("id"),
                "written": False,
                "would_write": False,
                "reason": "agenda_already_exists_for_candidate",
                "before": before,
                "after": before,
                "proposal_queue_written": False,
            }
        _validate_candidate(candidate)
        applied_at = _now(now).isoformat()
        updated_candidate = dict(candidate)
        updated_candidate["status"] = "applied_to_self_agenda"
        updated_candidate["target_agenda_id"] = existing.get("id")
        updated_candidate["self_agenda_write"] = False
        updated_candidate["applied_at"] = applied_at
        updated_candidate["existing_self_agenda_match"] = True
        preview_candidates = dict(candidates_data)
        candidate_list = list(candidates_data.get("candidates") or [])
        candidate_list[idx] = updated_candidate
        preview_candidates["candidates"] = candidate_list
        preview_candidates["updated_at"] = applied_at
        rendered_candidates = yaml.safe_dump(preview_candidates, allow_unicode=True, sort_keys=False)
        after = {
            "self_agenda_sha256": before["self_agenda_sha256"],
            "agenda_candidates_sha256": hashlib.sha256(rendered_candidates.encode("utf-8")).hexdigest(),
            "proposal_queue_sha256": before["proposal_queue_sha256"],
        }
        result = {
            "candidate_id": candidate_id,
            "target_agenda_id": existing.get("id"),
            "written": False,
            "would_write": True,
            "reason": "dry_run_existing_agenda_match" if not apply else "marked_existing_agenda_match",
            "before": before,
            "after": after,
            "proposal_queue_written": False,
            "self_agenda_written": False,
        }
        if not apply:
            return result

        backups: dict[str, str] = {}
        for path, label in (
            (AGENDA_CANDIDATES_FILE, "agenda_candidates"),
            (AUDIT_FILE, "audit"),
        ):
            if path.exists():
                backup = path.with_name(
                    f"{path.name}.cw022-new-agenda-existing-match-backup-{_now(now).strftime('%Y%m%d-%H%M%S')}"
                )
                shutil.copy2(path, backup)
                backups[label] = str(backup)
        _write_yaml(AGENDA_CANDIDATES_FILE, preview_candidates)
        audit_record = {
            "applied_at": applied_at,
            "cw": "CW-022",
            "action": "mark_existing_new_agenda_candidate_applied",
            "candidate_id": candidate_id,
            "source_cluster_id": candidate.get("source_cluster_id"),
            "target_agenda_id": existing.get("id"),
            "target_files": [str(AGENDA_CANDIDATES_FILE)],
            "before": before,
            "after": {
                "self_agenda_sha256": _file_hash(SELF_AGENDA_FILE),
                "agenda_candidates_sha256": _file_hash(AGENDA_CANDIDATES_FILE),
                "proposal_queue_sha256": _load_proposal_hash(),
            },
            "proposal_queue_written": False,
            "self_agenda_written": False,
            "execution": "not_approved_not_executed",
        }
        _append_audit(audit_record)
        result["written"] = True
        result["backup"] = backups
        result["after"] = audit_record["after"]
        result["audit_file"] = str(AUDIT_FILE)
        return result
    _validate_candidate(candidate)

    target_agenda_id = _next_agenda_id(self_agenda, now=applied_at)
    agenda_item = _build_agenda_item(candidate, applied_at, target_agenda_id)
    preview_self = dict(self_agenda)
    preview_self["updated_at"] = applied_at
    preview_self["agenda_items"] = list(self_agenda.get("agenda_items") or []) + [agenda_item]

    updated_candidate = dict(candidate)
    updated_candidate["status"] = "applied_to_self_agenda"
    updated_candidate["target_agenda_id"] = agenda_item["id"]
    updated_candidate["self_agenda_write"] = True
    updated_candidate["applied_at"] = applied_at
    preview_candidates = dict(candidates_data)
    preview_candidates["updated_at"] = applied_at
    candidate_list = list(candidates_data.get("candidates") or [])
    candidate_list[idx] = updated_candidate
    preview_candidates["candidates"] = candidate_list

    rendered_self = yaml.safe_dump(preview_self, allow_unicode=True, sort_keys=False)
    rendered_candidates = yaml.safe_dump(preview_candidates, allow_unicode=True, sort_keys=False)
    after = {
        "self_agenda_sha256": hashlib.sha256(rendered_self.encode("utf-8")).hexdigest(),
        "agenda_candidates_sha256": hashlib.sha256(rendered_candidates.encode("utf-8")).hexdigest(),
        "proposal_queue_sha256": before["proposal_queue_sha256"],
    }
    result = {
        "candidate_id": candidate_id,
        "target_agenda_id": agenda_item["id"],
        "written": False,
        "would_write": True,
        "reason": "dry_run" if not apply else "created",
        "before": before,
        "after": after,
        "proposal_queue_written": False,
        "agenda_item": agenda_item,
    }
    if not apply:
        return result

    backups: dict[str, str] = {}
    for path, label in (
        (SELF_AGENDA_FILE, "self_agenda"),
        (AGENDA_CANDIDATES_FILE, "agenda_candidates"),
        (AUDIT_FILE, "audit"),
    ):
        if path.exists():
            backup = path.with_name(f"{path.name}.cw022-new-agenda-apply-backup-{_now(now).strftime('%Y%m%d-%H%M%S')}")
            shutil.copy2(path, backup)
            backups[label] = str(backup)
    _write_yaml(SELF_AGENDA_FILE, preview_self)
    _write_yaml(AGENDA_CANDIDATES_FILE, preview_candidates)
    audit_record = {
        "applied_at": applied_at,
        "cw": "CW-022",
        "action": "apply_new_agenda_preview_to_self_agenda",
        "candidate_id": candidate_id,
        "source_cluster_id": candidate.get("source_cluster_id"),
        "target_agenda_id": agenda_item["id"],
        "target_files": [str(SELF_AGENDA_FILE), str(AGENDA_CANDIDATES_FILE)],
        "before": before,
        "after": {
            "self_agenda_sha256": _file_hash(SELF_AGENDA_FILE),
            "agenda_candidates_sha256": _file_hash(AGENDA_CANDIDATES_FILE),
            "proposal_queue_sha256": _load_proposal_hash(),
        },
        "expected": {
            "self_agenda_sha256": expected_self_agenda_sha256,
            "agenda_candidates_sha256": expected_candidates_sha256,
        },
        "why_not_existing_agenda": candidate.get("why_not_existing_agenda", ""),
        "proposal_queue_written": False,
        "execution": "not_approved_not_executed",
    }
    _append_audit(audit_record)
    result["written"] = True
    result["backup"] = backups
    result["after"] = audit_record["after"]
    result["audit_file"] = str(AUDIT_FILE)
    return result


def _ready_new_agenda_candidates(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in data.get("candidates") or []
        if isinstance(item, dict)
        and item.get("candidate_kind") == "new_agenda"
        and item.get("status") == "new_agenda_preview_ready"
        and item.get("action") == "create_agenda"
        and item.get("candidate_id")
    ]
    return sorted(
        candidates,
        key=lambda item: (
            _parse_ts(item.get("generated_at"))
            or _parse_ts((item.get("evidence_summary") or {}).get("first_seen_at"))
            or datetime.min.replace(tzinfo=TZ),
            str(item.get("candidate_id") or ""),
        ),
    )


def apply_ready_new_agenda_candidates(
    *,
    apply: bool = False,
    cap: int = 3,
    now: str | None = None,
) -> dict[str, Any]:
    candidates_data = _read_yaml(AGENDA_CANDIDATES_FILE, {"version": 3, "candidates": []})
    selected = _ready_new_agenda_candidates(candidates_data)[: max(0, int(cap))]
    before = {
        "self_agenda_sha256": _file_hash(SELF_AGENDA_FILE),
        "agenda_candidates_sha256": _file_hash(AGENDA_CANDIDATES_FILE),
        "new_agenda_apply_audit_sha256": _file_hash(AUDIT_FILE),
        "proposal_queue_sha256": _load_proposal_hash(),
    }
    result = {
        "written": False,
        "would_write": bool(selected),
        "selected_count": len(selected),
        "written_count": 0,
        "applied": [],
        "before": before,
        "after": before,
        "proposal_queue_written": False,
        "cap": int(cap),
        "reason": "no_ready_new_agenda_candidates" if not selected else ("created" if apply else "dry_run"),
    }
    if not selected:
        return result

    if not apply:
        preview_self = _read_yaml(SELF_AGENDA_FILE, {"version": "1.4", "agenda_items": []})
        allocated = {
            str(item.get("id"))
            for item in preview_self.get("agenda_items") or []
            if isinstance(item, dict) and item.get("id")
        }
        for candidate in selected:
            existing = _existing_agenda_for_candidate(preview_self, candidate)
            target_id = existing.get("id") if existing else _next_agenda_id(preview_self, allocated=allocated, now=now)
            if target_id:
                allocated.add(str(target_id))
                if not existing:
                    preview_self.setdefault("agenda_items", []).append({"id": target_id})
            result["applied"].append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "target_agenda_id": target_id,
                    "written": False,
                    "would_write": existing is None,
                    "proposal_queue_written": False,
                }
            )
        return result

    for candidate in selected:
        current_self_hash = _file_hash(SELF_AGENDA_FILE)
        current_candidates_hash = _file_hash(AGENDA_CANDIDATES_FILE)
        item_result = apply_new_agenda_candidate(
            str(candidate.get("candidate_id")),
            expected_self_agenda_sha256=current_self_hash,
            expected_candidates_sha256=current_candidates_hash,
            apply=True,
            now=now,
        )
        result["applied"].append(item_result)
        if item_result.get("written"):
            result["written_count"] += 1

    result["written"] = result["written_count"] > 0
    result["after"] = {
        "self_agenda_sha256": _file_hash(SELF_AGENDA_FILE),
        "agenda_candidates_sha256": _file_hash(AGENDA_CANDIDATES_FILE),
        "new_agenda_apply_audit_sha256": _file_hash(AUDIT_FILE),
        "proposal_queue_sha256": _load_proposal_hash(),
    }
    return result


def _normalization_mappings(self_agenda: dict[str, Any]) -> list[dict[str, str]]:
    canonical_ids = {
        str(item.get("id"))
        for item in self_agenda.get("agenda_items") or []
        if isinstance(item, dict) and CANONICAL_AGENDA_ID_RE.match(str(item.get("id") or ""))
    }
    preview_agenda = {
        "agenda_items": [
            item
            for item in self_agenda.get("agenda_items") or []
            if isinstance(item, dict) and not _legacy_cw022_agenda_id(item.get("id"))
        ]
    }
    mappings: list[dict[str, str]] = []
    for item in self_agenda.get("agenda_items") or []:
        if not isinstance(item, dict):
            continue
        old_id = str(item.get("id") or "")
        if not _legacy_cw022_agenda_id(old_id):
            continue
        if item.get("source") != "auto_discovery" or item.get("auto_discovered") is not True:
            continue
        new_id = _next_agenda_id(preview_agenda, allocated=canonical_ids)
        canonical_ids.add(new_id)
        preview_agenda.setdefault("agenda_items", []).append({"id": new_id})
        mappings.append({"old_agenda_id": old_id, "new_agenda_id": new_id})
    return mappings


def _apply_mapping_to_self_agenda(data: dict[str, Any], id_map: dict[str, str]) -> dict[str, Any]:
    updated = dict(data)
    items: list[dict[str, Any]] = []
    for item in data.get("agenda_items") or []:
        if not isinstance(item, dict):
            items.append(item)
            continue
        old_id = item.get("id")
        next_item = dict(item)
        if old_id in id_map:
            next_item["id"] = id_map[old_id]
            next_item["legacy_agenda_id"] = old_id
        items.append(next_item)
    updated["agenda_items"] = items
    return updated


def _apply_mapping_to_candidates(data: dict[str, Any], id_map: dict[str, str]) -> dict[str, Any]:
    updated = dict(data)
    candidates: list[dict[str, Any]] = []
    for candidate in data.get("candidates") or []:
        if not isinstance(candidate, dict):
            candidates.append(candidate)
            continue
        next_candidate = dict(candidate)
        old_id = candidate.get("target_agenda_id")
        if old_id in id_map:
            next_candidate["target_agenda_id"] = id_map[old_id]
            next_candidate["legacy_target_agenda_id"] = old_id
        candidates.append(next_candidate)
    updated["candidates"] = candidates
    return updated


def _apply_mapping_to_audit(data: dict[str, Any], id_map: dict[str, str]) -> dict[str, Any]:
    updated = dict(data)
    records: list[dict[str, Any]] = []
    for record in data.get("records") or []:
        if not isinstance(record, dict):
            records.append(record)
            continue
        next_record = dict(record)
        old_id = record.get("target_agenda_id")
        if old_id in id_map:
            next_record["target_agenda_id"] = id_map[old_id]
            next_record["legacy_target_agenda_id"] = old_id
        records.append(next_record)
    updated["records"] = records
    return updated


def normalize_applied_new_agenda_ids(
    *,
    expected_self_agenda_sha256: str | None = None,
    expected_candidates_sha256: str | None = None,
    expected_audit_sha256: str | None = None,
    apply: bool = False,
    now: str | None = None,
) -> dict[str, Any]:
    normalized_at = _now(now).isoformat()
    candidates_data = _read_yaml(AGENDA_CANDIDATES_FILE, {"version": 3, "candidates": []})
    self_agenda = _read_yaml(SELF_AGENDA_FILE, {"version": "1.4", "agenda_items": []})
    audit_data = _read_yaml(AUDIT_FILE, {"version": 1, "records": []})
    before = {
        "self_agenda_sha256": _file_hash(SELF_AGENDA_FILE),
        "agenda_candidates_sha256": _file_hash(AGENDA_CANDIDATES_FILE),
        "new_agenda_apply_audit_sha256": _file_hash(AUDIT_FILE),
        "proposal_queue_sha256": _load_proposal_hash(),
    }
    _check_expected_hashes(
        expected_self_agenda_sha256=expected_self_agenda_sha256,
        expected_candidates_sha256=expected_candidates_sha256,
        current_self_hash=before["self_agenda_sha256"],
        current_candidates_hash=before["agenda_candidates_sha256"],
        apply=apply,
    )
    _check_expected_audit_hash(
        expected_audit_sha256=expected_audit_sha256,
        current_audit_hash=before["new_agenda_apply_audit_sha256"],
        apply=apply,
    )

    mappings = _normalization_mappings(self_agenda)
    if not mappings:
        return {
            "written": False,
            "would_write": False,
            "reason": "no_legacy_cw022_agenda_ids",
            "mappings": [],
            "before": before,
            "after": before,
            "proposal_queue_written": False,
        }

    id_map = {item["old_agenda_id"]: item["new_agenda_id"] for item in mappings}
    preview_self = _apply_mapping_to_self_agenda(self_agenda, id_map)
    preview_self["updated_at"] = normalized_at
    preview_candidates = _apply_mapping_to_candidates(candidates_data, id_map)
    preview_candidates["updated_at"] = normalized_at
    preview_audit = _apply_mapping_to_audit(audit_data, id_map)
    preview_audit.setdefault("version", 1)
    audit_record = {
        "normalized_at": normalized_at,
        "cw": "CW-022",
        "action": "normalize_new_agenda_ids",
        "mappings": mappings,
        "target_files": [str(SELF_AGENDA_FILE), str(AGENDA_CANDIDATES_FILE), str(AUDIT_FILE)],
        "before": before,
        "proposal_queue_written": False,
        "execution": "not_approved_not_executed",
    }
    preview_audit.setdefault("records", []).append(audit_record)

    rendered_self = yaml.safe_dump(preview_self, allow_unicode=True, sort_keys=False)
    rendered_candidates = yaml.safe_dump(preview_candidates, allow_unicode=True, sort_keys=False)
    audit_record["after"] = {
        "self_agenda_sha256": hashlib.sha256(rendered_self.encode("utf-8")).hexdigest(),
        "agenda_candidates_sha256": hashlib.sha256(rendered_candidates.encode("utf-8")).hexdigest(),
        "proposal_queue_sha256": before["proposal_queue_sha256"],
    }
    rendered_audit = yaml.safe_dump(preview_audit, allow_unicode=True, sort_keys=False)
    after = {
        **audit_record["after"],
        "new_agenda_apply_audit_sha256": hashlib.sha256(rendered_audit.encode("utf-8")).hexdigest(),
    }
    result = {
        "written": False,
        "would_write": True,
        "reason": "dry_run" if not apply else "normalized",
        "mappings": mappings,
        "before": before,
        "after": after,
        "proposal_queue_written": False,
    }
    if not apply:
        return result

    backups: dict[str, str] = {}
    for path, label in (
        (SELF_AGENDA_FILE, "self_agenda"),
        (AGENDA_CANDIDATES_FILE, "agenda_candidates"),
        (AUDIT_FILE, "audit"),
    ):
        if path.exists():
            backup = path.with_name(f"{path.name}.cw022-id-normalization-backup-{_now(now).strftime('%Y%m%d-%H%M%S')}")
            shutil.copy2(path, backup)
            backups[label] = str(backup)
    _write_yaml(SELF_AGENDA_FILE, preview_self)
    _write_yaml(AGENDA_CANDIDATES_FILE, preview_candidates)
    _write_yaml(AUDIT_FILE, preview_audit)
    result["written"] = True
    result["backup"] = backups
    result["after"] = {
        "self_agenda_sha256": _file_hash(SELF_AGENDA_FILE),
        "agenda_candidates_sha256": _file_hash(AGENDA_CANDIDATES_FILE),
        "new_agenda_apply_audit_sha256": _file_hash(AUDIT_FILE),
        "proposal_queue_sha256": _load_proposal_hash(),
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-id")
    parser.add_argument("--normalize-applied-ids", action="store_true")
    parser.add_argument("--apply-ready", action="store_true")
    parser.add_argument("--cap", type=int, default=3)
    parser.add_argument("--expected-self-agenda-sha256")
    parser.add_argument("--expected-candidates-sha256")
    parser.add_argument("--expected-audit-sha256")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    if args.apply_ready:
        result = apply_ready_new_agenda_candidates(
            apply=args.apply,
            cap=args.cap,
            now=args.now,
        )
    elif args.normalize_applied_ids:
        result = normalize_applied_new_agenda_ids(
            expected_self_agenda_sha256=args.expected_self_agenda_sha256,
            expected_candidates_sha256=args.expected_candidates_sha256,
            expected_audit_sha256=args.expected_audit_sha256,
            apply=args.apply,
            now=args.now,
        )
    else:
        if not args.candidate_id:
            parser.error("--candidate-id is required unless --normalize-applied-ids or --apply-ready is set")
        result = apply_new_agenda_candidate(
            args.candidate_id,
            expected_self_agenda_sha256=args.expected_self_agenda_sha256,
            expected_candidates_sha256=args.expected_candidates_sha256,
            apply=args.apply,
            now=args.now,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
