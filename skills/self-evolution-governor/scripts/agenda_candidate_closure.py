#!/usr/bin/env python3
"""CW-022 agenda candidate closure helper.

Creates advisory proposal_queue entries from agenda_candidates.yaml. This helper
never creates or mutates status=approved; proposal_router.py remains the only
execution router and only consumes explicitly approved proposals.
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
AGENDA_CANDIDATES_FILE = STATE_DIR / "agenda_candidates.yaml"
AGENDA_DECISIONS_FILE = STATE_DIR / "agenda_speak_decisions.yaml"
PROPOSAL_FILE = STATE_DIR / "proposal_queue.yaml"
ALLOWED_OUTPUT_STATUSES = {"draft", "pending_user_approval"}
PENDING_STATUSES = {"pending_user_approval", "pending"}
EXPECTED_CURRENT_BY_KIND = {
    "agenda_candidate": ("candidate_ready", "create_proposal"),
    "quality_proposal": ("quality_proposal_ready", "generate_quality_proposal"),
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


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


def _proposal_id(candidate_id: str) -> str:
    return f"P-CW022-{candidate_id}"


def _load_candidates() -> dict[str, Any]:
    return _read_yaml(AGENDA_CANDIDATES_FILE, {"version": 3, "candidates": []})


def _load_decisions() -> dict[str, Any]:
    return _read_yaml(AGENDA_DECISIONS_FILE, {"version": 1, "decisions": []})


def _load_queue() -> dict[str, Any]:
    data = _read_yaml(PROPOSAL_FILE, {"version": 1, "updated_at": now_iso(), "proposals": []})
    data.setdefault("version", 1)
    data.setdefault("proposals", [])
    return data


def _find_candidate(candidate_id: str) -> dict[str, Any]:
    data = _load_candidates()
    for candidate in data.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("candidate_id") == candidate_id or candidate.get("agenda_id") == candidate_id:
            return dict(candidate)
    raise SystemExit(f"candidate not found: {candidate_id}")


def _find_speak_decision(candidate_id: str) -> dict[str, Any]:
    data = _load_decisions()
    for decision in data.get("decisions") or []:
        if isinstance(decision, dict) and decision.get("candidate_id") == candidate_id:
            return dict(decision)
    return {}


def _existing_proposal(queue: dict[str, Any], candidate_id: str) -> dict[str, Any] | None:
    for proposal in queue.get("proposals") or []:
        if not isinstance(proposal, dict):
            continue
        source = proposal.get("source") or {}
        if source.get("candidate_id") == candidate_id:
            return proposal
    return None


def _candidate_index() -> dict[str, dict[str, Any]]:
    data = _load_candidates()
    indexed = {}
    for candidate in data.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or candidate.get("agenda_id") or "")
        if candidate_id:
            indexed[candidate_id] = dict(candidate)
    return indexed


def _current_candidate_is_valid(proposal: dict[str, Any], candidates: dict[str, dict[str, Any]]) -> tuple[bool, str]:
    source = proposal.get("source") or {}
    candidate_id = str(source.get("candidate_id") or "")
    candidate_kind = source.get("candidate_kind") or "agenda_candidate"
    if not candidate_id:
        return False, "missing_source_candidate_id"

    candidate = candidates.get(candidate_id)
    if not candidate:
        expected_status = EXPECTED_CURRENT_BY_KIND.get(candidate_kind, ("candidate_ready", ""))[0]
        return False, f"no_current_{expected_status}_candidate"

    expected_status, expected_action = EXPECTED_CURRENT_BY_KIND.get(candidate_kind, ("candidate_ready", ""))
    if candidate.get("candidate_kind") != candidate_kind:
        return False, f"candidate_kind_changed:{candidate.get('candidate_kind')}!= {candidate_kind}"
    if candidate.get("status") != expected_status:
        return False, f"candidate_status_changed:{candidate.get('status')}!= {expected_status}"
    if expected_action and candidate.get("action") != expected_action:
        return False, f"candidate_action_changed:{candidate.get('action')}!= {expected_action}"
    return True, "current_candidate_still_valid"


def _validate_candidate(candidate: dict[str, Any], *, expected_kind: str, expected_status: str) -> None:
    candidate_kind = candidate.get("candidate_kind") or "agenda_candidate"
    if candidate_kind != expected_kind:
        raise SystemExit(f"candidate {candidate.get('candidate_id')} is {candidate_kind}, expected {expected_kind}")
    if candidate.get("status") != expected_status:
        raise SystemExit(f"candidate {candidate.get('candidate_id')} is not {expected_status}")


def _scores(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "maturity_score": candidate.get("maturity_score", 0),
        "evidence_strength": candidate.get("evidence_strength", 0),
        "evidence_count": candidate.get("evidence_count", 0),
        "qualified_evidence_count": candidate.get("qualified_evidence_count", 0),
        "actionable_qualified_count": candidate.get("actionable_qualified_count", 0),
        "observation_days": candidate.get("observation_days", 0),
    }


def _proposal_summary(candidate: dict[str, Any], candidate_kind: str) -> str:
    if candidate_kind == "quality_proposal":
        return (
            "Draft a concrete quality proposal for the monitoring/Grafana anomaly "
            "trend, using only the candidate evidence summary and leaving execution "
            "blocked until explicit approval."
        )
    return (
        "Draft a concrete improvement proposal for this matured agenda candidate, "
        "using only the candidate evidence summary and leaving execution blocked "
        "until explicit approval."
    )


def _build_proposal(
    candidate: dict[str, Any],
    *,
    candidate_kind: str,
    proposal_status: str,
    speak_decision: dict[str, Any],
) -> dict[str, Any]:
    if proposal_status not in ALLOWED_OUTPUT_STATUSES:
        raise SystemExit("proposal status must be draft or pending_user_approval")
    candidate_id = str(candidate.get("candidate_id") or candidate.get("agenda_id") or "")
    timestamp = now_iso()
    return {
        "id": _proposal_id(candidate_id),
        "title": candidate.get("title", ""),
        "type": candidate.get("type", ""),
        "status": proposal_status,
        "scores": _scores(candidate),
        "evidence": [
            {
                "source": "agenda_candidates.yaml",
                "candidate_id": candidate_id,
                "agenda_id": candidate.get("agenda_id"),
                "evidence_count": candidate.get("evidence_count", 0),
                "qualified_evidence_count": candidate.get("qualified_evidence_count", 0),
                "actionable_qualified_count": candidate.get("actionable_qualified_count", 0),
                "observation_days": candidate.get("observation_days", 0),
            }
        ],
        "suggested_action": {
            "summary": _proposal_summary(candidate, candidate_kind),
            "requires_ops_gate": True,
        },
        "approval": {
            "required": True,
            "approved_by": None,
            "approved_at": None,
        },
        "execution": {
            "gate_task_id": None,
            "out_dir": None,
            "status": "not_started",
        },
        "verification": {
            "method": "",
            "result": None,
        },
        "source": {
            "cw": "CW-022",
            "candidate_id": candidate_id,
            "agenda_id": candidate.get("agenda_id"),
            "candidate_kind": candidate_kind,
            "candidate_status": candidate.get("status"),
            "candidate_action": candidate.get("action"),
            "candidate_generated_at": candidate.get("generated_at"),
            "speak_decision": {
                "decision": speak_decision.get("decision"),
                "reason": speak_decision.get("reason"),
                "mapped_action": speak_decision.get("mapped_action"),
            },
        },
        "timestamps": {
            "created_at": timestamp,
            "updated_at": timestamp,
            "expires_at": (datetime.now(TZ) + timedelta(days=7)).isoformat(),
        },
    }


def _close_candidate(
    candidate_id: str,
    *,
    expected_kind: str,
    expected_status: str,
    apply: bool = False,
    proposal_status: str = "pending_user_approval",
) -> dict[str, Any]:
    if proposal_status not in ALLOWED_OUTPUT_STATUSES:
        raise SystemExit("proposal status must be draft or pending_user_approval")
    candidate = _find_candidate(candidate_id)
    _validate_candidate(candidate, expected_kind=expected_kind, expected_status=expected_status)
    queue = _load_queue()
    before_hash = _file_hash(PROPOSAL_FILE)
    existing = _existing_proposal(queue, str(candidate.get("candidate_id") or candidate_id))
    if existing:
        return {
            "candidate_id": candidate_id,
            "proposal_id": existing.get("id"),
            "status": existing.get("status"),
            "written": False,
            "would_write": False,
            "reason": "proposal_already_exists_for_candidate",
            "before_sha256": before_hash,
            "after_sha256": before_hash,
        }

    speak_decision = _find_speak_decision(candidate_id)
    proposal = _build_proposal(
        candidate,
        candidate_kind=expected_kind,
        proposal_status=proposal_status,
        speak_decision=speak_decision,
    )
    preview_queue = dict(queue)
    preview_queue["proposals"] = list(queue.get("proposals") or []) + [proposal]
    preview_queue["updated_at"] = now_iso()
    rendered = yaml.safe_dump(preview_queue, allow_unicode=True, sort_keys=False)
    after_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    result = {
        "candidate_id": candidate_id,
        "proposal_id": proposal["id"],
        "proposal_status": proposal["status"],
        "written": False,
        "would_write": apply,
        "reason": "dry_run" if not apply else "created",
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "proposal": proposal,
    }
    if not apply:
        return result

    if proposal["status"] == "approved":
        raise SystemExit("refusing to write approved proposal")
    if PROPOSAL_FILE.exists():
        backup = PROPOSAL_FILE.with_name(
            f"{PROPOSAL_FILE.name}.cw022-closure-backup-{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(PROPOSAL_FILE, backup)
        result["backup"] = str(backup)
    _write_yaml(PROPOSAL_FILE, preview_queue)
    result["written"] = True
    return result


def close_agenda_candidate(
    candidate_id: str,
    *,
    apply: bool = False,
    proposal_status: str = "pending_user_approval",
) -> dict[str, Any]:
    return _close_candidate(
        candidate_id,
        expected_kind="agenda_candidate",
        expected_status="candidate_ready",
        apply=apply,
        proposal_status=proposal_status,
    )


def close_quality_proposal(
    candidate_id: str,
    *,
    apply: bool = False,
    proposal_status: str = "pending_user_approval",
) -> dict[str, Any]:
    return _close_candidate(
        candidate_id,
        expected_kind="quality_proposal",
        expected_status="quality_proposal_ready",
        apply=apply,
        proposal_status=proposal_status,
    )


def reconcile_proposal_queue(*, apply: bool = False, now: str | None = None) -> dict[str, Any]:
    timestamp = now or now_iso()
    queue = _load_queue()
    candidates = _candidate_index()
    before_hash = _file_hash(PROPOSAL_FILE)
    stale = []
    preview_queue = dict(queue)
    preview_proposals = []

    for proposal in queue.get("proposals") or []:
        if not isinstance(proposal, dict):
            preview_proposals.append(proposal)
            continue
        if proposal.get("status") not in PENDING_STATUSES:
            preview_proposals.append(proposal)
            continue

        is_valid, reason = _current_candidate_is_valid(proposal, candidates)
        if is_valid:
            preview_proposals.append(proposal)
            continue

        updated = dict(proposal)
        updated["previous_status"] = proposal.get("status")
        updated["status"] = "stale_pending"
        updated["stale_reason"] = reason
        updated.setdefault("timestamps", {})
        if isinstance(updated["timestamps"], dict):
            updated["timestamps"]["stale_at"] = timestamp
            updated["timestamps"]["updated_at"] = timestamp
        else:
            updated["timestamps"] = {"stale_at": timestamp, "updated_at": timestamp}
        source = updated.setdefault("source", {})
        if isinstance(source, dict):
            source["reconcile_checked_at"] = timestamp
            source["reconcile_reason"] = reason
        stale.append({
            "proposal_id": proposal.get("id"),
            "candidate_id": (proposal.get("source") or {}).get("candidate_id"),
            "previous_status": proposal.get("status"),
            "new_status": "stale_pending",
            "reason": reason,
        })
        preview_proposals.append(updated)

    preview_queue["proposals"] = preview_proposals
    preview_queue["updated_at"] = timestamp
    rendered = yaml.safe_dump(preview_queue, allow_unicode=True, sort_keys=False)
    after_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    would_write = bool(stale)
    result = {
        "checked_at": timestamp,
        "written": False,
        "would_write": would_write,
        "stale_pending": stale,
        "stale_count": len(stale),
        "before_sha256": before_hash,
        "after_sha256": after_hash if would_write else before_hash,
    }
    if not apply or not would_write:
        return result

    if PROPOSAL_FILE.exists():
        backup = PROPOSAL_FILE.with_name(
            f"{PROPOSAL_FILE.name}.cw022-reconcile-backup-{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(PROPOSAL_FILE, backup)
        result["backup"] = str(backup)
    _write_yaml(PROPOSAL_FILE, preview_queue)
    result["written"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--close-agenda-candidate", metavar="CANDIDATE_ID")
    group.add_argument("--close-quality-proposal", metavar="CANDIDATE_ID")
    group.add_argument("--reconcile-proposal-queue", action="store_true")
    parser.add_argument("--proposal-status", choices=sorted(ALLOWED_OUTPUT_STATUSES), default="pending_user_approval")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    if args.reconcile_proposal_queue:
        result = reconcile_proposal_queue(apply=args.apply)
    elif args.close_agenda_candidate:
        result = close_agenda_candidate(
            args.close_agenda_candidate,
            apply=args.apply,
            proposal_status=args.proposal_status,
        )
    else:
        result = close_quality_proposal(
            args.close_quality_proposal,
            apply=args.apply,
            proposal_status=args.proposal_status,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
