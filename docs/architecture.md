# Architecture Documentation (V1.4.1c)

## The Self-Evolution Pipeline Architecture

The Self-Evolution Governor implements a **12-step metacognitive pipeline** that runs daily at 04:00. Rather than operating as an external evaluator, it lives inside Hermes Agent's own skill tree and state space, consuming the same logs, cron output, and interaction history that the agent produces during normal operation.

```
                    ┌──────────────────────────────────────────┐
                    │           DAILY PIPELINE (04:00)          │
                    │                                            │
                    │  ┌─────────┐  ┌──────────┐  ┌──────────┐ │
                    │  │Collect  │→│Unmatched  │→│Unmatched  │ │
                    │  │Signals  │ │ Review    │ │ Cluster   │ │
                    │  └─────────┘  └──────────┘  └──────────┘ │
                    │       │                                       │
                    │       ▼                                       │
                    │  ┌─────────┐  ┌──────────┐  ┌──────────┐ │
                    │  │New      │→│New Agenda │→│Build     │ │
                    │  │Agenda   │  │Apply Ready│  │Runtime   │ │
                    │  │Preview  │  │           │  │Digest    │ │
                    │  └─────────┘  └──────────┘  └──────────┘ │
                    │       │                                       │
                    │       ▼                                       │
                    │  ┌─────────┐  ┌──────────┐  ┌──────────┐ │
                    │  │Agenda   │→│Proposal   │→│Speak     │ │
                    │  │Mature   │  │Route      │  │Gate      │ │
                    │  └─────────┘  └──────────┘  └──────────┘ │
                    │       │                                       │
                    │       ▼                                       │
                    │  ┌─────────┐  ┌──────────┐  ┌──────────┐ │
                    │  │Build    │→│Restart    │→│Cron      │ │
                    │  │Console  │  │Console    │  │Delivery  │ │
                    │  └─────────┘  └──────────┘  └──────────┘ │
                    │                                            │
                    └──────────────────┬───────────────────────┘
                                       │ runtime_digest.md injected
                                       │ into every Hermes session
                                       ▼
                    ┌──────────────────────────────────────────┐
                    │            HERMES SESSIONS                │
                    │  (self_agenda.yaml → agenda candidates → │
                    │   proposal_queue.yaml → approved proposals│
                    │   → ops-gate execution design phase)      │
                    └──────────────────────────────────────────┘
                                       │
                                       │ user decisions → feedback signals
                                       ▼
                    ┌──────────────────────────────────────────┐
                    │         NEXT PIPELINE CYCLE               │
                    │  (collects new signals from feedback,     │
                    │   updates agenda maturity scores)          │
                    └──────────────────────────────────────────┘
```

## 12-Step Pipeline

| Step | Script | Purpose | Input → Output |
|------|--------|---------|----------------|
| **1** | `collect_signals.py` | Collect 13+ signal groups from ops, cron, skills, gateway, config | State files, cron results → `signals.jsonl` |
| **2** | `unmatched_signal_review.py` | Review signals that don't match any agenda topic | `signals.jsonl` → filtered unmatched pool |
| **3** | `unmatched_cluster_ledger.py` | Cluster unmatched signals into potential new agenda topics | Unmatched pool → cluster ledger |
| **4** | `new_agenda_preview.py` | Preview promising clusters as draft agenda items | Cluster ledger → `new_agenda_preview.md` |
| **5** | `new_agenda_apply_ready.py` | Apply confirmed agenda items to `self_agenda.yaml` | Preview → actual agenda insert |
| **6** | `build_runtime_digest.py` | Generate session context digest for agent injection | Signals + proposals + agenda → `runtime_digest.md` |
| **7** | `agenda_maturation.py` | Score existing agenda items, emit mature candidates | Agenda + signals → candidates |
| **8** | `proposal_router.py` | Proposal state machine + verify + cleanup | `proposal_queue.yaml` → updated status |
| **9** | `speak_gate.py` | Score, gate, quota for proposals and candidates | Candidates + proposals → speak/digest/silent decision |
| **10** | `build_console.py` | Rebuild the Evolution Console static site | All state files → MkDocs static pages |
| **11** | `restart_console.py` | Reload the httpd serving the console | Signal to systemd → console restart |
| **12** | Cron delivery instruction | Self-check and cron output metadata tagging | Evidence files → ops-gate postcheck |

### Daily Pipeline Execution Order (04:00)

```
 1. collect_signals.py
 2. unmatched_signal_review.py
 3. unmatched_cluster_ledger.py
 4. new_agenda_preview.py
 5. new_agenda_apply_ready.py
 6. build_runtime_digest.py
 7. agenda_maturation.py --write-journal --emit-candidates
 8. proposal_router.py --cleanup
 9. proposal_router.py --verify-implemented
10. speak_gate.py --include-agenda-candidates
11. build_console.py
12. restart_console.py
```

