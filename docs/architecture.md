# Architecture Documentation

## The Closed-Loop Feedback Architecture

The Self-Evolution Governor implements a **closed-loop metacognitive feedback system** on top of Hermes Agent. Rather than operating as an external evaluator, it runs as a periodic pipeline within the agent's own context, consuming the same logs, state, and interaction history that the agent produces during normal operation.

```
                    ┌──────────────────────────────────┐
                    │        HERMES CORE AGENT          │
                    │  (conversations, tool calls,      │
                    │   error handling, memory)          │
                    └──────────┬───────────────────────┘
                               │ outputs logs, state,
                               │ interaction data
                               ▼
                    ┌──────────────────────────────────┐
                    │     SELF-EVOLUTION GOVERNOR       │
                    │                                    │
                    │  ┌─────┐  ┌─────┐  ┌─────┐       │
                    │  │Audit│─▶│Score│─▶│Store│       │
                    │  └─────┘  └─────┘  └──┬──┘       │
                    │                       │           │
                    │  ┌─────┐  ┌─────┐  ┌──▼──┐       │
                    │  │Apply│◀─│Veto │◀─│Plan │       │
                    │  └──┬──┘  └─────┘  └─────┘       │
                    │     │                             │
                    └─────┼─────────────────────────────┘
                          │ improvement proposals /
                          │ configuration changes
                          ▼
                    ┌──────────────────────────────────┐
                    │        HERMES CORE AGENT          │
                    │  (updated behavior, new config,   │
                    │   patched capabilities)            │
                    └──────────────────────────────────┘
```

The feedback loop ensures that every improvement feeds back into the agent's runtime, which then generates new signals for the next audit cycle.

## Pipeline Step Details

### Step 1: Audit (`1_audit_sources.sh`)

The audit phase collects **16 discrete signals** from **3 signal phases**. Each signal is a structured observation that feeds into the scoring engine.

**Signal Sources:**

| # | Phase | Signal | Description |
|---|-------|--------|-------------|
| 1 | Phase 1: Log Analysis | Error frequency | Count of errors in recent logs |
| 2 | | Error severity | Distribution of error severities |
| 3 | | Error novelty | New error types vs. known patterns |
| 4 | | Recovery rate | How often the agent recovers vs. fails |
| 5 | Phase 2: Interaction | Task completion rate | % of user tasks successfully completed |
| 6 | | Response latency | Time to first response/action |
| 7 | | Tool call accuracy | % of tool calls that succeed |
| 8 | | Conversation length | Avg turns before resolution |
| 9 | | User satisfaction | Implicit signals (repeats, corrections) |
| 10 | | Stagnation index | Same patterns repeated without improvement |
| 11 | Phase 3: Evolution | Proposal acceptance rate | % of past proposals accepted |
| 12 | | Proposal impact | Measured improvement from applied proposals |
| 13 | | Gap recurrence | Gaps that reappear after being addressed |
| 14 | | Coverage blind spots | Capability areas never audited |
| 15 | | Cooldown status | Which gaps are in cooldown |
| 16 | | Journal depth | How much history is available for trending |

**Output:** `state/signals_cache.json`

### Step 2: Score (`2_score_proposals.sh`)

Each detected capability gap is scored using a weighted formula:

```
score = (evidence_strength × w1) + (frequency × w2) + (impact × w3) + (recency × w4)
```

**Component Breakdown:**

| Component | Weight (Default) | Range | Description |
|-----------|------------------|-------|-------------|
| `evidence_strength` | 0.35 | 0.0 – 1.0 | Quality and quantity of supporting evidence |
| `frequency` | 0.25 | 0.0 – 1.0 | How often the gap occurs |
| `impact` | 0.25 | 0.0 – 1.0 | Severity of consequences if unaddressed |
| `recency` | 0.15 | 0.0 – 1.0 | How recently the gap was observed |

**Score normalization:** Raw scores are normalized to a 0.0 – 1.0 range. A `score_explanation` string is generated for each proposal, breaking down the components.

**Output:** Scored proposal list appended to signals cache.

### Step 3: Store (`3_store_journal.sh`)

Findings are written to `state/evolution_journal.md` in a structured markdown format:

```markdown
## [2026-05-06 16:30:00] — Audit Cycle #42

### Detected Gaps
1. **Gap: Low tool call accuracy in file_search**
   - Score: 0.81 | Evidence: 4 | Phase: Interaction
   - Explanation: High frequency (0.9) + moderate impact (0.7) +
     strong evidence (0.85) + recent activity (0.7)

### Proposals
1. **Proposal: Add fuzzy matching to file_search**
   - Score: 0.78 | Status: pending | Applied: no
```

