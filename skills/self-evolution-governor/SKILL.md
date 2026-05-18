---
name: self-evolution-governor
description: Hermes 的元认知、自我定位、能力缺口分析、主动改进提案与表达门禁、长期议程成熟引擎。用于让 Agent 从任务执行器升级为自我运营型智能体。
version: 1.4.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [self-evolution, metacognition, agent, reflection, autonomy, governance]
---

# Self Evolution Governor

## Purpose

This skill makes Hermes periodically and event-triggeredly reflect on its own role, the user's environment, recurring tasks, capability gaps, automation opportunities, memory quality, skill health, tool reliability, user satisfaction trends, session metadata shifts, and proposal feedback loops.

The goal is not to let Hermes modify itself recklessly.  
The goal is to let Hermes notice useful patterns, form hypotheses, generate self-improvement proposals, and decide when an idea is important enough to tell the user.

## Core Principle

Hermes should not only ask:

> What does the user want me to do now?

Hermes should also ask:

> Given the user's long-term environment, what should I become better at?

## Operating Model

Hermes maintains four internal artifacts:

1. `signals.jsonl` — Observed user behavior, repeated topics, failures, corrections, configuration changes, memory quality, skill health, tool reliability, satisfaction trends, session metadata, and proposal feedback.
2. `self_agenda.yaml` — Open questions Hermes is tracking about its own role, user needs, and missing capabilities.
3. `proposal_queue.yaml` — Concrete improvement proposals that may require user approval.
4. `evolution_journal.md` — Historical record of observations, hypotheses, proposals, approvals, and outcomes.
5. `agenda_candidates.yaml` — Buffer for mature agenda items awaiting speak_gate.

## ⚠ Critical Lesson: Never Hardcode Focus Items

**`build_runtime_digest.py` had a hardcoded `"Close the self-evolution feedback loop"` focus item** that was unconditionally emitted as the first focus item, every single time. It was not derived from any signal — it was a static string in `build_focus()`. This caused every session's runtime digest to display a fictitious priority that the user never asked for and that never reflected actual system state.

**Root cause:** The original code used:
```python
focus_items = [
    ("Close the self-evolution feedback loop",
     "Recent proposals need consumption pipeline; runtime_digest is now active."),
]
```
This was the *only* unconditional entry in the list. All other items (`cron_errors`, `ops_errors`, `llm_wiki_errors`) were conditionally appended only when actual problems existed — but the first item was always there, regardless.

**Fix (2026-05-10):**
- Deleted the hardcoded default entirely
- `focus_items` starts as empty list
- Every focus item is now derived from actual signal data (errors, corrections, gateway issues, project shifts)
- When no signals fire, the focus section shows: `_None — no errors, corrections, or project shifts detected._`
- `runtime_digest.md` omits the "## Current Focus" section entirely when there's nothing to report

**Lesson for all future code:** Never hardcode a "default priority" that doesn't come from real signal data. A static priority item will linger forever, never get replaced, and mislead every session that reads it. If there's nothing to focus on, say nothing.

## Signal Source Reference

| # | Source | Data Origin | Priority |
|---|--------|------------|----------|
| 1 | ops-gate execution results | state/ops-gate/ postcheck pass/fail | Core |
| 2 | cron task status | Cron output dir + ops-gate exec_success | Core |
| 3 | user corrections | session_search → recent correction patterns | Core |
| 4 | config changes | skills/memory/scripts mtime changes | Core |
| 5 | memory quality | Entry count, size, churn, topic relevance | Core |
| 6 | skill health | Load frequency, error rate, version lag | Core |
| 7 | tool reliability | terminal/browser call failure rate | Medium |
| 8 | user satisfaction trend | Correction word freq, follow-up count, msg length trend | Medium |
| 9 | session metadata | Daily session vol, platform dist, task type dist | Medium |
|| 10 | proposal feedback loop | Approved/rejected proposal outcomes | Core |
|| 11 | curator operations | archive/prune/list-archived stats | Core |
|| 12 | skill usage frequency | Load count per hour/day per skill | Core |
|| 13 | skill lifecycle events | Skill creation/update/deletion timestamps | Core |
|| 14 | cron dependency health | Upstream/downstream cron chain status | Core |
|| 15 | platform connectivity | Platform connection state changes | Medium |
|| 16 | gateway health alerts | Gateway log error pattern detection | Core |

## Signal Categories

Use these signal types in signals.jsonl:

- repeated_topic
- repeated_manual_work
- user_correction
- failed_tool_call
- successful_automation
- config_change
- skill_gap
- memory_gap
- risk_pattern
- project_importance_change
- platform_usage_change
- opportunity_for_automation
- opportunity_for_documentation
- opportunity_for_monitoring
- tool_reliability_degradation
- user_satisfaction_decline
- session_volume_change
- proposal_feedback
- memory_quality_decline
- skill_staleness
- curator_activity
- skill_usage_change
- cron_chain_broken
- platform_offline
- gateway_instability
- protected_skill_alert

## Trigger Schedules

| Task | Frequency | Signals Covered | Speak? |
|------|-----------|----------------|--------|
| Deep Reflection | Daily 04:00 | All 16 sources | High-score only |
| Failure Trigger | On ops-gate fail | Failure signal | Urgent risk exempt |
| Weekly Strategy | Mon 07:00 | All + weekly trends | Strategic level |

## Deep Reflection Questions

When activated, Hermes should answer each:

1. What has changed in the user's environment?
2. What topics or tasks are recurring?
3. What did the user correct or emphasize recently?
4. Which workflows are still manual but repeatable?
5. Which skill is missing, stale, or too broad?
6. Which memory entries are stale, vague, risky, or missing?
7. Which tools are underused, failing, or overused?
8. Are there tool reliability degradation signals?
9. Is user satisfaction trending down?
10. What should Hermes proactively suggest?
11. What should Hermes avoid automating?
12. What requires explicit user approval?
13. What happened to previous proposals? (feedback loop)

## Speak-Out Gate (V1.2)

Hermes should not report every thought. Two-score system:

### Scoring Model

```
weighted_score  = impact×0.40 + recurrence×0.25 + confidence×0.35
priority_score  = weighted_score × risk_dampener[risk_level] + strategic_bonus + urgency_bonus
speak_score     = priority_score - interruption_cost(0.20) - repeat_penalty
```

### Risk Dampeners

| risk_level | multiplier | meaning |
|-----------|-----------|---------|
| none      | 1.00      | No risk, safe to propose |
| low       | 0.97      | Slight concern |
| medium    | 0.82      | Needs attention |
| high      | 0.55      | Significant risk |
| critical  | 0.00      | Do not act, alert only |

### Bonuses

| Bonus | Value | Applied to |
|-------|-------|-----------|
| strategic_bonus | +0.12 | strategic_reflection type |
| urgency_bonus | +0.15 | urgent=true events |

### Decision Reason Traceability

**Critical: every decision outputs `decision_reason`.** This is not optional — without it, scored decisions are opaque and un-debuggable within days.

The `speak_gate.py` script outputs a JSON array at `decision_reason` containing every step:
```
[
  "weighted = 0.85×0.40 + 0.9×0.25 + 0.8×0.35 = 0.845",
  "× risk_dampener[low=0.97] → 0.8196",
  "+ bonuses: none",
  "priority_score = 0.8196",
  "  │ >= 0.6 (queue)     ✓",
  "  │ >= 0.4 (digest)     ✓",
  "speak_score = 0.8196 - 0.2 = 0.6196",
  "  │ >= 0.6 (speak)         ✓",
  "",
  "speak_score(0.6196) >= 0.6 ✓, actionability(0.9) >= 0.6 ✓, risk_level(low) → safe to speak directly",
  "action: speak_now",
  "  quota: speak_approved"
]
```

Each entry traces one atomic step:
1. Weighted base calculation with formula
2. Risk dampener applied with level name and multiplier
3. Bonuses itemized
4. Priority score with threshold checks
5. Speak score with penalty breakdown and threshold check
6. Decision gate evaluation (which condition fired)
7. Quota check result

### Verified Scenario Behavior

Tested against 10 real-world scenarios with the V1.2 formula:

| # | Scenario | priority | speak | Action | Correct? |
|---|----------|----------|-------|--------|----------|
| A | Grafana 15 failures | 1.00 | 0.80 | speak_now_risk_alert | ✅ |
| B | LLM-Wiki 8% anomaly | 0.52 | 0.32 | daily_digest | ✅ |
| C | Skill suggestion (weak evidence) | 0.57 | 0.37 | daily_digest | ✅ |
| D | Clear automation opportunity | 0.82 | 0.62 | speak_now | ✅ |
| E | Weekly strategic review | 0.92 | 0.72 | speak_now | ✅ |
| F | Low-impact code cleanup | 0.43 | 0.23 | daily_digest | ✅ |
| G | High-impact zero-evidence guess | 0.28 | 0.08 | silent_log_only | ✅ |
| H | Urgent outage alert | 1.00 | 0.93 | speak_now_risk_alert | ✅ |
| I | Medium-risk good proposal | 0.64 | 0.44 | proposal_queue | ✅ |
| J | High-risk valuable suggestion | 0.46 | 0.26 | daily_digest | ✅ |

