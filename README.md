# Self-Evolution Governor for Hermes Agent

**Give your Hermes agent metacognition — periodic self-reflection, capability gap detection, proactive improvement proposals, and a full governance pipeline.**

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-brightgreen)
![Hermes Agent](https://img.shields.io/badge/Hermes%20Agent-compatible-purple)

---

## Architecture Overview

The Self-Evolution Governor implements a closed-loop metacognitive pipeline that runs periodically to audit the agent's own performance, detect gaps, and propose improvements — then optionally applies them.

```
┌─────────────────────────────────────────────────────────────────┐
│                    SELF-EVOLUTION PIPELINE                       │
│                                                                  │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐  │
│  │ 1. AUDIT  │───▶│ 2. SCORE │───▶│ 3. STORE │───▶│ 4. PLAN  │  │
│  │(sources)  │    │(formula) │    │(journal) │    │(agenda)  │  │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘  │
│       │               │               │               │         │
│       ▼               ▼               ▼               ▼         │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐  │
│  │ 5.VETO   │◀───│ 6.APPLY  │◀───│ 7.REVIEW │◀───│(proposals)│  │
│  │(threshold)│    │(scripts) │    │(results) │    │          │  │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘  │
│       │                                                         │
│       └──────────────(loop back to Step 1)────────────────────▶│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Features

- **🔍 Metacognitive Audit** — Scans 16+ signals across 3 phases to detect capability gaps, error patterns, and stagnation.
- **📊 Weighted Scoring** — Each proposal is scored with a confidence formula combining evidence strength, frequency, impact, and recency.
- **📓 Evolution Journal** — Persistent, timestamped record of all audits, proposals, and their resolutions.
- **📋 Revision Agenda** — Structured backlog of improvement proposals with status tracking.
- **⚙️ Configurable Thresholds** — Five knobs to tune sensitivity, from proposal surfacing to cool-down periods.
- **🔌 Plugin Architecture** — Optional `runtime_digest` plugin for deeper runtime introspection.
- **🚀 Portable Design** — All paths relative to `$HERMES_HOME` via `_paths.py`.
- **📦 Demo-Ready** — Ships with sample state files so you can inspect the pipeline immediately after install.

## Quick Start

```bash
# Clone the repository
git clone https://github.com/NousResearch/hermes-self-evolution.git
cd hermes-self-evolution

# Run the setup script
bash setup.sh
```

The setup script will:
1. Verify `$HERMES_HOME` is set
2. Create the directory structure under `$HERMES_HOME/self-evolution/`
3. Copy pipeline scripts and configuration
4. Optionally prompt to install the runtime_digest plugin
5. Place demo state files for immediate exploration

## How It Works

The pipeline runs on a configurable schedule (default: daily) and executes six steps:

| Step | Script | Purpose |
|------|--------|---------|
| **1. Audit** | `1_audit_sources.sh` | Collects 16 signals from 3 phases: error logs, interaction patterns, evolution history |
| **2. Score** | `2_score_proposals.sh` | Applies the weighted scoring formula to each detected gap |
| **3. Store** | `3_store_journal.sh` | Writes findings to `evolution_journal.md` with timestamps and explanations |
| **4. Plan** | `4_plan_agenda.sh` | Generates or updates the revision agenda with scored proposals |
| **5. Veto** | `5_veto_threshold.sh` | Filters proposals below `min_score_to_surface` and deduplicates |
| **6. Apply** | `6_apply_proposal.sh` | Executes approved improvement proposals (configurable auto-apply or manual) |

## File Structure

```
$HERMES_HOME/self-evolution/
├── 1_audit_sources.sh      # Step 1: Signal collection
├── 2_score_proposals.sh     # Step 2: Scoring
├── 3_store_journal.sh       # Step 3: Journaling
├── 4_plan_agenda.sh         # Step 4: Agenda generation
├── 5_veto_threshold.sh      # Step 5: Threshold filtering
├── 6_apply_proposal.sh      # Step 6: Proposal application
├── _paths.py                # Portable path resolution
├── install_plugin.sh        # Plugin installer (optional)
├── state/
│   ├── evolution_journal.md # Audit history
│   ├── revision_agenda.md   # Proposed improvements
│   ├── state.json           # Internal pipeline state
│   └── signals_cache.json   # Cached signal data
├── plugins/
│   └── runtime_digest.py    # Optional introspection plugin
└── demo/
    ├── example_journal.md   # Demo journal for evaluation
    └── example_agenda.md    # Demo agenda for evaluation
```

## Prerequisites

- **Hermes Agent** installed and running (required for signal sources)
- **Python 3.10+** (for plugin scripts and score computation)
- **Bash 4.0+** (for pipeline scripts)
- **Unix-like environment** (Linux / macOS / WSL)

## Configuration

All tuning is done via environment variables or a `.env` file in the self-evolution directory:

| Variable | Default | Description |
|----------|---------|-------------|
| `SELF_EVOLVE_MIN_SCORE` | `0.72` | Minimum score to surface a proposal |
| `SELF_EVOLVE_MIN_EVIDENCE` | `3` | Minimum evidence count to consider a gap |
| `SELF_EVOLVE_COOLDOWN_DAYS` | `7` | Days before re-evaluating a rejected gap |
| `SELF_EVOLVE_MAX_PROPOSALS` | `5` | Maximum proposals per cycle |
| `SELF_EVOLVE_AUTO_APPLY` | `false` | Auto-apply proposals (true) or require manual review (false) |

## Plugin Installation (Optional)

The `runtime_digest` plugin provides deeper runtime introspection during the audit phase. To install:

```bash
bash install_plugin.sh
```

This enables additional signal sources including memory pressure, token usage patterns, and call frequency analysis.

## Video Series

Learn the full system through our YouTube video series:

- **Episode 1:** What is Metacognitive Self-Evolution? (link TBD)
- **Episode 2:** Architecture Deep Dive & Pipeline Walkthrough (link TBD)
- **Episode 3:** Tuning & Threshold Configuration (link TBD)
- **Episode 4:** Custom Plugin Development (link TBD)
- **Episode 5:** Production Deployment Best Practices (link TBD)

## Contributing

Contributions are welcome! Please follow these guidelines:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

Please ensure all pipeline scripts remain portable (use `_paths.py` for path resolution) and include demo state files for any new features.

## License

MIT License — see [LICENSE](LICENSE) for details.

---

*Built by Nous Research. Inspired by the principle that agents should improve themselves.*