### Step 4: Plan (`4_plan_agenda.sh`)

Scored proposals are organized into `state/revision_agenda.md`. The agenda is a prioritized backlog:

```markdown
# Revision Agenda

## High Priority (score ≥ 0.85)
- [ ] Proposal: Add fuzzy matching to file_search        0.91

## Medium Priority (score 0.72 – 0.84)
- [ ] Proposal: Improve error recovery in execute_tool   0.78
- [ ] Proposal: Cache conversation summaries             0.74

## Low Priority (score < 0.72) — Surface Only
- [ ] Proposal: Optimize token usage in long threads      0.68
```

### Step 5: Veto (`5_veto_threshold.sh`)

Proposals below `min_score_to_surface` are held back. Deduplication logic prevents repeated proposals for the same gap within the cooldown window. The veto step also checks:

- Is the proposal already applied?
- Is the gap in cooldown?
- Has the proposal been rejected >2 times?

**State Machine for Proposal Lifecycle:**

```
                    ┌──────────┐
                    │  DETECTED │
                    └─────┬────┘
                          │ scored ≥ threshold
                          ▼
                    ┌──────────┐
              ┌────▶│ PROPOSED │◀────────┐
              │     └─────┬────┘          │
              │           │                │
              │     ┌─────┴─────┐         │
              │     │           │         │
              │     ▼           ▼         │
              │ ┌────────┐ ┌────────┐     │
              │ │APPROVED│ │REJECTED│     │
              │ └───┬────┘ └───┬────┘     │
              │     │          │          │
              │     ▼          │ cooldown │
              │ ┌────────┐     │ expires  │
              │ │APPLIED │─────┘          │
              │ └────────┘                │
              │      │                    │
              │      │ gap recurs         │
              └──────┘                    │
                     │                    │
                     └────────────────────┘
                        (re-detected after cooldown)
```

### Step 6: Apply (`6_apply_proposal.sh`)

If `SELF_EVOLVE_AUTO_APPLY=true`, approved proposals are executed automatically. Otherwise, the proposal is surfaced for manual review. Application methods include:

- **Config patch:** Update Hermes configuration values
- **Script injection:** Add or modify tool implementations
- **Prompt adjustment:** Update system prompt or instructions
- **Plugin enablement:** Activate a relevant plugin

## File Relationships

The following diagram shows which files feed into which processes:

```
Signal Sources (logs, state, etc.)
        │
        ▼
┌──────────────────┐
│  signals_cache   │─── Step 1 writes raw signals
│  .json           │
└────────┬─────────┘
         │ Step 2 reads + scores
         ▼
┌──────────────────┐
│  signals_cache   │─── Step 2 appends scores
│  .json (scored)  │
└────────┬─────────┘
         │ Step 3 reads scored signals
         ▼
┌──────────────────┐
│ evolution_journal│─── Step 3 writes audit history
│ .md              │
└────────┬─────────┘
         │ Step 4 reads journal + signals
         ▼
┌──────────────────┐
│ revision_agenda  │─── Step 4 writes prioritized proposals
│ .md              │
└────────┬─────────┘
         │ Step 5 reads agenda, writes filtered agenda
         ▼
┌──────────────────┐
│ revision_agenda  │─── Step 5 may remove low-score items
│ .md (filtered)   │
└────────┬─────────┘
         │ Step 6 reads approved proposals
         ▼
┌──────────────────┐
│ state.json       │─── Step 6 updates application state
│                  │     (applied proposals, cooldowns)
└──────────────────┘
```

## Integration Points with Hermes Core

The Self-Evolution Governor integrates with Hermes Agent at these touchpoints:

| Integration Point | Direction | Mechanism |
|------------------|-----------|-----------|
| **Log ingestion** | Hermes → Governor | Reads Hermes log files from `$HERMES_HOME/logs/` |
| **State inspection** | Hermes → Governor | Reads `state.json` from Hermes runtime |
| **Configuration patching** | Governor → Hermes | Writes config overrides to `$HERMES_HOME/config/` |
| **Plugin system** | Governor ↔ Hermes | Shared plugin registry under `$HERMES_HOME/plugins/` |
| **Tool definitions** | Governor → Hermes | May register/modify tool implementations |
| **System prompt** | Governor → Hermes | May propose prompt updates for behavior changes |

All paths are resolved through `_paths.py`, which bases everything on `$HERMES_HOME`, ensuring portability across installations.
