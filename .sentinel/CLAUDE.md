# Sentinel Learnings for xpoes123/SharpLab

Auto-maintained by Sentinel's memory system. Last updated: 2026-04-21 08:52 UTC

These are patterns learned from completed tasks on this repo.
Claude Code loads this file automatically.

## Warnings (avoid these)

- Temporal workflows should guard against replay of partial side effects (confidence: 7)
- ValueError from max() on empty generators goes unhandled (confidence: 7)
- Same bug pattern exists in /odds and /best-line endpoints (confidence: 6)
- Discord API calls should be idempotent or preceded by state checks (confidence: 4)
- Don't ignore setuptools flat-layout error—it prevents wrong installs (confidence: 4)
- Wildcard include may need tuning if subpackages shouldn't be included (confidence: 4)
- Remember to test 'pip install -e .' after config changes, not just setup (confidence: 4)
- Avoid src-layout migration if it blocks other cross-project imports (confidence: 4)
- Live data source exclusions may filter out entire dataset (confidence: 3)
- Race condition: bot restart between send() and DB mark causes dupes (confidence: 3)

## Conventions & Preferences

- Use try-except with fallback when filtering empty sequences (confidence: 6)
- Consider transaction-like patterns for multi-step operations (confidence: 5)
- Commit DB state before sending Discord messages when possible (confidence: 4)
- Prefer explicit package inclusion over src-layout when refactoring costly (confidence: 4)
- Use pyproject.toml [tool.setuptools] over legacy setup.py for modern projects (confidence: 4)
- Document which dirs are packages vs data/docs in discovery config (confidence: 4)
- Exclude non-package dirs (data/, docs/) from setuptools discovery (confidence: 4)
- Document why fallback behavior is acceptable in code comments (confidence: 3)
- Test edge cases where filters exclude all data sources (confidence: 3)
- Prefer list comprehension over generator when fallback needed (confidence: 3)

## Learned Patterns

- Empty sequence check before max()/min() on generators (confidence: 8)
- DB state updates after external API calls should be atomic or pre-committed (confidence: 6)
- Copy-pasted error handling in similar code paths (confidence: 6)
- Async crash windows between message send and state persistence (confidence: 5)
- Flat-layout multi-package projects need explicit setuptools config (confidence: 4)
- Use [tool.setuptools.packages.find] with include/exclude filters (confidence: 4)
- Wildcard patterns (package*) match package and subpackages (confidence: 4)
- Test with 'pip install -e .' to verify package discovery works (confidence: 4)
- Multiple top-level dirs = setuptools ambiguity guard triggers (confidence: 4)
- LIVE_SOURCES filtering logic duplicated across multiple endpoints (confidence: 3)
- Periodic polling with configurable intervals for sports data
- Threshold-based detection for significant value movements
- Discord webhook integration for real-time notifications
- Database snapshots for historical tracking and analysis
- Modular configuration system for dynamic thresholds
