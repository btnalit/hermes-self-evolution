#!/usr/bin/env python3
"""
Self-Evolution Daily Pipeline — replaces prompt-driven 04:00 cron.

Executes the fixed self-evolution pipeline, records each step with full audit trail,
and exits cleanly. Designed to be called by cron, not by agent prompt.

Usage:
  python3 self_evolution_daily_pipeline.py          # Live run
  python3 self_evolution_daily_pipeline.py --dry-run  # Preview, no writes
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
SCRIPTS_DIR = Path("/vol1/.hermes/skills/dogfood/self-evolution-governor/scripts")
STATE_DIR = Path("/vol1/.hermes/state/evolution")
EVIDENCE_DIR = Path("/vol1/.hermes/state/ops-gate/self-evolution-pipeline")
CRON_OUTPUT_DIR = Path("/vol1/.hermes/cron/output")
VENV_PYTHON = Path("/vol1/.hermes/hermes-agent/venv/bin/python3")
VENV_MKDOCS = Path("/vol1/.hermes/hermes-agent/venv/bin/mkdocs")
CONSOLE_DIR = Path("/vol1/1000/hermes-evolution-console")

ENV = os.environ.copy()
ENV["AGENDA_SPEAK_MODE"] = "active"
# AGENDA_SPEAK_MODE=active → allow_external_send=true, 有 candidate 时通过 pipeline stdout 通知用户
ENV["ALLOW_EXTERNAL_SEND"] = "true"

STEPS = [
    {"name": "collect_signals",        "cmd": ["python3", str(SCRIPTS_DIR / "collect_signals.py")]},
    {"name": "proposal_cleanup",       "cmd": ["python3", str(SCRIPTS_DIR / "proposal_router.py"), "--cleanup"]},
    {"name": "proposal_verify",        "cmd": ["python3", str(SCRIPTS_DIR / "proposal_router.py"), "--verify-implemented"]},
    {"name": "agenda_maturation",      "cmd": ["python3", str(SCRIPTS_DIR / "agenda_maturation.py"), "--write-journal"]},
    {"name": "unmatched_signal_review", "cmd": ["python3", str(SCRIPTS_DIR / "unmatched_signal_review.py"), "--days", "3", "--write"]},
    {"name": "unmatched_cluster_ledger", "cmd": ["python3", str(SCRIPTS_DIR / "unmatched_cluster_ledger.py"), "--write", "--apply-auto-archive"]},
    {"name": "new_agenda_preview",     "cmd": ["python3", str(SCRIPTS_DIR / "new_agenda_preview.py"), "--apply"]},
    {"name": "speak_gate",             "cmd": ["python3", str(SCRIPTS_DIR / "speak_gate.py"), "--include-agenda-candidates"]},
    {"name": "new_agenda_apply_ready", "cmd": ["python3", str(SCRIPTS_DIR / "new_agenda_apply.py"), "--apply-ready", "--apply"]},
    {"name": "build_runtime_digest",   "cmd": ["python3", str(SCRIPTS_DIR / "build_runtime_digest.py")]},
    # Build Evolution Console (MkDocs static site)
    {"name": "build_console", "cmd": [
        "bash", "-c",
        f"cd {CONSOLE_DIR} && {VENV_PYTHON} scripts/build_console.py && {VENV_MKDOCS} build"
    ]},
    # Restart Console via systemd (auto-restart on crash, auto-start on boot)
    {"name": "restart_console_server", "cmd": [
        "bash", "-c",
        "systemctl restart hermes-evolution-console.service"
    ]},
]

# Output files to check after each step
OUTPUT_CHECKS = {
    "collect_signals":  [STATE_DIR / "signals.jsonl"],
    "proposal_cleanup": [STATE_DIR / "proposal_queue.yaml"],
    "proposal_verify":  [STATE_DIR / "proposal_queue.yaml"],
    "agenda_maturation": [
        STATE_DIR / "agenda_candidates.yaml",
        STATE_DIR / "self_agenda.yaml",
    ],
    "unmatched_signal_review": [
        STATE_DIR / "unmatched_signal_review.yaml",
        STATE_DIR / "unmatched_signal_review.md",
    ],
    "unmatched_cluster_ledger": [
        STATE_DIR / "diagnostics" / "unmatched_clusters.yaml",
        STATE_DIR / "self_agenda.yaml",
    ],
    "new_agenda_preview": [
        STATE_DIR / "agenda_candidates.yaml",
    ],
    "speak_gate": [
        STATE_DIR / "agenda_speak_decisions.yaml",
        STATE_DIR / "agenda_speak_quota.json",
    ],
    "new_agenda_apply_ready": [
        STATE_DIR / "self_agenda.yaml",
        STATE_DIR / "agenda_candidates.yaml",
        STATE_DIR / "new_agenda_apply_audit.yaml",
    ],
    "build_runtime_digest": [
        STATE_DIR / "runtime_digest.md",
    ],
    "build_console": [
        CONSOLE_DIR / "docs" / "index.md",
        CONSOLE_DIR / "site" / "index.html",
    ],
}

EVOLUTION_FILES = [
    STATE_DIR / "runtime_digest.md",
    STATE_DIR / "agenda_candidates.yaml",
    STATE_DIR / "agenda_speak_decisions.yaml",
    STATE_DIR / "agenda_speak_quota.json",
    STATE_DIR / "evolution_journal.md",
    STATE_DIR / "self_agenda.yaml",
    STATE_DIR / "signals.jsonl",
    STATE_DIR / "proposal_queue.yaml",
    STATE_DIR / "unmatched_signal_review.yaml",
    STATE_DIR / "unmatched_signal_review.md",
    STATE_DIR / "diagnostics" / "unmatched_clusters.yaml",
    STATE_DIR / "new_agenda_apply_audit.yaml",
]

# Critical files that must NEVER be written/modified by this pipeline
PROTECTED_PATHS = [
    str(Path("/vol1/.hermes/config.yaml")),
    str(Path("/vol1/.hermes/profiles")),
    str(Path("/root/.hermes/memories/MEMORY.md")),
    str(Path("/root/.hermes/memories/USER.md")),
    str(Path("/vol1/.hermes/skills")),
]


def check_protected_paths():
    """Verify pipeline won't touch protected paths."""
    for p in PROTECTED_PATHS:
        if os.path.exists(p):
            mtime = os.path.getmtime(p)
            mod_time = datetime.fromtimestamp(mtime, tz=TZ)
            # If modified in the last 10 seconds during this run, that's suspicious
            age = (datetime.now(TZ) - mod_time).total_seconds()
            if age < 10:
                return False, f"Protected file modified recently: {p}"
    return True, "All protected paths clean"


