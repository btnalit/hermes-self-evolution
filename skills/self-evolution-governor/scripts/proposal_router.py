#!/usr/bin/env python3
"""
Self-Evolution Governor - Proposal Router.
Consumes approved proposals from proposal_queue.yaml and routes them to ops-gate.

State machine:
  draft -> pending_user_approval -> approved -> scheduled -> running -> implemented -> verified
                                                                              -> failed
                                                                  -> rollback_required

  Other terminal states: rejected, deferred, expired

Usage:
  python3 proposal_router.py                          # Process all approved proposals
  python3 proposal_router.py --status                  # Show queue summary
  python3 proposal_router.py --dry-run                 # Preview, don't modify
  python3 proposal_router.py --verify-implemented       # Auto-verify implemented proposals (whitelist-gated)
  python3 proposal_router.py --verify-implemented --dry-run  # Preview verification, don't modify
  python3 proposal_router.py --cleanup                 # Archive old terminal-state proposals
  python3 proposal_router.py --cleanup --dry-run       # Preview cleanup
  python3 proposal_router.py --cleanup-scope           # Show cleanup scope safety documentation
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

from _paths import EVOLUTION_DIR, PROPOSAL_FILE

TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def generate_proposal_id() -> str:
    today = datetime.now(TZ).strftime("%Y%m%d")
    seq = uuid.uuid4().hex[:4]
    return f"P-{today}-{seq}"


def read_proposals() -> dict:
    if not PROPOSAL_FILE.exists():
        return {"version": 1, "updated_at": now_iso(), "proposals": []}
    content = PROPOSAL_FILE.read_text()
    if not content.strip() or content.strip() in ("{}", "[]"):
        return {"version": 1, "updated_at": now_iso(), "proposals": []}
    import yaml  # type: ignore
    try:
        return yaml.safe_load(content) or {"version": 1, "updated_at": now_iso(), "proposals": []}
    except Exception:
        return {"version": 1, "updated_at": now_iso(), "proposals": []}


def write_proposals(data: dict):
    import yaml
    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    PROPOSAL_FILE.write_text(content)


def create_proposal(
    title: str,
    proposal_type: str,
    scores: dict | None = None,
    evidence: list | None = None,
    suggested_action: str | None = None,
    requires_ops_gate: bool = True,
    expires_days: int = 7,
) -> dict:
    data = read_proposals()
    now = now_iso()
    s = scores or {}

    proposal = {
        "id": generate_proposal_id(),
        "title": title,
        "type": proposal_type,
        "status": "draft",
        "scores": {
            "impact": s.get("impact", 0.5),
            "recurrence": s.get("recurrence", 0.5),
            "confidence": s.get("confidence", 0.5),
            "actionability": s.get("actionability", 0.5),
            "risk_level": s.get("risk_level", "none"),
            "priority_score": s.get("priority_score", 0.0),
            "speak_score": s.get("speak_score", 0.0),
        },
        "evidence": evidence or [],
        "suggested_action": {
            "summary": suggested_action or "",
            "requires_ops_gate": requires_ops_gate,
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
        "timestamps": {
            "created_at": now,
            "updated_at": now,
            "expires_at": None if expires_days <= 0 else (datetime.now(TZ) + timedelta(days=expires_days)).isoformat(),
        },
    }

    data.setdefault("proposals", []).append(proposal)
    data["updated_at"] = now
    write_proposals(data)
    return proposal


def process_approved_proposals(dry_run: bool = False) -> list[dict]:
    data = read_proposals()
    processed = []
    now = now_iso()

    for p in data.get("proposals", []):
        if p.get("status") != "approved":
            continue

        pid = p.get("id", "?")
        title = p.get("title", "")
        requires_ops = p.get("suggested_action", {}).get("requires_ops_gate", True)

        if dry_run:
            processed.append({
                "id": pid,
                "title": title,
                "action": "WOULD_ROUTE_TO_OPS_GATE",
                "requires_ops_gate": requires_ops,
            })
            continue

        p["status"] = "scheduled"
        p["execution"]["status"] = "scheduled"
        p["timestamps"]["updated_at"] = now
        processed.append({
            "id": pid,
            "title": title,
            "action": "SCHEDULED_FOR_OPS_GATE",
            "note": "Ready for ops_gate_runner.py execution. Requires precheck+execute+verify.",
        })

    if not dry_run and processed:
        data["updated_at"] = now
        write_proposals(data)

    return processed


def show_status() -> dict:
    data = read_proposals()
    proposals = data.get("proposals", [])
    by_status: dict[str, int] = {}
    for p in proposals:
        s = p.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1

    return {
        "ts": now_iso(),
        "total": len(proposals),
        "by_status": by_status,
        "stale_days": STALE_DAYS,
        "auto_archive_days": AUTO_ARCHIVE_DAYS,
        "proposals": [
            {
                "id": p["id"],
                "title": p.get("title", ""),
                "status": p.get("status", ""),
                "type": p.get("type", ""),
                "priority_score": p.get("scores", {}).get("priority_score", 0),
            }
            for p in proposals
        ],
    }


# ── Lifecycle constants ──
STALE_DAYS = 7       # Proposals older than this and not yet approved → flagged as stale
AUTO_ARCHIVE_DAYS = 14  # Proposals in terminal state and older than this → auto-archived

# ── Verification method whitelist (Phase A: P-20260428-av-v13a) ──
# Only verification.method values matching these patterns are accepted.
# This prevents arbitrary shell commands or injection from being executed.
VERIFICATION_METHOD_WHITELIST = [
    r'dry-run\s+(comparison|across|scan|test)',
    r'all\s+(false.positive|test|case|scenario).*(correctly|passed|ignored)',
    r'manual\s+(verification|inspection|check|review|test)',
    r'test\s+(suite|case|scenario|run).*pass',
    r'verified\s+by\s+user',
    r'approved\s+by',
    r'\d+\s+test',
    r'Old\s+\d+\s+FP',
    r'syntax\s+(check|ok|passed)',
    r'AST\s+parse\s+OK',
    r'verification\s+(method|result).*(pass|fail)',
]


def is_verification_method_allowed(method: str) -> bool:
    """Check if a verification method string matches the whitelist.

    Rejects empty strings, strings shorter than 10 chars, and strings
    containing shell metacharacters (;, |, $, `, \\\\, &&, ||).
    """
    if not method or len(method.strip()) < 10:
        return False
    # Reject shell metacharacters
    if re.search(r'[;&$`|(){}\\]', method):
        return False
    for pattern in VERIFICATION_METHOD_WHITELIST:
        if re.search(pattern, method, re.IGNORECASE):
            return True
    return False


