# Changelog

## 1.4.1 (2026-05-04)
- Initial public release
- Portable paths via `_paths.py` (`$HERMES_HOME`)
- All 5 pipeline scripts portablized
- Demo state files for quick start
- Optional `runtime_digest` plugin
- `setup.sh` deployment script

## 1.4.0 (2026-04-28)
- Internal release — path portability refactor
- Introduced `_paths.py` for centralized path resolution
- Removed hardcoded paths from all pipeline scripts
- Added `$HERMES_HOME` environment variable support

## 1.3.0 (2026-04-15)
- Added veto/threshold filtering step (Step 5)
- Implemented `min_score_to_surface` parameter
- Added deduplication logic for repeated proposals
- Added cooldown mechanism for rejected gaps

## 1.2.0 (2026-04-01)
- Added scoring engine with weighted formula
- Introduced `score_explanation` strings for transparency
- Added `evolution_journal.md` with structured output
- Implemented `revision_agenda.md` with priority tiers

## 1.1.0 (2026-03-15)
- Added signal collection (16 signals across 3 phases)
- Initial audit pipeline (Step 1)
- Basic state management in `state/` directory
- Plugin architecture foundation

## 1.0.0 (2026-03-01)
- Proof of concept
- Manual proposal tracking
- Basic journaling
- Hermes Agent integration proof