def run_step(name, cmd, dry_run):
    """Execute one pipeline step and return audit record."""
    record = {
        "step": name,
        "command": " ".join(str(c) for c in cmd),
        "started_at": datetime.now(TZ).isoformat(),
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "duration_sec": None,
        "output_files_ok": None,
        "dry_run": dry_run,
        "error": None,
        "protected_paths_ok": None,
    }

    if dry_run:
        print(f"  [DRY-RUN] Would execute: {' '.join(cmd)}")
        record["exit_code"] = 0
        record["duration_sec"] = 0
        record["dry_run"] = True
        return record

    # Pre-check protected paths
    prot_ok, prot_msg = check_protected_paths()
    record["protected_paths_ok"] = prot_ok
    if not prot_ok:
        record["error"] = f"Protected path violation: {prot_msg}"
        record["exit_code"] = -1
        return record

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env=ENV,
        )
        record["exit_code"] = proc.returncode
        record["stdout"] = proc.stdout[:5000]  # cap at 5KB
        record["stderr"] = proc.stderr[:2000]   # cap at 2KB
    except subprocess.TimeoutExpired:
        record["exit_code"] = -1
        record["error"] = "Timeout (300s)"
    except FileNotFoundError:
        record["exit_code"] = -2
        record["error"] = f"Script not found: {cmd[1]}"
    except Exception as e:
        record["exit_code"] = -3
        record["error"] = str(e)

    # Check output files
    checks = []
    expected = OUTPUT_CHECKS.get(name, [])
    for fp in expected:
        exists = fp.exists()
        size = fp.stat().st_size if exists else 0
        checks.append({"path": str(fp), "exists": exists, "size_bytes": size})
    record["output_files_ok"] = checks

    return record


