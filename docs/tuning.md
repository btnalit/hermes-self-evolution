# Tuning Guide

This guide explains how to tune the Self-Evolution Governor for your specific use case. The default values work well for general-purpose Hermes Agent deployments, but you may need to adjust them based on your environment, workload, and tolerance for noise.

---

## Configuration Parameters

All parameters are set via environment variables or a `.env` file in the self-evolution directory.

| Variable | Default | Range | Description |
|----------|---------|-------|-------------|
| `SELF_EVOLVE_MIN_SCORE` | `0.72` | 0.0 – 1.0 | Minimum score to surface a proposal |
| `SELF_EVOLVE_MIN_EVIDENCE` | `3` | 1 – 20 | Minimum evidence count to consider a gap |
| `SELF_EVOLVE_COOLDOWN_DAYS` | `7` | 1 – 90 | Days before re-evaluating a rejected gap |
| `SELF_EVOLVE_MAX_PROPOSALS` | `5` | 1 – 50 | Maximum proposals per cycle |
| `SELF_EVOLVE_AUTO_APPLY` | `false` | true/false | Auto-apply or require manual review |

---

## When to Adjust `min_score_to_surface` (Default: 0.72)

The **most impactful** tuning parameter. Controls how conservative or aggressive the governor is.

### Lower it (e.g., 0.50 – 0.70) if:
- You're **missing important improvements** that don't have strong evidence yet
- Your agent is in **early development** and needs frequent iteration
- You want to **surface edge cases** that occur rarely but matter
- You're **testing** the pipeline and want to see more proposals

### Raise it (e.g., 0.80 – 0.95) if:
- You're getting **too many noise proposals** that waste review time
- Your agent is **stable and mature** and only needs critical fixes
- You have **limited bandwidth** for reviewing proposals
- You want only **high-confidence** improvements

### Diagnostic signs:
- **Too many low-quality proposals** → Raise the threshold
- **Proposals you care about never surface** → Lower the threshold
- **Score explanations show borderline values** (0.68 – 0.74) → Adjust by 0.02 increments

---

## When to Adjust `min_evidence_count` (Default: 3)

Controls how many times a gap must be observed before it's considered a real pattern.

### Lower it (e.g., 1 – 2) if:
- Your agent runs **infrequently** and log volume is low
- You want to catch **critical issues immediately** (security, data loss)
- You're in **rapid prototyping** mode
- Each interaction is very high-stakes

### Raise it (e.g., 5 – 10) if:
- Your agent has **high throughput** (1000s of interactions/day)
- You're seeing **false positives** from transient errors
- You want to filter out **statistical noise**
- Each gap should represent a **well-established pattern**

### Diagnostic signs:
- **Proposals based on single incidents** → Raise min_evidence (unless critical)
- **Gaps that are clearly real but don't meet evidence threshold** → Lower it
- **Check evolution_journal.md** — look at the evidence_count field for each gap

---

## When to Adjust `cooldown_days` (Default: 7)

Prevents the system from repeatedly proposing the same rejected improvement.

### Lower it (e.g., 1 – 3) if:
- Your agent's **behavior changes rapidly** (learning new tools, updated models)
- You've made **other changes** that might resolve the gap differently now
- The gap is **time-sensitive** and needs re-evaluation soon

### Raise it (e.g., 14 – 30) if:
- You've **explicitly decided** not to address a gap
- The gap would require **major engineering work** to fix
- Proposals keep reappearing that you've already rejected
- Your agent's environment is **stable** with slow change

### Diagnostic signs:
- **Same proposal reappears every cycle** → Increase cooldown
- **A rejected gap is now resolvable but stuck in cooldown** → Decrease cooldown
- **Check evolution_journal.md** — look for "Rejected" status entries repeating

---

## How to Interpret `score_explanation`

Each proposal includes a `score_explanation` string that breaks down the score. Example:

```
Score: 0.78
Explanation: evidence_strength=0.85(0.35) + frequency=0.90(0.25) + 
             impact=0.70(0.25) + recency=0.70(0.15)
```

### Reading the explanation:

| Component | Value | Weight | Weighted | Meaning |
|-----------|-------|--------|----------|---------|
| `evidence_strength` | 0.85 | 0.35 | 0.298 | Strong evidence exists |
| `frequency` | 0.90 | 0.25 | 0.225 | Gap happens often |
| `impact` | 0.70 | 0.25 | 0.175 | Moderate severity |
| `recency` | 0.70 | 0.15 | 0.105 | Observed recently |

**Total:** 0.298 + 0.225 + 0.175 + 0.105 = 0.803 → normalized to 0.78

### Diagnostic patterns:

- **High evidence_strength, low frequency** → Rare but well-documented issue. May deserve attention if impact is high.
- **High frequency, low impact** → Annoyance but not critical. Consider lowering threshold if you want to fix all annoyances.
- **Low recency** → Old gap that may have already been resolved. Check if evidence is stale.
- **All components mid-range** → The gap is borderline. Watch it — may become high-priority as more evidence accumulates.

---

