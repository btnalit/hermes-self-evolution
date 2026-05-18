# Self-Evolution Governor for Hermes Agent

**Give your Hermes agent metacognition — periodic self-reflection, capability gap detection, proactive improvement proposals, and a full governance pipeline.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-brightgreen)]()
[![Hermes Agent](https://img.shields.io/badge/Hermes%20Agent-compatible-purple)]()
[![GitHub release](https://img.shields.io/github/v/release/btnalit/hermes-self-evolution)]()
[![GitHub last commit](https://img.shields.io/github/last-commit/btnalit/hermes-self-evolution)]()

---

A self-contained **metacognitive governance system** for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It runs a daily 12-step pipeline that collects environmental signals, reviews unmatched patterns, matures strategic agendas, routes proposals through a 10-state lifecycle, and injects runtime context back into agent sessions — closing the feedback loop without requiring code changes to the Hermes core.

## Architecture Overview

```
                          SELF-EVOLUTION DAILY PIPELINE (04:00)

  ┌──────────┐   ┌─────────────┐   ┌──────────────┐   ┌────────────┐   ┌────────────────┐
  │ Collect  │──▶│ Unmatched   │──▶│ Unmatched    │──▶│ New Agenda │──▶│ New Agenda     │
  │ Signals  │   │ Signal Rev. │   │ Cluster      │   │ Preview    │   │ Apply Ready    │
  └──────────┘   └─────────────┘   └──────────────┘   └────────────┘   └───────┬────────┘
                                                                                │
  ┌──────────┐   ┌─────────────┐   ┌──────────────┐   ┌────────────┐           │
  │ Build    │◀──│ Speak Gate  │◀──│ Proposal     │◀──│ Agenda     │◀──────────┘
  │ Runtime  │   │ (score+quota)│   │ Router       │   │ Maturation │
  │ Digest   │   └─────────────┘   └──────────────┘   └────────────┘
  └────┬─────┘
       │
  ┌────▼─────┐   ┌─────────────┐   ┌──────────────┐
  │ Build    │──▶│ Restart     │──▶│ Cron Delivery │──▶ ops-gate evidence
  │ Console  │   │ Console     │   │ Instruction   │
  └──────────┘   └─────────────┘   └──────────────┘

       │ runtime_digest.md → injected into every Hermes session
       ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │  HERMES SESSIONS: self_agenda → candidates → proposal_queue        │
  │  → approved → pre_execution_design → ops-gate (no direct execute)  │
  │  user feedback → signal loop → next pipeline cycle                  │
  └─────────────────────────────────────────────────────────────────────┘
```

## Features

- **🔍 13+ Signal Sources** — Scans ops-gate results, cron status, skill health, memory quality, tool reliability, config changes, proposal feedback, gateway health, Sannai cron, and more.
- **📊 Agenda Maturation Engine** — Long-term agenda items mature over time as evidence accumulates (score = ev_strength × 0.35 + frequency × 0.25 + impact × 0.25 + recency × 0.15). Structural vs actionable evidence separation prevents false positives.
- **📓 Evolution Journal** — Persistent timestamped audit trail of all observations, score changes, and proposal transitions.
- **📋 Proposal State Machine** — 10-state lifecycle: `draft → submitted → pending_user_approval → approved / rejected / stale_pending → pre_execution_design → scheduled → running → implemented`.
- **🧠 Speak Gate** — Two-tier scoring system. Quality gate (`ev_strength > 0.5`) + daily quota (1 surface/day) + agenda quota (1 per item). Controlled mode prevents external sends without approval.
- **🔗 Unmatched Signal Pipeline** — Signals that don't match any agenda topic are reviewed, clustered, and proposed as new agenda items.
- **⚙️ Configurable Thresholds** — Defaults: maturity score 0.72, min evidence 3, observation window 3 days. All tunable in `_maturation_config`.
- **🔌 Optional Plugin** — `runtime_digest` auto-injection plugin for Hermes sessions (`on_session_start` hook).
- **🚀 Zero Core Patches** — All scripts run from the skill directory. No modifications to Hermes source code needed.

## Quick Start

```bash
# Clone
git clone https://github.com/btnalit/hermes-self-evolution.git
cd hermes-self-evolution

# Install
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
bash setup.sh

# Run the full pipeline manually
cd "$HERMES_HOME/skills/dogfood/self-evolution-governor/scripts"
python3 collect_signals.py
python3 unmatched_signal_review.py
python3 unmatched_cluster_ledger.py
python3 new_agenda_preview.py
python3 new_agenda_apply_ready.py
python3 build_runtime_digest.py
python3 agenda_maturation.py --write-journal --emit-candidates
python3 proposal_router.py --cleanup
python3 proposal_router.py --verify-implemented
python3 speak_gate.py --include-agenda-candidates

# Optional: install the Hermes plugin
hermes plugins install "$(pwd)/../../../../plugin/hermes-self-evolution" --enable
```

## Daily Cron Pipeline (04:00)

The following 12-step pipeline runs every day via `self_evolution_daily_pipeline.py`:

```
 1. collect_signals.py               # 13+ signal groups
 2. unmatched_signal_review.py       # Review unmatched signals
 3. unmatched_cluster_ledger.py      # Cluster into new agenda topics
 4. new_agenda_preview.py            # Preview promising clusters
 5. new_agenda_apply_ready.py        # Apply to self_agenda.yaml
 6. build_runtime_digest.py          # Session context digest
 7. agenda_maturation.py             # Score + mature agenda items
 8. proposal_router.py --cleanup     # Clean stale proposals
 9. proposal_router.py --verify      # Verify implemented proposals
10. speak_gate.py                    # Score + gate + decide
11. build_console.py                 # Rebuild Evolution Console
12. restart_console.py               # Reload MkDocs httpd
```

## Pipeline Scripts

| Step | Script | Purpose | Input → Output |
|------|--------|---------|----------------|
| **1** | `collect_signals.py` | 13+ signal groups from ops, cron, skills, gateway, config | State files, cron → `signals.jsonl` |
| **2** | `unmatched_signal_review.py` | Review signals that don't match any agenda topic | `signals.jsonl` → filtered unmatched pool |
| **3** | `unmatched_cluster_ledger.py` | Cluster unmatched signals into potential new agenda topics | Unmatched → cluster ledger |
| **4** | `new_agenda_preview.py` | Preview promising clusters as draft agenda items | Ledger → `new_agenda_preview.md` |
| **5** | `new_agenda_apply_ready.py` | Apply confirmed agenda items to `self_agenda.yaml` | Preview → actual agenda insert |
| **6** | `build_runtime_digest.py` | Generate session context digest | Signals + proposals → `runtime_digest.md` |
| **7** | `agenda_maturation.py` | Score existing agenda items, emit candidates | Agenda + signals → candidates |
| **8** | `proposal_router.py` | Proposal state machine + cleanup + verify | `proposal_queue.yaml` → updated status |
| **9** | `speak_gate.py` | Score, gate, quota for proposals + candidates | Candidates → speak/digest/silent |
| **10** | `build_console.py` | Rebuild Evolution Console static site | State → MkDocs pages |
| **11** | `restart_console.py` | Reload httpd serving the console | Signal → systemctl → restart |
| **12** | `self_evolution_daily_pipeline.py` | Orchestrator: runs all steps with error handling + cron delivery | All preceding outputs → evidence files |

## File Structure

```
hermes-self-evolution/
├── README.md                         # This file
├── CHANGELOG.md
├── setup.sh                          # Idempotent deployment script
├── .gitignore
│
├── scripts/
│   └── self_evolution_daily_pipeline.py  # 12-step pipeline orchestrator
│
├── skills/self-evolution-governor/   # Core skill package
│   ├── SKILL.md                      # 800-line skill definition (Chinese)
│   ├── scripts/
│   │   ├── collect_signals.py        # 13+ signal collectors
│   │   ├── unmatched_signal_review.py    # Unmatched signal review
│   │   ├── unmatched_cluster_ledger.py   # Cluster into agenda topics
│   │   ├── new_agenda_preview.py         # Draft agenda preview
│   │   ├── new_agenda_apply_ready.py     # Apply agenda items
│   │   ├── build_runtime_digest.py   # Session context digest
│   │   ├── agenda_maturation.py      # Agenda maturity scoring (V1.4.1c)
│   │   ├── proposal_router.py        # Proposal lifecycle state machine
│   │   ├── speak_gate.py             # Two-tier scoring + quota
│   │   ├── agenda_candidate_closure.py   # Close candidates on approval
│   │   ├── agenda_review_action.py       # Review action generator
│   │   └── weekly_strategy_facts.py      # Strategic observations
│   └── templates/
│       └── proposal.yaml             # Proposal template
│
├── plugin/hermes-self-evolution/     # Optional Hermes plugin
│   ├── plugin.yaml
│   ├── __init__.py                   # on_session_start hook
│   └── README.md
│
├── demo/                             # Anonymized example state files
│   ├── signals.jsonl.example
│   ├── self_agenda.yaml.example
│   ├── proposal_queue.yaml.example
│   ├── agenda_candidates.yaml.example
│   ├── agenda_speak_decisions.yaml.example
│   ├── runtime_digest.md.example
│   ├── evolution_journal.md.example
│   └── HERMES_FOCUS.md.example
│
└── docs/
    ├── architecture.md               # V1.4.1c architecture deep dive
    └── tuning.md                     # Threshold configuration guide
```

## Prerequisites

- Hermes Agent 0.10+ (tested on 0.14.0)
- Hermes HOME directory initialized (`~/.hermes` or `$HERMES_HOME`)
- Skills directory structure: `$HERMES_HOME/skills/dogfood/`
- Cron system for scheduling the daily pipeline
- Optional: MkDocs for the Evolution Console

## Ecosystem Integration

```
Hermes Agent (main)         Sannai Profile (isolated)
      │                            │
      ├── collect_signals.py       ├── sannai cron status (separate)
      ├── ops-gate evidence        │
      ├── cron results             │
      ├── skill/system health      │
      └── session metadata         │
              │                    │
              └───── evolution ────┘
                        │
              ┌─────────┴──────────┐
              │                    │
         self_agenda.yaml    runtime_digest.md
         proposal_queue       → injected into every
         evolution_journal      Hermes session
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for all version history.

## License

MIT