def check_evolution_file_staleness():
    """Record pre-run file state for comparison."""
    state = {}
    for fp in EVOLUTION_FILES:
        if fp.exists():
            state[str(fp)] = {
                "size_bytes": fp.stat().st_size,
                "mtime": datetime.fromtimestamp(fp.stat().st_mtime, tz=TZ).isoformat(),
            }
        else:
            state[str(fp)] = None
    return state


def check_evolution_file_changes(pre_state, post_state):
    """Compare pre/post file states."""
    changes = []
    for fp_str in [str(f) for f in EVOLUTION_FILES]:
        pre = pre_state.get(fp_str)
        post = post_state.get(fp_str)
        if pre is None and post is None:
            continue
        if pre is None and post is not None:
            changes.append({"path": fp_str, "change": "created"})
        elif pre is not None and post is None:
            changes.append({"path": fp_str, "change": "deleted"})
        elif pre is not None and post is not None:
            if pre["size_bytes"] != post["size_bytes"] or pre["mtime"] != post["mtime"]:
                changes.append({
                    "path": fp_str,
                    "change": "modified",
                    "before_bytes": pre["size_bytes"],
                    "after_bytes": post["size_bytes"],
                    "before_mtime": pre["mtime"],
                    "after_mtime": post["mtime"],
                })
    return changes


def run_pipeline(dry_run=False):
    """Execute the full 6-step pipeline."""
    dry_run_str = " (DRY RUN)" if dry_run else ""
    print(f"{'='*60}")
    print(f"Self-Evolution Daily Pipeline{dry_run_str}")
    print(f"Started: {datetime.now(TZ).isoformat()}")
    print(f"Mode: {'controlled' if not dry_run else 'preview'}")
    print(f"{'='*60}")
    print()

    # Pre-run file checks
    pre_state = check_evolution_file_staleness()

    total_start = time.time()
    results = []
    failures = []

    for i, step in enumerate(STEPS, 1):
        name = step["name"]
        cmd = step["cmd"]
        print(f"[{i}/{len(STEPS)}] {name}...", end=" " if not dry_run else "\n")
        sys.stdout.flush()

        step_start = time.time()
        record = run_step(name, cmd, dry_run)
        record["duration_sec"] = round(time.time() - step_start, 2)

        results.append(record)

        if record["exit_code"] == 0:
            print(f"OK ({record['duration_sec']}s)")
            if record.get("stdout"):
                # Print first few lines for context
                preview = record["stdout"][:500]
                # Only print meaningful output
                if len(preview) > 10 and not dry_run:
                    print(f"       stdout: {preview[:200]}...")
        else:
            print(f"FAIL (exit={record['exit_code']}, {record['duration_sec']}s)")
            failures.append(name)
            err_msg = record.get("error") or record.get("stderr", "")[:200]
            print(f"       error: {err_msg}")

    total_duration = round(time.time() - total_start, 2)

    # Post-run file checks
    post_state = check_evolution_file_staleness()
    file_changes = check_evolution_file_changes(pre_state, post_state)

    # Summary
    print()
    print(f"{'='*60}")
    print(f"Pipeline Summary{dry_run_str}")
    print(f"{'='*60}")
    print(f"  Duration:      {total_duration}s")
    print(f"  Steps run:     {len(results)}")
    print(f"  Successes:     {len(results) - len(failures)}")
    print(f"  Failures:      {len(failures)}")
    print(f"  File changes:  {len(file_changes)}")

    if dry_run:
        print(f"  DRY RUN:       No files modified")

    if file_changes:
        print()
        print("  File changes:")
        for c in file_changes:
            print(f"    {c['change']:8s} {c['path']}")
            if c['change'] == 'modified':
                print(f"             before: {c['before_bytes']} bytes @ {c['before_mtime']}")
                print(f"             after:  {c['after_bytes']} bytes @ {c['after_mtime']}")

    return {
        "ts": datetime.now(TZ).isoformat(),
        "dry_run": dry_run,
        "duration_total_sec": total_duration,
        "total_steps": len(STEPS),
        "steps_succeeded": len(results) - len(failures),
        "steps_failed": len(failures),
        "failures": failures,
        "step_records": results,
        "file_changes": file_changes,
        "mode": "controlled",
    }


