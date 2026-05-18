#!/usr/bin/env python3
"""
Agenda review action helper.

Default mode is dry-run. Live changes require:

  --apply --approved-by <name>

This helper records explicit human review outcomes for agenda items that would
otherwise linger in review_pending/candidate_ready forever.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

TZ = timezone(timedelta(hours=8))
STATE_DIR = Path("/vol1/.hermes/state/evolution")
AGENDA_FILE = STATE_DIR / "self_agenda.yaml"

ALLOWED_ACTIONS = {
    "deferred": "Valid agenda, not current priority.",
    "resolved": "Issue has been handled or no longer matters.",
    "archived": "Not useful enough to keep active.",
    "converted_to_proposal": "Agenda became a proposal or external task.",
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def load_agenda() -> dict[str, Any]:
    if not AGENDA_FILE.exists() or not AGENDA_FILE.read_text().strip():
        return {"agenda_items": []}
    data = yaml.safe_load(AGENDA_FILE.read_text()) or {}
    return data if isinstance(data, dict) else {"agenda_items": []}


def write_agenda(data: dict[str, Any]) -> None:
    AGENDA_FILE.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False))


def find_item(data: dict[str, Any], agenda_id: str) -> dict[str, Any] | None:
    for item in data.get("agenda_items") or []:
        if isinstance(item, dict) and item.get("id") == agenda_id:
            return item
    return None


def apply_review_action(
    agenda_id: str,
    action: str,
    reason: str,
    approved_by: str | None,
    apply: bool,
) -> dict[str, Any]:
    if action not in ALLOWED_ACTIONS:
        raise SystemExit(f"unsupported action: {action}")
    if apply and not approved_by:
        raise SystemExit("--apply requires --approved-by")

    data = load_agenda()
    item = find_item(data, agenda_id)
    if not item:
        raise SystemExit(f"agenda not found: {agenda_id}")

    old_status = item.get("status")
    timestamp = now_iso()
    review_record = {
        "at": timestamp,
        "action": action,
        "reason": reason,
        "approved_by": approved_by,
        "dry_run": not apply,
    }

    result = {
        "ts": timestamp,
        "dry_run": not apply,
        "agenda_id": agenda_id,
        "title": item.get("title"),
        "old_status": old_status,
        "new_status": action,
        "action_meaning": ALLOWED_ACTIONS[action],
        "reason": reason,
        "approved_by": approved_by,
        "would_write": apply,
    }

    if not apply:
        return result

    backup = AGENDA_FILE.with_name(
        f"{AGENDA_FILE.name}.review-action-backup-{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}"
    )
    shutil.copy2(AGENDA_FILE, backup)
    item["status"] = action
    item["review_decision"] = review_record
    item.setdefault("review_history", []).append(review_record)
    item["updated_at"] = timestamp
    data["updated_at"] = timestamp
    write_agenda(data)
    result["backup"] = str(backup)
    result["would_write"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agenda-id", required=True)
    parser.add_argument("--action", required=True, choices=sorted(ALLOWED_ACTIONS))
    parser.add_argument("--reason", required=True)
    parser.add_argument("--approved-by")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    result = apply_review_action(
        agenda_id=args.agenda_id,
        action=args.action,
        reason=args.reason,
        approved_by=args.approved_by,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