## What to Look for in `evolution_journal.md`

The journal is your primary diagnostic tool. Here's what to check:

### 1. Score Trends
Look for gaps whose scores are **trending upward** over successive audits. A gap going from 0.65 → 0.71 → 0.78 is getting worse and will soon cross the threshold.

### 2. Evidence Accumulation
Check `evidence_count` over time. Rapid evidence accumulation indicates a growing problem.

### 3. Proposal Lifecycle
Track proposals through their lifecycle:
- **Pending** → Proposed but not yet reviewed
- **Approved** → Waiting to be applied
- **Applied** → Successfully implemented
- **Rejected** → Reviewed and declined
- **Superseded** → Replaced by a better proposal

### 4. Gap Recurrence
Gaps that keep recurring after being "solved" indicate **root cause not addressed**. The proposal may need a different approach.

### 5. Coverage Gaps
Check which capability areas rarely generate proposals. This may indicate **blind spots** in signal collection rather than actual health.

---

## How to Diagnose Silent Agenda Items

If a gap you care about never appears in the revision agenda:

1. **Check the signals cache** — Is the gap being detected at all?
   ```bash
   cat $HERMES_HOME/self-evolution/state/signals_cache.json | jq '.'
   ```
2. **Check the journal** — Is the gap present but below threshold?
   ```bash
   grep -A5 "score: 0\." $HERMES_HOME/self-evolution/state/evolution_journal.md
   ```
3. **Lower the threshold temporarily** to see what's being held back:
   ```bash
   export SELF_EVOLVE_MIN_SCORE=0.50
   bash 5_veto_threshold.sh
   ```
4. **Check cooldown** — The gap may be in cooldown from a previous rejection.
5. **Check evidence count** — The gap may not have enough evidence yet.

---

## How to Reduce Noise (Too Many Proposals)

If you're overwhelmed by proposals:

| Problem | Solution |
|---------|----------|
| Too many proposals per cycle | Decrease `SELF_EVOLVE_MAX_PROPOSALS` (e.g., 3) |
| Low-quality proposals surfacing | Increase `SELF_EVOLVE_MIN_SCORE` (e.g., 0.80) |
| Transient errors causing proposals | Increase `SELF_EVOLVE_MIN_EVIDENCE` (e.g., 5) |
| Same proposal keeps reappearing | Increase `SELF_EVOLVE_COOLDOWN_DAYS` (e.g., 14) |
| All proposals seem unnecessary | Increase both `MIN_SCORE` and `MIN_EVIDENCE` |

---

## Common Problems and Solutions

### Problem: Pipeline doesn't detect any gaps
- **Check:** Are signal sources accessible? Does `1_audit_sources.sh` produce output?
- **Solution:** Run `bash 1_audit_sources.sh --debug` to see raw signal collection
- **Check:** Is `$HERMES_HOME` set correctly?
- **Solution:** Run `echo $HERMES_HOME` to verify

### Problem: Scores are always low (below 0.50)
- **Check:** Are evidence sources producing quality data?
- **Solution:** Lower `min_score_to_surface` temporarily for testing
- **Check:** Is the agent generating enough log volume? Low volume = low scores

### Problem: Auto-apply keeps failing
- **Check:** Does the apply script have write permissions to Hermes config?
- **Solution:** Run `bash 6_apply_proposal.sh --dry-run proposal=ID` to test
- **Check:** Is `SELF_EVOLVE_AUTO_APPLY=true` actually set?

### Problem: Journal grows too large
- **Solution:** Implement journal rotation (the pipeline automatically prunes entries older than 90 days)
- **Manual clean:** Archive old sections from `evolution_journal.md` manually

### Problem: Pipeline scripts not found
- **Check:** Is the self-evolution directory at `$HERMES_HOME/self-evolution/`?
- **Solution:** Re-run `bash setup.sh` from the cloned repo

---

## Quick Reference: Common Tuning Profiles

| Profile | MIN_SCORE | MIN_EVIDENCE | COOLDOWN | MAX_PROPOSALS | AUTO_APPLY |
|---------|-----------|--------------|----------|---------------|------------|
| **Conservative** (stable agent) | 0.85 | 5 | 14 | 3 | false |
| **Balanced** (default) | 0.72 | 3 | 7 | 5 | false |
| **Aggressive** (active development) | 0.55 | 2 | 3 | 10 | true |
| **Exploratory** (testing/auditing) | 0.40 | 1 | 1 | 20 | false |
| **Critical-only** (production) | 0.90 | 8 | 30 | 2 | true |

---

## Experimental Tweaks

For advanced users, the scoring weights in `2_score_proposals.sh` can also be adjusted:

```
w1 (evidence_strength): 0.35
w2 (frequency):         0.25
w3 (impact):            0.25
w4 (recency):           0.15
```

**When to adjust weights:**
- If **impact** matters most (e.g., security), raise `w3` to 0.35
- If **recent patterns** are more relevant, raise `w4` to 0.25
- If **evidence quality** is consistently poor, lower `w1` to 0.25

Ensure weights still sum to 1.0 after adjustment.