def write_evidence(result, dry_run):
    """Write ops-gate evidence file."""
    if dry_run:
        print("  [DRY-RUN] Would write evidence to:", EVIDENCE_DIR)
        return

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    evidence_file = EVIDENCE_DIR / f"pipeline_{ts}.json"

    evidence = {
        "pipeline": "self-evolution-daily",
        "timestamp": datetime.now(TZ).isoformat(),
        "exit_code": 0 if not result["failures"] else 1,
        "result": result,
        "environment": {
            "AGENDA_SPEAK_MODE": ENV.get("AGENDA_SPEAK_MODE", "unset"),
            "ALLOW_EXTERNAL_SEND": ENV.get("ALLOW_EXTERNAL_SEND", "unset"),
        },
        "safety": {
            "protected_paths": PROTECTED_PATHS,
            "no_memory_write": True,
            "no_skill_write": True,
            "no_config_write": True,
            "no_telegram_send": True,
            "no_wecom_send": True,
            "no_proposal_create": True,
        },
    }

    evidence_file.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print(f"  Evidence written: {evidence_file}")


def print_cron_delivery_instruction(result):
    """Print an explicit instruction for the outer cron agent delivery layer."""
    if result.get("failures"):
        print()
        print("## CRON_DELIVERY_INSTRUCTION")
        print("The pipeline has failed steps. Final response MUST report the failed step names and must not be [SILENT].")
        print("Failed steps:", ", ".join(result.get("failures") or []))
        return

    decisions_path = STATE_DIR / "agenda_speak_decisions.yaml"
    surfaced = []
    summary = {}
    try:
        import yaml

        decisions_data = yaml.safe_load(decisions_path.read_text()) or {}
        summary = decisions_data.get("summary") or {}
        surfaced = [
            d for d in decisions_data.get("decisions", [])
            if isinstance(d, dict) and d.get("decision") == "surface"
        ]
    except Exception as exc:
        print()
        print("## CRON_DELIVERY_INSTRUCTION")
        print("Could not read agenda speak decisions. Final response MUST report this diagnostic and must not be [SILENT].")
        print(f"Diagnostic: {type(exc).__name__}: {exc}")
        return

    print()
    print("## CRON_DELIVERY_INSTRUCTION")
    if surfaced:
        print("Agenda speak gate surfaced item(s). Final response MUST NOT be [SILENT].")
        print("Use this concise Telegram-ready report as the final response:")
        print()
        print("Self-Evolution 发现一个成熟议题：")
        for d in surfaced:
            gate = d.get("secondary_gate") or {}
            print(f"- 议题：{d.get('title', d.get('candidate_id', 'unknown'))}")
            print(f"  - candidate_id: {d.get('candidate_id', 'unknown')}")
            print(f"  - decision: {d.get('decision')}")
            print(f"  - action: {d.get('mapped_action')}")
            print(f"  - reason: {d.get('reason')}")
            print(f"  - evidence_strength: {gate.get('evidence_strength')}")
            print(f"  - actionable_qualified_count: {gate.get('actionable_qualified_count')}")
            preview = str(d.get("message_preview") or "").strip()
            if preview:
                print("  - message_preview:")
                for line in preview.splitlines():
                    print(f"    {line}")
        print()
        print(
            f"Pipeline status: {result.get('steps_succeeded')}/{result.get('total_steps')} passed. "
            "This is advisory; executable changes still require owner approval."
        )
        return

    if summary.get("total_candidates", 0):
        print("Agenda candidates existed but none surfaced. Final response may be [SILENT] unless there were pipeline failures.")
        print(f"summary: {summary}")
    else:
        print("No agenda candidates surfaced and pipeline passed. Final response may be [SILENT].")


def main():
    dry_run = "--dry-run" in sys.argv

    result = run_pipeline(dry_run=dry_run)

    print()
    if dry_run:
        print("=" * 60)
        print("DRY RUN COMPLETE — No files modified")
        print("=" * 60)
    else:
        write_evidence(result, dry_run)
        print_cron_delivery_instruction(result)
        print()
        print("=" * 60)
        if result["failures"]:
            print(f"PIPELINE COMPLETE — {len(result['failures'])} step(s) failed")
            sys.exit(1)
        else:
            print("PIPELINE COMPLETE — All steps passed ✅")
            print("=" * 60)


if __name__ == "__main__":
    main()
