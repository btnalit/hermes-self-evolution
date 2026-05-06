# Self-Evolution Governor for Hermes Agent

**Give your Hermes agent metacognition — periodic self-reflection, capability gap detection, proactive improvement proposals, and a full governance pipeline.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-brightgreen)]()
[![Hermes Agent](https://img.shields.io/badge/Hermes%20Agent-compatible-purple)]()
[![GitHub release](https://img.shields.io/github/v/release/btnalit/hermes-self-evolution)]()
[![GitHub last commit](https://img.shields.io/github/last-commit/btnalit/hermes-self-evolution)]()

---

A self-contained **metacognitive governance system** for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It runs a daily pipeline that collects environmental signals, scores improvement opportunities, matures strategic agendas, and injects runtime context back into agent sessions — closing the feedback loop without requiring code changes to the Hermes core.

## Architecture Overview

```
                          SELF-EVOLUTION CLOSED LOOP
                                ┌─────────────────┐
                    ┌──────────▶│  1. COLLECT      │
                    │           │  (16 signal src) │
                    │           └────────┬─────────┘
                    │                    ▼
                    │           ┌─────────────────┐
                    │           │  2. MATURE       │
                    │           │  (agenda engine) │
                    │           └────────┬─────────┘
                    │                    ▼
                    │           ┌─────────────────┐
                    │           │  3. SPEAK GATE   │
                    │           │  (score + quota) │
                    │           └────────┬─────────┘
                    │                    ▼
                    │           ┌─────────────────┐
                    │           │  4. PROPOSE      │
                    │           │  (queue + route) │
                    │           └────────┬─────────┘
                    │                    ▼
                    │           ┌─────────────────┐
                    │           │  5. INJECT       │
                    │           │  (runtime digest)│
                    │           └────────┬─────────┘
                    │                    ▼
                    │     User reviews → approves → executes
                    │                    │
                    └────────────────────┘
```

## Features

- **🔍 16+ Signal Sources** — Scans ops-gate results, cron status, skill health, memory quality, tool reliability, config changes, proposal feedback, gateway health, and more.
- **📊 Two-Tier Scoring** — `priority_score` (worth talking about?) + `speak_score` (worth interrupting the user?) with risk dampeners, strategic bonuses, and daily quotas.
- **📓 Evolution Journal** — Persistent timestamped audit trail of all observations, score changes, and proposal transitions.
- **📋 Proposal State Machine** — 10-state lifecycle: `draft → pending → approved → scheduled → running → implemented → verified`.
- **🧠 Agenda Maturation Engine** — Long-term agenda items mature over time as evidence accumulates. Structural vs actionable evidence separation prevents false positives.
- **⚙️ Configurable Thresholds** — Defaults: maturity score 0.72, min evidence 3, observation window 3 days. All tunable.
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

# Run the pipeline manually
cd "$HERMES_HOME/skills/dogfood/self-evolution-governor/scripts"
python3 collect_signals.py
python3 agenda_maturation.py --emit-candidates
python3 build_runtime_digest.py

# Optional: install the Hermes plugin
hermes plugins install "$(pwd)/../../../../plugin/hermes-self-evolution" --enable
```

## Pipeline Scripts

| Step | Script | Purpose | Input → Output |
|------|--------|---------|----------------|
| **1. Audit** | `collect_signals.py` | Collect 16 signal sources | state/ files, cron output → `signals.jsonl` |
| **2. Route** | `proposal_router.py` | Proposal state machine + verify + cleanup | `proposal_queue.yaml` → Updated status |
| **3. Mature** | `agenda_maturation.py` | Long-term agenda maturity engine | Agenda + signals → candidates |
| **4. Gate** | `speak_gate.py` | Score, gate, quota for proposals | Proposals → speak/digest/silent decision |
| **5. Inject** | `build_runtime_digest.py` | Generate session context digest | Signals + proposals → `runtime_digest.md` |

**Daily cron order (04:00):**
```
1. collect_signals.py
2. proposal_router.py --cleanup
3. proposal_router.py --verify-implemented
4. agenda_maturation.py --write-journal --emit-candidates
5. speak_gate.py --include-agenda-candidates
6. build_runtime_digest.py
```

## File Structure

```
hermes-self-evolution/
├── README.md                         # This file
├── CHANGELOG.md
├── setup.sh                          # Idempotent deployment script
├── .gitignore
│
├── skills/self-evolution-governor/   # Core skill package
│   ├── SKILL.md                      # 800-line skill definition
│   ├── scripts/
│   │   ├── _paths.py                 # Portable path resolver ($HERMES_HOME)
│   │   ├── collect_signals.py        # 16 signal collectors
│   │   ├── proposal_router.py        # Proposal lifecycle state machine
│   │   ├── agenda_maturation.py      # Agenda maturity scoring engine
│   │   ├── speak_gate.py             # Two-tier scoring + quota system
│   │   └── build_runtime_digest.py   # Session context digest builder
│   └── templates/
│       └── proposal.yaml             # Proposal template
│
├── plugin/hermes-self-evolution/     # Optional Hermes plugin
│   ├── plugin.yaml
│   ├── __init__.py                   # on_session_start hook
│   └── README.md
│
├── demo/                             # Example state files
│   ├── signals.jsonl.example
│   ├── self_agenda.yaml.example
│   ├── proposal_queue.yaml.example
│   ├── agenda_candidates.yaml.example
│   ├── runtime_digest.md.example
│   └── evolution_journal.md.example
│
└── docs/
    ├── architecture.md               # System architecture deep dive
    └── tuning.md                     # Threshold configuration guide
```

## Prerequisites

- **Hermes Agent** installed and running
- **Python 3.10+**
- **Unix-like environment** (Linux / macOS / WSL)

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_score_to_surface` | 0.72 | Minimum maturity score to produce a candidate |
| `min_evidence_count` | 3 | Minimum evidence entries before candidate_ready |
| `min_observation_days` | 3 | Minimum observation window before surfacing |
| `cooldown_days` | 7 | Don't re-surface same agenda item within N days |
| `max_surface_per_day` | 1 | Max one mature agenda surfaced daily |

See [`docs/tuning.md`](docs/tuning.md) for detailed tuning guidance and scenario-based profiles.

## Plugin Installation (Optional)

```bash
hermes plugins install ./plugin/hermes-self-evolution --enable
```

The plugin uses the `on_session_start` hook to log runtime digest availability. Actual digest injection happens via the skill's SKILL.md instructions, which are loaded into every session.

## Video Series

- **Episode 1:** What is Self-Evolution Governance?
- **Episode 2:** Architecture Deep Dive & Pipeline Walkthrough *(coming soon)*
- **Episode 3:** Tuning & Production Deployment

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

All pipeline scripts must remain portable — use `_paths.py` for path resolution, never hardcode paths.

## License

MIT — see [LICENSE](LICENSE) for details.

---

*Built for Hermes Agent. Agents should improve themselves.*
