# Changelog

## 1.4.3 (2026-05-09)
- **Fix: Session signal pipeline dead end** — `collect_session_signals()` 和 `collect_recent_session_mentions()` 从读 evolution_journal 改为读真实 sessions 数据
  - `collect_session_signals()` → sessions.json（平台/活跃度/最近会话）
  - `collect_recent_session_mentions()` → sessions/*.jsonl（skill 真实出现频率 + 话题关键词）
  - 隐私：只有摘要统计，不存原文
- **Fix: 信号逆序遍历** — `agenda_maturation.py` 加载信号后 `signals.reverse()`，确保最新 session 信号优先匹配，不被旧 config_change 堵死
- **Fix: 管道 live 模式** — pipeline 中 agenda_maturation 从 `--explain-scores` 改为 `--write-journal`（实际写入 YAML）
- **Fix: relevance 数值** — session_metadata 0.10→0.65，recent_session_mention 0.10→0.70（能进 qualified）
- **Fix: strong_sources 更新** — strategic_positioning 和 cleanup_candidate 新增对应的 session 信号源
- **Fix: self_agenda.yaml 匹配规则** — include_keywords 清空 + exclude_keywords 移除 `recent`（不再误杀 `recent_session_mention` 类型）
- **Fix: 排除词误杀** — A-002 的 exclude_keywords 移除 `recent`，避免 `recent_session_mention` 信号被信号类型名称自身误杀
- 三个穿透性缺陷（根因级逻辑漏洞，非调参）

## 1.4.2 (2026-05-07)
- Fix: `_traceback_re` false positive — markdown table docs trigger `has_error`
  - Added 3-layer context guard (table row / backtick / prose keyword)
  - Content-wide search preserved for cross-line `Traceback:\n/path/` matching
  - 20/20 tests, 10 FP eliminated across 7d data, 0 regression
- P-20260507-traceback-guard

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