def cleanup_expired_proposals(dry_run: bool = False) -> dict:
    """
    Scan all proposals and:
    1. Mark expired (past expires_at) as 'expired'
    2. Mark stale (pending > STALE_DAYS) as 'deferred'
    3. Auto-archive terminal-state proposals older than AUTO_ARCHIVE_DAYS
    """
    data = read_proposals()
    now = datetime.now(TZ)
    now_str = now_iso()
    modified = []
    skipped = 0

    for p in data.get("proposals", []):
        pid = p.get("id", "?")
        title = p.get("title", "")
        ts = p.get("timestamps", {})
        status = p.get("status", "draft")

        # Check expires_at
        expires_at_str = ts.get("expires_at")
        if expires_at_str and status in ("draft", "pending_user_approval"):
            try:
                expires = datetime.fromisoformat(expires_at_str)
                if now > expires:
                    if dry_run:
                        modified.append(f"{pid} ({title}): would mark expired")
                    else:
                        p["status"] = "expired"
                        ts["updated_at"] = now_str
                        modified.append(f"{pid} ({title}): marked expired")
                    continue
            except (ValueError, TypeError):
                pass

        # Check stale (pending for > STALE_DAYS)
        created_at_str = ts.get("created_at")
        if created_at_str and status in ("draft", "pending_user_approval"):
            try:
                created = datetime.fromisoformat(created_at_str)
                age = (now - created).days
                if age >= STALE_DAYS:
                    if dry_run:
                        modified.append(f"{pid} ({title}): would mark deferred (age={age}d)")
                    else:
                        p["status"] = "deferred"
                        ts["updated_at"] = now_str
                        modified.append(f"{pid} ({title}): marked deferred (age={age}d)")
                    continue
            except (ValueError, TypeError):
                pass

        # Auto-archive terminal state (implemented, verified, rejected, expired, failed)
        terminal_states = ("implemented", "verified", "rejected", "expired", "failed", "deferred", "rollback_required")
        updated_at_str = ts.get("updated_at") or ts.get("created_at")
        if status in terminal_states and updated_at_str:
            try:
                updated = datetime.fromisoformat(updated_at_str)
                age = (now - updated).days
                if age >= AUTO_ARCHIVE_DAYS:
                    if dry_run:
                        modified.append(f"{pid} ({title}): would archive (status={status}, age={age}d)")
                    else:
                        # Remove from active proposals list
                        skipped += 1
                        modified.append(f"{pid} ({title}): archived (status={status}, age={age}d)")
                        continue
            except (ValueError, TypeError):
                pass

    if not dry_run:
        # Rebuild proposals list, removing archived terminal-state items
        filtered = []
        for p in data.get("proposals", []):
            status = p.get("status", "")
            if status not in terminal_states:
                filtered.append(p)
                continue
            # Check age for terminal-state items
            ts2 = p.get("timestamps", {})
            ref_ts = ts2.get("updated_at") or ts2.get("created_at") or ""
            if not ref_ts:
                filtered.append(p)
                continue
            try:
                ref_dt = datetime.fromisoformat(ref_ts)
                if (now - ref_dt).days >= AUTO_ARCHIVE_DAYS:
                    continue  # remove from list
            except (ValueError, TypeError):
                pass
            filtered.append(p)

        data["proposals"] = filtered
        data["updated_at"] = now_str
        write_proposals(data)

    result = {
        "ts": now_str,
        "dry_run": dry_run,
        "modified": len(modified),
        "skipped": skipped,
        "details": modified,
    }
    return result