## Signal Sources

The system collects from **13+ signal groups**, organized by domain:

| # | Signal Group | Description | Source |
|---|-------------|-------------|--------|
| 1 | ops_gate_result | Ops-gate execution outcomes | Evidence files |
| 2 | cron_result | Cron job status (ok/fail/error) | Cron output + postcheck |
| 3 | cron_dependency | Upstream/downstream task chain health | Dependency ledger |
| 4 | platform_status | Gateway health per platform | Gateway state |
| 5 | config_change | Hermes config.yaml modifications | File mtime + diff |
| 6 | skill_health | Load frequency, error rate, version lag | Skill directory scan |
| 7 | skill_lifecycle_state | Skill lifecycle events (create/edit/delete) | Skill directory scan |
| 8 | skill_usage_telemetry | Which skills are used how often | Session metadata |
| 9 | source_absent | Expected but missing signals | Cross-check between signal groups |
| 10 | session_metadata | Session summary statistics | Recent session analysis |
| 11 | recent_session_mention | Specific phrases/topics from sessions | Session transcript scan |
| 12 | gateway_health | Gateway process uptime, memory, message flow | systemctl + process check |
| 13 | sannai_cron_status | Sannai profile cron job health | Sannai cron output |

## State File Landscape

```
/vol1/.hermes/state/evolution/
├── signals.jsonl                  # Raw signal data (append-only log)
├── self_agenda.yaml               # 7+ agenda items with maturity scores
├── proposal_queue.yaml            # Proposal lifecycle (approved/stale/pending)
├── agenda_candidates.yaml         # Candidates emitted by maturation engine
├── agenda_speak_decisions.yaml    # Speak gate decision log
├── evolution_journal.md           # Audit trail of all pipeline activity
├── HERMES_FOCUS.md                # Weekly operational focus guidance
├── runtime_digest.md              # Session context (auto-injected into prompts)
├── speak_quota.json               # Daily speak gate quota tracking
├── weekly_strategy_facts.json     # Strategic observations for weekly runs
├── score_explanations/            # Per-run scoring breakdowns (YAML)
├── diagnostics/                   # Diagnostic context files
└── proposals/                     # Individual proposal detail files
```

## Proposal State Machine (10-State Lifecycle)

```
                    ┌──────────────┐
                    │  draft       │
                    └──────┬───────┘
                           │ evidence accumulates
                           ▼
                    ┌──────────────┐
                    │  submitted   │
                    └──────┬───────┘
                           │ scored ≥ 0.75 maturity
                           ▼
                    ┌──────────────┐
              ┌────▶│ pending_user_ │────┐
              │     │ approval      │    │
              │     └──────┬───────┘    │
              │            │            │
              │      ┌─────┴─────┐      │
              │      │           │      │
              │      ▼           ▼      │
              │ ┌─────────┐ ┌──────────┐│
              │ │ approved │ │ rejected ││
              │ └────┬─────┘ └────┬─────┘│
              │      │            │      │
              │      ▼            │ stale│
              │ ┌──────────┐      │      │
              │ │pre_execution│    └──────┘
              │ │_design    │
              │ └────┬─────┘
              │      │ ops-gate routing
              │      ▼
              │ ┌──────────┐
              │ │ scheduled │
              │ └────┬─────┘
              │      │
              │      ▼
              │ ┌──────────┐
              │ │ running   │
              │ └────┬─────┘
              │      │
              │      ▼
              │ ┌──────────┐
              │ │implemented│
              │ └────┬─────┘
              │      │ gap recurs after cooldown
              └──────┘
```

States: `draft → submitted → pending_user_approval → approved / rejected / stale_pending → pre_execution_design → scheduled → running → implemented`

## Key Design Principles

1. **All paths are deployment-specific.** No `_paths.py` — each Hermes installation hardcodes paths for reliability. This repo serves as reference implementation.
2. **Speak gate controls user interruption.** Daily quota (1 surface/day), agenda quota (1 per item), quality gate (ev_strength > 0.5).
3. **Agenda items mature, not just score.** A-20260429-003 took 17 days from draft → approved: evidence strength 1.00, trend +0.22, recurrence 1.00.
4. **Ops-gate is the execution boundary.** No proposal bypasses ops-gate for execution. `pre_execution_design` phase enforces this.
5. **No core Hermes patches.** All scripts run from the skill directory. Zero modifications to Hermes source code.
6. **Sannai state stays isolated.** Sannai profile signals are collected separately; never mixed with main evolution scoring.