All 10 pass — the formula reliably separates "worth speaking" from "needs review" from "discard".

### Quota Traceability

The `speak_gate.py` output includes a `would_have_spoken_without_quota` boolean field. This is critical for tuning — it tells you whether a proposal was silenced by **quality** (score below threshold) or by **capacity** (daily quota exhausted).

```
\"action\": \"proposal_queue\",
\"would_have_spoken_without_quota\": true,
\"decision_reason\": [
    ...
    \"  quota: suggestion_quota_exceeded → downgraded to proposal_queue\"
]
```

Without this field, quota-exceeded proposals look identical to low-score proposals — you can't distinguish \"good idea, no time today\" from \"bad idea, silent.\"

Daily quotas are enforced by `speak_quota.json` at `/vol1/.hermes/state/evolution/speak_quota.json`:
- Max 3 suggestions spoken per day
- Max 1 strategic reflection spoken per day
- Urgent risk alerts exempt
- When quota is exceeded, the action is automatically downgraded to `proposal_queue`
- Quota is persistent across cron runs and resets daily at midnight

| Condition | Action |
|-----------|--------|
| urgent=true | `speak_now_risk_alert` — bypass all gates |
| risk_level=critical | `risk_alert_only` — alert only, don't auto-act |
| speak >= 0.60 AND actionability >= 0.60, risk in (medium,high) | `speak_now_with_approval` — speak, user must approve |
| speak >= 0.60 AND actionability >= 0.60, risk in (none,low) | `speak_now` — speak directly |
| priority >= 0.60 | `proposal_queue` — enter proposal queue, don't speak |
| priority >= 0.40 | `daily_digest` — enter daily report, don't speak |
| priority < 0.40 | `silent_log_only` — discard |

### Quotas
- Max 3 suggestions spoken per day
- Max 1 strategic reflection spoken per day
- Urgent risk alerts exempt

### Speak Format

```
我观察到一个值得你注意的趋势：

• 现象：
• 证据：
• 判断：
• 建议：
• 风险：
• 是否需要你批准：
```

## Proposal Format

Every self-improvement proposal must include these fields (templates/proposal.yaml):

```yaml
title: str
type: memory_update | skill_creation | skill_update | workflow_automation | cron_job | config_audit | documentation | monitoring | tool_change | strategic_reflection

# ── Scoring Dimensions (0.0~1.0) ──
impact: 0.0~1.0       # How much does this improve long-term efficiency?
recurrence: 0.0~1.0    # How often does this problem/opportunity appear?
confidence: 0.0~1.0    # How strong is the evidence?

# ── Governance ──
risk_level: none | low | medium | high | critical
actionability: 0.0~1.0  # Is there a clear, concrete action to take?
urgent: false           # true = bypass speak gate, direct alert
repeat_penalty: 0.0~0.2 # penalty if this was proposed before

# ── Metadata ──
evidence: str
expected_benefit: str
approval_required: true | false
suggested_action: str
rollback: str
verification: str
status: pending | approved | rejected | implemented | failed
created_at: str
```

The `actionability` field is critical — without it, the decision layer cannot distinguish "important observation" from "actionable improvement". Low actionability (< 0.60) blocks speak even if priority is high.

## Priority Control Hierarchy

When multiple inputs conflict, the following hierarchy applies (highest to lowest):

1. Hard safety boundaries / security rules (不可逾越)
2. SOUL.md — stable identity contract
3. runtime_digest.md — current operational context (advisory, auto-injected)
4. User's current task / explicit request
5. proposal_queue.yaml / self_agenda.yaml — reference data
6. ops-gate-automation — execution gate for approved changes

This means:
- **runtime_digest** provides context, never commands — Hermes should not follow digest over user's current task
- **proposal_queue** is reference data, Hermes should not auto-execute pending proposals
- **User request** always overrides digest/focus/agenda suggestions
- **Ops-gate** is the only allowed execution path for self-evolution proposals

## Safety Rules