def verify_implemented(dry_run: bool = False) -> dict:
    """
    Auto-verify proposals with status 'implemented'.

    Safety: For each 'implemented' proposal, checks that verification.method
    matches the VERIFICATION_METHOD_WHITELIST.  Proposals with non-whitelisted
    or suspicious methods are skipped with a warning.
    """
    data = read_proposals()
    now_str = now_iso()
    verified = []
    skipped = []

    for p in data.get("proposals", []):
        if p.get("execution", {}).get("status") != "implemented":
            continue
        pid = p.get("id", "?")
        title = p.get("title", "")
        method = p.get("verification", {}).get("method", "")

        if is_verification_method_allowed(method):
            if dry_run:
                verified.append(f"{pid} ({title}): would mark verified (method={method[:60]}...)")
            else:
                p["status"] = "verified"
                p["execution"]["status"] = "verified"
                p["verification"]["result"] = "pass"
                p["timestamps"]["updated_at"] = now_str
                verified.append(f"{pid} ({title}): marked verified")
        else:
            skipped.append({
                "id": pid,
                "title": title,
                "reason": "verification.method not in whitelist",
                "method_preview": method[:80] if method else "(empty)",
            })

    if not dry_run and verified:
        data["updated_at"] = now_str
        write_proposals(data)

    return {
        "ts": now_str,
        "dry_run": dry_run,
        "verified": len(verified),
        "skipped": len(skipped),
        "details_verified": verified,
        "details_skipped": skipped,
    }


def show_cleanup_scope() -> dict:
    """
    Document the exact scope of --cleanup to prevent accidental data loss.

    Affected states:
      - draft, pending_user_approval: can be marked 'expired' if past expires_at
      - draft, pending_user_approval: can be marked 'deferred' if > STALE_DAYS (7d)
      - implemented, verified, rejected, expired, failed, deferred, rollback_required:
        can be archived (removed) if > AUTO_ARCHIVE_DAYS (14d)

    NEVER affected:
      - approved — never expired, never deferred, never archived
      - scheduled — never expired, never deferred, never archived
      - running — never expired, never deferred, never archived

    Scope is limited to:
      1. Expiration: only draft/pending_user_approval proposals past their
         expires_at timestamp.
      2. Staleness: only draft/pending_user_approval proposals older than
         STALE_DAYS since created_at.
      3. Archival: only proposals in terminal states (implemented/verified/
         rejected/expired/failed/deferred/rollback_required) where the
         updated_at is older than AUTO_ARCHIVE_DAYS.
    """
    return {
        "ts": now_iso(),
        "affected_states": {
            "can_expire": ["draft", "pending_user_approval"],
            "can_defer": ["draft", "pending_user_approval"],
            "can_archive": ["implemented", "verified", "rejected", "expired",
                            "failed", "deferred", "rollback_required"],
        },
        "never_affected": ["approved", "scheduled", "running"],
        "time_bounds": {
            "expire_after": "expires_at",
            "defer_after": f"{STALE_DAYS}d since created_at",
            "archive_after": f"{AUTO_ARCHIVE_DAYS}d since updated_at",
        },
        "note": "Cleanup never touches approved/scheduled/running proposals. "
                "These are active workflow states and are explicitly excluded.",
    }


def main():
    if "--verify-implemented" in sys.argv:
        dry_run = "--dry-run" in sys.argv
        result = verify_implemented(dry_run=dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if "--cleanup-scope" in sys.argv:
        print(json.dumps(show_cleanup_scope(), ensure_ascii=False, indent=2))
        return

    if "--status" in sys.argv:
        print(json.dumps(show_status(), ensure_ascii=False, indent=2))
        return

    if "--cleanup" in sys.argv:
        dry_run = "--dry-run" in sys.argv
        result = cleanup_expired_proposals(dry_run=dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    dry_run = "--dry-run" in sys.argv
    processed = process_approved_proposals(dry_run=dry_run)

    result = {
        "ts": now_iso(),
        "dry_run": dry_run,
        "processed": len(processed),
        "details": processed,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