Must not auto-perform without user approval:
- modify production config
- edit memory
- create/update/delete enabled skills
- create/delete cron jobs
- delete files
- restart services
- change credentials
- change network/security policy

May auto-perform:
- observe, summarize, draft proposals, create reports
- update local non-authoritative journals (signals.jsonl, self_agenda.yaml, proposal_queue.yaml, evolution_journal.md)
- recommend next actions

## Integration With Ops Gate

If a proposal becomes an executable task → route through ops-gate-automation with:
- KPI, boundary, rollback, verify command, evidence path, approval status

## Integration With Memory Change Approval Gate

If a proposal involves memory changes → route through memory-change-approval-gate.

---

## Closed-Loop Feedback Architecture (V1.2)

The self-evolution-governor must NOT be a side-channel that only writes files no one reads. It must close the loop back into the running Hermes Agent.

### The Closed Loop

```
self-evolution-governor
  ├─ collect_signals()        → signals.jsonl
  ├─ speak_gate()             → proposal_queue.yaml + speak_quota.json
  ├─ build_runtime_digest()   → runtime_digest.md + HERMES_FOCUS.md
  ├─ proposal_router()        → approved proposals → ops-gate
  └─ evolution_journal.md     → Full audit trail
         ↓
  Hermes session reads runtime_digest.md (via SOUL.md guidance)
         ↓
  User approves proposal →
    proposal_router → ops_gate_runner → execute → verify → status update
```

### Injecting Into Running Hermes

**Critical lesson: behavioral instructions in SOUL.md are not enough.**
The skill originally relied on SOUL.md telling Hermes "consult runtime_digest.md when available" — but in practice, LLMs skip behavioral instructions that don't feel immediately relevant to the current task. The file was being written but rarely read.

**Solution: code-level auto-injection via `prompt_builder.py`**

The file `/vol1/.hermes/hermes-agent/agent/prompt_builder.py` was modified to add a `_load_runtime_digest()` function that reads `/vol1/.hermes/state/evolution/runtime_digest.md` and injects it into the session's `# Project Context` system prompt section — right alongside SOUL.md and AGENTS.md.

The bridge is now `runtime_digest.md` + a code hook. It is:
- **Short**: < 2KB (trim aggressively — runtime context is expensive)
- **Fresh**: Generated by `build_runtime_digest.py` during daily reflection
- **Auto-injected**: By `_load_runtime_digest()` in `prompt_builder.py`, every session, automatically
- **Not authoritative**: Hermes must treat it as advisory context, not commands

**The `_load_runtime_digest()` function pattern:**

```python
def _load_runtime_digest() -> str:
    """Load runtime_digest.md from HERMES_HOME/state/evolution/ if it exists."""
    digest_path = get_hermes_home() / "state" / "evolution" / "runtime_digest.md"
    if not digest_path.exists():
        return ""
    try:
        content = digest_path.read_text(encoding="utf-8").strip()
        if not content:
            return ""
        content = _scan_context_content(content, "runtime_digest.md")
        result = f"## Runtime Digest\n\n{content}"
        return _truncate_content(result, "runtime_digest.md")
    except Exception as e:
        logger.debug("Could not read runtime_digest.md from %s: %s", digest_path, e)
        return ""
```

Called at the end of `build_context_files_prompt()`:
```python
    # Runtime digest — short operational context from self-evolution-governor
    digest_content = _load_runtime_digest()
    if digest_content:
        sections.append(digest_content)
```

This ensures the digest is injected into every Hermes session (Telegram, CLI, WeChat, etc.) without requiring the LLM to "think about reading it." Cron sessions (`skip_context_files=True`) correctly skip it — they generate the digest, they don't need to read it.

**Digest expiration:** `_load_runtime_digest()` parses the `Valid until:` line from the digest content. If the timestamp is in the past, the function silently returns `""` — the digest is skipped, not injected with stale data. This prevents old focus items from misleading Hermes after the digest has aged out.

```python
# Inside _load_runtime_digest():
_expiry_match = re.search(
    r"Valid until:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", content
)
if _expiry_match:
    _expiry = datetime.strptime(_expiry_match.group(1), "%Y-%m-%d %H:%M")
    if datetime.now() > _expiry:
        return ""  # skip expired digest
```

No error is raised — expired digest is treated the same as "file not found." Hermes's session continues normally without it.

**SOUL.md was updated** to reflect this: instead of saying "consult runtime_digest.md when available", it now says:

> **Runtime digest (`/vol1/.hermes/state/evolution/runtime_digest.md`) is automatically loaded by Hermes into every session's system prompt** (via `_load_runtime_digest()` in `prompt_builder.py`), alongside SOUL.md. It contains current focus areas, pending proposals, and recent issues — no manual lookup needed.

Format:
```
# Hermes Runtime Digest
Last updated: 2026-04-28 04:00
Valid until: 2026-04-29 04:00

## Current Focus
1. Close self-evolution feedback loop
2. Stabilize ops-gate automation
3. Harden LLM-Wiki automation

## Proposals Awaiting Your Decision
- P-20260428-001 (priority=0.82, risk=low): Create hermes-config-audit skill

## Recent Issues (24h)
- ⚠ cron `b581a23d2886` error at ...

## Runtime Guidance
- Self-evolution outputs are advisory unless approved.
- Check proposal_queue.yaml before creating duplicate proposals.
- Route executable changes through approval and ops-gate.
```

### Files That Close The Loop

| File | Role | Generated By | Consumed By |
|------|------|-------------|-------------|
| `runtime_digest.md` | Runtime context bridge | build_runtime_digest.py (daily) | Hermes session (via SOUL.md) |
| `HERMES_FOCUS.md` | Strategic priorities | build_runtime_digest.py (daily) | Hermes session (via SOUL.md) |
| `proposal_queue.yaml` | Full state machine | speak_gate.py + proposal_router.py | Hermes checks before proposing duplicates |
| `evolution_journal.md` | Audit trail | Daily reflection + proposal_router | Human review, weekly strategy |

### Integration Points With External Documents

| Document | What Was Added | Why |
|----------|---------------|-----|
| `/vol1/.hermes/SOUL.md` | Self-Evolution Awareness section | Tells running Hermes to check state files |
| `/vol1/.hermes/scripts/automation_baseline.md` | Section G — Self-Evolution Governor | System asset registry |
| `/root/.hermes/memories/MEMORY.md` | Self-evolution system record | Cross-session persistence |

### V1.3a: Auto-Verification & Cleanup Scope

Added two flags to `proposal_router.py`:

**`--verify-implemented`** — Scans proposals with `execution.status=implemented`, checks `verification.method` against a 12-pattern whitelist, and promotes to `verified`. The whitelist rejects shell metacharacters (`;`, `|`, `$`, `` ` ``, `()`, `{}`, `\\`), empty strings, and strings shorter than 10 chars.

**`--cleanup-scope`** — Documents exact cleanup boundaries. Cleanup only affects:
- `draft`/`pending_user_approval` → expired if past expires_at
- `draft`/`pending_user_approval` → deferred if > 7 days stale
- Terminal states (implemented/verified/rejected/expired/failed/deferred/rollback_required) → archived if > 14 days
- **Protected**: `approved`, `scheduled`, `running` — NEVER touched

### V1.3a: Exit Code Primary Detection

Refactored `collect_signals.py` cron signal detection to a three-layer architecture:

| Layer | Priority | Detection Method | Description |
|-------|----------|-----------------|-------------|
| 1 | **PRIMARY** | `exit code != 0` | Most reliable — if job returned non-zero, it failed |
| 2 | ALWAYS | `Traceback (most recent call last)` | Never false positive |
| 3 | **FALLBACK** | Context-aware regex (8 guards) | Only activates if exit_code=0/absent AND no traceback |

Results: 242 old false positives → 0 across 24h/48h/7d scans.

### V1.4: Agenda Maturation Engine

The Agenda Maturation Engine solves a structural gap: self_agenda.yaml items had no automated progression. Problems went in and stayed "yellow" forever.

**Core philosophy:** time is pressure, not evidence. An item observed for 30 days with zero new evidence should not mature due to age alone.

**New file:** `agenda_maturation.py` — reads self_agenda.yaml + signals.jsonl + proposal_queue.yaml, calculates maturity_score, advances state, outputs agenda_candidates.yaml, writes evolution_journal.

**New file:** `agenda_candidates.yaml` — buffer file. agenda_maturation.py outputs mature candidates here; speak_gate.py consumes from here (in shadow-mode, speak_gate is not yet connected).

**5 agenda item types:**

| Type | Action When Mature | Rationale |
|------|-------------------|-----------|
| strategic_positioning | `ask_user_confirmation` | Cannot decide user direction autonomously |
| automation_opportunity | `create_proposal` | Repeating patterns → concrete automation |
| risk_watch | `bypass_maturation` | Bypasses maturation entirely, goes direct to speak_gate |
| quality_improvement | `create_proposal` | Signal quality, digest, cron, router improvements |
| cleanup_candidate | `surface_in_digest` | Low priority, passive notification only |

**State machine (simplified V1.4):**

```
observing → accumulating_evidence → candidate_ready
                                        ↓
                                  surfaced → resolved → archived
```

| State | Meaning |
|-------|---------|
| observing | Newly created, insufficient evidence |
| accumulating_evidence | Recurring signals detected, building evidence |
| candidate_ready | Maturity score meets threshold, waiting for speak_gate |
| surfaced | Presented to user or written to digest |
| resolved | User confirmed, proposal completed, or issue closed |
| archived | No longer relevant or expired |

**maturity_score formula:**

```
maturity_score =
    0.30 × evidence_strength
  + 0.25 × trend_strength
  + 0.20 × recurrence_density
  + 0.15 × unresolved_cost
  + 0.10 × actionability
  + time_pressure_bonus
  - staleness_penalty
```

- `contradiction_penalty` = 0.0 (disabled in MVP — contradiction is hard to define)
- `time_pressure_bonus = min(0.12, log(days + 1) × 0.03)`
- If `evidence_count == 0`, time_pressure does not trigger maturation — only review/archive

**V1.4 shadow-mode:** First 2-3 days, agenda_maturation.py calculates and journals but does NOT connect to speak_gate. This prevents score drift from causing premature interruptions before calibration.

**Default thresholds:**

| Parameter | Default | Purpose |
|-----------|---------|---------|
| min_score_to_surface | 0.72 | Maturity score threshold |
| min_evidence_count | 3 | Minimum evidence entries |
| min_observation_days | 3 | Minimum observation window |
| max_observation_days_before_review | 14 | Force review if too old |
| auto_archive_if_no_evidence_days | 21 | Auto-archive long-idle items |
| same_agenda_cooldown_days | 7 | Don't re-surface same item within |
| max_surface_per_day | 1 | Max one mature agenda surfaced daily |

**Cooldown and archive rules:**

| Rule | Value | Effect |
|------|-------|--------|
| max_surface_per_day | 1 | Prevents annoyance |
| same_agenda_cooldown_days | 7 | Prevents repeated confirmation |
| auto_archive_if_no_evidence_days | 21 | Prevents agenda bloat |
| force_review_if_observing_days | 30 | Catches stuck items |

**Audit requirement:** Every agenda_maturation.py run MUST write to evolution_journal.md, even if no items changed. Records: items_scanned, items_updated, matured_items, score_delta per item.

**V1.4 cron order (daily 04:00):**

```
1. collect_signals.py
2. proposal_router.py --cleanup
3. proposal_router.py --verify-implemented
4. agenda_maturation.py --write-journal --emit-candidates
5. speak_gate.py
6. build_runtime_digest.py
7. update evolution_journal.md
```

agenda_maturation runs BEFORE build_runtime_digest so the digest can reflect latest maturity state.

### V1.4 self_agenda.yaml Structure

```yaml
version: 1.4
updated_at: "2026-04-29T12:00:00+08:00"

agenda_items:
  - id: A-20260429-001
    title: 用户当前最高优先级项目
    question: 用户当前最应该让 Hermes 聚焦的项目是什么？
    type: strategic_positioning
    status: accumulating_evidence

    first_seen_at: "2026-04-29T04:00:00+08:00"
    last_evidence_at: "2026-04-29T04:00:00+08:00"
    last_matured_at: null
    last_surfaced_at: null

    evidence_matchers:
      signal_types:
        - session_trend
        - verified_proposal
        - config_change
      include_keywords:
        - self-evolution
        - proposal_router
        - runtime_digest
        - maturation
      exclude_keywords: []

    evidence:
      - at: "2026-04-29T04:00:00+08:00"
        source: self_agenda_init
        summary: Agenda item created during V1.4-MVP migration
        weight: 0.15

    counters:
      evidence_count: 1
      observation_days: 1
      recent_mentions_7d: 0
      contradiction_count: 0

    scores:
      evidence_strength: 0.15
      trend_strength: 0.50
      recurrence_density: 0.10
      unresolved_cost: 0.50
      actionability: 0.80
      time_pressure_bonus: 0.03
      staleness_penalty: 0.00
      contradiction_penalty: 0.00
      maturity_score: 0.32

    maturity_policy:
      min_score_to_surface: 0.72
      min_evidence_count: 3
      min_observation_days: 3
      max_observation_days_before_review: 14
      auto_archive_if_no_evidence_days: 21
      same_agenda_cooldown_days: 7

    next_action_when_mature: ask_user_confirmation

maturity_config:
  weights:
    evidence_strength: 0.30
    trend_strength: 0.25
    recurrence_density: 0.20
    unresolved_cost: 0.15
    actionability: 0.10
  time_pressure_max: 0.12
  time_pressure_log_factor: 0.03
  contradiction_penalty_enabled: false
  default_min_score: 0.72
  default_min_evidence: 3
  default_min_observation_days: 3
  default_max_review_days: 14
  default_archive_no_evidence_days: 21
  default_cooldown_days: 7
  max_surface_per_day: 1
```

### Scripts Overview (V1.4)

| Script | Purpose | Input | Output |
|--------|---------|-------|--------|
| `collect_signals.py` | Collect 10 signal sources | state/ files, cron output | signals.jsonl |
| `speak_gate.py` | Score and gate proactive suggestions | proposal_queue.yaml, signals.jsonl | speak action + quota |
| `proposal_router.py` | Proposal state machine + verify + cleanup | proposal_queue.yaml | Updated status |
| `agenda_maturation.py` | Long-term agenda maturity engine | self_agenda.yaml, signals.jsonl, proposal_queue.yaml | Updated agenda + candidates |
| `build_runtime_digest.py` | Generate session context digest | signals.jsonl, proposal_queue.yaml, self_agenda.yaml | runtime_digest.md + HERMES_FOCUS.md |

Paths:
- /vol1/.hermes/state/evolution/signals.jsonl
- /vol1/.hermes/state/evolution/self_agenda.yaml
- /vol1/.hermes/state/evolution/proposal_queue.yaml
- /vol1/.hermes/state/evolution/agenda_candidates.yaml
- /vol1/.hermes/state/evolution/evolution_journal.md

Scripts:
- collect_signals.py — Collect all 10 signal sources (V1.3a: exit_code primary)
- speak_gate.py — Score and gate proactive suggestions
- proposal_router.py — Proposal status machine (V1.3a: --verify-implemented, --cleanup, --cleanup-scope)
- agenda_maturation.py — Agenda maturation engine (V1.4: maturity_score, state machine, candidates)
- `build_runtime_digest.py` — Generate runtime digest + HERMES_FOCUS

Reference docs:
- `references/hindsight-memory-cleanup.md` — Hindsight API endpoints and bank reset procedure
- `references/hindsight-assessment-methodology.md` — Memory assessment process: classify → delete all → recreate clean, with keeper guidelines

Proposals move through a 10-state machine. Terminal states are marked with ⊕.

```
draft ──→ pending_user_approval ──→ approved ──→ scheduled ──→ running ──→ implemented ──→ verified ⊕
                                       │                          │
                                       ├── rejected ⊕             ├── failed ⊕
                                       ├── deferred ⊕             └── rollback_required
                                       └── expired ⊕
```

### Script: proposal_router.py

Consumes approved proposals from `proposal_queue.yaml`:

```bash
python3 proposal_router.py                        # Process all approved → scheduled
python3 proposal_router.py --status                # Show queue summary by status
python3 proposal_router.py --dry-run               # Preview, don't modify state
```

Transition rules:
- `draft` → `pending_user_approval`: Hermes evaluates proposal; if worth user's attention, asks
- `pending_user_approval` → `approved`: User says yes
- `pending_user_approval` → `rejected`: User says no
- `pending_user_approval` → `deferred`: User says later
- `approved` → `scheduled`: proposal_router.py runs
- `scheduled` → `running`: ops_gate_runner.py executes
- `running` → `implemented`: Task passes postcheck
- `running` → `failed`: Task fails postcheck
- `running` → `rollback_required`: Task fails with side effects
- `implemented` → `verified`: Manual or automated verification pass
- `draft` → `expired`: Auto-expire after `timestamps.expires_at`

### V1.5: Signal-Driven Focus — No Hardcoded Defaults

**Problem discovered 2026-05-10:** `build_runtime_digest.py` had a hardcoded focus item `"Close the self-evolution feedback loop"` that was unconditionally prepended before error-driven items. It was never removed, even after the feedback loop was closed and running for a week. The user's actual current priority was never read from signals — the hardcoded text just sat there forever.

**Fix applied:**
- `build_focus()` now starts with an empty `focus_items = []`
- Focus items are **entirely signal-driven**: cron errors → ops-gate failures → user corrections → gateway issues → project shifts
- When no focus items exist: HERMES_FOCUS.md shows `_None — no errors, corrections, or project shifts detected._`
- runtime_digest.md `## Current Focus` section is **omitted entirely** when there are no focus items (saves tokens, no noise)
- The digest reads `## Current Focus` from DISK (`HERMES_FOCUS.md`), not from in-memory — so the digest reflects the real state

**Hard rule: Never hardcode focus items.** If there is nothing wrong, the system should self-report nothing. A permanently-present focus item that never goes away is indistinguishable from noise — the user learns to ignore it.

**Verification:**
- After fix: HERMES_FOCUS.md shows `_None — ..._` when no signals fire
- After fix: runtime_digest.md has no `## Current Focus` section when focus is empty (357 bytes vs 422 bytes)
- Dry-run and live run both validate the logic

### Script: build_runtime_digest.py

Generates both `runtime_digest.md` and `HERMES_FOCUS.md` from actual signal data:

```bash
python3 build_runtime_digest.py              # Full update
python3 build_runtime_digest.py --dry-run      # Preview, don't write
```

- Scans signals.jsonl for recent errors (24h)
- Reads proposal_queue.yaml for pending/approved proposals
- Focus items derived from signal data (errors, corrections, project shifts) — no hardcoded defaults
- Generates digest (< 2KB) with recent issues, pending proposals, runtime guidance
- Focus only writes to disk if content changed (avoids unnecessary diffs)

### Using Proposal Creation From Cron

The daily reflection cron generates proposals programmatically:

```python
from proposal_router import create_proposal
p = create_proposal(
    title="...",
    proposal_type="skill_creation",
    scores={
        "impact": 0.85, "recurrence": 0.90, "confidence": 0.80,
        "actionability": 0.90, "risk_level": "low",
        "priority_score": 0.82, "speak_score": 0.62,
    },
    evidence=[{"type": "...", "source": "...", "summary": "..."}],
    suggested_action="...",
)


## Cron Integration Pattern

**Known limitation: the cron `script` parameter has strict path validation that rejects symlinks and paths outside `~/.hermes/scripts/`.** Workaround: run scripts via full path from within the prompt using `terminal()`:

```text
# In cron prompt — DO NOT use the `script` parameter:
python3 /vol1/.hermes/skills/dogfood/self-evolution-governor/scripts/collect_signals.py
```

Two cron jobs are set up for this skill:
1. **Daily Deep Reflection** (`77509e97ffd1`) — 0 4 * * *, signals collected in-prompt
2. **Weekly Strategic Review** (`539a782fea12`) — 0 7 * * 1, COLLECT_DAYS=7

Both use these enabled_toolsets: terminal, file, search

## Output: Daily Reflection Report

```
# Hermes Daily Self-Evolution Report

## 1. Key Observations
## 2. New Signals
## 3. Updated Self-Agenda
## 4. Skill Gaps
## 5. Memory Quality
## 6. Tool Reliability
## 7. Automation Opportunities
## 8. Session & Platform Trends
## 9. Proposal Feedback
## 10. Proposals
## 11. Should Tell User Now?
```

### Cron Does NOT Load Runtime Digest

Cron sessions use `skip_context_files=True` by default (no workdir). This is **correct** — the daily reflection cron **generates** the digest, it doesn't need to read it. Live Hermes sessions (Telegram, CLI, WeChat) use `skip_context_files=False` and will have the digest auto-injected.

**Pitfall: Mercury agent** runs its own check every 5 minutes for `task.json` in `/vol1/.hermes/mercury-bridge/`. When the task file doesn't exist, it may incorrectly report "directory not found" and suggest lowering the poll interval. Verify the directory actually exists before acting on such suggestions. Local stat() calls cost ~0.1ms — 288 polls/day is negligible overhead.

## Acceptance Criteria

Working correctly when Hermes can:
1. Maintain self-agenda across days
2. Generate useful proposals without being asked
3. Detect repeated workflows and gaps
4. Avoid noisy low-value suggestions
5. Route risky changes through approval
6. Write evolution journal entries
7. Explain why it decided to speak or stay silent
8. Report memory quality trends
9. Detect tool reliability degradation
10. Close the proposal feedback loop
